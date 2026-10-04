"""Show ledger progress without treating partial predictions as quality reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shlex
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def progress(directory: Path, scheduled: int) -> dict:
    identity = None
    for filename in ("run.json", "freeze.json"):
        if (directory / filename).is_file():
            candidate = json.loads((directory / filename).read_text())
            if "document_ids" in candidate:
                identity = candidate
                break
    expected = None
    if identity is not None:
        ids = identity["document_ids"]
        if (not isinstance(ids, list) or not ids or any(not isinstance(id, str) or
                Path(id).name != id or id in (".", "..") for id in ids) or len(set(ids)) != len(ids)):
            raise ValueError("Invalid frozen document schedule")
        expected = {f"{id}.json" for id in ids}
        scheduled = len(expected)
    ledger = json.loads((directory / "completed.json").read_text()) if (directory / "completed.json").exists() else {}
    if not isinstance(ledger, dict) or len(ledger) > scheduled or (expected is not None and set(ledger) - expected):
        raise ValueError("Completion ledger differs from the scheduled documents")
    failed = 0
    failures, timings = Counter(), []
    for name in ledger:
        if Path(name).name != name or not name.endswith(".json"):
            raise ValueError("Unsafe prediction name in completion ledger")
        path = directory / "predictions" / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != ledger[name]:
            raise ValueError("Completed prediction differs from its ledger")
        prediction = json.loads(path.read_text())
        failed += prediction.get("record") is None
        if prediction.get("record") is None:
            failures[prediction.get("failure_type") or "unspecified"] += 1
        seconds = prediction.get("runtime_seconds", {}).get("model")
        if seconds is not None:
            if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
                raise ValueError("Invalid recorded model timing")
            timings.append(seconds)
    sessions = sorted((directory / "sessions").iterdir()) if (directory / "sessions").exists() else []
    progress_at = datetime.fromtimestamp((directory / "completed.json").stat().st_mtime, timezone.utc).isoformat() if ledger else None
    remaining = scheduled - len(ledger)
    timing_summary = None
    if timings:
        ordered = sorted(timings)
        mean = sum(timings) / len(timings)
        timing_summary = {"calls": len(timings), "total_seconds": round(sum(timings), 3),
                          "mean_seconds": round(mean, 3),
                          "p50_seconds": ordered[math.ceil(.5 * len(ordered)) - 1],
                          "p95_seconds": ordered[math.ceil(.95 * len(ordered)) - 1],
                          "remaining_stage_seconds_estimate": round(mean * remaining) if len(timings) >= 5 and mean > 0 else None,
                          "scope": "Completed per-document stages including failures and any page/repair requests; calls counts documents, not HTTP requests. Saved-OCR model stage only. Remaining-stage estimate uses their mean; excludes startup, interrupted uncommitted attempts, queued receipt stages, reports and human review. Partial sample and changing machine load make it provisional; no completion-time guarantee."}
    return {"completed": len(ledger), "scheduled": scheduled, "remaining": remaining, "failed": failed,
            "failure_types": dict(sorted(failures.items())), "model_stage_timing": timing_summary,
            "schedule_source": "frozen run" if identity is not None else "configured release target",
            "report_written": (directory / "report.json").exists(), "last_ledger_update_utc": progress_at,
            "sessions_without_runtime": [s.name for s in sessions if not (s / "runtime.json").exists()],
            "sessions_with_interruption_observation": [s.name for s in sessions if (s / "interruption.json").is_file()],
            "quality_state": "not audited by this progress command; report existence does not establish verified quality",
            "process_state": "not inspected; missing runtime metadata does not establish a live process"}


def runner_status(launch_path: Path, *, inspect: bool = False) -> dict:
    """Inspect only the recorded coordinator PID; never print process arguments."""
    status = {"state": "not_inspected", "scope": "Coordinator identity only; does not establish child-model health or progress."}
    if not launch_path.is_file():
        return {**status, "state": "launch_missing"}
    launch = json.loads(launch_path.read_text())
    pid, command = launch.get("pid"), launch.get("command")
    if (type(pid) is not int or pid <= 0 or not isinstance(command, list) or len(command) < 2 or
            any(not isinstance(part, str) for part in command) or
            Path(command[1]).name != "complete_release_evaluations.py"):
        raise ValueError("Invalid coordinator launch identity")
    status.update(pid=pid, started_at_utc=launch.get("started_at_utc"))
    if not inspect:
        return status
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return {**status, "state": "inspection_unavailable"}
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        return {**status, "state": "not_running"}
    if result.returncode != 0:
        return {**status, "state": "inspection_unavailable"}
    try:
        actual = shlex.split(result.stdout.strip())
    except ValueError:
        return {**status, "state": "identity_mismatch"}
    executable = Path(command[0]).resolve()
    allowed_executables = {executable}
    # macOS framework Python execs its app binary after the recorded bin launcher.
    # Accept only that binary in the same resolved framework version, never an
    # arbitrary process with a matching interpreter name.
    if executable.parent.name == "bin" and "Python.framework" in executable.parts:
        app_binary = executable.parents[1] / "Resources/Python.app/Contents/MacOS/Python"
        if app_binary.is_file():
            allowed_executables.add(app_binary.resolve())
    matches = len(actual) == len(command) and actual[1:] == command[1:] and Path(actual[0]).resolve() in allowed_executables
    return {**status, "state": "running" if matches else "identity_mismatch",
            "observed_at_utc": datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice", type=Path, default=Path("evals/invoice-model-heldout-2026-10-03"))
    parser.add_argument("--receipts", type=Path, default=Path("evals/cord-heldout-2026-10-03"))
    parser.add_argument("--runner-launch", type=Path, default=Path("artifacts/release-runner-2026-10-03/launch.json"))
    parser.add_argument("--inspect-runner", action="store_true", help="Check the recorded coordinator PID and full identity; process arguments are never printed")
    args = parser.parse_args()
    print(json.dumps({"observed_at_utc": datetime.now(timezone.utc).isoformat(),
                      "runner": runner_status(args.runner_launch, inspect=args.inspect_runner),
                      "invoice_model": progress(args.invoice, 180),
                      "receipt_validation_rules": progress(Path("evals/cord-validation-2026-10-03/ocr-rules"), 100),
                      "receipt_validation_model": progress(args.receipts / "validation-model-12", 12),
                      "receipt_test_rules": progress(args.receipts / "test-rules", 100),
                      "receipt_test_model": progress(args.receipts / "test-model", 100),
                      "receipt_comparison_written": (args.receipts / "comparison.json").exists()}, indent=2))


if __name__ == "__main__":
    main()
