"""Trusted supervisor for one bounded, isolated parser job."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from .baseline import extract_invoice_pages
from .intake import IntakeStore, JobClaim, _check_content
from .local_model import (
    PROMPT_SHA256, LocalModelConfig, ModelContextOverflow, ModelOutputInvalid,
    ModelRequestRejected, ModelUnavailable, extract_pages,
)
from .parser_protocol import MAX_PAGE_BYTES, MAX_PAGES, MAX_RESULT_BYTES, PARSER_VERSION
from .review import ReviewConflict, page_from_dict

PARSER_IMAGE = "docwork-parser:v3"
PARSER_TIMEOUT = 600
WORKER_LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30
KNOWN_REJECTIONS = frozenset({
    "PDF_INFO_FAILED", "PDF_ENCRYPTED", "PDF_PAGE_COUNT_UNKNOWN", "PDF_PAGE_LIMIT",
    "PDF_RENDER_FAILED", "IMAGE_PIXEL_LIMIT",
    "IMAGE_DECODE_FAILED", "PAGE_OUTPUT_LIMIT", "SPAN_LIMIT", "RESULT_OUTPUT_LIMIT",
    "SOURCE_SIZE_LIMIT", "UNSUPPORTED_MEDIA_TYPE",
})


class ParserFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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
                *, image: str) -> None:
    if shutil.which("docker") is None:
        raise ParserFailure("PARSER_UNAVAILABLE")
    command = parser_command(source, media_type, output, claim, image=image)
    container_name = command[command.index("--name") + 1]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=PARSER_TIMEOUT, check=False)
    except OSError as exc:
        raise ParserFailure("PARSER_UNAVAILABLE") from exc
    except subprocess.TimeoutExpired as exc:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=10, check=False)
        raise ParserFailure("PARSER_TIMEOUT") from exc
    if completed.returncode:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        if "Cannot connect to the Docker daemon" in error or "failed to connect to the docker API" in error:
            code = "PARSER_UNAVAILABLE"
        elif "Unable to find image" in error or "No such image" in error:
            code = "PARSER_IMAGE_MISSING"
        else:
            code = error if error in KNOWN_REJECTIONS else "PARSER_FAILED"
        raise ParserFailure(code)


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
                model_config: LocalModelConfig | None = None, model_request=None) -> str | None:
    """Claim and process one job; return the document ID or None if idle."""
    if extractor not in ("ocr_rules", "span_llm"):
        raise ValueError("Extractor must be ocr_rules or span_llm")
    if extractor == "span_llm" and model_config is None:
        raise ValueError("span_llm requires a local model configuration")
    claim = store.claim(worker_id, lease_seconds=WORKER_LEASE_SECONDS)
    if claim is None:
        return None
    try:
        try:
            source = store.object_path(claim.document_id)
        except ReviewConflict:
            store.fail(claim, "SOURCE_INTEGRITY_FAILED")
            return claim.document_id
        status = store.status(claim.document_id)
        with tempfile.TemporaryDirectory(prefix="parse-", dir=store.object_root / "quarantine") as scratch:
            output = Path(scratch)
            # The unprivileged container user must be able to write its only output mount.
            output.chmod(0o777)
            with _keep_lease(store, claim):
                (runner or _docker_run)(source, status["media_type"], output, claim, image=image)
                pages, page_bytes = validate_output(output, status["source_sha256"])
                if extractor == "span_llm":
                    if model_request is None:
                        result = extract_pages(pages, model_config)
                    else:
                        result = extract_pages(pages, model_config, model_request)
                else:
                    result = None
                    record = extract_invoice_pages(pages)
            if result is not None:
                store.complete(claim, pages, result.record, page_bytes, profile=extractor,
                               model_id=model_config.model_id, prompt_sha256=PROMPT_SHA256,
                               extra_issues=result.issues)
            else:
                store.complete(claim, pages, record, page_bytes)
    except ModelUnavailable:
        store.fail(claim, "MODEL_UNAVAILABLE")
    except ModelRequestRejected:
        store.fail(claim, "MODEL_API_REJECTED")
    except ModelContextOverflow:
        store.fail(claim, "MODEL_CONTEXT_OVERFLOW")
    except ModelOutputInvalid:
        store.fail(claim, "MODEL_OUTPUT_INVALID")
    except ParserFailure as exc:
        store.fail(claim, exc.code)
    except (OSError, ValueError):
        store.fail(claim, "PARSER_OUTPUT_INVALID")
    return claim.document_id
