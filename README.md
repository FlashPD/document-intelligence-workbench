# Local Document Intelligence & Review Workbench

A local invoice workbench under development. The intended product extracts structured fields and line items, links each suggestion to page evidence, flags conflicts, and exports only after human review. See the [architecture plan](arch_plan/document-intelligence-workbench-plan.md).

## Current status

Phase 0 has a runnable `ocr_rules` spike for two demo PNGs and a separate 12-document, six-layout development set. It normalizes OCR line boxes, extracts header fields and line items, checks source references and arithmetic, and preserves an observed total when it conflicts with a computed total. A local [review and export slice](docs/review.md) stores candidate revisions in SQLite, records issue decisions, binds approvals to exact revisions, and writes JSON/CSV exports. [Bounded intake](docs/intake.md) now stores PDF/PNG/JPEG originals and enqueues durable parser jobs. [Phase 0 notes](docs/phase0.md) record the preflight; the [development report](docs/development-baseline.md) records the measured baseline and its failure cases.

The OCR entrypoint is restricted to trusted repository fixtures. Intake accepts other documents for storage and signature checks, but does not parse them yet; an isolated parser is required before those bytes can be processed. The review slice is a local CLI prototype without authentication or a UI; there is no local language model or processing worker yet.

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

See [review workflow](docs/review.md) for a complete fixture-to-export example.
See [intake status](docs/intake.md) for the current queued-document boundary.
