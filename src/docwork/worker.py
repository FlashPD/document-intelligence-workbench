"""Trusted supervisor for one bounded, isolated parser job."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .baseline import extract_invoice_pages
from .intake import IntakeStore, JobClaim, JobStopped, _check_content, validate_profile
from .local_model import (
    PROMPT_SHA256, LocalModelConfig, ModelContextOverflow, ModelOutputInvalid,
    ModelRequestRejected, ModelUnavailable, extract_pages,
)
from .parser_protocol import MAX_PAGE_BYTES, MAX_PAGES, MAX_RESULT_BYTES, PARSER_VERSION
from .review import ReviewConflict, page_from_dict
from .storage_budget import StorageLimitExceeded

PARSER_IMAGE = "docwork-parser:v3"
PARSER_TIMEOUT = 600
WORKER_LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30
CHECKPOINT_VERSION = "parser-checkpoint-v1"
KNOWN_REJECTIONS = frozenset({
    "PDF_INFO_FAILED", "PDF_ENCRYPTED", "PDF_PAGE_COUNT_UNKNOWN", "PDF_PAGE_LIMIT",
    "PDF_RENDER_FAILED", "IMAGE_PIXEL_LIMIT",
    "IMAGE_DECODE_FAILED", "PAGE_OUTPUT_LIMIT", "SPAN_LIMIT", "RESULT_OUTPUT_LIMIT",
    "SOURCE_SIZE_LIMIT", "UNSUPPORTED_MEDIA_TYPE",
})


class ParserFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _docker_image_id(image: str) -> str:
    """Resolve a local tag once; run by immutable ID to avoid a tag-change race."""
    if shutil.which("docker") is None:
        raise ParserFailure("PARSER_UNAVAILABLE")
    try:
        completed = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                                   capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ParserFailure("PARSER_UNAVAILABLE") from exc
    identity = completed.stdout.decode("utf-8", errors="replace").strip()
    if completed.returncode:
        error = completed.stderr.decode("utf-8", errors="replace")
        code = "PARSER_IMAGE_MISSING" if "No such image" in error or "No such object" in error else "PARSER_UNAVAILABLE"
        raise ParserFailure(code)
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
        raise ParserFailure("PARSER_IDENTITY_INVALID")
    return identity


def parser_cache_key(source_sha256: str, media_type: str, parser_identity: str) -> str:
    # The image binds renderer, OCR code/assets and fixed configuration. Host
    # source hashes also invalidate results when import/contract checks change.
    root = Path(__file__).parent
    sources = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in (
        "worker.py", "intake.py", "parser_protocol.py", "contracts.py", "review.py", "ocr.py", "geometry.py",
    )}
    payload = {"version": CHECKPOINT_VERSION, "source_sha256": source_sha256,
               "media_type": media_type, "parser_identity": parser_identity,
               "parser_version": PARSER_VERSION, "host_sources": sources}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _regular_file(path: Path, maximum: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ParserFailure("PARSER_OUTPUT_INVALID")
    size = path.stat().st_size
    if not 0 < size <= maximum:
        raise ParserFailure("PARSER_OUTPUT_INVALID")
    return path.read_bytes()


def validate_output(output: Path, source_hash: str) -> tuple[tuple, tuple[bytes, ...]]:
    """Import only the expected regular files and valid canonical data."""
    encoded = _regular_file(output / "result.json", MAX_RESULT_BYTES)
    try:
        result = json.loads(encoded)
        if set(result) != {"parser_version", "source_sha256", "pages"}:
            raise ValueError("Unexpected parser output fields")
        if result["parser_version"] != PARSER_VERSION or result["source_sha256"] != source_hash:
            raise ValueError("Parser provenance mismatch")
        entries = result["pages"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PAGES:
            raise ValueError("Invalid page count")
        expected = {"result.json"} | {f"page-{number:04d}.png" for number in range(1, len(entries) + 1)}
        if {entry.name for entry in output.iterdir()} != expected:
            raise ValueError("Unexpected parser outputs")
        pages = []
        images = []
        for number, entry in enumerate(entries, start=1):
            if set(entry) != {"page_sha256", "page"}:
                raise ValueError("Unexpected page fields")
            path = output / f"page-{number:04d}.png"
            page_bytes = _regular_file(path, MAX_PAGE_BYTES)
            if hashlib.sha256(page_bytes).hexdigest() != entry["page_sha256"]:
                raise ValueError("Rendered page hash mismatch")
            _check_content(path, path.name, "image/png")
            page = page_from_dict(entry["page"])
            if page.number != number or len(page.spans) > 5000:
                raise ValueError("Unsupported canonical page")
            width = int.from_bytes(page_bytes[16:20], "big")
            height = int.from_bytes(page_bytes[20:24], "big")
            if (page.width_px, page.height_px) != (width, height):
                raise ValueError("Page dimensions do not match raster")
            for span in page.spans:
                if not re.fullmatch(rf"p{number}-l\d{{4}}", span.id) or span.method != "tesseract-eng":
                    raise ValueError("Unexpected OCR span provenance")
            pages.append(page)
            images.append(page_bytes)
        return tuple(pages), tuple(images)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ParserFailure("PARSER_OUTPUT_INVALID") from exc


def parser_command(source: Path, media_type: str, output: Path, claim: JobClaim,
                   *, image: str) -> list[str]:
    """One container policy shared by production parsing and live verification."""
    container_name = f"docwork-{claim.job_id[:16]}-{claim.fence}"
    return [
        "docker", "run", "--rm", "--pull", "never", "--name", container_name,
        "--network", "none", "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "64",
        "--memory", "1g", "--cpus", "2", "--user", "65534:65534",
        "--tmpfs", "/tmp:rw,nosuid,noexec,size=64m",
        "--mount", f"type=bind,src={source},dst=/input/original,readonly",
        "--mount", f"type=bind,src={output},dst=/output",
        image, "/input/original", media_type, "/output",
    ]


def _docker_run(source: Path, media_type: str, output: Path, claim: JobClaim,
                *, image: str, stop_event: threading.Event | None = None) -> None:
    if stop_event is not None and stop_event.is_set():
        raise JobStopped("Processing stopped")
    if shutil.which("docker") is None:
        raise ParserFailure("PARSER_UNAVAILABLE")
    command = parser_command(source, media_type, output, claim, image=image)
    container_name = command[command.index("--name") + 1]
    try:
        if stop_event is None:
            completed = subprocess.run(command, capture_output=True, timeout=PARSER_TIMEOUT, check=False)
        else:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + PARSER_TIMEOUT
            try:
                while True:
                    if stop_event.is_set() or time.monotonic() >= deadline:
                        remove_owned_container(container_name)
                        process.kill()
                        process.communicate()
                        if stop_event.is_set():
                            raise JobStopped("Processing stopped")
                        raise ParserFailure("PARSER_TIMEOUT")
                    try:
                        stdout, stderr = process.communicate(timeout=.1)
                        completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
    except OSError as exc:
        raise ParserFailure("PARSER_UNAVAILABLE") from exc
    except subprocess.TimeoutExpired as exc:
        try:
            removed = subprocess.run(["docker", "rm", "-f", container_name],
                                     capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as cleanup_error:
            raise ParserFailure("PARSER_CLEANUP_FAILED") from cleanup_error
        if removed.returncode and "No such container" not in removed.stderr.decode("utf-8", errors="replace"):
            raise ParserFailure("PARSER_CLEANUP_FAILED") from exc
        raise ParserFailure("PARSER_TIMEOUT") from exc
    if completed.returncode:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        if "Cannot connect to the Docker daemon" in error or "failed to connect to the docker API" in error:
            code = "PARSER_UNAVAILABLE"
        elif "Unable to find image" in error or "No such image" in error:
            code = "PARSER_IMAGE_MISSING"
        elif completed.returncode == 137:
            # SIGKILL can be an OOM kill or an external stop. With --rm the
            # container state is gone; never infer OOM from this exit alone.
            code = "PARSER_KILLED"
        else:
            code = error if error in KNOWN_REJECTIONS else "PARSER_FAILED"
        raise ParserFailure(code)


def remove_owned_container(name: str) -> None:
    if not re.fullmatch(r"docwork-[0-9a-f]{16}-[1-9][0-9]*", name):
        raise ReviewConflict("Invalid owned parser container name")
    try:
        result = subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ParserFailure("PARSER_CLEANUP_FAILED") from exc
    if result.returncode and b"No such container" not in result.stderr:
        raise ParserFailure("PARSER_CLEANUP_FAILED")


class _StopSignal(threading.Event):
    def __init__(self, shutdown):
        super().__init__()
        self.shutdown = shutdown

    def is_set(self):
        return super().is_set() or bool(self.shutdown and self.shutdown.is_set())


@contextmanager
def _control(store, claim, shutdown):
    stopped, finished = _StopSignal(shutdown), threading.Event()

    def monitor():
        while not finished.wait(.05):
            if shutdown is not None and shutdown.is_set():
                stopped.set()
                return
            try:
                store.check_claim(claim)
            except (ReviewConflict, OSError):
                stopped.set()
                return

    thread = threading.Thread(target=monitor, name="docwork-attempt-control", daemon=True)
    thread.start()
    try:
        yield stopped
    finally:
        finished.set()
        thread.join()


@contextmanager
def _keep_lease(store: IntakeStore, claim: JobClaim, *,
                lease_seconds: int = WORKER_LEASE_SECONDS,
                interval_seconds: float = HEARTBEAT_SECONDS):
    """Renew a processing claim while blocking parser and model calls run."""
    if interval_seconds <= 0 or interval_seconds >= lease_seconds:
        raise ValueError("Heartbeat interval must be shorter than the lease")
    stop = threading.Event()
    errors: list[Exception] = []

    def heartbeat() -> None:
        while not stop.wait(interval_seconds):
            try:
                store.renew(claim, lease_seconds=lease_seconds)
            except Exception as exc:
                errors.append(exc)
                return

    thread = threading.Thread(target=heartbeat, name="docwork-lease-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()
    if errors:
        raise ReviewConflict("Processing lease renewal failed") from errors[0]


def process_one(store: IntakeStore, worker_id: str, *, image: str = PARSER_IMAGE,
                runner=None, extractor: str = "ocr_rules",
                model_config: LocalModelConfig | None = None, model_request=None,
                parser_identity: str | None = None, reparse: bool = False,
                honor_job_profile: bool = False, stop_event: threading.Event | None = None) -> str | None:
    """Claim and process one job; return the document ID or None if idle."""
    if extractor not in ("ocr_rules", "span_llm"):
        raise ValueError("Extractor must be ocr_rules or span_llm")
    if extractor == "span_llm" and model_config is None and not honor_job_profile:
        raise ValueError("span_llm requires a local model configuration")
    if parser_identity is not None and (runner is None or not re.fullmatch(r"sha256:[0-9a-f]{64}", parser_identity)):
        raise ValueError("An injected parser identity requires a runner and an immutable SHA-256 ID")
    claim = store.claim(worker_id, lease_seconds=WORKER_LEASE_SECONDS,
                        reclaim_expired=not honor_job_profile)
    if claim is None:
        return None
    try:
        if honor_job_profile:
            selected = validate_profile(json.loads(claim.profile_json))
            extractor = selected["extractor"]
            reparse = claim.reparse or reparse
            if extractor == "span_llm":
                if model_config is None or model_config.model_id != selected["model_id"]:
                    raise ModelUnavailable("Queued model profile is unavailable")
                model_config = LocalModelConfig(model_config.endpoint, selected["model_id"],
                                                selected["timeout_seconds"], selected["max_output_tokens"],
                                                model_config.api_key)
        else:
            from .intake import processing_profile
            selected = processing_profile(extractor, model_id=model_config.model_id if extractor == "span_llm" else None,
                                          timeout_seconds=model_config.timeout_seconds if model_config else 150,
                                          max_output_tokens=model_config.max_output_tokens if model_config else 2048)
            store.configure_claim(claim, selected, reparse=reparse)
        try:
            source = store.object_path(claim.document_id)
        except ReviewConflict:
            store.fail(claim, "SOURCE_INTEGRITY_FAILED")
            return claim.document_id
        status = store.status(claim.document_id)
        with _keep_lease(store, claim, lease_seconds=WORKER_LEASE_SECONDS,
                         interval_seconds=HEARTBEAT_SECONDS), _control(store, claim, stop_event) as stopped:
            store.set_stage(claim, "PARSING")
            if runner is None:
                # Allow the bounded parser output and its current raster copy
                # before creating scratch files; imports are guarded separately.
                store.storage_budget.check(MAX_RESULT_BYTES + MAX_PAGE_BYTES * (MAX_PAGES + 1))
            with tempfile.TemporaryDirectory(prefix=f"parse-{claim.job_id[:16]}-{claim.fence}-", dir=store.object_root / "quarantine") as scratch:
                output = Path(scratch)
                # The unprivileged container user must be able to write its only output mount.
                output.chmod(0o777)
                identity = parser_identity if runner is not None else _docker_image_id(image)
                cache_key = parser_cache_key(status["source_sha256"], status["media_type"], identity) if identity else None
                # Arbitrary injected runners have no trustworthy identity and
                # therefore do not checkpoint unless their caller supplies one.
                checkpoint = None
                if cache_key and not reparse:
                    try:
                        checkpoint = store.load_parser_checkpoint(claim, cache_key)
                        if checkpoint:
                            encoded, images = checkpoint
                            (output / "result.json").write_bytes(encoded)
                            for number, data in enumerate(images, start=1):
                                (output / f"page-{number:04d}.png").write_bytes(data)
                            pages, page_bytes = validate_output(output, status["source_sha256"])
                    except (OSError, ValueError, ParserFailure) as exc:
                        raise ParserFailure("PARSER_CHECKPOINT_INVALID") from exc
                if checkpoint:
                    store.record_parser_reuse(claim, cache_key)
                else:
                    if runner is None:
                        _docker_run(source, status["media_type"], output, claim, image=identity, stop_event=stopped)
                    else:
                        runner(source, status["media_type"], output, claim, image=image)
                    if stopped.is_set():
                        raise JobStopped("Processing stopped")
                    store.check_claim(claim)
                    pages, page_bytes = validate_output(output, status["source_sha256"])
                    if cache_key:
                        store.save_parser_checkpoint(claim, cache_key, identity,
                                                     (output / "result.json").read_bytes(), page_bytes)
            # Only imported, checked bytes survive into extraction. A model
            # outage or abrupt exit cannot strand the parser's scratch output.
            if extractor == "span_llm":
                from .request_control import cancellable_request
                store.set_stage(claim, "EXTRACTING")

                def request(config, payload):
                    if stopped.is_set():
                        raise JobStopped("Processing stopped")
                    value = model_request(config, payload) if model_request else cancellable_request(config, payload, stopped)
                    if stopped.is_set():
                        raise JobStopped("Processing stopped")
                    store.check_claim(claim)
                    return value

                result = extract_pages(pages, model_config, request)
            else:
                store.set_stage(claim, "EXTRACTING")
                result = None
                record = extract_invoice_pages(pages)
            if stopped.is_set():
                raise JobStopped("Processing stopped")
            store.set_stage(claim, "CHECKING")
            if result is not None:
                store.complete(claim, pages, result.record, page_bytes, profile=extractor,
                               model_id=model_config.model_id, prompt_sha256=PROMPT_SHA256,
                               extra_issues=result.issues)
            else:
                store.complete(claim, pages, record, page_bytes)
    except JobStopped:
        if not (stop_event and stop_event.is_set()):
            try:
                store.check_claim(claim)
            except JobStopped:
                pass
        store.finish_stopped(claim, shutdown=bool(stop_event and stop_event.is_set()))
    except StorageLimitExceeded as exc:
        store.fail(claim, exc.code)
    except ModelUnavailable:
        store.fail(claim, "MODEL_UNAVAILABLE")
    except ModelRequestRejected:
        store.fail(claim, "MODEL_API_REJECTED")
    except ModelContextOverflow:
        store.fail(claim, "MODEL_CONTEXT_OVERFLOW")
    except ModelOutputInvalid:
        store.fail(claim, "MODEL_OUTPUT_INVALID")
    except ParserFailure as exc:
        if exc.code == "PARSER_CLEANUP_FAILED":
            store.cleanup_failed(claim)
        else:
            store.fail(claim, exc.code)
    except (OSError, ValueError):
        store.fail(claim, "PARSER_OUTPUT_INVALID")
    except ReviewConflict:
        raise
    except Exception:
        # Unexpected extractor failures remain explicit outcomes and cannot
        # strand the serial queue or leak exception text into runtime status.
        if stop_event and stop_event.is_set():
            store.finish_stopped(claim, shutdown=True)
        else:
            store.fail(claim, "PROCESSING_FAILED")
    return claim.document_id
