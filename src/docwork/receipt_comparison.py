"""Paired descriptive CORD comparison, separate from invoice scoring/gates."""

from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path

from .invoice_run import _write_new
from .model_runtime import file_hash
from .receipt import summarize_receipts
from .receipt_run import verify_receipt_run


def verify_receipt_comparison(manifest_path: Path, baseline: Path, model: Path, comparison: Path) -> dict:
    saved = json.loads(comparison.read_text())
    with tempfile.TemporaryDirectory(prefix="docwork-comparison-audit-") as directory:
        expected = compare_receipts(manifest_path, baseline, model, Path(directory) / "comparison.json")
    if saved != expected:
        raise ValueError("Receipt comparison differs from audited deterministic rescoring")
    return saved


def compare_receipts(manifest_path: Path, baseline: Path, model: Path, output: Path) -> dict:
    if output.resolve() == manifest_path.resolve() or any(output.resolve().is_relative_to(directory.resolve()) for directory in (baseline, model)):
        raise ValueError("Comparison output must be outside its corpus manifest and run evidence")
    for directory in (baseline, model):
        verify_receipt_run(manifest_path, directory)
    left, right = [json.loads((directory / "report.json").read_text()) for directory in (baseline, model)]
    identities = [json.loads((directory / "run.json").read_text()) for directory in (baseline, model)]
    if (identities[0]["variant"] != "ocr_rules" or identities[1]["variant"] != "span_llm" or
            identities[0]["split"] != identities[1]["split"] or
            identities[0]["document_ids"] != identities[1]["document_ids"] or
            identities[0]["freeze_sha256"] != identities[1]["freeze_sha256"] or
            identities[1]["ocr_run_sha256"] != file_hash(baseline / "report.json")):
        raise ValueError("Receipt reports do not describe a compatible paired shared-OCR experiment")

    def metrics(summary):
        return {"row_detection_f1": summary["row_detection"]["f1"],
                "eligible_exact_row_f1": summary["eligible_exact_rows"]["f1"],
                **{f"header_{name}_f1": score["f1"] for name, score in summary["header_fields"].items()}}

    a, b = metrics(left["summary"]), metrics(right["summary"])
    samples = {name: [] for name in a}
    rng = random.Random(42)
    left_scores, right_scores = [{doc["id"]: doc for doc in report["documents"]} for report in (left, right)]
    ids = identities[0]["document_ids"]
    for _ in range(1000):
        sampled = [rng.choice(ids) for _ in ids]
        x, y = [metrics(summarize_receipts([{**scores[id], "id": f"bootstrap-{i}"}
                                          for i, id in enumerate(sampled)])) for scores in (left_scores, right_scores)]
        for name in samples:
            if x[name] is not None and y[name] is not None:
                samples[name].append(y[name] - x[name])
    intervals = {}
    for name, values in samples.items():
        values.sort()
        intervals[name] = [round(values[int(q * (len(values) - 1))], 4) for q in (.025, .975)] if values else None
    report = {"comparison_version": "paired-cord-receipts-v1", "split": identities[0]["split"],
              "documents": len(ids), "baseline_report_sha256": file_hash(baseline / "report.json"),
              "model_report_sha256": file_hash(model / "report.json"), "manifest_sha256": file_hash(manifest_path),
              "implementation_sha256": file_hash(Path(__file__)), "baseline": left["summary"], "model": right["summary"],
              "delta_model_minus_rules": {name: round(b[name] - a[name], 4) if a[name] is not None and b[name] is not None else None for name in a},
              "paired_95_intervals": intervals, "draws": 1000, "seed": 42,
              "scope": identities[0]["scope"], "interpretation": "Descriptive receipt results; no default promotion, automatic approval, or invoice-quality claim."}
    if output.exists() or output.is_symlink():
        raise ValueError("Receipt comparison must use a new output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_new(output, report)
    return report
