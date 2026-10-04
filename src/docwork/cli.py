"""Local preflight, trusted-fixture OCR baseline, and review prototype CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import sqlite3
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
from .review import ReviewBlocked, ReviewConflict, ReviewStore, _atomic_write, page_from_dict, record_from_dict
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
    release = commands.add_parser("release-check", help="Audit portfolio evidence and run fresh deterministic contracts offline")
    release.add_argument("--output-dir", type=Path, required=True, help="New immutable audit directory")
    release.add_argument("--parser-report", help="Repository-relative current-build parser report")
    release.add_argument("--model-workflow-directory", help="Repository-relative fresh real-model workflow evidence")
    release.add_argument("--browser-directory", help="Repository-relative fresh browser evidence")
    release.add_argument("--pilot-directory", help="Repository-relative stopped author pilot session")
    release.add_argument("--demo-recording", help="Repository-relative recording; content needs human review")
    baseline = commands.add_parser("baseline", help="Run OCR/rules on a self-authored sample PNG")
    baseline.add_argument("fixture", type=Path)
    baseline.add_argument("--output", type=Path)
    development = commands.add_parser("eval-development", help="Score all frozen self-authored development PNGs")
    development.add_argument("--output", type=Path, default=Path("artifacts/development-baseline.json"))
    corpus = commands.add_parser("eval-verify-corpus", help="Verify the frozen 540-document invoice corpus")
    corpus.add_argument("manifest", type=Path, nargs="?", default=Path("datasets/invoices-v1/manifest.json"))
    invoice_run = commands.add_parser("eval-run-invoices", help="OCR trusted corpus previews and score development or calibration")
    invoice_run.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
    invoice_run.add_argument("--output-dir", type=Path, required=True)
    invoice_run.add_argument("--resume", action="store_true")
    invoice_run.add_argument("--split", choices=("development", "calibration"), default="development")
    invoice_run.add_argument("--extractor", choices=("ocr_rules", "spatial_rules"), default="ocr_rules",
                             help="spatial_rules is experimental; calibration rejected default promotion")
    invoice_run.add_argument("--ocr-psm", type=int, choices=(1, 3), default=1,
                             help="Tesseract page segmentation mode; 1 enables orientation detection")
    invoice_verify = commands.add_parser("eval-verify-invoice-run", help="Rescore saved invoice predictions and verify their report")
    invoice_verify.add_argument("run_directory", type=Path)
    invoice_verify.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
    freeze = commands.add_parser("eval-freeze-invoices", help="Freeze the experimental default baseline from development/calibration evidence")
    freeze.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
    freeze.add_argument("--evidence", type=Path, action="append", required=True, help="Verified development or calibration run directory")
    freeze.add_argument("--output", type=Path, required=True)
    heldout = commands.add_parser("eval-heldout-invoices", help="Score every test invoice with an explicit frozen baseline")
    heldout.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
    heldout.add_argument("--freeze", type=Path, required=True)
    heldout.add_argument("--output-dir", type=Path, required=True)
    heldout.add_argument("--resume", action="store_true")
    heldout_verify = commands.add_parser("eval-verify-heldout", help="Verify and rescore a frozen held-out invoice run offline")
    heldout_verify.add_argument("run_directory", type=Path)
    heldout_verify.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
    paired_model = commands.add_parser("eval-invoice-model", help="Compare the pinned model with all frozen test OCR records")
    paired_model.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
    paired_model.add_argument("--baseline", type=Path, default=Path("evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1"))
    paired_model.add_argument("--development", type=Path, default=Path("evals/local-model-span-v2-2026-10-02/model"))
    paired_model.add_argument("--profile", type=Path, default=Path("config/model-mac-instruct.json"))
    paired_model.add_argument("--output-dir", type=Path, required=True)
    paired_model.add_argument("--resume", action="store_true")
    paired_verify = commands.add_parser("eval-verify-invoice-model", help="Audit and rescore a saved paired model comparison")
    paired_verify.add_argument("run_directory", type=Path)
    paired_verify.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
    paired_verify.add_argument("--baseline", type=Path, default=Path("evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1"))
    cord = commands.add_parser("cord", help="Explicitly download, prepare, or verify pinned CORD receipt data")
    cord.add_argument("action", choices=("fetch", "prepare", "verify"))
    cord.add_argument("--profile", type=Path, default=Path("config/cord-v2.json"))
    cord.add_argument("--cache", type=Path, default=Path("artifacts/cord-v2/shards"))
    cord.add_argument("--output-dir", type=Path, default=Path("artifacts/cord-v2/prepared-v1"))
    receipt_run = commands.add_parser("eval-receipts", help="Evaluate receipt rules or the pinned model, with sealed test settings")
    receipt_run.add_argument("--manifest", type=Path, default=Path("artifacts/cord-v2/prepared-v1/manifest.json"))
    receipt_run.add_argument("--profile", type=Path, default=Path("config/model-mac-instruct.json"))
    receipt_run.add_argument("--split", choices=("validation", "test"), default="validation")
    receipt_run.add_argument("--variant", choices=("ocr_rules", "span_llm"), default="ocr_rules")
    receipt_run.add_argument("--ocr-run", type=Path)
    receipt_run.add_argument("--freeze", type=Path)
    receipt_run.add_argument("--limit", type=int, help="Validation smoke subset only; test always schedules all 100")
    receipt_run.add_argument("--resume", action="store_true")
    receipt_run.add_argument("--output-dir", type=Path, required=True)
    receipt_freeze = commands.add_parser("eval-freeze-receipts", help="Freeze receipt settings from verified validation evidence")
    receipt_freeze.add_argument("--manifest", type=Path, default=Path("artifacts/cord-v2/prepared-v1/manifest.json"))
    receipt_freeze.add_argument("--profile", type=Path, default=Path("config/model-mac-instruct.json"))
    receipt_freeze.add_argument("--rules", type=Path, required=True)
    receipt_freeze.add_argument("--model", type=Path, required=True)
    receipt_freeze.add_argument("--output", type=Path, required=True)
    receipt_verify = commands.add_parser("eval-verify-receipts", help="Audit receipt evidence and reproduce scores offline")
    receipt_verify.add_argument("run_directory", type=Path)
    receipt_verify.add_argument("--manifest", type=Path, default=Path("artifacts/cord-v2/prepared-v1/manifest.json"))
    receipt_compare = commands.add_parser("eval-compare-receipts", help="Write an audited paired CORD comparison")
    receipt_compare.add_argument("baseline", type=Path)
    receipt_compare.add_argument("model", type=Path)
    receipt_compare.add_argument("--manifest", type=Path, default=Path("artifacts/cord-v2/prepared-v1/manifest.json"))
    receipt_compare.add_argument("--output", type=Path, required=True)
    finalize = commands.add_parser("eval-finalize-heldout", help="Finalize fully saved frozen predictions after a reporting-only failure")
    finalize.add_argument("run_directory", type=Path)
    finalize.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
    priority = commands.add_parser("eval-review-priority", help="Measure triage coverage on a verified development or calibration invoice run")
    priority.add_argument("run_directory", type=Path)
    priority.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
    priority.add_argument("--output", type=Path, required=True)
    priority_verify = commands.add_parser("eval-verify-review-priority", help="Recompute a saved triage report")
    priority_verify.add_argument("run_directory", type=Path)
    priority_verify.add_argument("report", type=Path)
    priority_verify.add_argument("--manifest", type=Path, default=Path("datasets/invoices-v1/manifest.json"))
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
    workflow_verify = commands.add_parser("eval-verify-model-workflow", help="Check saved real-model upload workflow evidence offline")
    workflow_verify.add_argument("run_directory", type=Path)
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
    intake.add_argument("--max-artifact-mib", type=int, help="Persist the whole-workbench artifact budget")
    intake.add_argument("--disk-reserve-mib", type=int, help="Persist the minimum free-disk reserve")
    intake_actions = intake.add_subparsers(dest="action", required=True)
    intake_submit = intake_actions.add_parser("submit", help="Validate and store one local document")
    intake_submit.add_argument("file", type=Path)
    intake_submit.add_argument("--mime", required=True, choices=("application/pdf", "image/png", "image/jpeg"))
    intake_submit.add_argument("--extractor", choices=("ocr_rules", "span_llm"), default="ocr_rules")
    intake_submit.add_argument("--model-id")
    intake_status = intake_actions.add_parser("status", help="Show submission and job state")
    intake_status.add_argument("document_id")
    intake_page = intake_actions.add_parser("page", help="Show the verified rendered page path")
    intake_page.add_argument("document_id")
    intake_page.add_argument("--number", type=int, default=1, help="One-based source page number")
    intake_retry = intake_actions.add_parser("retry", help="Requeue a failed unreviewed document")
    intake_retry.add_argument("document_id")
    for action_name in ("cancel", "delete", "deletion-status", "attempts"):
        intake_actions.add_parser(action_name).add_argument("document_id")
    intake_actions.add_parser("resume-deletions", help="Resume pending cleanup and deletion jobs")
    intake_actions.add_parser("storage", help="Show artifact usage and configured limits")
    intake_reprocess = intake_actions.add_parser("reprocess", help="Queue a new extraction without reusing approval")
    intake_reprocess.add_argument("document_id")
    intake_reprocess.add_argument("--revision", type=int, required=True)
    intake_reprocess.add_argument("--extractor", choices=("ocr_rules", "span_llm"))
    intake_reprocess.add_argument("--model-id")
    intake_reprocess.add_argument("--reparse", action="store_true")
    intake_reconcile = intake_actions.add_parser("reconcile", help="Audit stored objects and rendered pages")
    intake_reconcile.add_argument("--prune", action="store_true", help="Remove aged unreferenced files")
    intake_reconcile.add_argument("--min-age-seconds", type=int, default=86400,
                                  help="Minimum orphan age for pruning (default: 86400)")
    intake_process = intake_actions.add_parser("process-one", help="Run one job in the isolated parser container")
    intake_process.add_argument("--worker-id", default="local-worker")
    intake_process.add_argument("--image", default="docwork-parser:v3")
    intake_process.add_argument("--reparse", action="store_true", help="Bypass saved parsing results and refresh the checkpoint")
    intake_process.add_argument("--extractor", choices=("ocr_rules", "span_llm"))
    intake_process.add_argument("--model-endpoint", help="Loopback HTTP URL of a local chat completion server")
    intake_process.add_argument("--model-id", help="Model ID served by the local endpoint")
    backup = commands.add_parser("backup", help="Create, verify, or restore a portable workbench backup")
    backup_actions = backup.add_subparsers(dest="action", required=True)
    backup_create = backup_actions.add_parser("create", help="Snapshot SQLite and all referenced artifacts")
    backup_create.add_argument("--db", type=Path, default=Path("artifacts/review.sqlite"))
    backup_create.add_argument("--objects", type=Path, default=Path("artifacts/intake"))
    backup_create.add_argument("--exports", type=Path, help="Custom export root; default is beside the database")
    backup_create.add_argument("--output", type=Path, required=True, help="New backup directory")
    backup_verify = backup_actions.add_parser("verify", help="Check database, references, and every artifact checksum offline")
    backup_verify.add_argument("bundle", type=Path)
    backup_restore = backup_actions.add_parser("restore", help="Restore into a new workbench directory")
    backup_restore.add_argument("bundle", type=Path)
    backup_restore.add_argument("--output", type=Path, required=True, help="New destination; existing directories are refused")
    browser = commands.add_parser("serve", help="Run the loopback browser review prototype")
    browser.add_argument("--db", type=Path, default=Path("artifacts/review.sqlite"))
    browser.add_argument("--objects", type=Path, default=Path("artifacts/intake"))
    browser.add_argument("--port", type=int, default=8765)
    browser.add_argument("--model-profile", type=Path, help="Verify local assets and own a pinned authenticated model server")
    browser.add_argument("--max-artifact-mib", type=int)
    browser.add_argument("--disk-reserve-mib", type=int)
    replay = commands.add_parser("demo-replay", help="Open a model-free portfolio demo using recorded development OCR")
    replay.add_argument("--output-dir", type=Path, help="New workbench directory; default creates a unique directory under artifacts/demo-replay")
    replay.add_argument("--port", type=int, default=8765)
    replay.add_argument("--prepare-only", action="store_true", help="Verify and prepare the offline workbench without starting HTTP")
    args = parser.parse_args(argv)
    exit_code = 0
    try:
        if args.command == "demo-replay":
            import uuid
            from .demo_replay import prepare_replay
            from .web import serve
            root = Path(__file__).resolve().parents[2]
            output = args.output_dir or root / "artifacts/demo-replay" / uuid.uuid4().hex
            replay = prepare_replay(root, output)
            print(f"Recorded OCR replay · fictional development invoices · no live extraction\nWorkbench saved at {output.resolve()}", flush=True)
            if not args.prepare_only:
                serve(output / "review.sqlite", output / "objects", args.port, demo_replay=replay)
            return 0
        if args.command == "serve":
            from .web import serve
            storage_options = {"max_artifact_bytes": args.max_artifact_mib * 1024**2 if args.max_artifact_mib is not None else None,
                               "disk_reserve_bytes": args.disk_reserve_mib * 1024**2 if args.disk_reserve_mib is not None else None}
            if args.model_profile:
                import uuid
                from .model_runtime import load_profile, managed_server
                root = Path(__file__).resolve().parents[2]
                profile = load_profile(args.model_profile)
                session = root / "artifacts" / "model-sessions" / uuid.uuid4().hex
                session.mkdir(parents=True)
                _atomic_write(session / "profile.json", args.model_profile.read_bytes())
                runtime = None
                try:
                    with managed_server(root, profile, session / "server.log") as (config, runtime):
                        print(f"Pinned model ready: {profile['profile']} ({config.model_id})", flush=True)
                        serve(args.db, args.objects, args.port, model_config=config, model_profile=profile["profile"], **storage_options)
                finally:
                    if runtime is not None:
                        _atomic_write(session / "runtime.json", (json.dumps(runtime, indent=2) + "\n").encode())
            else:
                serve(args.db, args.objects, args.port, **storage_options)
            return 0
        if args.command == "doctor":
            data = doctor()
        elif args.command == "release-check":
            from .release_readiness import audit_release
            report = audit_release(Path(__file__).resolve().parents[2], args.output_dir,
                                   parser_report=args.parser_report, workflow_directory=args.model_workflow_directory,
                                   browser_directory=args.browser_directory, pilot_directory=args.pilot_directory,
                                   demo_recording=args.demo_recording)
            data = {"status": report["status"], "checks": [{"id": c["id"], "status": c["status"]} for c in report["checks"]],
                    "report": str(args.output_dir / "report.json"), "html": str(args.output_dir / "index.html")}
            exit_code = {"evidence_complete": 0, "pending": 1, "invalid": 2}[report["status"]]
        elif args.command == "backup":
            from .backup import create_backup, restore_backup, verify_backup
            if args.action == "create":
                data = create_backup(args.db, args.objects, args.output, export_root=args.exports)
            elif args.action == "verify":
                data = verify_backup(args.bundle)
            else:
                data = restore_backup(args.bundle, args.output)
        elif args.command == "baseline":
            data = baseline_fixture(args.fixture)
        elif args.command == "eval-verify-model-workflow":
            from .workflow_evidence import verify_workflow_evidence
            data = verify_workflow_evidence(args.run_directory)
        elif args.command == "eval-verify-corpus":
            from .corpus import verify_synthetic_corpus
            data = verify_synthetic_corpus(args.manifest)
        elif args.command == "eval-run-invoices":
            from .invoice_run import run_invoice_baseline
            data = run_invoice_baseline(args.manifest, args.output_dir, resume=args.resume,
                                        ocr_psm=args.ocr_psm, split=args.split, extractor=args.extractor)
            print(args.output_dir / "report.json")
            return 0 if data["status"] == "scored" else 2
        elif args.command == "eval-verify-invoice-run":
            from .invoice_run import verify_invoice_run
            data = verify_invoice_run(args.manifest, args.run_directory)
        elif args.command == "eval-freeze-invoices":
            from .heldout import create_freeze
            create_freeze(Path(__file__).resolve().parents[2], args.manifest, args.output, args.evidence)
            print(args.output)
            return 0
        elif args.command == "eval-heldout-invoices":
            from .heldout import run_heldout
            data = run_heldout(args.manifest, args.freeze, args.output_dir, resume=args.resume)
            print(args.output_dir / "report.json")
            return 0 if data["status"] == "scored" and not data["summary"]["failures_by_type"] else 2
        elif args.command == "eval-verify-heldout":
            from .heldout import verify_heldout
            data = verify_heldout(args.manifest, args.run_directory)
        elif args.command == "eval-invoice-model":
            from .invoice_model_run import run_invoice_model
            data = run_invoice_model(Path(__file__).resolve().parents[2], args.manifest, args.baseline,
                                     args.development, args.profile, args.output_dir, resume=args.resume)
            print(args.output_dir / "report.json")
            return 2 if data["summary"]["failures_by_type"] else 1 if data["comparison"]["status"] == "regression" else 0
        elif args.command == "eval-verify-invoice-model":
            from .invoice_model_run import verify_invoice_model
            data = verify_invoice_model(args.manifest, args.baseline, args.run_directory)
        elif args.command == "cord":
            from .cord import fetch_cord, prepare_cord, verify_cord
            if args.action == "fetch":
                data = fetch_cord(args.profile, args.cache)
            elif args.action == "prepare":
                data = prepare_cord(args.profile, args.cache, args.output_dir)
            else:
                data = verify_cord(args.output_dir / "manifest.json")
        elif args.command == "eval-receipts":
            from .receipt_run import run_receipts
            data = run_receipts(Path(__file__).resolve().parents[2], args.manifest, args.profile,
                                args.output_dir, split=args.split, variant=args.variant,
                                ocr_run=args.ocr_run, freeze_path=args.freeze, limit=args.limit, resume=args.resume)
            print(args.output_dir / "report.json")
            return 2 if data["summary"]["failures_by_type"] else 0
        elif args.command == "eval-freeze-receipts":
            from .receipt_run import freeze_receipts
            data = freeze_receipts(args.manifest, args.rules, args.model, args.profile, args.output)
        elif args.command == "eval-verify-receipts":
            from .receipt_run import verify_receipt_run
            data = verify_receipt_run(args.manifest, args.run_directory)
        elif args.command == "eval-compare-receipts":
            from .receipt_comparison import compare_receipts
            data = compare_receipts(args.manifest, args.baseline, args.model, args.output)
        elif args.command == "eval-finalize-heldout":
            from .heldout import finalize_saved_heldout
            data = finalize_saved_heldout(args.manifest, args.run_directory)
            print(args.run_directory / "report.json")
            return 0 if data["status"] == "scored" and not data["summary"]["failures_by_type"] else 2
        elif args.command == "eval-review-priority":
            from .priority_evaluation import write_priority_report
            write_priority_report(args.manifest, args.run_directory, args.output)
            print(args.output)
            return 0
        elif args.command == "eval-verify-review-priority":
            from .priority_evaluation import verify_priority_report
            data = verify_priority_report(args.manifest, args.run_directory, args.report)
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
            from .intake import processing_profile
            store = IntakeStore(args.db, args.objects,
                                max_artifact_bytes=args.max_artifact_mib * 1024**2 if args.max_artifact_mib is not None else None,
                                disk_reserve_bytes=args.disk_reserve_mib * 1024**2 if args.disk_reserve_mib is not None else None)
            if args.action == "submit":
                with args.file.open("rb") as source:
                    document_id = store.submit(source, args.file.name, args.mime,
                                               profile=processing_profile(args.extractor, model_id=args.model_id))
                data = store.status(document_id)
            elif args.action == "cancel":
                store.cancel(args.document_id)
                data = store.status(args.document_id)
            elif args.action == "reprocess":
                profile = processing_profile(args.extractor, model_id=args.model_id) if args.extractor else None
                store.reprocess(args.document_id, profile=profile, reparse=args.reparse, expected_revision=args.revision)
                data = store.status(args.document_id)
            elif args.action == "delete":
                data = store.request_delete(args.document_id)
                store.run_deletions()
                data = store.deletion_status(args.document_id)
            elif args.action == "deletion-status":
                data = store.deletion_status(args.document_id)
            elif args.action == "resume-deletions":
                store.recover_stops()
                data = {"completed": store.run_deletions()}
            elif args.action == "attempts":
                data = store.attempts(args.document_id)
            elif args.action == "storage":
                data = store.storage_budget.inventory()
            elif args.action == "retry":
                store.retry(args.document_id)
                data = store.status(args.document_id)
            elif args.action == "reconcile":
                data = store.reconcile(prune=args.prune, min_age_seconds=args.min_age_seconds)
                if data["missing"] or data["corrupt"] or data["metadata_errors"]:
                    exit_code = 2
            elif args.action == "process-one":
                store.recover_stops()
                model_config = (LocalModelConfig(args.model_endpoint or "", args.model_id or "")
                                if args.extractor == "span_llm" or args.model_endpoint else None)
                document_id = process_one(store, args.worker_id, image=args.image,
                                          extractor=args.extractor or "ocr_rules", model_config=model_config,
                                          reparse=args.reparse, honor_job_profile=args.extractor is None)
                data = store.status(document_id) if document_id else {"status": "IDLE"}
            elif args.action == "page":
                data = {"document_id": args.document_id, "page_number": args.number,
                        "page_image": str(store.page_image_path(args.document_id, args.number))}
            else:
                data = store.status(args.document_id)
        else:
            data = evaluate_development(Path(__file__).resolve().parents[2], baseline_fixture)
    except (KeyError, OSError, RuntimeError, ValueError, sqlite3.Error, ReviewConflict, ReviewBlocked, subprocess.TimeoutExpired) as exc:
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
