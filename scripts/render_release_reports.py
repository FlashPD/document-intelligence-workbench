"""Render audited invoice/receipt comparisons as a standalone portfolio report."""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

from docwork.invoice_model_run import verify_invoice_model
from docwork.evaluation_sessions import audit_sessions
from docwork.model_runtime import file_hash
from docwork.receipt_comparison import verify_receipt_comparison


def require_release_scope(invoice: dict, receipts: dict) -> None:
    if (invoice.get("split") != "test" or invoice["summary"]["documents_scheduled"] != 180 or
            receipts.get("split") != "test" or receipts.get("documents") != 100):
        raise ValueError("Release export requires all 180 test invoices and 100 official test receipts")


def supplementary_evidence(directory: Path, stage: str) -> dict:
    """Call only after the domain verifier audits the completed report bundle."""
    report = json.loads((directory / "report.json").read_text())
    documents = report["documents"]
    if len(documents) != report["summary"]["documents_scheduled"]:
        raise ValueError("Timing requires every scheduled document")
    seconds = []
    for doc in documents:
        prediction = json.loads((directory / "predictions" / f"{doc['id']}.json").read_text())
        value = prediction.get("runtime_seconds", {}).get(stage)
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise ValueError("Stage timing is missing or invalid")
        seconds.append(value)
    seconds.sort()
    if not seconds:
        raise ValueError("Timing cannot describe an empty run")
    sessions = [json.loads(path.read_text()) for path in sorted((directory / "sessions").glob("*/runtime.json"))] if stage == "model" else []
    lifecycle = audit_sessions(directory) if stage == "model" and (directory / "sessions").exists() else None
    peaks = [s["peak_sampled_rss_bytes"] for s in sessions if s.get("peak_sampled_rss_bytes") is not None]
    cases = []
    for doc in documents:
        errors = sum(v["fp"] + v["fn"] for v in doc["header"].values())
        row_errors = doc["gold_rows"] + doc["predicted_rows"] - 2 * doc["exact_rows"]
        if doc["failure_type"] or errors or row_errors:
            cases.append({"id": doc["id"], "failure_type": doc["failure_type"],
                          "header_fp_plus_fn": errors, "row_fp_plus_fn": row_errors,
                          "exact_rows": doc["exact_rows"], "gold_rows": doc["gold_rows"]})
    cases.sort(key=lambda c: (c["failure_type"] is None, -c["row_fp_plus_fn"], -c["header_fp_plus_fn"], c["id"]))
    return {"stage": stage, "documents": len(seconds), "sum_seconds": round(sum(seconds), 3),
            "p50_seconds": seconds[math.ceil(.5 * len(seconds)) - 1],
            "p95_seconds": seconds[math.ceil(.95 * len(seconds)) - 1],
            "peak_sampled_server_rss_bytes": max(peaks) if peaks else None,
            "model_sessions": lifecycle,
            "representative_cases": cases[:3],
            "case_selection": "Processing failures first, then largest exact-row FP+FN, header FP+FN, and ID; at most three. "
                              "Examples do not replace all-document denominators; no causal explanation inferred.",
            "timing_method": "Nearest-rank percentiles over every scheduled document stage, including failures and page/repair requests; saved OCR inference, "
                             "uncontrolled machine load. Sampled RSS is process memory, not GPU or workbench peak."}


