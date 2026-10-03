# Portfolio release runbook

The portfolio presentation should show a local, evidence-linked invoice workflow and explain its measured limits. A [captioned scripted recording](../evals/review-browser-2026-10-03-v2/index.html) is now available, with [Chrome verification](browser-verification.md) of keyboard, rotation, page navigation, revision, and export behavior on fictional recorded-OCR fixtures. The product remains experimental while the complete invoice/receipt model comparisons, human review pilot, and current-build model workflow rerun are pending. The [system card](system-card.md) and [data card](data-card.md) distinguish implemented behavior from the larger [architecture plan](../arch_plan/document-intelligence-workbench-plan.md).

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
  evals/real-model-upload-2026-10-03
```

These are offline integrity/contract checks. They do not require Docker, Tesseract, model weights, or CORD downloads. Recorded model evidence is audited without executing the model.

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

The [browser-release clean-source check](../evals/review-release-checkout-2026-10-03/report.json) passes all four offline commands above, including 253 deterministic tests, in a temporary copy of 2,721 tracked/current-change files. It excludes the active inference bundle and starts without `artifacts/`, a virtual environment, or model assets. Saved source/log hashes identify the check. The [initial packaging check](../evals/portfolio-checkout-2026-10-03/report.json) remains as historical evidence. These are source-snapshot reproduction checks before commit; cloning the eventual release tag remains a separate final check.

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

Select **Local span model** for uploads. Credentials stay in the server; no endpoint/key entry is required. Missing assets fail startup instead of downloading. This profile is verified on macOS arm64; other hosts can use the rules workflow, but this is not a verified Linux/CUDA model profile. Run only one model workload at a time on the Mac. Let the current serialized evaluation finish before starting another model server.

## Generate the release audit

```sh
make release-check OUTPUT=artifacts/portfolio-audit-001
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
  --demo-recording evals/review-browser-2026-10-03-v2/demo.webm
```

Run the audit only after supplied evidence writers stop. It checks input stability around verification and marks changed evidence invalid. Source drift in historical parser/model/browser reports remains visible; it does not rewrite original reports or rerun costly inference. Existing pending progress can be inspected with `python3.12 scripts/evaluation_status.py`. The serialized coordinator and final comparison export are described in [release evaluation](release-evaluation.md).

The parser default points to the [browser-release parser report](../evals/review-release-parser-2026-10-03/report.json). The earlier [first verification attempt](../evals/portfolio-parser-2026-10-03/report.json) retained its failed stale-image check; the image was rebuilt before repeating verification. This preserves the failed attempt rather than rewriting it into a passing result.

After the offline replay entrypoint, the [queue-release parser refresh](../evals/queue-release-parser-2026-10-03/report.json) passes all 16 workflow/recovery/resource checks, and the [browser refresh](../evals/queue-release-browser-2026-10-03/report.json) passes all 15 normal-workbench controls. They identify the newer source and parser image. The browser refresh captures frames without making a new video; the earlier captioned recording remains separately labeled historical presentation evidence. Use these explicit overrides when auditing this source snapshot:

```sh
PYTHONPATH=src python3.12 -m docwork.cli release-check \
  --output-dir artifacts/queue-release-audit-fresh \
  --parser-report evals/queue-release-parser-2026-10-03/report.json \
  --browser-directory evals/queue-release-browser-2026-10-03 \
  --demo-recording evals/review-browser-2026-10-03-v2/demo.webm
```

The [20-document parser queue](parser-queue.md) separately measures real serial processing of development PNG/PDF originals with declared concurrent model load. It is supplemental performance evidence, not an additional passing gate in `release-check`. The full invoice/receipt comparisons, current real-model workflow rerun, and human pilot are still required. A fresh six-case pilot was prepared at `artifacts/review-pilot-author-2026-10-03-v4`; its outcomes remain pending until the author reviews it and its server stops. Supply that directory to the final audit only after completion and shutdown.

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

1. Let the frozen invoice model run and serialized CORD evaluations finish; audit and export both complete comparisons without selecting successful cases or tuning on test results.
2. After those jobs finish, rerun the real-model HTTP workflow against the current source with a new output directory. Its previous report is valid historical evidence but predates worker/browser changes.
3. Finish the six-case author review pilot yourself and stop its server. Its report can describe effort; there is no manual-entry baseline to support time saved.
4. Review the captioned scripted recording and its scope, or replace it with a narrated continuous capture. The four-case automated browser check covers stated rotated/multi-page geometry; OS popup menus and broader visual acceptance remain separate. Add genuine scanner-captured development evidence with permission and declared labels.
5. Review the system/data cards and fresh-checkout instructions, identify any remaining architecture criteria deliberately deferred, and tag an experimental release with scoped claims when that evidence is ready.

Packaging documentation and an audit do not publish or tag a release. Optional PC/GPU inference, framework migration, telemetry, and a VLM are separate workstreams; no claim is made that they have been completed.
