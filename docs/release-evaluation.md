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

The manifest verifier checks all asset hashes and rejects a layout family, normalized supplier name, or byte-identical asset appearing in multiple splits. The [synthetic corpus verifier](invoice-corpus.md) also enforces 180/180/180 coverage and the exact date-exclusion policy. These checks cannot detect every visual near duplicate; corpus review and generation rules must address those separately. This invoice scorer does not measure evidence-box accuracy or review time. The receipt adapter below has a separate metric contract.

## Held-out model comparison

The `eval-invoice-model` runner schedules all 180 test invoices, using exactly the saved OCR pages from the [verified default test baseline](invoice-heldout-run.md). It performs fresh inference with the pinned Qwen3 profile and the unchanged `span-invoice-v2` prompt selected on the earlier 12-document development set. This isolates extraction differences from OCR differences. It does not exercise fresh OCR, the original PDFs, or a Docling layout pipeline, and is named `span_llm` accordingly.

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-invoice-model \
  --output-dir artifacts/invoice-model-test-001
PYTHONPATH=src python3.12 -m docwork.cli eval-invoice-model \
  --output-dir artifacts/invoice-model-test-001 --resume
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-invoice-model \
  artifacts/invoice-model-test-001
```

Use a new directory for the first command. `freeze.json` binds the manifest, baseline and development reports, every baseline OCR prediction, model profile, prompt, and pipeline source snapshot **before inference**. The model sees only canonical OCR pages, never gold fields or rows. Each document writes an atomic prediction and hash completion ledger. Resume rejects changed settings, inputs, source, or completed predictions; completed failures remain failures. An interrupted uncommitted document can be recomputed. Every owned server session retains a redacted log and hash-bound runtime/shutdown metadata. Startup verifies cached model/runtime assets and does not download anything.

The report applies the existing frozen invoice scorer, includes both summaries, per-family results, failure types, model timings, paired differences, and parent-group bootstrap intervals within the six fixed test families. A descriptive regression gate uses the predeclared 0.02 absolute margin. Exit 1 means a quality regression; exit 2 means failed extractions or unusable evidence. A model win would not promote the default or remove human approval. The test baseline was already published before this model freeze, and the model has no full-corpus calibration run; the report discloses both limits. Timings include failed model calls and exclude fresh OCR, PDF parsing, and human wait. Early inference overlapped dataset preparation and deterministic checks, so these are uncontrolled machine-load timings, not a warm/cold latency study.

## CORD receipt evaluation

[CORD v2](https://github.com/clovaai/cord) is a separate real receipt benchmark under CC BY 4.0. The [dataset pin](../config/cord-v2.json) fixes the official Hugging Face revision `7f0115a4b758a71d6473b8d085751692da2fef98`, validation/test Parquet SHA-256 hashes, counts, attribution, and license. Only those two 100-row shards are downloaded, about 476 MB; the official 800 training rows remain unimported. Import retains all official validation and test rows, hashes original bytes and converted PNGs, and rejects byte-identical content crossing those splits.

The official test split is held out from this project's development and tuning. Exposure of this public benchmark during model pretraining has not been audited and cannot be ruled out.

Prepare data explicitly in a local environment with the pinned optional readers:

```sh
python3.12 -m venv artifacts/cord-tools
artifacts/cord-tools/bin/python -m pip install -e '.[evaluation]'
PYTHONPATH=src python3.12 -m docwork.cli cord fetch
PYTHONPATH=src artifacts/cord-tools/bin/python -m docwork.cli cord prepare
PYTHONPATH=src python3.12 -m docwork.cli cord verify
```

Preparation requires a new output directory and publishes `manifest.json` only after conversion succeeds. A failed import can leave an incomplete directory; choose a new `--output-dir` on retry and pass its manifest to the evaluation commands. Dataset images, shards, and preparation tools are ignored under `artifacts/`. The application still has no mandatory third-party Python dependencies. No extraction/evaluation command downloads data or models.

The receipt schema measures five released amount labels: subtotal, discount, service charge, tax, and total. Top-level menu rows measure description, quantity, unit price, and line total. Store identity, invoice number, dates, currency, payment fields, void items, and submenus are outside this adapter. Missing released labels are **masked**, not treated as known empty values. Unparseable numeric labels are explicitly excluded. This is narrower than the full CORD hierarchical parsing task.

Amount normalization uses an explicit integer-rupiah convention: dot/comma groups of three digits are thousands separators, an optional trailing `.00`/`,00` has zero fractional units, and `Rp`, `IDR`, or `@` prefixes can be removed. Ambiguous nonzero decimal/grouping forms are excluded rather than guessed. Quantities accept printed `x` prefixes/suffixes and decimal counts. The same normalization applies to observed model/rules values and gold labels; original printed strings remain available on predictions.

Rows match one-to-one using exact normalized description, or exact quantity plus line total, maximizing matched count then cell agreement. Duplicates retain multiplicity. Exact rows require agreement on **eligible labeled cells**; they cannot establish correctness of unlabeled optional quantities or unit prices. Missing/failed records contribute false negatives for every eligible field and row. Extraneous unmatched rows count as false positives. Receipt F1 values and bootstrap intervals are reported separately from invoice scores.

English Tesseract PSM 1 is the initial measured OCR profile; Indonesian OCR assets are not installed or silently substituted. The rules adapter processed all 100 validation receipts but reached only **0.1273 total F1** and **0.0774 eligible exact-row F1**. In the fixed first validation image, OCR retained prices but omitted the item names and total label; a text-only model cannot reliably reconstruct that missing source text. All 307 extracted values had valid OCR references and numeric/text alignment, demonstrating that these checks do not establish label correctness. This development diagnostic is not held-out receipt quality or proof of Indonesian language coverage.

Run validation, then freeze before opening the test run:

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-receipts \
  --output-dir artifacts/cord-validation-rules-001
PYTHONPATH=src python3.12 -m docwork.cli eval-receipts --variant span_llm \
  --ocr-run artifacts/cord-validation-rules-001 --limit 12 \
  --output-dir artifacts/cord-validation-model-001
PYTHONPATH=src python3.12 -m docwork.cli eval-freeze-receipts \
  --rules artifacts/cord-validation-rules-001 --model artifacts/cord-validation-model-001 \
  --output artifacts/cord-freeze-001.json
PYTHONPATH=src python3.12 -m docwork.cli eval-receipts --split test \
  --freeze artifacts/cord-freeze-001.json --output-dir artifacts/cord-test-rules-001
PYTHONPATH=src python3.12 -m docwork.cli eval-receipts --split test --variant span_llm \
  --freeze artifacts/cord-freeze-001.json --ocr-run artifacts/cord-test-rules-001 \
  --output-dir artifacts/cord-test-model-001
PYTHONPATH=src python3.12 -m docwork.cli eval-compare-receipts \
  artifacts/cord-test-rules-001 artifacts/cord-test-model-001 \
  --output artifacts/cord-comparison-001.json
```

