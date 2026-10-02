# Development baseline: `ocr-rules-v0.2`

Measured September 30, 2026 on the Apple M1 MacBook Pro with 16 GB RAM, Python 3.12.12, and Tesseract 5.4.1 English OCR. The [development manifest](../datasets/development-v0.json) SHA-256 is `f49e349a900e3d09612d00179027d10730f91786a059707f77ebb57f9589e60d`. The [recorded run](../evals/development-baseline-v0.2.json) contains per-document scores. Reproduce it with `make eval-development`; the fresh result is written to `artifacts/development-baseline.json`.

This is author-created development data: 12 fictional, one-page PNG invoices from six layout families, two pages per family. One page has a deliberately inconsistent total; one has synthetic blur and low contrast; one is cropped and rotated. Three representative pages were visually inspected, and the manifest's box transforms were checked mechanically. It is not a real scanned corpus, and these layouts were used to tune the baseline.

| Diagnostic | Result |
|---|---:|
| Documents processed | 12 / 12 |
| Exact header values | 100 / 120 |
| Exact required header values | 48 / 60 |
| All five required fields correct | 8 / 12 documents |
| Exact line totals, matched by source order | 17 / 18 |
| Correct row count | 11 / 12 documents |
| Mean header source-box IoU, all labeled slots | 0.381 |
| Injected total conflicts detected | 1 / 1 |
| False total-conflict warnings | 0 / 11 |
| Sum of OCR time across 12 serial pages | About 5.3 seconds |

Exact header scoring compares canonical strings, with no fuzzy normalization. Missing predictions and processing failures count as errors. Line-total matching uses source order for this small diagnostic; the planned release evaluator will use a frozen row matcher. Box IoU compares the whole OCR line rectangle with the generated value-only box, using zero for missing evidence. A span ID or overlap alone does not prove semantic support.

The first regex-only pass found 4/18 line totals because Tesseract separated many rightmost amounts into their own spans. A development change joined spans on the same page row using vertical position, yielding 17/18. The remaining missing row is on the sideways page; the current OCR adapter does not rotate it upright. The two-column family also omits colons and merges header text across columns, leaving required fields missing. The low-contrast page renders dates as `MM/DD/YYYY`, which the strict parser flags as ambiguous rather than silently guessing a locale.

These measurements show feasibility and specific failure modes only. They do not establish accuracy on held-out synthetic invoices, real invoices, or CORD receipts. No local language model, isolated parser, review workflow, or human-time comparison has been measured yet.

## Development comparison gate

The evaluator now writes versioned reports with the frozen manifest hash, invoice schema and scoring versions, execution kind, runtime versions, extractor settings, and a SHA-256 fingerprint of the extraction/OCR/validation source files. The model profile records its prompt hash, token/timeout limits, and server alias; it does not verify the server's actual model weights. Source fingerprints identify code bytes but do not replace pinned model/OCR assets or a dependency lock.

Generate two fresh baseline runs and inspect their comparison:

```sh
make eval-repeatability
open artifacts/development-comparison.html
```

To compare the rules baseline with a configured local span model:

```sh
make eval-development
PYTHONPATH=src python3.12 -m docwork.cli eval-development-model --model-endpoint http://127.0.0.1:8080 --model-id local-invoice
PYTHONPATH=src python3.12 -m docwork.cli eval-compare artifacts/development-baseline.json artifacts/development-model.json --allow-change extractor
```

The JSON and self-contained, script-free HTML reports default to `artifacts/development-comparison.json` and `artifacts/development-comparison.html`. Override with `--output` and `--html`. Comparisons record the SHA-256 of both input files. Input summaries are recomputed from document scores and checked against the submitted summaries. Every manifest document must occur exactly once, with the expected field/row eligibility and layout group. Failed predictions stay in the denominators. Invalid, truncated, or unreadable JSON causes a CLI error; structurally inconsistent reports produce an `unusable_evidence` report.

The gate returns these process exit codes:

| Exit | Status | Meaning |
|---|---|---|
| 0 | `pass` | No gated point estimate regressed beyond the configured tolerance; completion count did not drop |
| 1 | `regression` | Required-header exact match, line-total exact match by source order, or row-count accuracy dropped by more than 0.02, or fewer documents completed |
| 2 | `unusable_evidence` / CLI error | Missing/incompatible metadata, invalid scores, omitted documents, undeclared changes, non-fresh evidence, or no successful predictions in either run |

`--max-regression` changes the absolute tolerance. `--allow-change extractor` declares a change to rules, model, prompt, configuration, or pipeline source bytes. `--allow-change runtime_versions` declares a Python/Tesseract runtime change. These flags cannot bypass dataset, split, schema, scoring, eligibility, or fresh-evidence checks. Legacy reports, including the original September 30 run above, remain historical artifacts and cannot pass the new gate; rerun extraction rather than inventing their missing metadata.

Quality intervals use 2,000 paired percentile bootstrap samples with seed 1729 by default. Each sampled layout family includes both of its documents in both variants, retaining the within-family dependence. The report also shows all six families separately. These intervals describe a very small, author-created development corpus; they do not establish performance on arbitrary unseen vendors. The gate uses point estimates, not a claim of statistical significance. All-failed runs are unusable, and individual failures remain visible even when a comparison passes.

The current metrics are canonical-string exact matches and source-order row amounts, **not** the planned release macro-F1 and duplicate-aware row matching. Extra rows affect the row-count gate. Evidence overlap remains an OCR-line versus value-box diagnostic, not semantic attribution accuracy. Stage times are retained in input summaries, but the comparison makes no latency or speedup claim: hardware capacity, model runtime, warmup, and failed-stage timings are not fully captured.

### Recorded repeatability check — October 2, 2026

Two consecutive fresh Tesseract runs on the same Apple M1 / 16 GB Mac completed all 12 documents. Both reproduced 100/120 exact header values, 48/60 required values, 17/18 line totals, and 11/12 correct row counts. No processing failures occurred. Every reported quality delta and paired interval was zero; the gate returned `pass`. Summed OCR times were 5.336 and 5.351 seconds, without a controlled latency claim.

The [first run](../evals/development-repeatability-2026-10-02/baseline.json), [repeat run](../evals/development-repeatability-2026-10-02/repeat.json), and [comparison](../evals/development-repeatability-2026-10-02/comparison.json) preserve this evidence. Deterministic CI recomputes the comparison from those hashed inputs; it does not rerun OCR. To render the recorded comparison locally:

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-compare evals/development-repeatability-2026-10-02/baseline.json evals/development-repeatability-2026-10-02/repeat.json
```

This checks repeatability of the same rules extractor, not a model improvement. A fresh real-model comparison, pinned runtime/assets, the larger separated invoice/receipt corpora, and held-out scoring remain necessary for the portfolio release.
