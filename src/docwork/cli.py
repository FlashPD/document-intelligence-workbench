"""Local preflight, trusted-fixture OCR baseline, and review prototype CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .baseline import BASELINE_VERSION, extract_invoice
from .evaluation import evaluate_development
from .intake import IntakeStore
from .local_model import (
    PROMPT_SHA256, PROMPT_VERSION, LocalModelConfig, ModelContextOverflow,
    ModelOutputInvalid, ModelRequestRejected, ModelUnavailable, extract_pages,
)
from .ocr import tesseract_page
from .review import ReviewBlocked, ReviewConflict, ReviewStore, page_from_dict, record_from_dict
from .validation import validate_invoice
from .worker import process_one


def _run(args: list[str], timeout: int = 5) -> str | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def doctor() -> dict:
    hardware = _run(["system_profiler", "SPHardwareDataType"]) if sys.platform == "darwin" else None
    facts = {}
    if hardware:
        for line in hardware.splitlines():
            key, _, value = line.strip().partition(": ")
            if key in {"Model Name", "Model Identifier", "Chip", "Memory"}:
                facts[key.lower().replace(" ", "_")] = value
    tesseract = shutil.which("tesseract")
    langs = _run([tesseract, "--list-langs"]) if tesseract else None
    tesseract_version = _run([tesseract, "--version"]) if tesseract else None
    docker = shutil.which("docker")
    docker_version = _run([docker, "info", "--format", "{{.ServerVersion}}"], timeout=3) if docker else None
    usage = shutil.disk_usage(Path.cwd())
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "python_3_12_or_newer": sys.version_info >= (3, 12),
        "hardware": facts,
        "free_disk_gib": round(usage.free / 1024**3, 1),
        "tesseract_available": bool(tesseract),
        "tesseract_version": tesseract_version.splitlines()[0] if tesseract_version else None,
        "tesseract_languages": [line for line in (langs or "").splitlines()[1:] if line],
        "docker_daemon_available": docker_version is not None,
        "docker_server_version": docker_version,
        "local_model_status": "configured_per_processing_job; not_probed_by_doctor",
        "phase_0_ready_for_fixture_baseline": bool(tesseract and langs and "eng" in langs),
    }


def baseline_fixture(path: Path) -> dict:
    repo_samples = Path(__file__).resolve().parents[2] / "samples"
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(repo_samples.resolve()) or resolved.suffix.lower() != ".png":
        raise ValueError("Phase 0 baseline accepts only PNGs in this repository's samples directory")
    start = time.perf_counter()
    page = tesseract_page(resolved)
    ocr_seconds = time.perf_counter() - start
    record = extract_invoice(page)
    issues = validate_invoice(record, page)
    return {
        "mode": "fresh_fixture_ocr_rules",
        "baseline_version": BASELINE_VERSION,
        "python_version": platform.python_version(),
        "tesseract_version": (_run(["tesseract", "--version"]) or "unknown").splitlines()[0],
        "source_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "source_name": resolved.name,
        "runtime_seconds": {"ocr": round(ocr_seconds, 3), "total": round(time.perf_counter() - start, 3)},
        "page": asdict(page),
        "record": record.to_dict(),
        "issues": [asdict(issue) for issue in issues],
    }


def model_fixture(path: Path, config: LocalModelConfig) -> dict:
    """Run fresh trusted-fixture OCR and the local span model, without gold labels."""
    repo_samples = Path(__file__).resolve().parents[2] / "samples"
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(repo_samples.resolve()) or resolved.suffix.lower() != ".png":
        raise ValueError("Development model evaluation accepts only committed sample PNGs")
    start = time.perf_counter()
    page = tesseract_page(resolved)
    ocr_seconds = time.perf_counter() - start
    model_start = time.perf_counter()
    try:
        extracted = extract_pages((page,), config)
    except (ModelUnavailable, ModelRequestRejected, ModelOutputInvalid, ModelContextOverflow) as exc:
        record, issues, failure = None, [], type(exc).__name__
    else:
        record = extracted.record.to_dict()
        issues = [asdict(issue) for issue in validate_invoice(extracted.record, page)]
        failure = None
    result = {
        "python_version": platform.python_version(),
        "tesseract_version": (_run(["tesseract", "--version"]) or "unknown").splitlines()[0],
        "page": asdict(page), "record": record, "issues": issues,
        "runtime_seconds": {"ocr": round(ocr_seconds, 3),
                            "model": round(time.perf_counter() - model_start, 3)},
    }
    if failure:
        result["failure_type"] = failure
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docwork")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Report local Phase 0 runtime facts")
    baseline = commands.add_parser("baseline", help="Run OCR/rules on a self-authored sample PNG")
    baseline.add_argument("fixture", type=Path)
    baseline.add_argument("--output", type=Path)
    development = commands.add_parser("eval-development", help="Score all frozen self-authored development PNGs")
    development.add_argument("--output", type=Path, default=Path("artifacts/development-baseline.json"))
    model_eval = commands.add_parser("eval-development-model", help="Score the local span model on the same development PNGs")
    model_eval.add_argument("--model-endpoint", required=True)
    model_eval.add_argument("--model-id", required=True)
    model_eval.add_argument("--output", type=Path, default=Path("artifacts/development-model.json"))
    review = commands.add_parser("review", help="Local review of trusted fixture candidates")
    review.add_argument("--db", type=Path, default=Path("artifacts/review.sqlite"))
    actions = review.add_subparsers(dest="action", required=True)
    seed = actions.add_parser("seed", help="OCR a trusted sample into a new review record")
    seed.add_argument("fixture", type=Path)
    show = actions.add_parser("show", help="Show current or historical record")
    show.add_argument("document_id")
    show.add_argument("--revision", type=int)
    history = actions.add_parser("history", help="Show review events")
    history.add_argument("document_id")
    edit = actions.add_parser("edit", help="Create a new revision with a field correction")
    edit.add_argument("document_id")
    edit.add_argument("path", help="fields.total or line_items.row-001.description")
    edit.add_argument("value")
    edit.add_argument("--revision", type=int, required=True)
    edit.add_argument("--actor", required=True)
    edit.add_argument("--evidence", action="append", help="Replacement source span ID; repeat for multiple")
    acknowledge = actions.add_parser("acknowledge", help="Resolve a blocking issue with a reason")
    acknowledge.add_argument("document_id")
    acknowledge.add_argument("code")
    acknowledge.add_argument("path")
    acknowledge.add_argument("--revision", type=int, required=True)
    acknowledge.add_argument("--actor", required=True)
    acknowledge.add_argument("--reason", required=True)
    approve = actions.add_parser("approve", help="Approve the exact current revision")
    approve.add_argument("document_id")
    approve.add_argument("--revision", type=int, required=True)
    approve.add_argument("--actor", required=True)
    export = actions.add_parser("export", help="Write an immutable approved JSON or CSV export")
    export.add_argument("document_id")
    export.add_argument("--format", choices=("json", "csv"), required=True)
    intake = commands.add_parser("intake", help="Store bounded documents and enqueue parser jobs")
    intake.add_argument("--db", type=Path, default=Path("artifacts/review.sqlite"))
    intake.add_argument("--objects", type=Path, default=Path("artifacts/intake"))
    intake_actions = intake.add_subparsers(dest="action", required=True)
    intake_submit = intake_actions.add_parser("submit", help="Validate and store one local document")
    intake_submit.add_argument("file", type=Path)
    intake_submit.add_argument("--mime", required=True, choices=("application/pdf", "image/png", "image/jpeg"))
    intake_status = intake_actions.add_parser("status", help="Show submission and job state")
    intake_status.add_argument("document_id")
    intake_page = intake_actions.add_parser("page", help="Show the verified rendered page path")
    intake_page.add_argument("document_id")
    intake_page.add_argument("--number", type=int, default=1, help="One-based source page number")
    intake_retry = intake_actions.add_parser("retry", help="Requeue a failed unreviewed document")
    intake_retry.add_argument("document_id")
    intake_process = intake_actions.add_parser("process-one", help="Run one job in the isolated parser container")
    intake_process.add_argument("--worker-id", default="local-worker")
    intake_process.add_argument("--image", default="docwork-parser:v2")
    intake_process.add_argument("--extractor", choices=("ocr_rules", "span_llm"), default="ocr_rules")
    intake_process.add_argument("--model-endpoint", help="Loopback HTTP URL of a local chat completion server")
    intake_process.add_argument("--model-id", help="Model ID served by the local endpoint")
    browser = commands.add_parser("serve", help="Run the loopback browser review prototype")
    browser.add_argument("--db", type=Path, default=Path("artifacts/review.sqlite"))
    browser.add_argument("--objects", type=Path, default=Path("artifacts/intake"))
    browser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .web import serve
            serve(args.db, args.objects, args.port)
            return 0
        if args.command == "doctor":
            data = doctor()
        elif args.command == "baseline":
            data = baseline_fixture(args.fixture)
        elif args.command == "eval-development-model":
            config = LocalModelConfig(args.model_endpoint, args.model_id)
            data = evaluate_development(Path(__file__).resolve().parents[2],
                                        lambda path: model_fixture(path, config))
            data["mode"] = "fresh_development_span_llm"
            data["extractor_version"] = PROMPT_VERSION
            data["model_id"] = config.model_id
            data["prompt_sha256"] = PROMPT_SHA256
            del data["baseline_version"]
        elif args.command == "review":
            store = ReviewStore(args.db)
            if args.action == "seed":
                candidate = baseline_fixture(args.fixture)
                document_id = store.ingest(candidate["source_sha256"], candidate["source_name"],
                                           page_from_dict(candidate["page"]), record_from_dict(candidate["record"]))
                data = store.get(document_id)
            elif args.action == "show":
                data = store.get(args.document_id, args.revision)
            elif args.action == "history":
                data = store.history(args.document_id)
            elif args.action == "edit":
                revision = store.edit(args.document_id, args.revision, args.path, args.value, args.actor,
                                      tuple(args.evidence) if args.evidence is not None else None)
                data = store.get(args.document_id, revision)
            elif args.action == "acknowledge":
                store.acknowledge(args.document_id, args.revision, args.code, args.path, args.reason, args.actor)
                data = store.get(args.document_id)
            elif args.action == "approve":
                data = store.approve(args.document_id, args.revision, args.actor)
            else:
                data = store.export(args.document_id, args.format)
        elif args.command == "intake":
            store = IntakeStore(args.db, args.objects)
            if args.action == "submit":
                with args.file.open("rb") as source:
                    document_id = store.submit(source, args.file.name, args.mime)
                data = store.status(document_id)
            elif args.action == "retry":
                store.retry(args.document_id)
                data = store.status(args.document_id)
            elif args.action == "process-one":
                model_config = (LocalModelConfig(args.model_endpoint or "", args.model_id or "")
                                if args.extractor == "span_llm" else None)
                document_id = process_one(store, args.worker_id, image=args.image,
                                          extractor=args.extractor, model_config=model_config)
                data = store.status(document_id) if document_id else {"status": "IDLE"}
            elif args.action == "page":
                data = {"document_id": args.document_id, "page_number": args.number,
                        "page_image": str(store.page_image_path(args.document_id, args.number))}
            else:
                data = store.status(args.document_id)
        else:
            data = evaluate_development(Path(__file__).resolve().parents[2], baseline_fixture)
    except (KeyError, OSError, RuntimeError, ValueError, ReviewConflict, ReviewBlocked, subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"docwork: {exc}\n")
    rendered = json.dumps(data, indent=2) + "\n"
    if args.command in ("baseline", "eval-development", "eval-development-model") and args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
        print(args.output)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
