"""Show ledger progress without treating partial predictions as quality reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def progress(directory: Path, scheduled: int) -> dict:
    ledger = json.loads((directory / "completed.json").read_text()) if (directory / "completed.json").exists() else {}
    failed = 0
    for name in ledger:
        if Path(name).name != name:
            raise ValueError("Unsafe prediction name in completion ledger")
        prediction = json.loads((directory / "predictions" / name).read_text())
        failed += prediction.get("record") is None
    return {"completed": len(ledger), "scheduled": scheduled, "failed": failed,
            "report_written": (directory / "report.json").exists()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice", type=Path, default=Path("evals/invoice-model-heldout-2026-10-03"))
    parser.add_argument("--receipts", type=Path, default=Path("evals/cord-heldout-2026-10-03"))
    args = parser.parse_args()
    print(json.dumps({"invoice_model": progress(args.invoice, 180),
                      "receipt_validation_rules": progress(Path("evals/cord-validation-2026-10-03/ocr-rules"), 100),
                      "receipt_validation_model": progress(args.receipts / "validation-model-12", 12),
                      "receipt_test_rules": progress(args.receipts / "test-rules", 100),
                      "receipt_test_model": progress(args.receipts / "test-model", 100),
                      "receipt_comparison_written": (args.receipts / "comparison.json").exists()}, indent=2))


if __name__ == "__main__":
    main()