The validation model smoke uses the first 12 official validation rows in source order; its coverage is recorded explicitly. Omit `--limit` for all 100 validation receipts. Test runs refuse limits and require settings frozen from verified validation evidence. The model variant consumes the exact saved OCR from its paired rules run. Run only one owned model workload at a time on this Mac. `--resume` retains completed failures and requires the original identity. `eval-verify-receipts <run_directory>` checks the corpus, predictions, artifact inventory, source/profile hashes, owned model lifecycle, and deterministic rescoring without inference. The original invoice scorer and product schema remain separate.

`scripts/complete_release_evaluations.py` serializes invoice completion, the receipt validation model smoke, receipt freeze, both complete test variants, and paired comparison. It can wait for an already-running invoice evaluation with `--wait-for-invoice`; an interrupted invoice run must be resumed explicitly. `scripts/render_release_reports.py` verifies the completed reports before exporting a standalone HTML comparison. Neither script treats an empty, partial, or failed run as a passing quality result.

The current serialized run also requests automatic HTML export after all inference and offline audits finish:

```sh
PYTHONPATH=src python3.12 scripts/complete_release_evaluations.py \
  --invoice-run evals/invoice-model-heldout-2026-10-03 --wait-for-invoice \
  --receipt-root evals/cord-heldout-2026-10-03 \
  --html-output evals/release-comparison-2026-10-03.html
python3.12 scripts/evaluation_status.py
```

The invoice and receipt model stages require several hours of serial CPU/GPU inference on this Mac. Progress is an immutable completion count, not a quality estimate. For an interrupted run, resume the invoice command first if necessary, then restart the coordinator; it retains completed reports and ledger entries. HTML export requires a new output path and recomputes both paired comparisons, including receipt intervals, before publishing.

The release export now requires the complete 180-invoice and 100-receipt **test** splits; a validation run or limited smoke cannot be labeled a release. Alongside the HTML it writes `<output-stem>.evidence.json`, bound to the audited invoice report and receipt comparison hashes. The export shows per-variant stage total/P50/P95 seconds across all scheduled calls, including failures, and sampled model-server RSS where available. Percentiles use nearest rank. OCR and saved-OCR model timing boundaries remain separate; uncontrolled machine load cannot establish warm/cold or concurrent capacity.

Each variant also shows up to three mechanically selected failure/disagreement examples: processing failures first, then largest exact-row FP+FN, header FP+FN, and document ID. No cause is inferred from those scores, and complete denominators remain the headline. Missing, negative, or non-finite stage timings refuse export. Both output paths must be new; if publication is interrupted, choose a new output stem on retry.

Human workflow measurements are independent of extraction metrics. The [assisted author pilot](review-pilot.md) uses six declared development invoices, separately records active/idle/paused time, retains unfinished cases, and requires approved JSON exports. Human results remain pending; automated browser checks cannot supply them.
