# Local Document Intelligence & Review Workbench

A local invoice workbench under development. The intended product extracts structured fields and line items, links each suggestion to page evidence, flags conflicts, and exports only after human review. See the [architecture plan](arch_plan/document-intelligence-workbench-plan.md).

## Current status

Phase 0 has a runnable `ocr_rules` spike for two self-authored PNG invoices. It normalizes OCR line boxes, extracts header fields and a line item, checks source references and arithmetic, and preserves an observed total when it conflicts with a computed total. [Phase 0 notes](docs/phase0.md) record the preflight, observed run, limits, and next work.

This is a trusted-fixture development command, not an upload service. Arbitrary documents require the planned isolated parser and intake controls. There is no review UI, local language model, durable queue, or export yet.

## Run the spike

On macOS with Python 3.12 and Tesseract with English language data:

```sh
make doctor
make test
make smoke-ocr
make demo-baseline
```

`make demo-baseline` writes fresh results, including OCR spans and normalized source boxes, to ignored `artifacts/`. The deterministic contract suite runs without Tesseract; `smoke-ocr` exercises real OCR on the committed fixtures. No model or dataset download is performed by these commands.
