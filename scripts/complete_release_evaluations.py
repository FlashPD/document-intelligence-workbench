"""Finish the serialized invoice/receipt experiments without concurrent models.

This invokes the public CLI and checks each saved report. It never modifies model
settings or retries ledger-completed failures to improve a test score.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from docwork.invoice_model_run import verify_invoice_model
from docwork.receipt_comparison import compare_receipts, verify_receipt_comparison


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice-run", type=Path, required=True)
    parser.add_argument("--wait-for-invoice", action="store_true", help="Wait for an already running owned invoice model session")
    parser.add_argument("--receipt-root", type=Path, required=True)
    parser.add_argument("--html-output", type=Path, help="Export an audited standalone comparison after both experiments finish")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = root / "artifacts/cord-v2/prepared-v1/manifest.json"
    baseline = root / "evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1"
    if args.wait_for_invoice:
        stopped_at = None
        while not (args.invoice_run / "report.json").exists():
            sessions = list((args.invoice_run / "sessions").iterdir())
            if sessions and all((session / "runtime.json").exists() for session in sessions):
                stopped_at = stopped_at or time.monotonic()
                if time.monotonic() - stopped_at > 180:
                    raise RuntimeError("Invoice inference stopped without a report; resume that frozen run first")
            print("Waiting for the frozen invoice model run before starting receipt inference", flush=True)
            time.sleep(30)
    verify_invoice_model(root / "datasets/invoices-v1/manifest.json", baseline, args.invoice_run)
    args.receipt_root.mkdir(parents=True, exist_ok=True)

    def run(arguments: list[str], report: Path):
        command = [sys.executable, "-m", "docwork.cli", *arguments]
        if report.exists():
            print(f"Retaining existing evidence: {report}", flush=True)
            return
        result = subprocess.run(command, cwd=root, check=False)
        # A quality regression or fully recorded processing failures have their
        # own report. An execution failure cannot advance to the next stage.
        if result.returncode not in (0, 1, 2) or not report.is_file():
            raise RuntimeError(f"Evaluation did not publish complete evidence: {report}")

    development_rules = root / "evals/cord-validation-2026-10-03/ocr-rules"
    subprocess.run([sys.executable, "-m", "docwork.cli", "eval-verify-receipts", str(development_rules)], cwd=root, check=True)
    development_model = args.receipt_root / "validation-model-12"
    model_arguments = ["eval-receipts", "--variant", "span_llm", "--limit", "12", "--ocr-run", str(development_rules),
                       "--output-dir", str(development_model)]
    if development_model.exists():
        model_arguments.append("--resume")
    run(model_arguments, development_model / "report.json")
    freeze = args.receipt_root / "freeze.json"
    run(["eval-freeze-receipts", "--rules", str(development_rules), "--model", str(development_model),
         "--output", str(freeze)], freeze)
    test_rules = args.receipt_root / "test-rules"
    rules_arguments = ["eval-receipts", "--split", "test", "--freeze", str(freeze), "--output-dir", str(test_rules)]
    if test_rules.exists():
        rules_arguments.append("--resume")
    run(rules_arguments, test_rules / "report.json")
    test_model = args.receipt_root / "test-model"
    test_arguments = ["eval-receipts", "--split", "test", "--variant", "span_llm", "--freeze", str(freeze),
                      "--ocr-run", str(test_rules), "--output-dir", str(test_model)]
    if test_model.exists():
        test_arguments.append("--resume")
    run(test_arguments, test_model / "report.json")
    comparison = args.receipt_root / "comparison.json"
    if not comparison.exists():
        compare_receipts(manifest, test_rules, test_model, comparison)
    else:
        verify_receipt_comparison(manifest, test_rules, test_model, comparison)
    if args.html_output is not None:
        subprocess.run([sys.executable, str(root / "scripts/render_release_reports.py"),
                        "--invoice", str(args.invoice_run), "--receipts", str(args.receipt_root),
                        "--output", str(args.html_output)], cwd=root, check=True)
    print(json.dumps({"status": "completed", "invoice": str(args.invoice_run / "report.json"),
                      "receipts": str(comparison), "html": str(args.html_output) if args.html_output else None}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
