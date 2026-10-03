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
from .comparison import ALLOWED_CHANGES, compare_files, render_html
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
    corpus = commands.add_parser("eval-verify-corpus", help="Verify the frozen 540-document invoice corpus")
    corpus.add_argument("manifest", type=Path, nargs="?", default=Path("datasets/invoices-v1/manifest.json"))
    invoice_run = commands.add_parser("eval-run-invoices", help="OCR trusted corpus previews and score the development split")
    invoice_run.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
    invoice_run.add_argument("--output-dir", type=Path, required=True)
    invoice_run.add_argument("--resume", action="store_true")
    invoice_run.add_argument("--ocr-psm", type=int, choices=(1, 3), default=1,
                             help="Tesseract page segmentation mode; 1 enables orientation detection")
    invoice_verify = commands.add_parser("eval-verify-invoice-run", help="Rescore saved invoice predictions and verify their report")
    invoice_verify.add_argument("run_directory", type=Path)
    invoice_verify.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
    model_eval = commands.add_parser("eval-development-model", help="Score the local span model on the same development PNGs")
    model_eval.add_argument("--model-endpoint", required=True)
    model_eval.add_argument("--model-id", required=True)
    model_eval.add_argument("--output", type=Path, default=Path("artifacts/development-model.json"))
    managed_eval = commands.add_parser("eval-local-model", help="Verify assets and own a local model server for evaluation")
    managed_eval.add_argument("--profile", type=Path, default=Path("config/model-mac-instruct.json"))
    managed_eval.add_argument("--output-dir", type=Path, required=True, help="New directory for scores, predictions, and runtime evidence")
    models = commands.add_parser("models", help="Explicitly fetch or verify pinned model/runtime assets")
    models.add_argument("action", choices=("fetch", "verify"))
    models.add_argument("--profile", type=Path, default=Path("config/model-mac-instruct.json"))
    verify_evidence = commands.add_parser("eval-verify", help="Verify and rescore saved model evidence without inference")
    verify_evidence.add_argument("run_directory", type=Path)
    comparison = commands.add_parser("eval-compare", help="Audit and compare two fresh development runs")
    comparison.add_argument("baseline", type=Path)
    comparison.add_argument("candidate", type=Path)
    comparison.add_argument("--allow-change", action="append", choices=ALLOWED_CHANGES, default=[])
    comparison.add_argument("--max-regression", type=float, default=.02)
    comparison.add_argument("--bootstrap-samples", type=int, default=2000)
    comparison.add_argument("--seed", type=int, default=1729)
    comparison.add_argument("--output", type=Path, default=Path("artifacts/development-comparison.json"))
    comparison.add_argument("--html", type=Path, default=Path("artifacts/development-comparison.html"))
    release_score = commands.add_parser("eval-score-invoices", help="Score saved invoice predictions against a frozen manifest")
    release_score.add_argument("manifest", type=Path)
    release_score.add_argument("predictions", type=Path)
    release_score.add_argument("--split", choices=("development", "calibration", "test"), required=True)
    release_score.add_argument("--output", type=Path, required=True, help="New immutable report path")
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
    intake_reconcile = intake_actions.add_parser("reconcile", help="Audit stored objects and rendered pages")
    intake_reconcile.add_argument("--prune", action="store_true", help="Remove aged unreferenced files")
    intake_reconcile.add_argument("--min-age-seconds", type=int, default=86400,
                                  help="Minimum orphan age for pruning (default: 86400)")
    intake_process = intake_actions.add_parser("process-one", help="Run one job in the isolated parser container")
    intake_process.add_argument("--worker-id", default="local-worker")
    intake_process.add_argument("--image", default="docwork-parser:v3")
    intake_process.add_argument("--extractor", choices=("ocr_rules", "span_llm"), default="ocr_rules")
    intake_process.add_argument("--model-endpoint", help="Loopback HTTP URL of a local chat completion server")
    intake_process.add_argument("--model-id", help="Model ID served by the local endpoint")
    browser = commands.add_parser("serve", help="Run the loopback browser review prototype")
    browser.add_argument("--db", type=Path, default=Path("artifacts/review.sqlite"))
    browser.add_argument("--objects", type=Path, default=Path("artifacts/intake"))
    browser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    exit_code = 0
    try:
        if args.command == "serve":
            from .web import serve
            serve(args.db, args.objects, args.port)
            return 0
        if args.command == "doctor":
            data = doctor()
        elif args.command == "baseline":
            data = baseline_fixture(args.fixture)
        elif args.command == "eval-verify-corpus":
            from .corpus import verify_synthetic_corpus
            data = verify_synthetic_corpus(args.manifest)
        elif args.command == "eval-run-invoices":
            from .invoice_run import run_invoice_baseline
            data = run_invoice_baseline(args.manifest, args.output_dir, resume=args.resume,
                                        ocr_psm=args.ocr_psm)
            print(args.output_dir / "report.json")
            return 0 if data["status"] == "scored" else 2
        elif args.command == "eval-verify-invoice-run":
            from .invoice_run import verify_invoice_run
            data = verify_invoice_run(args.manifest, args.run_directory)
        elif args.command == "models":
            from .model_runtime import fetch_assets, load_profile, verify_assets
            profile = load_profile(args.profile)
            root = Path(__file__).resolve().parents[2]
            if args.action == "fetch":
                data = fetch_assets(root, profile)
            else:
                paths = verify_assets(root, profile)
                data = {"profile": profile["profile"], "status": "verified",
                        "assets": {key: str(path) for key, path in paths.items()}}
        elif args.command == "eval-local-model":
            from .model_runtime import run_managed_evaluation
            data = run_managed_evaluation(Path(__file__).resolve().parents[2], args.profile,
                                          args.output_dir, model_fixture)
            print(args.output_dir / "report.json")
            return 0 if data["summary"]["documents_processed"] == data["summary"]["documents_scheduled"] else 2
        elif args.command == "eval-verify":
            from .evidence import verify_model_evidence
            data = verify_model_evidence(args.run_directory, Path(__file__).resolve().parents[2])
        elif args.command == "eval-development-model":
            config = LocalModelConfig(args.model_endpoint, args.model_id)
            data = evaluate_development(Path(__file__).resolve().parents[2],
                                        lambda path: model_fixture(path, config), extractor={
                                            "variant": "span_llm", "version": PROMPT_VERSION,
                                            "model_id": config.model_id, "prompt_sha256": PROMPT_SHA256,
                                            "timeout_seconds": config.timeout_seconds,
                                            "max_output_tokens": config.max_output_tokens,
                                            "weights_identity": "server alias; weights hash not verified",
                                        })
            data["mode"] = "fresh_development_span_llm"
            data["extractor_version"] = PROMPT_VERSION
            data["model_id"] = config.model_id
            data["prompt_sha256"] = PROMPT_SHA256
            del data["baseline_version"]
        elif args.command == "eval-compare":
            inputs = {args.baseline.resolve(), args.candidate.resolve(),
                      (Path(__file__).resolve().parents[2] / "datasets" / "development-v0.json").resolve()}
            if args.output.resolve() in inputs or args.html.resolve() in inputs or args.output.resolve() == args.html.resolve():
                raise ValueError("comparison output paths must be distinct from each other and the inputs")
            data = compare_files(args.baseline, args.candidate, Path(__file__).resolve().parents[2],
                                 allow_changes=tuple(args.allow_change), max_regression=args.max_regression,
                                 bootstrap_samples=args.bootstrap_samples, seed=args.seed)
            args.html.parent.mkdir(parents=True, exist_ok=True)
            args.html.write_text(render_html(data))
            exit_code = {"pass": 0, "regression": 1, "unusable_evidence": 2}[data["status"]]
        elif args.command == "eval-score-invoices":
            from .release_evaluation import score_saved_invoice_run
            from .review import _atomic_write
            output = args.output.resolve()
            if output == args.manifest.resolve() or output.is_relative_to(args.predictions.resolve()):
                raise ValueError("Report path must be outside the manifest and prediction directory")
            data = score_saved_invoice_run(args.manifest, args.predictions, args.split)
            _atomic_write(output, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
            print(output)
            return 2 if data["status"] == "incomplete_evidence" else 0
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
            elif args.action == "reconcile":
                data = store.reconcile(prune=args.prune, min_age_seconds=args.min_age_seconds)
                if data["missing"] or data["corrupt"] or data["metadata_errors"]:
                    exit_code = 2
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
    if args.command in ("baseline", "eval-development", "eval-development-model", "eval-compare") and args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
        print(args.output)
        if args.command == "eval-compare":
            print(f"{data['status']}: {args.html}")
    else:
        print(rendered, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
