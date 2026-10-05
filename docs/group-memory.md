# Native group-memory accounting

The [group collector](../scripts/group_memory.py) and [workload adapter](../scripts/benchmark_group_memory.py) extend the frozen cold/warm upload protocol without changing application, parser, extraction or scoring code. This addresses B06/G12's missing Docker host-VM boundary. It does not, by itself, close whole-memory acceptance or full v1.

## What the measurements mean

The macOS `footprint` utility can inspect same-user tasks without administrator credentials. Its group query de-duplicates multiply mapped objects and reports dirty, swapped, clean, reclaimable and wired categories, per-process charged ledgers and shared-mapping attribution. Selected tasks comprise the server's entire descendant tree, Docker backend/controller processes, and one Virtual Machine Service process whose open backing store matches Docker's `vms/0/data/Docker.raw`. A process name alone cannot establish that association. PID/start-time checks before and after inspection reject exited/reused targets.

The collector records the utility's actual JSON byte counters rather than adding RSS, guest occupancy, parser working sets or individual process footprints. `swapped` is a subset of dirty memory, charged at its original size; `wired` is also not an additional allocation. The derived **accounted resident** quantity is `dirty - swapped + clean + reclaimable`; **accounted memory** is `dirty + clean + reclaimable`. Both retain the native tool's accounting boundaries and are observed samples, not an exact continuous physical-memory peak. [Apple's Metal memory guide](https://developer.apple.com/documentation/xcode/analyzing-the-memory-usage-of-your-metal-app) explains the distinction between dirty, compressed/swapped and resident memory; the [WWDC explanation](https://developer.apple.com/videos/play/wwdc2022/10106/) covers unified-memory Metal resources and charged compressed size.

Driver/IOKit, graphics, owned-unmapped and nofootprint categories are preserved when present. Absence of a named category is not proof of zero driver use. Clean categories exclude globally shared OS cache; unrelated kernel/driver allocations, other-user services and browser memory remain outside the selected group. Docker VM memory includes its guest/kernel/cache and measurement observer; it is an inclusive infrastructure cost rather than incremental application-only memory. System auxiliary footprint remains separate and can exceed installed RAM because it reports logical charged dirty memory.

Per-process lifetime footprint maxima establish a conservative dirty bound only for the **observed process identities**. Long-running Docker maxima may predate the workload, and maxima do not occur simultaneously. This bound excludes unobserved short-lived processes and does not bound clean/nofootprint residency. It is not the workload's measured group peak. Unavailable queries remain explicit; competing containers invalidate the group boundary instead of disappearing from the report.

## Freeze and run

Use the reference macOS/Python 3.12 host, running Docker Desktop, the installed pinned parser and explicitly cached model/runtime assets. The wrapper delegates all uploads and processing to the existing benchmark server and normal serial supervisor. Rules and model run separately; no test suites, builds, browser verification, upgrades, downloads or unrelated inference should overlap controlled measurements.

```sh
PYTHONPATH=src python3.12 scripts/benchmark_group_memory.py freeze \
  --output-dir artifacts/group-freeze-new \
  --host-workloads 'Describe actual interactive/background activity and instrumentation'
PYTHONPATH=src python3.12 scripts/benchmark_group_memory.py run \
  artifacts/group-freeze-new --variant ocr_rules \
  --output-dir artifacts/group-rules-new
PYTHONPATH=src python3.12 scripts/benchmark_group_memory.py run \
  artifacts/group-freeze-new --variant span_llm \
  --output-dir artifacts/group-model-new
```

Choose new output directories. The freeze retains the original 34-rules/14-model schedule, original/source/image/profile hashes, three cold launches, warmup, ten warm uploads and twenty-document rules queue. A separately hash-bound protocol/snapshot binds the additional collector, adapter/tests and `/usr/bin/footprint` binary. A 0.5-second wait follows each native inspection; each inspection takes time, so actual observation intervals/gaps are reported rather than assumed. The protocol declares a **12 GiB sampled group-accounting budget** and a five-second maximum gap between complete observations. The sampled budget is not an OS-enforced aggregate cap or a guarantee about unseen peaks. Production's parser/artifact limits remain unchanged.

Native JSON preserves category/shared-object counters, selection identities, query windows and selected container names. Compressed per-launch artifacts keep the complete data; report summaries bind their checksums. Only containers named for the owned observer and job/fence identities in these disposable workbenches are permitted. The adapter records cleanup and query errors and refuses omitted/duplicate launch artifacts or missing optional-model samples. It does not promote the model, approve invoices or rewrite earlier timing reports.

```sh
PYTHONPATH=src python3.12 scripts/benchmark_group_memory.py verify \
  artifacts/group-freeze-new artifacts/group-rules-new
PYTHONPATH=src python3.12 -m unittest discover -s tests -p test_group_memory.py -v
```

Saved verification needs Python only. It verifies the base performance report and source snapshots, then rebuilds the group summaries from all four compressed launch artifacts. It rejects counter/identity/shared-object changes, incomplete queries within accepted samples, tampering, missing model observations and attribution violations. Original protocols without classified error summaries retain their original summary shape; source drift is disclosed, not silently rebound.

## Current evidence and remaining work

The [October 4 evidence bundle](../evals/group-memory-2026-10-04/README.md) retains successful same-user active-VM queries, their failed/limited attempts, the shared-mapping preflight and controlled workload results. A real **64 MiB shared file mapping** in two owned processes increases native group-accounted residency by **67,256,576 bytes**; independent per-process observations exceed the de-duplicated group by **70,139,904 bytes**. The first preflight exposed an optional native `specific_to_pid` field; its failure and old source are preserved. Group-owned mappings need no individual attribution field. The temporary file/processes are removed.

The initial rules schedule completes all 34 uploads with warm P95 **1.766 seconds** and observed group-accounted maximum **1.842 GiB**. It precedes stronger classified-error/competing-workload checks and remains a separate historical diagnostic. The final rules run also completes all 34 uploads: warm P50/P95 **1.695/1.786 s**, cold workflow **2.042/2.104 s**, and **24 serial queue pages in 42.278 s**. Its observed group-accounted maximum is **1.891 GiB**. All frozen sampled-budget/gap checks pass, but 27 queries fail across 55 complete observations. Every parsing stage intersects a native inspection window; all 34 millisecond extraction/checking stages lack an intersecting window. Startup/tail gaps and excluded boundaries remain explicit. The model schedule also completes all **14 uploads**, with warm P50/P95 **77.786/88.232 s**, cold workflow **100.374/107.266 s**, observed group-accounted maximum **5.472 GiB** and accounted-resident maximum **4.443 GiB**. Its 1,333 complete observations include 1,321 model observations and 12 query errors. All fourteen parsing/extraction stages intersect windows, but none of the checking stages do. Model IOAccelerator, IOKit and owned-unmapped graphics categories are present. All runtimes/observers/collectors close cleanly. These complete runs precede the storage-guard fix and parser-source refresh. Both base application and collector source drift are disclosed by saved verification. At that intermediate milestone, all twelve corrected-source checkout checks and 409 tests passed while fresh timing was underway; the completed corrected schedules are recorded below. These remaining coverage decisions must be resolved before G12 can close. Refer to the [backlog](backlog.md), [operations guide](operations.md) and [release contract](v1-release-contract.md); a complete schedule is not full v1 acceptance.

## Corrected storage-guard build

The [later current-source refresh](../evals/storage-inventory-2026-10-04/group-memory/README.md) preserves the complete historical runs above and completes **34 rules/14 model** uploads on image `sha256:93332a57ebdc02c1368925eb02cf6de2ae85260f845f1894be3814ea0f157b27`. Warm P95 is **1.994/88.500 seconds**, cold workflow P95 **5.006/106.728 seconds**, queue **24 pages/41.960 seconds**. Observed group-accounted maxima are **1.835/5.426 GiB**. All sampled budget/gap checks and owned cleanup pass, and both reports verify from portable paths with base/collector source current.

Rules retain 67 complete observations/15 query errors; model retains 1,220/16, including 1,208 model observations. All parsing windows intersect; model extraction intersects 14/14 and checking 3/14. Rules extraction/checking intersect 0/34. First complete samples/tails remain explicit, including a 3.347-second rules startup delay and 4.719-second model tail. These are interval-overlap diagnostics, not proof of complete allocation/process observation or continuous peaks. G12 remains partial; final-source workflow/security applicability is reviewed after timing.

## Pending G12 acceptance decision

The contract says **“Unavailable peak coverage remains pending.”** Current evidence passes the frozen 12 GiB **sampled** group-accounting budget and five-second complete-observation gap, with all declared attempts, query errors, stage-window coverage, source identities and owned shutdown retained. It does not bound continuous peaks or excluded browser/global-cache/unattributed kernel-driver memory.

Two explicit decisions are available. Retaining the current requirement keeps G12 partial until stronger peak/boundary evidence exists. Alternatively, the user can explicitly revise G12 to accept this declared sample-based method on the reference host, requiring all scheduled attempts, latency/throughput reporting, complete observed budget/gap checks, error/stage/startup/tail disclosures and verified cleanup. That revision would accept these measurement exclusions; it would not establish continuous whole-application peak memory or an OS-enforced aggregate cap. It requires a recorded contract/ADR change before any gate closure. No such decision has been made. G06/G11 actual scan/manual evidence and final identity/clone requirements would remain unchanged.
