# Local Document Intelligence & Review Workbench

A local invoice workbench under development. The intended product extracts structured fields and line items, links each suggestion to page evidence, flags conflicts, and exports only after human review. See the [architecture plan](arch_plan/document-intelligence-workbench-plan.md).

## Current status

Phase 0 has a runnable `ocr_rules` spike for two demo PNGs and a separate 12-document, six-layout development set. It normalizes OCR line boxes, extracts header fields and line items, checks source references and arithmetic, and preserves an observed total when it conflicts with a computed total. [Phase 0 notes](docs/phase0.md) record the preflight and next work; the [development report](docs/development-baseline.md) records the measured baseline and its failure cases.

This is a trusted-fixture development command, not an upload service. Arbitrary documents require the planned isolated parser and intake controls. There is no review UI, local language model, durable queue, or export yet.

## Run the spike

On macOS with Python 3.12 and Tesseract with English language data:

```sh
make doctor
make test
make smoke-ocr
make demo-baseline
make eval-development
```

`make demo-baseline` and `make eval-development` write fresh results to ignored `artifacts/`. The development command verifies every committed image hash and label transform before scoring all 12 pages. The deterministic contract suite runs without Tesseract; `smoke-ocr` exercises real OCR on the committed demo fixtures. No model or dataset download is performed by these commands.
