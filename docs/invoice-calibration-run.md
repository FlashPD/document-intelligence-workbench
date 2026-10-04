# Invoice calibration run — October 3, 2026

The unchanged `ocr_rules` v0.3 extractor and Tesseract PSM 1 setting were run on all 180 **calibration** invoices from six layout families separate from development. This is a fresh host OCR run on hash-verified PNG previews. Multi-page documents use their committed page previews, so the result does not exercise PDF rendering, Docker isolation, or the upload worker. The [run identity, predictions, and scored report](../evals/invoice-calibration-2026-10-03/ocr-rules-v0.3-psm1/report.json) pass `eval-verify-invoice-run`. No calibration result was used to change the extractor or triage weights, and the test split remains sealed.

| Measure | Development PSM 1 | Calibration PSM 1 |
|---|---:|---:|
| Processed | 180/180 | 180/180 |
| Header macro F1 | 0.9911 | 0.9263 |
| All required fields exact, eligible documents | 150/166 | 146/166 |
| Row detection F1 | 0.9855 | 0.8364 |
| Exact-row F1 | 0.9817 | 0.8321 |
| Line-total field F1 | 0.9874 | 0.8401 |
| Sum of serial OCR times | 136.929 s | 121.029 s |

The row decline is largely missing rows: the calibration set has 526 labeled rows, while the extractor predicts 385 and matches 381. Family 10 has exact-row F1 **0.4742**, family 9 **0.7143**; the other four families range from **0.8308** to **0.9667**. In [invoice `inv-f10-01`](../evals/invoice-calibration-2026-10-03/ocr-rules-v0.3-psm1/predictions/inv-f10-01.json), two separately labeled “Field interview” rows become one predicted row, and the missing subtotal leaves total reconciliation unchecked. This example illustrates an observed failure, not a diagnosis of every missing row. These are synthetic, author-created families; the difference does not estimate performance on arbitrary invoices.

## Validation and review selection

The [verified triage report](../evals/invoice-calibration-2026-10-03/ocr-rules-v0.3-psm1/priority-report.json) evaluates the frozen additive priority score against calibration labels. At zero points, it selects 33/180 documents with no observed required-field or exact-row errors **on this split**. At two points, it selects 116/180, including 2 wrong required-field records and 36 records with row errors. The [development split](invoice-development-run.md#review-priority-diagnostic--october-3-2026) already has 7 wrong required-field records among 74 zero-point documents, so the combined evidence does not justify a skip-review threshold. The product continues to require human approval for every export.

Of 24 injected `TOTAL_MISMATCH` cases, validation flags 11 and misses 13; all 13 misses have `TOTAL_NOT_CHECKED` because a component is unavailable. It flags all 14 injected `AMBIGUOUS_DATE` cases. No false flags for either injected code appear in this split. `TOTAL_NOT_CHECKED` occurs on 129 documents overall, so it must not be read as a passed arithmetic check. The triage report also contains the full score-versus-coverage curve, each document's signals, a one-sided document-level Wilson upper bound, and a parent-group bootstrap 95th percentile. For the 33 zero-point calibration documents with no observed critical errors, Wilson's upper bound is 7.58%; the bootstrap percentile is zero because it cannot create unseen errors. The Wilson bound assumes independent documents, and the bootstrap holds the six layout families fixed. Neither estimates behavior on unseen families.

## Reproduce and verify

The OCR run requires local Tesseract English and a new output directory. Both verifiers below use committed evidence and need no OCR or model runtime.

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-run-invoices --split calibration \
  --output-dir artifacts/invoice-calibration-fresh
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-invoice-run \
  evals/invoice-calibration-2026-10-03/ocr-rules-v0.3-psm1
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-review-priority \
  evals/invoice-calibration-2026-10-03/ocr-rules-v0.3-psm1 \
  evals/invoice-calibration-2026-10-03/ocr-rules-v0.3-psm1/priority-report.json
```

The next extraction work should be developed and checked on the development families, then frozen before a new calibration run. The test families remain untouched until release settings are fixed.

## Spatial candidate rejected — October 3, 2026

The [v0.4 calibration run](../evals/invoice-calibration-2026-10-03/ocr-rules-v0.4-psm1/report.json) processes all 180 calibration invoices with the spatial candidate frozen after development. It uses the same Tesseract PSM 1, manifest, and scorer as v0.3. The new bundle contains every canonical OCR page, prediction, and a hash-bound pipeline source snapshot, and passes `eval-verify-invoice-run`. The development and calibration pipeline fingerprints are identical. This is a second diagnostic on a previously inspected calibration split, not held-out evidence; no grouping rule was changed after viewing these results.

| Measure | v0.3 default | v0.4 spatial candidate |
|---|---:|---:|
| Processed documents | 180/180 | 180/180 |
| Header macro F1 | 0.9263 | 0.9263 |
| All required fields exact | 146/166 | 146/166 |
| Row detection F1 | 0.8364 | 0.7909 |
| Detected gold rows | 381/526 | 346/526 |
| Exact-row F1 | 0.8321 | 0.7863 |
| Exact gold rows | 379/526 | 344/526 |
| Line-total F1 | 0.8401 | 0.7936 |

The candidate predicts 349 rows versus the default's 385. In [inv-f07-04](../evals/invoice-calibration-2026-10-03/ocr-rules-v0.4-psm1/predictions/inv-f07-04.json), a complete “Accessibility audit” row shares its visual height with `Discount: 0.00` in a separate summary sidebar. Spatial grouping combines the independent text regions and loses a row that v0.3 extracted correctly. This is one observed failure, not a diagnosis of every lost row. The experiment's development improvement did not generalize to these calibration layouts.

**Decision: reject default promotion.** `ocr_rules` remains v0.3 in both the worker and ordinary preview evaluations. `--extractor spatial_rules` explicitly selects v0.4 for experiments; the browser and worker do not expose that option. The candidate's row heuristics remain unchanged after this failure. Restoring the default and separating the modules changed dispatch and evaluation metadata. Replay on all 360 saved development/calibration OCR inputs reproduces each extractor's respective recorded records, with zero differences. That replay verifies behavior on recorded text; it is not a new OCR or held-out run.

The [candidate triage report](../evals/invoice-calibration-2026-10-03/ocr-rules-v0.4-psm1/priority-report.json) still flags only 11/24 injected total conflicts, with the other 13 uncheckable because a component is absent. `TOTAL_NOT_CHECKED` remains on 129 documents. At zero points it selects 23/180 records with no observed critical errors, but the independent-document Wilson upper bound is 10.53%; the development diagnostic still has seven critical errors at zero points. No skip-review threshold is adopted. Every product export still needs approval.

Serial OCR time sums to 121.667 seconds. Other local verification ran during parts of evaluation, so the timings do not support a speed comparison. These quality runs use verified PNG previews, rather than Docker PDF rendering. The test split remains unscored.

Run the candidate explicitly at a new path, or omit `--extractor` for the retained default:

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-run-invoices --split calibration \
  --extractor spatial_rules --output-dir artifacts/invoice-spatial-calibration-fresh
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-invoice-run \
  evals/invoice-calibration-2026-10-03/ocr-rules-v0.4-psm1
```

A future extraction candidate needs independent document-region boundaries developed on development examples before another frozen comparison. The negative result remains part of the portfolio evidence.
