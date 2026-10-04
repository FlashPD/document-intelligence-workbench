"""Export deterministic diagnostic examples from a verified completed comparison.

This reads gold only for offline analysis; it changes no predictions or settings.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from docwork.invoice_model_run import verify_invoice_model
from docwork.model_runtime import file_hash


def analyze(root: Path, output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("Invoice analysis requires a new output path")
    directory = root / "evals/invoice-model-heldout-2026-10-03"
    manifest = root / "datasets/invoices-v1/manifest.json"
    baseline = root / "evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1"
    if any(output.resolve().is_relative_to(path.resolve()) for path in (directory, baseline, manifest.parent)):
        raise ValueError("Diagnostics cannot write into immutable inputs")
    verify_invoice_model(manifest, baseline, directory)
    report = json.loads((directory / "report.json").read_text())
    gold = {doc["id"]: doc for doc in json.loads(manifest.read_text())["documents"]}
    successes = [doc for doc in report["documents"] if not doc["failure_type"]]
    selected = sorted(successes, key=lambda doc: (
        -(doc["gold_rows"] + doc["predicted_rows"] - 2 * doc["exact_rows"]),
        -sum(score["fp"] + score["fn"] for score in doc["header"].values()), doc["id"]))[:3]
    cases = []
    for doc in selected:
        path = directory / "predictions" / f"{doc['id']}.json"
        prediction = json.loads(path.read_text())
        cases.append({"id": doc["id"], "prediction_sha256": file_hash(path),
                      "gold_rows": doc["gold_rows"], "predicted_rows": doc["predicted_rows"], "exact_rows": doc["exact_rows"],
                      "header_errors": {name: {"gold": gold[doc["id"]]["fields"][name],
                                              "predicted": prediction["record"]["fields"][name], "score": score}
                                        for name, score in doc["header"].items() if score["fp"] or score["fn"]},
                      "gold_line_items": gold[doc["id"]]["line_items"],
                      "predicted_line_items": prediction["record"]["line_items"]})
    result = {"report_version": "invoice-model-diagnostics-v1",
              "manifest_sha256": file_hash(manifest), "invoice_report_sha256": file_hash(directory / "report.json"),
              "analysis_script_sha256": file_hash(Path(__file__)),
              "scheduled": report["summary"]["documents_scheduled"],
              "processed": report["summary"]["documents_processed"],
              "failures": [{"id": doc["id"], "failure_type": doc["failure_type"]}
                           for doc in report["documents"] if doc["failure_type"]],
              "families": {family: {variant: {"header_macro_f1": summary["header_macro_f1"],
                                              "exact_row_f1": summary["row_exact"]["f1"],
                                              "processed": summary["documents_processed"]}
                                     for variant, summary in variants.items()}
                           for family, variants in report["comparison"]["families"].items()},
              "cases": cases,
              "selection": "All processing failures listed separately. Among processed predictions, three cases selected by "
                           "largest exact-row FP+FN, then header FP+FN, then ID. No case selected for successful model output.",
              "scope": "Offline descriptive inspection of unchanged frozen test predictions. Examples do not replace "
                       "all-document metrics or identify error causes automatically. No test tuning, inference or model promotion."}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(Path(__file__).resolve().parents[1], args.output)
    print(json.dumps({"scheduled": result["scheduled"], "processed": result["processed"],
                      "examples": [case["id"] for case in result["cases"]], "output": str(args.output)}))


if __name__ == "__main__":
    main()
