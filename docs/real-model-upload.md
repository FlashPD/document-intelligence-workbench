# Real local model through uploads — October 3, 2026

The [recorded workflow](../evals/real-model-upload-2026-10-03/report.json) passes both fictional PNG upload checks using the actual HTTP API, isolated Docker parser, and pinned Qwen3-4B-Instruct-2507 model. This closes the earlier gap between the model's development evaluation and the product's upload/review/export path. It is a two-fixture integration result, not held-out invoice accuracy or a visual browser recording.

| Check | Observed result |
|---|---|
| Clean upload | Correct invoice number and row amount, supported source references, no validation issues; approval/export at revision 1 |
| Conflicting-total upload | Observed `275.00` preserved; `TOTAL_MISMATCH` blocks approval; fixture correction to `270.00` creates revision 2 and retains the original suggestion |
| Before approval | JSON export rejected with HTTP 409; the conflict also rejects approval with HTTP 422 |
| Revision control | Stale edits return HTTP 409; reopening SQLite retains the approval and idempotent JSON/CSV exports |
| Downloads | Page PNG and JSON/CSV bytes match the stored checksums |
| Model ownership | Hash-verified weights/runtime, an authenticated loopback child process, completed shutdown, and unchanged source/image inputs |

The automated reviewer is explicitly named `fictional-fixture-verification`. The command creates a temporary workbench database and reviews only the two literal hash-pinned repository fixtures. It cannot select a user's document or attach to an existing workbench database/server.

## Measurements and boundaries

Processing took **66.396 seconds** for the first clean upload and **57.521 seconds** for the subsequent conflicting-total upload. These are serial end-to-end HTTP worker times, including Docker parsing and model extraction, after server readiness. There was no explicit warmup, OS cache clearing, or controlled warm clean-page repeat. The first clean result exceeds the proposed 60-second feasibility target; the second fixture cannot establish warm clean-page latency.

Startup took 5.366 seconds. The runtime log records Apple M1 Metal and **37/37 layers offloaded to GPU**. One-second sampling observed maximum server RSS of 3,834,642,432 bytes (about 3.57 GiB). This is sampled process RSS, not peak GPU allocation, total workbench memory, or concurrent capacity. The model process stopped after both checks. Model/runtime hashes and inference settings are recorded in the report; parser image identity is separate from host preview OCR identity.

Saved artifacts include original model candidates, rendered pages, the corrected conflict revision, approval history, JSON/CSV downloads, profile/source snapshots, and the redacted runtime log. No ephemeral model or browser credential is included in runtime metadata exposed to the browser.

## Run the browser with the pinned model

With Docker running, the parser image built, and already-downloaded pinned assets verified:

```sh
make models-verify
make parser-build
make dev-model
```

Open the session URL printed by the server. Upload a fixture, choose **Local span model**, and process the next job. The ready model ID is shown automatically; endpoint/key entry is unnecessary. The key stays in the server-owned configuration. Model endpoint/ID overrides are rejected while a managed profile is active. **OCR rules** remains the default choice.

`make dev-model` is equivalent to `docwork serve --model-profile config/model-mac-instruct.json`. Startup verifies local assets and never downloads missing files. The CLI owns the model server until the workbench stops and then cleans up that process. A unique ignored `artifacts/model-sessions/` directory retains the profile, redacted diagnostics, and shutdown metadata. The ordinary `make dev` command retains its separately configured generic loopback model interface.

## Reproduce and audit

Use a new output directory for every live verification:

```sh
make model-workflow-verify OUTPUT=artifacts/model-workflow-fresh
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-model-workflow \
  evals/real-model-upload-2026-10-03
```

The verifier checks artifact inventory/hashes, profile/runtime identity and shutdown, both source-linked candidates, recalculated validation issues, preserved correction provenance, revision-bound exports, and approval history. It performs no fresh inference and needs neither model assets nor Docker. It establishes integrity relative to the saved report, not signed attestation of a model process.

## Candidate source refresh

The [candidate workflow report](../evals/portfolio-candidate-model-workflow-2026-10-03/report.json) repeats both real-model upload/review/export checks against the current source and rebuilt parser image after the invoice and receipt model workloads stop. Both cases pass, and offline verification reproduces the saved candidate, validation, correction, approval and export checks. The original report above remains historical.

Clean/conflicting-total processing takes **66.929/57.557 seconds**, including Docker parsing and model extraction after readiness. Peak sampled server RSS is 3,851,468,800 bytes, about 3.59 GiB; shutdown is recorded complete. These are two serial fixtures with uncontrolled machine load, not a warm/cold study or a total memory peak. The clean fixture again exceeds the original 60-second feasibility objective; an experimental label retains that failed objective.

The [complete held-out comparisons](heldout-model-comparison.md) and [browser verification](browser-verification.md) are now separately recorded. Multi-page model uploads, genuine scanner captures, controlled latency/memory studies and human review results remain unverified or deferred in the [candidate scope](portfolio-candidate.md). Every export still requires human approval.

## October 4 operations refresh

The [new workflow bundle](../evals/operations-2026-10-04/model-workflow/report.json) passes both uploaded fictional PNGs against the rebuilt parser image and current operations/reviewer dependencies. The automated fixture harness uses the server-established OS reviewer, rather than choosing an actor. It verifies original candidates, correction/stale revision behavior, approval, byte-verified JSON/CSV downloads and reopened idempotent exports. Offline verification passes; the clean-checkout verifier selected this report at that milestone. Historical reports remain unchanged.

The [separate controlled timing study](operations.md#october-4-measurement-record) records ten warm uploads per profile and three cold launches, with failures and memory boundaries explicit. Its frozen parser image predates the subsequent source-refresh build; final-configuration timing remains pending. The current workflow is integration evidence, not another quality, human-effort or productivity study. Full v1 remains pending under the [contract](v1-release-contract.md).

## Corrected storage-source workflow

The [fresh-source transfer run](../evals/storage-inventory-2026-10-04/README.md#fresh-source-setup-and-upgrade) repeats both real fictional workflows on the corrected parser image under the inherited outbound-denial policy. Clean/conflicting-total cases pass, including issue correction, revision-bound approval, byte-verified JSON/CSV and owned shutdown. The [standalone workflow](../evals/storage-inventory-2026-10-04/model-workflow/report.json) verifies offline and is now the checkout default. These integration durations do not replace the separate controlled 34-rules/14-model schedule or establish held-out/scan/human quality.
