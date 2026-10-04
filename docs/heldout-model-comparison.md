# Held-out local-model comparison

The frozen invoice experiment rejects promotion of the local span model: it scores below deterministic rules on the same saved OCR. This is a measured model-selection result, not an incomplete experiment or a reason to omit failed documents. `ocr_rules` v0.3 remains the product default, and every export requires human approval.

## Synthetic invoice results

The [verified model report](../evals/invoice-model-heldout-2026-10-03/report.json) accounts for every one of the 180 held-out invoices. The model produces records for 176 documents; four `ModelUnavailable` failures remain in every applicable denominator. The [rules baseline](invoice-heldout-run.md) produces records for all 180.

| Metric | OCR rules | Qwen3 span model | Model minus rules | Paired 95% interval |
|---|---:|---:|---:|---|
| Header macro F1 | 0.9981 | 0.9766 | -0.0215 | [-0.0366, -0.0090] |
| Row detection F1 | 0.9942 | 0.9219 | -0.0723 | [-0.1055, -0.0405] |
| Fully exact row F1 | 0.9423 | 0.7985 | -0.1438 | [-0.1967, -0.0976] |
| All required fields exact | 163/166 eligible | 157/166 eligible | — | — |
| Records produced | 180/180 scheduled | 176/180 scheduled | — | — |

Fourteen ambiguous printed dates are excluded under the original label policy; documents containing those excluded required fields do not enter the 166-document all-required-exact denominator. They still enter applicable field and row metrics. Wrong nonempty field values count as both FP and FN. No exclusions were added after inspecting these results.

The predeclared gate allows at most 0.02 absolute regression on the three compared metrics. The result is `regression`. The bootstrap makes 1,000 paired draws with seed 42, keeping degraded derivatives with their parents within the six fixed test families. The intervals describe this author-created corpus; they do not establish accuracy on unfamiliar real vendors.

| Test family | Rules exact-row F1 | Model exact-row F1 | Model records produced |
|---|---:|---:|---:|
| 13 | 0.9306 | 0.9000 | 29/30 |
| 14 | 0.9459 | 0.7297 | 30/30 |
| 15 | 0.9474 | 0.7887 | 27/30 |
| 16 | 0.9079 | 0.6289 | 30/30 |
| 17 | 0.9306 | 0.8844 | 30/30 |
| 18 | 0.9667 | 0.8372 | 30/30 |

The model's row regression is visible in all six families, including families with no processing failures. Dropping the four failures would not address those observed row errors.

## Inspected disagreements

The [diagnostic report](../evals/portfolio-candidate-invoice-analysis-2026-10-03/report.json) lists every processing failure and selects three processed examples by largest exact-row FP+FN, then header FP+FN, then document ID. It binds the original report, corpus, analysis script and selected prediction hashes. These examples were selected after scoring for descriptive inspection; they did not tune the frozen extractor.

- `inv-f16-30`: all four rows are detected but none is fully exact. Quantities are unit-price strings such as `82.00` instead of `2`. The predicted subtotal is `82.00` instead of the labeled `840.00`; its cited span exists and contains that price.
- `inv-f14-23`: all four rows have correct amounts, but descriptions include their quantity, such as `Illustration set 1` instead of `Illustration set`. Exact-row scoring records that disagreement.
- `inv-f16-01`: descriptions also include quantities, and one row's quantity is `54.00` instead of `3`, matching the unit-price value.

These inspected values demonstrate why an existing, aligned source reference does not prove the value belongs to the proposed field. They do not prove that every error has the same cause. The complete frozen predictions remain available for broader analysis.

## Timing and lifecycle

The 180 committed per-document stages, including failures, sum to 19,375.842 model-stage seconds. Nearest-rank P50/P95 are 102.645/162.536 seconds per document. One document can issue multiple page requests or a schema-repair request; these counts are not individual HTTP calls. These are saved-OCR extraction times, excluding new OCR, PDF parsing, human review, startup and lost uncommitted attempts. Machine load was uncontrolled, and the Mac slept during the experiment; the summed stage times are not experiment wall time or a controlled warm/cold latency comparison.

Two sessions have recorded shutdown/runtime metadata. The first abruptly interrupted session retains a [hash-bound observation](evaluation-recovery.md), with its shutdown and sampled RSS unavailable. The final report verifies against the original freeze; completed failures were not retried to improve quality. Available RSS samples cannot establish a whole-run memory peak.

## Receipt comparison and scope

The [verified CORD comparison](../evals/cord-heldout-2026-10-03/comparison.json) accounts for all 100 official test receipts. Rules produce 100 records; the model produces 95, retaining one `ModelContextOverflow` and four `ModelUnavailable` failures in the denominators. The adapter uses English OCR and scores five amount labels and top-level menu rows. It does not score invoice numbers, currency, store/payment labels, void items or menu subitems.

| Metric | OCR rules | Local span model |
|---|---:|---:|
| Subtotal F1 | 0.1389 | 0.1579 |
| Discount F1 | 0.5714 | 0.0000 |
| Service charge F1 | 0.1429 | 0.1667 |
| Tax F1 | 0.1364 | 0.0909 |
| Total F1 | 0.2435 | 0.1651 |
| Row detection F1 | 0.1755 | 0.2551 |
| Eligible exact-row F1 | 0.0957 | 0.0204 |

The model-minus-rules eligible exact-row delta is -0.0753, with a paired 95% interval of [-0.1425, -0.0121]. The row-detection delta is +0.0796, with interval [-0.0041, 0.1709]; the total-F1 delta is -0.0784, with interval [-0.1960, 0.0370]. Better row detection does not imply more correct rows, and those latter intervals include zero. Receipt results are descriptive, without the invoice regression gate or default promotion.

Only released parseable labels are eligible: 95 totals, 64 subtotals, five discounts, eleven service charges and forty taxes. Missing labels are masked; they are not known zero values. Exact rows score eligible labeled cells, so they do not establish correctness of unlabeled optional quantities or prices. Both variants show a substantial domain gap; neither supports a production receipt-quality claim. The official split was held out from project tuning, but public-benchmark exposure during model pretraining was not audited.

The model's 100 per-document stages sum to 4,050.266 seconds, with P50/P95 30.394/125.236 seconds. Rules OCR stages sum to 47.658 seconds, with P50/P95 0.343/1.308 seconds. These stages have different boundaries: model extraction reuses saved OCR, while the rules figure measures fresh OCR. They are not a controlled end-to-end speed comparison. The [standalone report](../evals/release-comparison-2026-10-03.html) includes the paired intervals, mechanically selected failure examples, and sampled-memory coverage for both domains.

The shared-OCR invoice experiment evaluates a pinned Qwen3-4B-Instruct-2507 Q4_K_M model through llama.cpp and the unchanged `span-invoice-v2` prompt. It is not a Docling layout or vision-model experiment. The prompt/profile came from the earlier 12-document development study, without a full-corpus model calibration run. The rules test report was already public before this model freeze. Those limits constrain the comparison; they are disclosed rather than retroactively repaired using test results.

## Reproduce the audit

With Python 3.12 and the committed corpus and evidence:

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-invoice-model \
  evals/invoice-model-heldout-2026-10-03
PYTHONPATH=src python3.12 scripts/analyze_invoice_comparison.py \
  --output artifacts/invoice-diagnostics-fresh.json
```

These commands audit and inspect saved results without inference, Docker, downloads, model assets or prediction changes. See [the scoring contract](release-evaluation.md) for fresh inference and receipt reproduction.
