# Frozen invoice baseline test — October 3, 2026

The retained `ocr_rules` v0.3 default now has a [verified held-out report](../evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1/report.json) on all 180 synthetic test invoices across six previously unscored layout families. This is an **experimental baseline** on committed PNG previews. It excludes PDF rendering and local-model inference; the required full paired model comparison and CORD evaluation remain open.

The [freeze](../evals/invoice-heldout-2026-10-03/freeze.json) was written before test extraction. It binds the corpus hash, complete test document list, default extractor/version, PSM 1, Python version, Tesseract executable and English/orientation asset hashes, source hashes, and development/calibration selection reports. The spatial candidate's calibration regression remains the reason for retaining the default. No extractor or review-policy change was made from test results.

Before the freeze, fresh [development](../evals/invoice-freeze-2026-10-03/development/report.json) and [calibration](../evals/invoice-freeze-2026-10-03/calibration/report.json) runs reproduced their previous summary scores exactly. These add saved OCR and source snapshots absent from the older default bundles. The original development/calibration reports remain intact.

| Measure | Development | Calibration | Test |
|---|---:|---:|---:|
| Processed/scheduled | 180/180 | 180/180 | 180/180 |
| Header macro F1, all eligible header fields | 0.9911 | 0.9263 | **0.9981** |
| All required fields exact, eligible documents | 150/166 | 146/166 | **163/166** |
| Row detection F1 | 0.9855 | 0.8364 | **0.9942** |
| Fully exact row F1 | 0.9817 | 0.8321 | **0.9423** |
| Line-total field F1 | 0.9874 | 0.8401 | **0.9923** |
| Processing failures | 0 | 0 | **0** |

Fourteen ambiguous printed issue dates in each split are explicitly excluded from exact date and all-required scoring. A processed document can still contain wrong/missing fields or rows; zero processing failures is not perfect extraction.

## Uncertainty and observed failures

The test report contains six family summaries and a 1,000-draw, seed-1729 parent-group bootstrap. Its 150 base groups keep every degraded derivative with its original and resample groups within the same six families.

| Metric | Conditional bootstrap 95% percentile interval |
|---|---:|
| Header macro F1 | 0.9961–0.9997 |
| Row detection F1 | 0.9843–1.0000 |
| Fully exact row F1 | 0.9136–0.9663 |

These intervals condition on the six observed author-created families. They do not estimate unseen layout families, arbitrary invoices, or real receipt populations. The markedly different calibration row result already shows sensitivity to layout choice. The corpus and extractor share an author; the architecture's synthetic-data limitations still apply.

There are 522 gold rows, 518 predicted rows, 517 matched rows, and 490 fully exact rows. Five gold rows are missing, one predicted row is unmatched, and 27 matched rows contain at least one wrong cell. Description F1 is 0.9500, below quantity/unit-price/line-total F1. Family 16 has the lowest exact-row F1 at 0.9079; the other five families range from 0.9306 to 0.9667.

Three invoices have wrong required invoice numbers: `inv-f13-29`, `inv-f15-29`, and `inv-f16-29`. In the [skewed `inv-f13-29` prediction](../evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1/predictions/inv-f13-29.json), `F13-004` becomes `F 13-004`. The [rotated `inv-f16-30` prediction](../evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1/predictions/inv-f16-30.json) retains the invoice number but extracts zero of four gold rows and raises `NO_LINE_ITEMS`. These failures remain in every scoring denominator.

Validation emits 24 `TOTAL_MISMATCH` and 72 `TOTAL_NOT_CHECKED` issues. An unchecked arithmetic relationship remains unchecked even when header exactness is high. The product still requires human approval for every export and retains the calibration experiment's rejected spatial candidate.

The sum of serial OCR time was 142.534 seconds and total extraction time 142.662 seconds. Unit/Docker verification also ran during portions of this experiment. These diagnostic times do not establish controlled latency, speed improvement, concurrent throughput, or the upload pipeline's PDF/model latency.

## Reporting correction and audit

All 180 predictions and their completion-ledger checksums were saved before report generation hit a bootstrap bug: repeated sampled document IDs violated the scorer's unique-document guard. The correction gives bootstrap replicates distinct statistical IDs. It changes report computation, while retaining every original prediction, the original freeze, and the original pipeline snapshot.

`reporting_correction` in the report records both the original frozen driver hash and the corrected reporting driver hash. [The corrected reporting source](../evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1/reporting_source_snapshot.py) is saved separately. `eval-finalize-heldout` verified all committed prediction hashes, refused incomplete evidence or changed extraction/scoring code, computed the report, and verified it before completion. There was no second test OCR pass or extraction retuning.

Audit the committed evidence offline:

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-heldout \
  evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1
```

This verifies corpus/asset hashes, full test coverage, freeze/source snapshots, committed prediction checksums and canonical evidence references, recalculated scores, timing/issues, family metrics, bootstrap intervals, and correction provenance. It requires neither Tesseract, Docker, nor model assets. It is an integrity check relative to the saved evidence, not signed attestation.

## Fresh experimental reproduction

The ordinary `eval-run-invoices` command continues to reject the test split. The separate held-out command requires an explicit freeze. With Tesseract installed, create a new freeze from the verified source-bound diagnostic runs, then run every test document at a new output path:

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-freeze-invoices \
  --evidence evals/invoice-freeze-2026-10-03/development \
  --evidence evals/invoice-freeze-2026-10-03/calibration \
  --output artifacts/invoice-test-repeat-freeze.json
PYTHONPATH=src python3.12 -m docwork.cli eval-heldout-invoices \
  --freeze artifacts/invoice-test-repeat-freeze.json \
  --output-dir artifacts/invoice-test-repeat
```

This is a **repeat on already-inspected test data**, not a second unseen test result. The committed original freeze identifies the pre-correction driver and cannot run fresh extraction under changed source. Preserve it and use the offline verifier for the historical result.

For an interrupted new run, pass `--resume` with its original freeze and directory. A committed prediction must match its ledger; an uncommitted interrupted write is recomputed. Changed inputs/source/runtime reject resume. Every processing failure is saved and counted; the CLI exits 2 when any document fails. A report is never overwritten.

The portfolio claim is now a measured synthetic baseline plus a separately [verified two-fixture real-model upload workflow](real-model-upload.md). The full held-out model comparison, CORD receipts, genuine scans, controlled review/latency studies, and recorded browser demo remain release milestones.
