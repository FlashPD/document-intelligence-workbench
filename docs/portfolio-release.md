# Portfolio release runbook

The [experimental portfolio candidate](portfolio-candidate.md) presents a local, evidence-linked invoice workflow and its measured limits. The [complete paired comparisons](heldout-model-comparison.md) account for 180 test invoices and 100 test receipts, including failed extractions. The local model regresses against rules on invoice headers and exact rows, so rules remain the default. A [refreshed captioned recording](../evals/portfolio-candidate-browser-2026-10-03/index.html) demonstrates verified browser controls on fictional recorded-OCR fixtures. The six-case human author pilot remains pending. The [system card](system-card.md) and [data card](data-card.md) distinguish this candidate from the broader [architecture plan](../arch_plan/document-intelligence-workbench-plan.md).

## Fresh checkout and first demo

Clone this repository and enter its root. Use Python 3.12; the default application and deterministic tests have no third-party Python dependencies. The commands below run directly from source, so an editable install is unnecessary. Commit-retained fixtures and evidence are required; a wheel alone is not the demo distribution.

```sh
git clone https://github.com/FlashPD/document-intelligence-workbench.git
cd document-intelligence-workbench
make test
make eval-verify-corpus
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-heldout \
  evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-model-workflow \
  evals/portfolio-candidate-model-workflow-2026-10-03
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-invoice-model \
  evals/invoice-model-heldout-2026-10-03
```

