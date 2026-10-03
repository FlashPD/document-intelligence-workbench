# Offline portfolio demo

`make demo-replay` opens the complete evidence/review/export workflow with recorded candidates. Python 3.12 is the only runtime requirement. It does not invoke Tesseract, Docker, a model, or downloads. Run from the repository root and open the printed one-time session URL. The server binds to loopback and uses the same session and same-origin protections as the live workbench.

The sidebar and document header label recorded OCR. Upload, sample extraction, and job processing are unavailable in this mode; the server enforces that boundary as well as hiding the controls. Use `make dev` for fresh trusted-sample OCR and Docker uploads, or `make dev-model` for the pinned local model.

## Walkthrough

| Case | Try | Engineering behavior |
|---|---|---|
| Clean invoice | Select invoice number and a row amount; approve and export | Cited page regions, mandatory approval, hash-verified JSON/CSV downloads |
| Printed total conflict | Inspect the printed total and `TOTAL_MISMATCH`; enter a reason to retain the printed value | Observed and computed totals stay distinct; issue acknowledgment is auditable |
| Low-contrast scan | Inspect the row flagged `INVALID_ROW_AMOUNT`; compare its source and correct the OCR suggestion | Imperfect candidates stay visible; corrections preserve original suggestions and create revisions |
| Two-page invoice | Select a row cited on page 2; rotate the preview | Field selection jumps to its source page; raster and evidence rotate together |

To demonstrate approval integrity, edit an approved invoice. Its new revision needs new approval before it can export. Previously downloaded exports remain bound to their historical revision. This demo starts with the original recorded suggestions and no approvals or review decisions.

The four cases are fixed, self-authored **development** documents (`inv-f02-02`, `inv-f01-12`, `inv-f03-27`, `inv-f06-04`). They demonstrate behavior; they are not a random sample or held-out quality measurement. Source bytes, saved candidates, and canonical OCR enter the workbench; gold fields and rows do not. Replay uses the audited default OCR/rules development run, not fresh extraction or model suggestions.

## Persistence and verification

Each launch writes a new directory under `artifacts/demo-replay/<unique-id>/`, containing SQLite, source/render objects, `replay.json`, and eventual immutable exports. Ctrl+C stops the server and leaves that directory intact. Starting another replay makes a fresh workbench. `replay.json` binds the selected source/prediction hashes and the corpus/baseline report hashes; JSON exports retain `extraction.profile: replay_ocr_rules`. Recorded parser-version metadata describes the original pipeline; it does not imply a parser ran during replay.

For a chosen port or explicit new destination:

```sh
PYTHONPATH=src python3.12 -m docwork.cli demo-replay \
  --port 8766 --output-dir artifacts/my-portfolio-demo
```

Existing destinations are refused to preserve review history. If the port is already occupied, choose another port and a new output directory. To inspect an existing saved workbench, run the ordinary server against its database and objects; document provenance remains replay, while the ordinary server also offers live intake:

```sh
PYTHONPATH=src python3.12 -m docwork.cli serve \
  --db artifacts/my-portfolio-demo/review.sqlite \
  --objects artifacts/my-portfolio-demo/objects --port 8766
```

For an offline preparation check without opening a socket:

```sh
PYTHONPATH=src python3.12 -m docwork.cli demo-replay \
  --prepare-only --output-dir artifacts/replay-preparation-check
make test
```

Preparation verifies the complete recorded development baseline, asset and prediction hashes, page dimensions, and evidence contracts before publishing a new workbench. Changed or incomplete evidence fails instead of silently switching to live processing. On failure, unpublished temporary workbench files are removed. The source checkout must include the committed corpus and development evidence; a wheel alone is insufficient.

With installed Chrome, the browser verifier exercises the replay entrypoint, all four case buttons, server-enforced processing restrictions, keyboard corrections, stale revisions, approval, downloads, rotations, multiple pages, and narrow-screen layout:

```sh
PYTHONPATH=src python3.12 scripts/verify_review_browser.py \
  --demo-replay --skip-recording --output-dir artifacts/replay-browser-check
```

It uses temporary browser/workbench state and writes screenshots plus a source-bound report. Omit `--skip-recording` to assemble captioned screenshot holds into a WebM. Automation supplies interface evidence and cannot satisfy the human review pilot or live-model release criteria.

## Recorded checks

The [October 3 Chrome check](../evals/demo-replay-browser-2026-10-03/report.json) passes all 16 controls on the four replay cases, with [captured frames](../evals/demo-replay-browser-2026-10-03/index.html). Its source snapshot and artifact hashes identify the tested implementation. No new video was recorded for this check.

The [clean-source preparation check](../evals/demo-replay-checkout-2026-10-03/report.json) starts from a temporary copy of 2,741 tracked/current-change files without `artifacts/`, model assets, a virtual environment, or the actively written held-out model bundle. It prepares all four cases successfully using the public CLI. This is a source-snapshot check before commit, not a clone of a release tag. The [full deterministic test log](../evals/demo-replay-checkout-2026-10-03/contracts.log) records 259 passing tests in the working source tree. CI also runs offline replay preparation after the contracts.
