"""Render audited invoice/receipt comparisons as a standalone portfolio report."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from docwork.invoice_model_run import verify_invoice_model
from docwork.receipt_comparison import verify_receipt_comparison


def render(invoice: dict, receipts: dict) -> str:
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
<section><h2>Failures and timing boundaries</h2><p>Invoice model failures: <code>{html.escape(json.dumps(after['failures_by_type']))}</code>.</p>
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(render(invoice, receipts))
    print(args.output)


if __name__ == "__main__":
    main()