def render(invoice: dict, receipts: dict, systems: dict | None = None) -> str:
    def value(number):
        return "—" if number is None else f"{number:.4f}" if isinstance(number, float) else html.escape(str(number))

    def table(rows, columns):
        return "<table><thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in columns) + "</tr></thead><tbody>" + "".join(
            "<tr>" + "".join(f"<td>{value(c)}</td>" for c in row) + "</tr>" for row in rows) + "</tbody></table>"

    before, after = invoice["baseline_summary"], invoice["summary"]
    header_rows = [(name, before["header_fields"][name]["f1"], after["header_fields"][name]["f1"])
                   for name in before["header_fields"]]
    invoice_rows = [("Header macro F1", before["header_macro_f1"], after["header_macro_f1"]),
                    ("Exact row F1", before["row_exact"]["f1"], after["row_exact"]["f1"]),
                    ("Processed / scheduled", f"{before['documents_processed']}/{before['documents_scheduled']}",
                     f"{after['documents_processed']}/{after['documents_scheduled']}")]
    receipt_rows = [(name, receipts["baseline"]["header_fields"][name]["f1"], receipts["model"]["header_fields"][name]["f1"])
                    for name in receipts["baseline"]["header_fields"]]
    receipt_rows.extend([("Eligible exact row F1", receipts["baseline"]["eligible_exact_rows"]["f1"], receipts["model"]["eligible_exact_rows"]["f1"]),
                         ("Processed / scheduled", f"{receipts['baseline']['documents_processed']}/{receipts['documents']}",
                          f"{receipts['model']['documents_processed']}/{receipts['documents']}")])
    rows = []
    for domain, report in (("Synthetic invoices", invoice["comparison"]), ("CORD receipts", receipts)):
        for name, delta in report["delta_model_minus_rules"].items():
            interval = report["paired_95_intervals"].get(name)
            rows.append((domain, name, delta, "—" if interval is None else f"[{interval[0]:.4f}, {interval[1]:.4f}]"))
    cols = ("Metric", "OCR rules", "Local span model")
    supplement = ""
    if systems:
        timing_rows = [(name, data["stage"], data["documents"], data["sum_seconds"], data["p50_seconds"], data["p95_seconds"],
                        None if data["peak_sampled_server_rss_bytes"] is None else round(data["peak_sampled_server_rss_bytes"] / 1024 ** 3, 3))
                       for name, data in systems.items()]
        case_rows = [(name, case["id"], case["failure_type"] or "Header/row disagreement", case["header_fp_plus_fn"],
                      case["row_fp_plus_fn"], f"{case['exact_rows']}/{case['gold_rows']}")
                     for name, data in systems.items() for case in data["representative_cases"]]
        lifecycle_rows = [(name, len(data["model_sessions"]["recorded_runtime_sessions"]),
                           len(data["model_sessions"]["interrupted_sessions"]), data["model_sessions"]["memory_coverage"])
                          for name, data in systems.items() if data.get("model_sessions")]
        supplement = f"""<section><h2>Measured stage timings</h2>
{table(timing_rows, ('Variant','Stage','Documents','Total seconds','P50 seconds','P95 seconds','Sampled server RSS GiB'))}
<p>Nearest-rank percentiles include every scheduled document stage, including failures. A model stage can contain multiple page requests or a schema-repair request; document counts are not HTTP request counts. OCR times describe verified corpus previews for invoices and prepared PNGs for receipts. Model times describe inference on those saved OCR inputs. Machine load was uncontrolled; no warm/cold, concurrency, or end-to-end latency claim. RSS is the largest available sampled process value across recorded runtime sessions, not GPU allocation, total application memory, or a whole-run peak when session metadata is missing.</p>
{table(lifecycle_rows, ('Variant','Runtime sessions recorded','Interrupted sessions','Memory coverage')) if lifecycle_rows else ''}</section>
<section><h2>Representative failures and disagreements</h2>
{table(case_rows, ('Variant','Document','Outcome','Header FP + FN','Exact-row FP + FN','Exact / gold rows'))}
<p>Up to three examples per variant: processing failures first, then largest exact-row FP + FN, header FP + FN, and document ID. Wrong nonempty headers contribute both FP and FN. Selection is mechanical and does not replace complete denominators or explain the cause of an error. Inspect the hash-bound saved predictions to investigate each example.</p></section>"""
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Document Intelligence — measured extraction comparisons</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:1000px;margin:auto;padding:32px;color:#182a38;background:#f5f8fa}}h1{{line-height:1.15}}section{{background:white;border:1px solid #d5dfe7;border-radius:12px;padding:24px;margin:24px 0}}table{{width:100%;border-collapse:collapse;margin:20px 0;font-variant-numeric:tabular-nums}}td,th{{text-align:left;border-bottom:1px solid #d5dfe7;padding:10px}}th{{color:#365a73}}code{{overflow-wrap:anywhere}}.label{{color:#365a73;font-weight:600}}@media(max-width:650px){{body{{padding:16px}}section{{padding:12px}}td,th{{padding:5px;font-size:13px}}}}</style>
<p class="label">Local Document Intelligence &amp; Review Workbench · Experimental evaluation</p>
<h1>Does a local model improve extraction?</h1>
<p>Both comparisons use identical verified OCR inputs per document. The model performs fresh inference using pinned weights and a local runtime. Every scheduled extraction failure counts. Receipt and invoice labels remain separate.</p>
<section><h2>Synthetic invoice test</h2>{table(invoice_rows, cols)}{table(header_rows, cols)}
<p>{html.escape(invoice['scope'])}</p><p>Gate: {html.escape(invoice['comparison']['status'])}. Human approval remains required for exports.</p></section>
<section><h2>CORD receipt test</h2>{table(receipt_rows, cols)}<p>{html.escape(receipts['scope'])}</p>
<p>Eligible exact rows score released labeled cells. Missing labels are masked. Menu subitems, void items, payment and store labels are excluded.</p>
<p>The official test split was held out from project tuning. Exposure during model pretraining has not been audited and cannot be ruled out for this public benchmark.</p>
<p>Data: Park et al., CORD (NeurIPS Document Intelligence Workshop, 2019), NAVER CLOVA, CC BY 4.0.</p></section>
<section><h2>Paired uncertainty</h2>{table(rows, ('Dataset','Metric','Model − rules','95% interval'))}
<p>Invoice derivatives are grouped with parents within the six fixed test families. Receipt intervals resample within the official test split. These intervals do not represent arbitrary unseen vendors or document types.</p></section>
{supplement}<section><h2>Failures and timing boundaries</h2><p>Invoice model failures: <code>{html.escape(json.dumps(after['failures_by_type']))}</code>.</p>
<p>Receipt model failures: <code>{html.escape(json.dumps(receipts['model']['failures_by_type']))}</code>.</p>
<p>Model times describe serial inference on saved OCR. They exclude new OCR/PDF rendering and human review, and do not establish a controlled warm/cold latency target.</p></section></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    verify_invoice_model(root / "datasets/invoices-v1/manifest.json",
                         root / "evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1", args.invoice)
    manifest = root / "artifacts/cord-v2/prepared-v1/manifest.json"
    receipts = verify_receipt_comparison(manifest, args.receipts / "test-rules", args.receipts / "test-model",
                                         args.receipts / "comparison.json")
    invoice = json.loads((args.invoice / "report.json").read_text())
    require_release_scope(invoice, receipts)
    systems = {"Synthetic invoices · OCR rules": supplementary_evidence(root / "evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1", "ocr"),
               "Synthetic invoices · span model": supplementary_evidence(args.invoice, "model"),
               "CORD receipts · OCR rules": supplementary_evidence(args.receipts / "test-rules", "ocr"),
               "CORD receipts · span model": supplementary_evidence(args.receipts / "test-model", "model")}
    evidence_output = args.output.with_suffix(".evidence.json")
    if args.output.exists() or evidence_output.exists():
        parser.error("Comparison export needs new HTML and evidence paths")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with evidence_output.open("x") as stream:
        stream.write(json.dumps({"invoice_report_sha256": file_hash(args.invoice / "report.json"),
                                 "receipt_comparison_sha256": file_hash(args.receipts / "comparison.json"),
                                 "systems": systems}, indent=2) + "\n")
    with args.output.open("x") as stream:
        stream.write(render(invoice, receipts, systems))
    print(args.output)


if __name__ == "__main__":
    main()
