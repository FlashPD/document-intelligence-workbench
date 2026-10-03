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
