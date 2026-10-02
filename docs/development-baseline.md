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

These September 30 measurements show feasibility and specific failure modes only. They do not establish accuracy on held-out synthetic invoices, real invoices, or CORD receipts. At that point, no local language model, isolated parser, review workflow, or human-time comparison had been measured. The subsequent model experiment is recorded below.

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

## Real local model feasibility — October 2, 2026

**Decision: retain `ocr_rules` as the default.** Qwen3-4B-Instruct-2507 Q4_K_M with the existing `span-invoice-v1` prompt completed all 12 development documents, but the line-total gate returned `regression`. This is an unsuccessful candidate quality comparison with a working, reproducible inference workflow. It is not a release-quality claim or a Docling/layout-model experiment.

The [fresh baseline](../evals/local-model-instruct-2026-10-02/baseline.json), [model report](../evals/local-model-instruct-2026-10-02/model/report.json), and [paired comparison](../evals/local-model-instruct-2026-10-02/comparison.json) account for every scheduled document. The model bundle includes all original candidate predictions and OCR spans, the extraction source snapshot, exact profile, hardware preflight, and runtime log. `eval-verify` checks artifact hashes and reconstructs all scores without loading a model. Deterministic CI verifies these recorded artifacts; it does not generate fresh model predictions.

| Diagnostic | OCR rules | Local span model |
|---|---:|---:|
| Documents processed | 12/12 | 12/12 |
| Exact header values | 100/120 | 75/120 |
| Exact required header values | 48/60 | 48/60 |
| Documents with all required fields correct | 8/12 | 4/12 |
| Exact line totals by source order | 17/18 | 7/18 |
| Correct row count | 11/12 | 11/12 |
| Mean header evidence-box IoU | 0.3810 | 0.2874 |
| Injected total conflict detected | 1/1 | 1/1 |
| False total-conflict warnings | 0/11 | 0/11 |

The line-total delta is −0.556, with a descriptive 95% paired layout-family bootstrap interval of [−0.875, −0.250]. Required-field accuracy ties at 0.80, while the number of entirely correct required-field records halves. That tradeoff would be hidden by looking only at the aggregate required-field rate. The six-family, author-created development set remains too small and too familiar to establish unseen-vendor performance.

Representative errors are inspectable in the preserved predictions:

- [Classic invoice `dev-01-01`](../evals/local-model-instruct-2026-10-02/model/predictions/dev-01-01.json): OCR contains `Subtotal: 150.00` and `Total: 160.00`, but the model returns null for both. Eight of the 12 documents have missing model totals. This is an extraction failure even though the response passes the JSON schema.
- [Two-column invoice `dev-04-01`](../evals/local-model-instruct-2026-10-02/model/predictions/dev-04-01.json): all ten headers and both line totals are correct, but both quantities are the literal string `value`. Evidence and numeric validation block review completion. Across the two-column family, required-field accuracy improves from 0.40 to 0.90 while row-amount accuracy declines from 1.00 to 0.50.
- [Conflicting invoice `dev-05-02`](../evals/local-model-instruct-2026-10-02/model/predictions/dev-05-02.json): the model preserves observed total `424.00`; validation reports the conflict with computed `419.00`. A quantity error also remains. Correctly detecting the total conflict does not make the whole record correct.

Eleven model candidates have at least one blocking validation issue. That is a review-required count, not a measured human workload or review-time improvement. No candidate was automatically approved or exported.

### Runtime and reproduction

The [profile](../config/model-mac-instruct.json) pins the 2,497,281,120-byte model and llama.cpp b11149 archive by SHA-256 and source revision. The owned child process uses one 8192-token slot, temperature 0, seed 42, a 2048-token output cap, and a 150-second request timeout. The prompt was not tuned during this run. A separate clean-demo smoke check preceded the full evaluation; it is excluded from these 12-document metrics.

On the Apple M1 / 16 GB Mac, the [runtime log](../evals/local-model-instruct-2026-10-02/model/server.log) confirms all 37/37 layers offloaded to the Apple M1 Metal device. Median model stage time was 65.614 seconds per page (range 33.686–84.603), totaling 763.726 seconds. OCR added 5.514 seconds; the entire managed command took 776.256 seconds including verification, startup, recording, and shutdown. Sampled peak server RSS was 4,841,783,296 bytes (about 4.51 GiB) across 764 samples. This is process RSS, not a separately measured GPU peak or full application memory footprint.

There was no explicit warmup and no clearing of OS/Metal caches. Later requests could reuse prompt prefixes. These are exploratory serial feasibility measurements, not a controlled throughput or cold/warm comparison. The server exited and its extracted temporary runtime directory was removed before the final report was written.

```sh
# Recheck recorded evidence offline; no model download or inference.
PYTHONPATH=src python3.12 -m docwork.cli eval-verify evals/local-model-instruct-2026-10-02/model

# Recreate the comparison and HTML report. Exit 1 is the expected regression.
PYTHONPATH=src python3.12 -m docwork.cli eval-compare evals/local-model-instruct-2026-10-02/baseline.json evals/local-model-instruct-2026-10-02/model/report.json --allow-change extractor
```

For new inference, follow the [pinned model runbook](intake.md#pinned-local-model-evaluation) with a new output directory. The next extraction experiment should address numeric value representation and table/row context, then repeat this same development comparison. Genuine scanned documents, verified container execution, pinned OCR assets, the larger separated invoice/receipt corpora, held-out scoring, and a human review pilot remain open release requirements.