These are offline integrity/contract checks. They do not require Docker, Tesseract, model weights, or CORD downloads. Recorded model evidence is audited without executing the model. Full receipt rescoring and its release-audit check require the explicitly prepared CORD source corpus described in [release evaluation](release-evaluation.md#cord-receipt-evaluation). Missing local receipt data stays pending; it does not block the offline demo or invoice verification.

For an immediate interactive demo with only Python 3.12:

```sh
make demo-replay
```

The [offline replay](demo-replay.md) loads four fictional development candidates with source highlights, real corrections, approval, and downloads. The interface and exports label recorded OCR; no OCR, parser, model, or downloads run. Each launch preserves its own workbench under `artifacts/demo-replay/` and starts with fresh unapproved candidates. This entrypoint makes the workflow reviewable before live runtime setup; it does not satisfy model, held-out quality, or human-pilot evidence requirements.

To run fresh trusted-fixture OCR, install Tesseract with English language data on the host, then:

```sh
make doctor
make smoke-ocr
make dev
```

The [candidate clean-source check](../evals/portfolio-candidate-checkout-2026-10-03/report.json) copies tracked and nonignored current-change files into an isolated temporary tree, starts without `artifacts/`, model assets or a virtual environment, and runs six offline checks: contracts, corpus, baseline, complete invoice model, current model workflow and replay preparation. It binds every copied input and each command log. Reproduce it with a new output directory:

```sh
python3.12 scripts/verify_release_checkout.py \
  --output-dir artifacts/checkout-fresh \
  --model-workflow evals/portfolio-candidate-model-workflow-2026-10-03
```

The earlier [browser-release check](../evals/review-release-checkout-2026-10-03/report.json), with 253 tests and 2,721 copied files, and [initial packaging check](../evals/portfolio-checkout-2026-10-03/report.json) remain historical. These checks reproduce source snapshots before commit. CORD source images remain under ignored `artifacts/` and are not copied into the clean snapshot; these commands do not claim full receipt rescoring in a bare checkout.

### Verify an exact commit or tag

Before committing, verify the current source, including nonignored new files:

```sh
make release-checkout OUTPUT=artifacts/checkout-working-fresh
```

After committing, verify only the files actually retained in Git:

```sh
make release-checkout REF=HEAD OUTPUT=artifacts/checkout-commit-fresh
```

`REF` accepts a local commit, branch or tag. For an eventual release tag, replace `HEAD` with its name and use a new output directory. The reference is resolved once to an immutable commit and tree before reading files. Staged changes, uncommitted fixes, untracked helpers, and local runtime assets cannot contribute to that check. Git blobs are copied directly, preserving executable modes and bypassing archive attributes that could omit or substitute source files. Symlinks, submodules and committed runtime directories are rejected.

The six checks are the same as the precommit check: all deterministic contracts, corpus integrity, invoice baseline, invoice model comparison, model HTTP workflow evidence, and offline replay preparation. A version 2 report records the commit/tree, each copied file's SHA-256, invoking verifier hash, Python version, exact commands, log hashes, and whether the tested files changed during execution. A nonzero check, skipped/empty contract suite, changed input, or incomplete copy fails the check. Exit 0 means these offline checks passed; exit 2 means verification failed. The report and per-command logs remain in the chosen output directory.

CI now runs this committed-tree check and retains its evidence/logs. This is an offline reproducibility check using the invoking verifier; it does not establish that a remote clone is available, run new OCR/inference, verify Linux model support, or complete the human pilot. After publishing, a separate clone of the actual tag should run the same command and demo. Keep the [release audit](#generate-the-release-audit) separate: packaging can pass while the author pilot remains pending.

Open the one-time loopback URL printed by `make dev`. Choose **Clean sample**; click fields to inspect source highlights, review the values, approve the current revision, and export JSON/CSV. Choose **Conflicting total**; inspect the printed total and `TOTAL_MISMATCH`, enter a reason to retain the printed value or correct an extraction error from its source, and approve the resulting revision. Export is blocked until approval. Fixture buttons use fresh host OCR, not Docker or model inference.

For uploaded PDF/PNG/JPEG files, Docker Desktop must be running and the parser image built:

```sh
make parser-build
make parser-verify OUTPUT=artifacts/parser-checkout-fresh.json
make dev
```

Building the image needs registry/package network access the first time. Subsequent parser jobs run the locally built immutable image with no network. The Dockerfile pins Python and Pillow versions but its base tag and apt packages are not fully content-pinned build inputs; the saved image ID establishes what actually ran, not a reproducible future package resolution.

For the pinned Apple Silicon model, use the [model setup guide](intake.md#pinned-local-model-evaluation). `make models-fetch` is an explicit roughly 2.5 GB weights download plus runtime assets. After setup:

```sh
make models-verify
make dev-model
```

Select **Local span model** for uploads. Credentials stay in the server; no endpoint/key entry is required. Missing assets fail startup instead of downloading. This profile is verified on macOS arm64; other hosts can use the rules workflow, but this is not a verified Linux/CUDA model profile. Run only one model workload at a time on the Mac; serialize future evaluations and live model servers.

## Generate the release audit

```sh
PYTHONPATH=src python3.12 -m docwork.cli release-check \
  --output-dir artifacts/portfolio-audit-001 \
  --demo-recording evals/portfolio-candidate-browser-2026-10-03/demo.webm
```

The command runs fresh deterministic contracts and audits existing complete evidence. It writes a new directory containing `index.html`, `report.json`, and `tests.log`. Open the HTML locally to inspect each check; the JSON binds input inventories, current audit source, and the test log. No model inference, parser jobs, human review, or downloads are started. A partial model ledger is neither scored nor presented as completed quality evidence.

| Exit | Audit status | Meaning |
|---|---|---|
| 0 | `evidence_complete` | Supplied checklist evidence passed; manual review and remaining architecture acceptance still apply |
| 1 | `pending` | Evidence is absent/incomplete or a historical workflow needs a current-source rerun |
| 2 | `invalid` | Contracts failed, a completed report is malformed/incompatible, or evidence changed during verification |

The audit checks corpus integrity, all 180 baseline/model test invoices, all 100 test receipts in the paired comparison, parser workflow/recovery/resource coverage, the real-model HTTP fixture workflow, browser evidence, documentation presence, an explicitly supplied six-case author pilot, and an explicitly supplied recording. Measured model regressions remain publishable evidence; they do not promote an extractor. Browser automation cannot satisfy the author-pilot check. A supplied video is hashed only: a person must check playback, contents, and credential redaction. Documentation checks establish presence/hashes, not editorial correctness. This checklist is deliberately not a complete implementation of every architecture acceptance criterion.

Use a fresh output directory every time. After recording a new parser run, finishing and stopping the author pilot, and capturing a demo, supply repository-relative paths:

```sh
PYTHONPATH=src python3.12 -m docwork.cli release-check \
  --output-dir artifacts/portfolio-audit-002 \
  --parser-report artifacts/parser-checkout-fresh.json \
  --model-workflow-directory artifacts/model-workflow-fresh \
  --pilot-directory artifacts/review-pilot-author-2026-10-03-v3 \
  --demo-recording evals/portfolio-candidate-browser-2026-10-03/demo.webm
```

Run the audit only after supplied evidence writers stop. It checks input stability around verification and marks changed evidence invalid. Source drift in historical parser/model/browser reports remains visible; it does not rewrite original reports or rerun costly inference. Existing pending progress can be inspected with `python3.12 scripts/evaluation_status.py`; that command verifies completion hashes and shows ledger update time without inferring process liveness from missing metadata. The serialized coordinator and final comparison export are described in [release evaluation](release-evaluation.md), with [interrupted-session recovery](evaluation-recovery.md) and explicit lifecycle/memory limitations.

The defaults now select the [candidate parser report](../evals/portfolio-candidate-parser-2026-10-03-v2/report.json), [current model workflow](../evals/portfolio-candidate-model-workflow-2026-10-03/report.json), and [candidate browser bundle](../evals/portfolio-candidate-browser-2026-10-03/report.json). The [first candidate parser attempt](../evals/portfolio-candidate-parser-2026-10-03/report.json) preserves a stale-image failure after source changes. The image was rebuilt before all sixteen checks were repeated. Earlier [parser](../evals/queue-release-parser-2026-10-03/report.json), [browser](../evals/queue-release-browser-2026-10-03/report.json), and [recording](../evals/review-browser-2026-10-03-v2/index.html) bundles remain historical.

The [20-document parser queue](parser-queue.md) separately measures real serial processing of development PNG/PDF originals. Its concurrent-model declaration was unverified and remains qualified; it cannot establish processing under model load. It is supplemental performance evidence. The six-case author pilot is prepared at `artifacts/review-pilot-author-2026-10-03-v4`; its outcomes remain pending until the author reviews it and its server stops. Supply that directory to the final audit only after completion and shutdown.

## Three minute presentation script

Use fictional samples and a disposable local workbench. Complete setup before recording. Frame the browser after the session URL has exchanged its token, and keep terminal session credentials and local paths out of the capture. Keep the extraction variant and whether OCR is live/replayed visible in the narration.

| Time | Show | Explain |
|---|---|---|
| 0:00–0:25 | Queue and clean invoice | Local invoice product, deterministic default, mandatory human review |
| 0:25–0:55 | Click header and row values | Source page/line evidence; reference validity is weaker than semantic truth |
| 0:55–1:35 | Conflicting total and correction/acknowledgment | Printed evidence stays distinct from computed totals; every correction creates a revision |
| 1:35–2:05 | Approval and JSON/CSV downloads | Export is bound to the exact approved revision and remains reproducible |
| 2:05–2:35 | Release audit and completed comparison when available | All-document denominators, failures, paired uncertainty, source drift, and stage timing boundaries |
| 2:35–3:00 | Cards and recovery evidence | Parser isolation, checkpoint/fencing design, backup restoration, narrow dataset scope |

A model upload can be a separate recorded segment with its actual elapsed time labeled; the recorded clean fixture took over a minute. Do not present edited waiting time as measured latency. A demo recording is presentation evidence, not a human productivity study. [Run the author pilot separately](review-pilot.md) and publish incomplete outcomes as well as completed ones.

## Remaining release work

1. Finish the six-case author review pilot yourself and stop its server. Its report can describe effort; there is no manual-entry baseline to support time saved. Audit at a new output path after stopping the server.
2. Review the captioned scripted recording and its declared scope, or replace it with a narrated continuous capture. Browser checks cover stated rotated/multi-page geometry; OS popup menus and broader visual acceptance remain separate.
3. Review the system/data cards and [deliberate deferrals](portfolio-candidate.md#deliberately-deferred-scope), then commit the candidate evidence. Genuine scanner captures, semantic attribution sampling and controlled warm/cold/memory studies are not silently presented as completed.
4. Run a fresh audit with the finished pilot and recording, then run `make release-checkout REF=HEAD` after committing (or supply the eventual local experimental tag). Retain that exact-commit report before publishing with scoped claims, and verify a remote clone of the published tag afterward.

Packaging documentation and an audit do not publish or tag a release. Optional PC/GPU inference, framework migration, telemetry, and a VLM are separate workstreams; no claim is made that they have been completed.
