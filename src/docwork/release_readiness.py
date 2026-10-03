"""Audit portfolio evidence without starting inference or promoting an extractor."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .corpus import verify_synthetic_corpus
from .heldout import verify_heldout
from .invoice_model_run import verify_invoice_model
from .model_runtime import file_hash
from .pilot_bundle import DOCUMENTS, SOURCES, report_pilot
from .receipt_comparison import verify_receipt_comparison
from .workflow_evidence import verify_workflow_evidence

DEFAULTS = {
    "manifest": "datasets/invoices-v1/manifest.json",
    "baseline": "evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1",
    "invoice_model": "evals/invoice-model-heldout-2026-10-03",
    "receipts": "evals/cord-heldout-2026-10-03",
    "receipt_manifest": "artifacts/cord-v2/prepared-v1/manifest.json",
    "parser": "evals/review-release-parser-2026-10-03/report.json",
    "workflow": "evals/real-model-upload-2026-10-03",
    "browser": "evals/review-browser-2026-10-03-v2",
}
DOCS = ("README.md", "docs/system-card.md", "docs/data-card.md", "docs/portfolio-release.md")
PARSER_CHECKS = {
    "container_parser.ParserSmoke." + name for name in (
        "test_abrupt_worker_exit_resumes_committed_parser_checkpoint",
        "test_backup_restores_interrupted_real_parser_checkpoint",
        "test_backup_restores_real_pdf_review_and_immutable_exports",
        "test_container_conflict_review_export_and_reopen",
        "test_container_duplicate_submissions_have_independent_approval",
        "test_container_jpeg_exif_rotation", "test_container_missing_image_retry",
        "test_container_processes_committed_png", "test_container_processes_two_page_pdf",
        "test_container_rejects_eleven_page_pdf", "test_container_rejects_malformed_pdf",
        "test_container_rejects_truncated_png", "test_container_runtime_restrictions",
        "test_model_outage_retry_reuses_real_two_page_parser_output",
    )
} | {
    "container_resources.ParserResourceChecks.test_cgroup_oom_is_recorded_then_real_parser_retry_succeeds",
    "container_resources.ParserResourceChecks.test_timeout_kills_container_then_real_parser_retry_succeeds",
}
BROWSER_CHECKS = {"declared pilot mode", "no source before start", "source canvas and field highlight",
                  "hidden empty workspace", "pause hides review", "reviewer label locked", "resume",
                  "approval", "JSON export", "completion"}
REVIEW_BROWSER_CHECKS = {"recorded provenance", "keyboard field navigation", "native page selector", "native action buttons", "source stays visible during correction",
                        "correction creates revision", "stale revision rejected", "unapproved export blocked",
                        "approval bound to revision", "JSON and CSV downloads", "rotated page pixels and highlights",
                        "page-specific rotation", "field jumps to source page", "missing evidence visible", "responsive layout"}


def safe_path(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or str(path) != relative:
        raise ValueError(f"Expected a repository-relative path: {relative}")
    for index in range(1, len(path.parts) + 1):
        if (root / Path(*path.parts[:index])).is_symlink():
            raise ValueError(f"Evidence symlink is not allowed: {relative}")
    return root / path


def inventory(root: Path, paths: list[Path]) -> dict:
    files = set()
    for path in paths:
        files.update(path.rglob("*")) if path.is_dir() else files.add(path)
    result = {}
    for path in sorted(files):
        checked = safe_path(root, str(path.relative_to(root)))
        if not checked.is_dir():
            result[str(path.relative_to(root))] = file_hash(checked)
    return result


def source_status(root: Path, hashes: dict, required: set[str]) -> dict:
    if not hashes or any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in hashes.values()):
        raise ValueError("Source hash inventory is empty or malformed")
    changed = []
    for name, digest in hashes.items():
        path = safe_path(root, name)
        if not path.is_file() or file_hash(path) != digest:
            changed.append(name)
    missing = sorted(required - set(hashes))
    return {"status": "pending" if changed or missing else "passed",
            "recorded_run": "verified", "source_drift": sorted(changed),
            "unrecorded_sources": missing,
            "note": "Historical evidence remains historical; rerun when source differs. Local hashes are not signed attestation."}


def verify_parser_report(root: Path, path: Path) -> dict:
    report = json.loads(path.read_text())
    checks = report["checks"]
    ids = [check["id"] for check in checks]
    if (report.get("report_version") != "parser-verification-v1" or report.get("status") != "passed" or
            report.get("inputs_unchanged") is not True or len(ids) != len(set(ids)) or
            not PARSER_CHECKS <= set(ids) or any(c["status"] != "passed" for c in checks) or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", report["image"]["Id"])):
        raise ValueError("Parser report lacks complete passing workflow/recovery/resource checks")
    required = {str(p.relative_to(root)) for p in (root / "src/docwork").glob("*.py")}
    required |= {str(p.relative_to(root)) for p in (root / "tests").glob("container_*.py")}
    required |= {"sandbox/Dockerfile", "scripts/verify_parser.py", "samples/clean.png", "samples/conflicting-total.png"}
    return {**source_status(root, report["source_sha256"], required), "checks": len(checks),
            "parser_image_id": report["image"]["Id"], "scope": report["scope"]}


def verify_browser_report(root: Path, directory: Path) -> dict:
    report = json.loads((directory / "report.json").read_text())
    checks = report["checks"]
    if report.get("report_version") == "review-browser-workflow-v1":
        if (report.get("status") != "passed" or report.get("human_timing_measurement") is not False or
                len(checks) != len(set(checks)) or not REVIEW_BROWSER_CHECKS <= set(checks) or
                report.get("cases") != ["inv-f02-02", "inv-f01-12", "inv-f05-30", "inv-f06-04"]):
            raise ValueError("Browser workflow coverage is incomplete or relabeled as human timing")
        actual = inventory(directory, [directory])
        actual.pop("report.json")
        if actual != report["artifacts"]:
            raise ValueError("Browser artifact inventory or checksums differ")
        snapshot = json.loads((directory / "source_snapshot.json").read_text())
        if {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()} != report["source_sha256"]:
            raise ValueError("Browser source snapshot differs")
        for frame in report["frames"]:
            if not frame["path"].startswith("frames/") or frame["path"] not in actual:
                raise ValueError("Browser frame is outside its artifact inventory")
        if len(report["frames"]) < 5 or "index.html" not in actual:
            raise ValueError("Browser demonstration is missing frames or its index")
        recording = report["recording"]
        if recording is not None and ("demo.webm" not in actual or
                (directory / "demo.webm").read_bytes()[:4] != b"\x1aE\xdf\xa3" or
                recording["playback"]["width"] != 1440 or recording["playback"]["height"] != 1190 or
                recording["playback"]["current_time"] <= 0):
            raise ValueError("Browser recording does not record successful playback")
        required = set(SOURCES) | {"scripts/verify_pilot_browser.py", "scripts/verify_review_browser.py", "src/docwork/geometry.py"}
        return {**source_status(root, report["source_sha256"], required), "checks": len(checks),
                "recording": recording, "note": "Scripted recorded-OCR browser workflow; no human timing or fresh extraction claim."}
    if (report.get("report_version") != "review-pilot-browser-check-v1" or report.get("status") != "passed" or
            report.get("human_timing_measurement") is not False or len(checks) != len(set(checks)) or
            not BROWSER_CHECKS <= set(checks) or file_hash(directory / "review.png") != report["screenshot_sha256"]):
        raise ValueError("Browser evidence is incomplete, changed, or relabeled as human timing")
    hashes = {**report["source_sha256"], "scripts/verify_pilot_browser.py": report["verification_script_sha256"]}
    return {**source_status(root, hashes, set(SOURCES) | {"scripts/verify_pilot_browser.py"}), "checks": len(checks),
            "note": "Automated disposable-fixture UI check; no human timing or complete visual acceptance claim."}


def run_contracts(root: Path) -> tuple[dict, str]:
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    started = time.perf_counter()
    try:
        result = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
                                cwd=root, env=env, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "invalid", "tests": None, "note": f"Contract runner failed: {type(exc).__name__}"}, str(exc)
    log = result.stdout + result.stderr
    count = re.search(r"Ran (\d+) tests? in", log)
    passed = result.returncode == 0 and count and int(count[1]) > 0 and re.search(r"\nOK\s*$", log)
    return {"status": "passed" if passed else "invalid", "tests": int(count[1]) if count else 0,
            "returncode": result.returncode, "seconds": round(time.perf_counter() - started, 3),
            "note": "Fresh deterministic contracts; skipped or empty suites cannot pass. No Docker or inference."}, log


def audit_release(root: Path, output: Path, *, parser_report: str | None = None,
                  workflow_directory: str | None = None, browser_directory: str | None = None,
                  pilot_directory: str | None = None, demo_recording: str | None = None) -> dict:
    root, output = root.resolve(strict=True), output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Release audit needs a new output directory")
    paths = {name: safe_path(root, value) for name, value in DEFAULTS.items()}
    for name, override in (("parser", parser_report), ("workflow", workflow_directory), ("browser", browser_directory)):
        if override:
            paths[name] = safe_path(root, override)
    # The audit may not write into source, a corpus, or any input evidence bundle.
    protected = [root / name for name in ("src", "ui", "tests", "scripts", "docs", "config", "datasets", "samples")]
    protected += [p if p.is_dir() or p.suffix == "" else p.parent for p in paths.values()]
    if pilot_directory:
        protected.append(safe_path(root, pilot_directory))
    if any(output.resolve().is_relative_to(p.resolve()) for p in protected):
        raise ValueError("Release audit output must be outside its input evidence and source directories")
    checks, inputs = [], {}
    audit_sources = [*sorted((root / "src/docwork").glob("*.py")), *sorted((root / "tests").glob("test_*.py")),
                     *sorted((root / "ui").glob("*")), root / "pyproject.toml", root / "Makefile"]
    source_hashes = inventory(root, audit_sources)

    def check(name, marker, consumed, verify):
        if marker is None or not marker.exists():
            checks.append({"id": name, "status": "pending", "note": "Required evidence has not been supplied or completed."})
            return
        try:
            before = inventory(root, consumed)
            detail = verify()
            if before != inventory(root, consumed):
                raise ValueError("Evidence changed during verification; rerun after the writer stops")
            inputs.update(before)
            checks.append({"id": name, **detail})
        except (KeyError, TypeError, ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            checks.append({"id": name, "status": "invalid", "note": f"{type(exc).__name__}: {exc}"})

    tests, test_log = run_contracts(root)
    checks.append({"id": "deterministic_contracts", **tests})
    manifest, baseline = paths["manifest"], paths["baseline"]
    check("invoice_corpus", manifest, [manifest],
          lambda: {**verify_synthetic_corpus(manifest), "status": "passed"})

    def invoices(directory, model=False):
        result = verify_invoice_model(manifest, baseline, directory) if model else verify_heldout(manifest, directory)
        report = json.loads((directory / "report.json").read_text())
        if report["split"] != "test" or result["documents"] != 180 or report["summary"]["documents_scheduled"] != 180:
            raise ValueError("Portfolio invoice evidence requires all 180 test invoices")
        return {"status": "passed", "summary": report["summary"],
                "comparison_status": report["comparison"]["status"] if model else None,
                "note": "Synthetic preview/shared-OCR evidence. A model regression is publishable evidence, not default promotion."}

    check("invoice_baseline", baseline / "report.json", [manifest, baseline], lambda: invoices(baseline))
    model = paths["invoice_model"]
    check("invoice_model_comparison", model / "report.json", [manifest, baseline, model], lambda: invoices(model, True))

    def receipts():
        directory = paths["receipts"]
        report = verify_receipt_comparison(paths["receipt_manifest"], directory / "test-rules",
                                           directory / "test-model", directory / "comparison.json")
        if report["split"] != "test" or report["documents"] != 100:
            raise ValueError("Portfolio receipt evidence requires all 100 official test receipts")
        return {"status": "passed", "documents": 100, "baseline": report["baseline"], "model": report["model"],
                "note": "Separate descriptive receipt comparison; no invoice-quality or pretraining independence claim."}

    check("receipt_model_comparison", paths["receipts"] / "comparison.json",
          [paths["receipt_manifest"], paths["receipts"]], receipts)
    check("parser_reliability", paths["parser"], [paths["parser"]], lambda: verify_parser_report(root, paths["parser"]))

    def workflow():
        detail = verify_workflow_evidence(paths["workflow"])
        report = json.loads((paths["workflow"] / "report.json").read_text())
        return {**detail, **source_status(root, report["source_sha256"], {"src/docwork/worker.py", "src/docwork/web.py"}),
                "note": "Two real-model HTTP fixture workflows; rerun changed sources before claiming current-build verification."}

    check("real_model_workflow", paths["workflow"] / "report.json", [paths["workflow"]], workflow)
    check("browser_workflow", paths["browser"] / "report.json", [paths["browser"]],
          lambda: verify_browser_report(root, paths["browser"]))
    missing_docs = [name for name in DOCS if not safe_path(root, name).is_file() or not (root / name).stat().st_size]
    checks.append({"id": "portfolio_documentation", "status": "pending" if missing_docs else "passed",
                   "missing": missing_docs, "note": "Document presence and hashes; editorial claims still need review."})
    inputs.update(inventory(root, [root / name for name in DOCS if name not in missing_docs]))

    def pilot():
        directory = safe_path(root, pilot_directory)
        protocol = json.loads((directory / "protocol.json").read_text())
        if (tuple(d["corpus_id"] for d in protocol["documents"]) != DOCUMENTS or
                protocol["participant"] != "project_author" or protocol["mode"] != "assisted_only"):
            raise ValueError("Pilot must retain all six declared author-study cases")
        report = report_pilot(root, directory)
        return {"status": "passed" if report["status"] == "complete" else "pending", "report": report,
                "note": "Supplied author session audited against revisions/exports. Human participation is not authenticated; no time-saved claim."}

    directory = safe_path(root, pilot_directory) if pilot_directory else None
    check("author_review_pilot", directory / "protocol.json" if directory else None,
          [directory / name for name in ("protocol.json", "source_snapshot.json", "timing.json")] if directory else [], pilot)

    def recording():
        path = safe_path(root, demo_recording)
        if path.suffix.lower() not in {".mp4", ".mov", ".webm"} or path.stat().st_size == 0:
            raise ValueError("Supply a nonempty MP4, MOV, or WebM recording")
        return {"status": "passed", "path": demo_recording,
                "note": "Supplied recording inventoried only; playback, content, and credential redaction require human review."}

    video = safe_path(root, demo_recording) if demo_recording else None
    check("demo_recording", video, [video] if video else [], recording)
    if source_hashes != inventory(root, audit_sources):
        checks.append({"id": "source_stability", "status": "invalid", "note": "Audit sources changed during verification."})
    status = "invalid" if any(c["status"] == "invalid" for c in checks) else (
        "pending" if any(c["status"] == "pending" for c in checks) else "evidence_complete")
    report = {"report_version": "portfolio-readiness-v1", "status": status,
              "created_at_utc": datetime.now(timezone.utc).isoformat(), "checks": checks,
              "input_sha256": inputs, "audit_source_sha256": source_hashes,
              "scope": "Portfolio evidence checklist, not exhaustive architecture acceptance, a signed attestation, "
                       "a production certification, or authorization to tag/publish. Partial inference is never scored. "
                       "No downloads, inference, parser jobs, or automated human review are started.",
              "test_log_sha256": hashlib.sha256(test_log.encode()).hexdigest()}
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".release-audit-", dir=output.parent))
    try:
        (staging / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        (staging / "tests.log").write_text(test_log)
        (staging / "index.html").write_text(render_readiness(report))
        if output.exists() or output.is_symlink():
            raise ValueError("Release audit output appeared during verification")
        staging.rename(output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return report


def render_readiness(report: dict) -> str:
    rows = "".join(f'<article class="{html.escape(c["status"])}"><h2>{html.escape(c["id"].replace("_", " "))}</h2>'
                   f'<p class="status">{html.escape(c["status"])}</p><pre>{html.escape(json.dumps({k: v for k, v in c.items() if k not in {"id", "status"}}, indent=2))}</pre></article>'
                   for c in report["checks"])
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Document Intelligence Workbench — release evidence</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;background:#f4f7fa;color:#192b3c;max-width:1000px;margin:auto;padding:32px}}h1{{line-height:1.15}}article{{background:white;border:1px solid #d7e0e8;border-left:6px solid #267449;border-radius:8px;padding:20px;margin:20px 0}}article.pending{{border-left-color:#b07812}}article.invalid{{border-left-color:#b33333}}h2{{text-transform:capitalize;margin:0}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace}}.status{{font-weight:700}}a{{color:#245b88}}@media(max-width:650px){{body{{padding:16px}}}}</style>
<p>Local Document Intelligence &amp; Review Workbench · Experimental</p><h1>Portfolio release evidence</h1>
<p class="status">{html.escape(report["status"])}</p><p>Snapshot: {html.escape(report["created_at_utc"])}</p>
<p>{html.escape(report["scope"])}</p><p><a href="report.json">Evidence and hashes</a> · <a href="tests.log">Fresh contract test log</a></p>
{rows}</html>'''
