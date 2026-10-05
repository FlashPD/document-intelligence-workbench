# Native group-memory evidence — October 4, 2026

This bundle addresses the missing Docker host-VM/shared-mapping boundary in B06/G12. It records same-user macOS `footprint` inspection, a real shared-mapping preflight and controlled fresh-upload workloads on the pinned arm64 parser image `sha256:09c23c3cf290412c5ae523c636c098d7f11c6a17a6a6ca6261e27419bfb6485c`. Application, parser, model, prompt, scoring and UI source are unchanged. **Whole-memory/full-v1 acceptance remains pending.** See the [method](../../docs/group-memory.md), [contract](../../docs/v1-release-contract.md) and [backlog](../../docs/backlog.md).

## Inventory and outcomes

| Artifact | Outcome and boundary |
|---|---|
| [Initial capability report](capabilities/report.json) and probe scripts/raw output | Same-user self-inspection works; noninteractive sudo is unavailable. The first idle probe finds no active VM under Docker resource saver. It is not a failed controlled workload. |
| [Active VM capability report](capabilities/active-report.json) | Observer wakes the VM; its actual open Docker backing store establishes association. Native/vmmap/footprint queries succeed; observer cleanup succeeds. |
| [Full category raw JSON](capabilities/categories-raw.json) and [cleanup](capabilities/categories-observer-cleanup.json) | Same-user full group query succeeds. This final capability observer stops cleanly with two guest samples/zero errors. The earlier short `probe-json.py` printed a false cleanup Boolean without retaining detailed cleanup evidence; it is not represented as a passing lifecycle check. |
| [Failed preflight](preflight-failed/report.json) | Missing optional `specific_to_pid` field exposed an overly strict validator before shared-allocation measurement. Old probe/source and failure remain retained. |
| [Passing preflight](preflight/report.json) | Two owned processes share a real 64 MiB file mapping. De-duplicated group-accounted resident delta is **67,256,576 bytes**; independent per-process sum exceeds the group by **70,139,904 bytes**. Temporary mapping/child cleanup succeeds. This preflight's exact source snapshot predates later error/ownership hardening. |
| [Initial rules report](initial-rules/report.json), [group report](initial-rules/group-report.json), [initial freeze](initial-freeze/group-protocol.json) | All 34 uploads ready; warm P95 **1.766 s**, observed group-accounted maximum **1.842 GiB**. Historical protocol precedes classified-error summary; saved verification preserves its original shape and discloses source drift. |
| [Final rules report](rules/report.json), [group report](rules/group-report.json), [freeze](freeze/group-protocol.json) | All **34 uploads** ready, zero failed/retried/checkpoint-reused documents. Warm upload P50/P95 **1.695/1.786 s**; cold workflow P50/P95 **2.042/2.104 s**. Serial queue: **24 pages in 42.278 s**, **34.060 pages/minute**. Observed group-accounted maximum **1.891 GiB**. All sampled 12 GiB budgets and five-second gaps pass; source unchanged and saved verification passes. |
| [Rules window review](coverage/rules.json), [analysis script](coverage/analyze.py) | All **34 parsing** stages intersect complete native query windows; **0/34 extraction** and **0/34 checking** stages do. Four launches yield **2/2/2/49** complete samples and **1/1/1/24** errors. Preserve millisecond-stage misses, startup/tail gaps and query failures; none imply zero allocations. |
| [Initial checkout](initial-checkout/report.json), [tooling checkout](tooling-checkout/report.json) | All nine isolated checks pass with **400** then **402** deterministic tests. Second snapshot precedes the final small backward-compatible protocol/error-summary changes; nine focused group tests subsequently pass. A final-source checkout after model timing is still pending. |

The final [model report](model/report.json), [group report](model/group-report.json), [saved verification](model/offline-verification.json) and [window review](coverage/model.json) now complete all **14 uploads** using the same final freeze and cached pinned assets. Warm upload P50/P95 is **77.786/88.232 s**; cold workflow **100.374/107.266 s**, with zero failures/retries/checkpoint reuse. Observed group-accounted maximum is **5.472 GiB**, accounted-resident maximum **4.443 GiB**. All sampled budgets/gaps pass; native/model sample counts are **1,333/1,321**, with **12 ProcessLookupError** queries (errno 3). Every model parsing/extraction stage intersects a complete inspection window; **0/14 checking** stages do. Four native collectors, four guest observers and four model runtimes close cleanly. Query-reported host free percentage reaches a minimum of **26%**; this is not an available-RAM total. First complete samples are **0.446–0.477 s** after collector creation, and tails are **0.556–4.442 s**. The maximum inter-observation gap is **3.656 s**. Final-source checkout after this completed measurement remains pending.

The raw gzip artifacts preserve selected PID/start identities, container names, native query start/end clocks, de-duplicated category totals, shared-object attribution and per-process charged ledgers. Native process-clock/date checks confirm the epoch conversion used in the post-run window review. Stage events are read content-free from disposable workbenches and durations cross-checked with the hash-bound base report. Window overlap does not prove an atomic or continuous memory observation.

The final rules errors include **20 ProcessLookupError** queries (errno 3) and **7 ValueError** queries, of which two native inspections are incomplete. Remaining ValueError labels are intentionally bounded `query_or_counter_error`; no more specific cause was retained. Transient processes may exit between selection/inspection, but an individual error is not assigned that cause without evidence. The first complete observations occur **0.469–0.626 s** after collector creation, and the tails after the last complete sample are **0.582–1.010 s**. Startup before collector creation and later shutdown remain outside these windows.

## Verify saved workloads

From the repository root with Python 3.12:

```sh
PYTHONPATH=src python3.12 scripts/benchmark_group_memory.py verify \
  evals/group-memory-2026-10-04/initial-freeze \
  evals/group-memory-2026-10-04/initial-rules
PYTHONPATH=src python3.12 scripts/benchmark_group_memory.py verify \
  evals/group-memory-2026-10-04/freeze \
  evals/group-memory-2026-10-04/rules
PYTHONPATH=src python3.12 scripts/benchmark_group_memory.py verify \
  evals/group-memory-2026-10-04/freeze \
  evals/group-memory-2026-10-04/model
```

These commands need no Docker, administrator privileges, native utility, runtime weights, OCR or inference. They rebuild summaries from checksum-bound complete native observations and verify the original performance artifacts/snapshots. They do not rerun measurement or close excluded boundaries.

Runtime databases, objects, model weights and assets are not copied into this bundle. Logical charged dirty memory (including swapped at its original size), resident-accounted categories and process lifetime maxima remain distinct. The VM includes guest/kernel/cache/observer overhead; guest/container counters are nested and never added. The browser, global shared OS cache and unattributed kernel/driver/other-user allocations are outside the selected group. **Observed maxima are lower bounds on continuous peaks.** No application-exclusive whole-memory peak, genuine scan quality, manual assessment, model promotion or full-release completion is claimed.

## Later source applicability

The [storage-guard source refresh](../storage-inventory-2026-10-04/README.md) changes one application leaf after these complete runs. The group verifier now reports `base_source_current` separately from `group_source_current`; earlier retained offline-verification files describe their invocation-time source, while a fresh audit discloses later drift. Original protocols, measurements and reports remain unchanged. All twelve corrected-source checkout checks and 409 tests pass; corrected-build controlled timing uses a new freeze/run, not these historical outcomes.
