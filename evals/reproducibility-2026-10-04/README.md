# Pinned-build and upgrade evidence — October 4, 2026

This bundle records B07 build-pin/upgrade work and refreshed rules timing. **Full local v1 remains pending.** See the [runbook](../../docs/parser-build.md), [backlog](../../docs/backlog.md), [acceptance contract](../../docs/v1-release-contract.md) and [standalone evidence view](index.html).

| Observation | Result | Evidence |
|---|---|---|
| Two uncached arm64 dependency rebuilds | Pass; identical 166-package version inventories, Python/Pillow, OCR bytes and copied source/build hashes | [Report](rebuild/report.json), [first log](rebuild/build-1.log), [second log](rebuild/build-2.log) |
| Pinned-image runtime/recovery suite | All sixteen pass; real Docker/Tesseract, two-page PDF and timeout/OOM/retry checks | [Report](parser/report.json), [test log](parser/tests.log) |
| Upgrade from exact experimental-release code | All five assertions pass; historical edits/decisions/approvals/exports preserved, legacy jobs migrate, backup restores/fences, new edit requires new approval | [Report](upgrade/report.json), [prior fixture state](upgrade/prior-state.json) |
| Deterministic contracts | All 360 pass, including four new build-lock/source/runtime drift checks | [Test log](tests.log) |
| Live pinned-model HTTP workflow | Both fictional upload/correction/approval/export fixtures pass; owned runtime shuts down; offline evidence verification passes | [Report](model-workflow/report.json), [runner log](model-workflow-runner.log) |
| Live Chrome lifecycle/operations | All eight controls pass on disposable fictional inputs and real Docker OCR | [Report](lifecycle/report.json), [review workspace](lifecycle/review-ready.png) |
| Isolated working-source checkout | All eight checks pass, including 360 tests, saved-model/pilot/correction evidence and offline replay | [Report](checkout/report.json), [contracts log](checkout/contracts.log) |
| Controlled pinned-image rules schedule | All 34 uploads ready; warm P50/P95 1.632/1.707 s; cold workflow 1.874/1.912 s; queue 24 pages in 41.613 s | [Freeze](performance-freeze/protocol.json), [report](performance-rules/report.json), [offline verification](performance-rules/offline-verification.json), [memory observations](performance-rules/memory.json) |

The two image IDs are `sha256:5dc04998631038c528372ac1fe747e52a6227d4aca01225bc354a49fb1a8eb43` and `sha256:5a56f3229e80096806e45708f81b66d1780ff46aecf6d7bb9c5c101319aff070`. The first is selected for local runtime checks. Dependency/source identities agree; OCI IDs differ. The probe verifies installed versions and OCR/build/source hashes, not every installed binary byte or bit-identical OCI output. Base layers may be cached; dependency installation layers rerun. Only arm64 has local runtime evidence.

The upgrade probe creates a disposable workbench using the exact `v0.1.0-experimental` committed source in a separate Python process. Its approvals are explicitly automated fixture actions on fictional recorded development candidates. It never opens user data, modifies the original pilot, measures quality, or establishes recovery from machine power loss. Temporary workbenches are removed; the recorded prior state contains fictional test data and temporary paths that are historical context, not prerequisites for reproduction.

Rules timing uses the normal supervised HTTP workflow with new uploads, one attempt each, no checkpoint reuse, three cold-process trials, one separately counted warmup, ten warm trials and a twenty-document serial queue. No test suite, build, upgrade probe, browser verifier or unrelated inference ran during timing. Cold means new application processes with OS/Docker caches retained. Component memory samples cannot establish whole-application unified-memory peaks. Complete pinned-image model timing remains pending. The separate two-fixture model workflow is functional evidence, not the controlled fourteen-upload schedule or new quality/human-review evidence. Chrome controls run separately from controlled rules measurements.

## Negative attempts preserved

- [Initial build log](first-build-attempt/build.log) and [context](first-build-attempt/context.json): snapshot `20260224T000000Z` lacked the recorded Poppler version, so exact-version installation failed. Passing rebuilds use `20261003T000000Z`.
- [Upgrade attempt 1](upgrade-attempt-1/report.json): verifier failed to canonicalize macOS `/var` versus `/private/var` paths.
- [Upgrade attempt 2](upgrade-attempt-2/report.json): verifier incorrectly expected a slots dataclass to have `__dict__`.
- [Upgrade attempt 3](upgrade-attempt-3/report.json): verifier compared runtime tuples to their JSON list representation.
- [Upgrade attempt 4](upgrade-attempt-4/report.json): verifier requested an internal `reparse` column from the public status response.

These failed observations remain unchanged and do not count as passing checks or product migration failures. The final upgrade probe corrects the verifier assumptions while retaining strict state/export comparisons. Original invoice/CORD predictions, author-pilot errors and draft-correction reports remain unchanged.

Successful reports bind their relevant source/input/image identities. [Source snapshot](source_snapshot.json) retains the successful build/upgrade/performance sources and parser test tooling. The bundle is local observed evidence, not a signed attestation, a committed/published release tree, a scanner study or a complete fifteen-gate audit. The [guide](../../docs/parser-build.md) gives reproduction commands; use new output directories.

The checkout report binds a stable working-source snapshot before the subsequent documentation-only result update and retention of the checkout output itself. It does not verify a new committed tree or published tag. The bundle inventory hashes retained files for later integrity checks; these hashes are not an independent attestation.
