# Operations and controlled upload evidence — October 4, 2026

This bundle records B06 operations and fresh-upload performance on an Apple M1 with 16 GiB RAM. Full v1 is pending under the [release contract](../../docs/v1-release-contract.md). Genuine scanner inputs, semantic assessments, whole-application memory coverage and final release closure are separate requirements.

The [final frozen protocol](freeze-003/protocol.json), [source snapshot](source_snapshot.json) and [hardware record](hardware.json) identify the workload and implementation. Cold means fresh application/model processes; the Docker VM and OS file caches remain warm. Rules and model run separately, with no concurrent test suite or browser verifier in the final timing runs. Interactive/background activity and sampling overhead remain declared. Each upload is a fresh document and parser attempt; there is no checkpoint reuse or retry.

## Rules result

The [rules report](rules-003/report.json) accounts for **34/34** review-ready outcomes: three cold clean uploads, one separately counted warmup, ten warm clean uploads and twenty queued originals. The [raw measurements](rules-003/measurements.json) and [memory samples](rules-003/memory.json) remain available.

| Measurement | P50 | P95 | Scope |
|---|---:|---:|---|
| Warm upload to observed terminal status | 1.517 s | 1.541 s | Ten clean fictional PNGs; all ready |
| Cold workflow including launch/readiness | 1.758 s | 1.759 s | Three fresh Python processes; Docker/cache warm |
| Queue parsing stage | 1.597 s | 3.525 s | Twenty fresh PNG/PDF originals |

The queue produces **24 pages in 38.694 seconds**, or **37.215 pages/minute**, with nonoverlapping persisted attempt intervals and one sampled active job. The warm rules objective of P95 at most sixty seconds passes on the declared clean sample. These are workflow timings, not extraction accuracy, arbitrary-vendor generalization, concurrent capacity, human effort or time-saved measurements. Polling makes terminal observations upper bounds on publication time.

## Optional model result

The [model report](model-003/report.json) accounts for **14/14** review-ready uploads: three cold-process clean uploads, one separately counted warmup and ten warm clean uploads. The [raw measurements](model-003/measurements.json) and [memory samples](model-003/memory.json) remain available. Warm upload P50/P95 is **65.964/66.785 seconds** and cold workflow P50/P95 **88.081/90.591 seconds**. All four owned model runtimes shut down cleanly; no unrelated inference was observed. The [pinned profile](model-profile.json) identifies assets and inference settings. Model timing has no sixty-second objective and cannot establish an accuracy gain or default promotion.

## Preserved earlier runs

- [First freeze](freeze-001/protocol.json) and [first report](rules-001/report.json): the original report is `interrupted` and cannot pass this no-retry protocol. All documents eventually became ready, but queue item ten had an abandoned attempt followed by recovery. A subsequent [read-only power-log inspection](rules-001/sleep-inspection.json) identified **151 seconds of idle sleep** at 09:37:38–09:40:09 America/Chicago. The inspection does not rewrite the original report. The revised runner owns idle-sleep inhibition and checks wall/monotonic divergence.
- [Second freeze](freeze-002/protocol.json) and [second report](rules-002/report.json): schedule/timing checks passed, but the deterministic suite overlapped the run without being specifically declared in its frozen workload text. It remains diagnostic; the third run supplies the controlled rules result.

## Operations checks and limits

The [HTTP report](http-report.json) records **ten real loopback checks** of authentication, capability separation, model/rules readiness, content-free queue age/failure/retry metrics, persisted stage coverage, deletion and storage refusal. Its parser/model failure categories are injected; it measures no OCR, inference or human review. The [pre-final test log](tests-before-final-checkout.log) records **355** deterministic tests before the additional freeze-drift test and final isolated checkout.

Metrics use persisted UTC intervals and publish counts alongside percentiles; older attempts have no invented stage coverage. Review wait is elapsed age of a completed extraction on a currently unapproved record, not active human effort; later edits can make a previously approved extraction unapproved again. Deletion removes per-document metric history. Endpoints require reviewer permission and omit document names/IDs/text/values, paths, actors and credentials.

Component RSS and container working-set observations are sampled lower bounds, not exact peaks. Container memory is nested inside Docker memory and is never added to Docker backend RSS. Process RSS can share pages, model RSS does not account for all Metal/unified-memory ownership, and Docker backend RSS is not an exclusive VM allocation. Missing parser samples remain null. Host free/speculative pages and pressure are context, not attributable application memory. Whole-application unified-memory acceptance remains pending.

The report field `docker_vm_memory_limit_bytes` contains Docker daemon `MemTotal` (8,215,117,824 bytes), not independent verification of the configured VM limit or peak usage. The runs retain 195/3,266 host samples and 42/962 Docker observations for rules/model, with zero sampling errors; some short cold parser lifetimes have no sample. These coverage limits cannot be repaired by summing RSS.

The [isolated checkout](checkout/report.json) passes all eight offline checks, including **356 deterministic tests**, current model-workflow integrity, corpus/prediction archives, author-pilot restoration, the new unapproved correction report and replay preparation. It binds 3,532 input files before this documentation-only result update and evidence retention. It is neither a committed tree nor a published tag. The [timing verification](timing-verification.json) confirms raw evidence/summary consistency and current host-code hashes; it does not imply the installed parser image is still the timing image.

## Rebuilt parser and current workflow

The [first parser audit](parser-first-attempt/report.json) failed its stale-image source inventory and two obsolete test expectations for export refusal. The [build log](parser-build/build.log) retains cached base/OCR/Pillow layers and refreshes copied source. The [rebuilt audit](parser-rebuilt/report.json) passes all sixteen checks, including independent Docker timeout/OOM events, cleanup, retry, checkpoint/backup restoration and immutable exports. The [current-image lifecycle report](lifecycle-current-image/report.json) passes eight native Chrome/Docker checks, including the new readiness display. [Sixteen replay controls](replay-browser/report.json) also pass. The prior-image [lifecycle report](lifecycle/report.json) remains historical.

The [real-model workflow](model-workflow/report.json) passes both fictional uploaded PNGs against the rebuilt image, including current server-established actor, correction, stale-revision refusal, approval, verified JSON/CSV downloads and restart/idempotent exports. It is scripted fixture verification, not human review or controlled performance. Its offline evidence verifier passes, and the checkout verifier now selects this bundle.

Timing uses image `sha256:ed27f389dffa98a267df581c6fce346c4690f111ef9293fa62ad3eecbd44f88b`; rebuilt runtime checks use `sha256:1882551df34e75e029bb64bdd9663b5843d4b11d87ba9073bab13ae6fc97dd8c`. The timing reports remain valid for their frozen prior configuration. **Final-configuration timing must be refreshed after parser build inputs are pinned.** No final-source G12 or full-v1 closure is claimed.

## Reproduce and verify

Use the [operations runbook](../../docs/operations.md) for new freezes/runs. From the repository root, retained rules consistency can be checked offline:

```sh
PYTHONPATH=src python3.12 scripts/benchmark_performance.py verify \
  evals/operations-2026-10-04/freeze-003 evals/operations-2026-10-04/rules-003
make operations-verify OUTPUT=artifacts/operations-http-fresh
```

Raw metrics/memory hashes and summary recomputation detect changed evidence. These are local consistency checks, not signed independent attestations or full-v1 acceptance. The benchmark workbenches, original parser outputs and model logs remain under ignored artifacts; no weights, credentials or runtime databases are published by this bundle. The frozen model profile identifies explicit setup assets; no download occurs during processing.
