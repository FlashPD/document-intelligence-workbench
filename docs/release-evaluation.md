# Frozen invoice scoring contract

The release scorer supports the [540-document invoice corpus](invoice-corpus.md). It scores saved candidate records; it does not run OCR or a model. A [frozen experimental baseline](invoice-heldout-run.md) now measures all 180 test invoices. The full paired local-model and receipt evaluations remain open. The 12-document development reports keep their existing `canonical-exact-source-order-v1` scorer, so historical comparisons do not change.

## Inputs and command

The manifest uses `manifest_version: "invoice-corpus-v1"`, a `dataset_id`, and a `documents` array. Each document has a unique `id`, a `split` (`development`, `calibration`, or `test`), `family_group`, all ten invoice `fields` (nullable strings), `line_items`, `source_sha256`, and at least one asset with a path relative to the manifest directory and a SHA-256 digest. The source hash must match an asset. A document's assets may include multiple pages and the original file. A field that cannot be judged from the visible document may be named in `field_exclusions` with a reason; the frozen corpus uses this for ambiguous printed dates.

Save one JSON prediction per scheduled document as `<id>.json` in a directory. Each file must contain the original `source_sha256` and either a candidate `record` in the workbench invoice schema or `record: null` with a `failure_type`. The scorer rejects extra files and mismatched source hashes. A missing file remains in all denominators and marks the report `incomplete_evidence`.

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-score-invoices datasets/invoices-v1/manifest.json artifacts/predictions/test \
  --split test --output artifacts/reports/invoice-test-v1.json
```

Use a new output path for each run. The report records the manifest hash, scoring implementation hash, each prediction hash, document scores, and aggregate metrics. The command exits 2 for incomplete evidence or invalid inputs. It does not turn a development or calibration score into a held-out result by changing a label; the manifest and selected split are recorded together.

## Scoring rules

- Header values use case-folded, whitespace-normalized exact comparison for text and `Decimal` equivalence for amounts. Missing is distinct from zero. Dates remain exact strings; the scorer never guesses locale. Invalid numeric gold labels are rejected.
- Every scheduled document counts. A failed extraction contributes false negatives for present eligible gold fields and rows. A predicted value where gold is absent is a false positive. Wrong nonempty values contribute both a false positive and a false negative. Explicitly excluded fields contribute no TP/FP/FN and are reported by name and count. Documents with an excluded required field are omitted from the all-required-exact denominator. Precision, recall, and F1 are undefined (`null`) when their denominator is zero; header macro F1 excludes fields with no positive or predicted instances.
- Rows are matched one-to-one without relying on source order. A pair is eligible when descriptions match, or when quantity, unit price, and line total all match. Among eligible pairs, the global assignment maximizes matched-row count, then exact cell agreement. Repeated descriptions and identical rows retain their multiplicity. Unmatched predictions and gold rows count against row detection and cell metrics.
- The report includes row detection F1, fully exact row F1, and per-cell F1. A matched row with a wrong value can be detected as a row while still failing exact-row and cell scoring.

The manifest verifier checks all asset hashes and rejects a layout family, normalized supplier name, or byte-identical asset appearing in multiple splits. The [synthetic corpus verifier](invoice-corpus.md) also enforces 180/180/180 coverage and the exact date-exclusion policy. These checks cannot detect every visual near duplicate; corpus review and generation rules must address those separately. This scorer does not yet measure evidence-box accuracy, review time, latency, or receipt extraction. Separate measured runs are required before a portfolio quality claim.
