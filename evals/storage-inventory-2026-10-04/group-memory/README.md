# Corrected-source controlled memory/timing refresh — October 4, 2026

New freeze `artifacts/group-memory-freeze-2026-10-04-003` binds the [storage-guard fix](../README.md), parser image `sha256:93332a57ebdc02c1368925eb02cf6de2ae85260f845f1894be3814ea0f157b27`, unchanged extraction/scoring/profile/input identities and current collector/utility. Earlier complete workloads remain historical in the [original bundle](../../group-memory-2026-10-04/README.md).

The corrected-source [rules report](rules/report.json) completes **34/34** fresh uploads with zero failures, retries or checkpoint reuse. Warm P50/P95 is **1.691/1.994 s**, cold workflow **2.134/5.006 s**. The first cold startup takes 2.755 seconds; that observation is retained. The serial queue produces **24 pages in 41.960 s**, **34.318 pages/minute**, with one active worker.

The [group report](rules/group-report.json) retains **67 complete observations/15 query errors** (7 ProcessLookupError, 8 ValueError), including three native-inspection-incomplete reasons and one startup job-registry-unavailable reason. Observed group-accounted maximum is **1.835 GiB**, accounted-resident maximum **1.640 GiB**. All sampled twelve-GiB budgets/five-second gaps pass. The first cold complete sample is delayed 3.347 seconds; the stage/startup/tail review is retained below. All four native collectors, VM observers and base samplers stop cleanly. These are sampled observations, not a continuous application-exclusive memory peak.

[Runtime verification](rules/offline-verification.json) and [portable verification](rules/portable-verification.json) pass with both base and group source current. Only top-level evidence/raw counter files are retained; runtime databases/objects and assets stay in ignored artifacts.

The [model report](model/report.json) completes **14/14 uploads**, zero failures/retries/checkpoint reuse, warm P50/P95 **84.896/88.500 s**, cold workflow **105.392/106.728 s**. Observed group-accounted maximum is **5.426 GiB**, resident-accounted **4.330 GiB**; all sampled budgets/gaps pass. **1,220 complete observations** include **1,208 model observations**, with **16 query errors** (14 ProcessLookupError, 2 ValueError). All four model runtimes/native collectors/guest observers/base samplers stop cleanly. [Runtime](model/offline-verification.json) and [portable verification](model/portable-verification.json) pass with both sources current. Original model runtime: `artifacts/group-memory-model-2026-10-04-002`; exec session 64328 is terminal, exit 0.

The post-run [rules](coverage/rules.json)/[model](coverage/model.json) window reviews intersect all parsing stages; model extraction intersects 14/14, rules extraction 0/34. Checking intersects **3/14 model** and **0/34 rules** stages. Native windows are inspection intervals, not continuous occupancy snapshots; overlap does not establish that every allocation/process was observed. Model first complete samples start 0.471–0.496 seconds after collector creation, tails range 0.522–4.719 seconds, maximum complete-sample gap **2.534 seconds**. Rules first sample/tails remain explicit, including the 3.347-second first cold delay. Runtime DB events were read only after measurement and cross-checked against frozen durations; the [review script](coverage/analyze.py) and content-free hashes are retained, not private/runtime workbenches. A Python-only verifier cannot independently reconstruct the event-window review without those runtime DBs.

No tests/builds/browser captures/downloads/unrelated inference overlap either controlled schedule. Later native workflow/authority/adversarial checks run after confirmed terminal completion and are separate integration evidence.

Offline rules audit:

```sh
PYTHONPATH=src python3.12 scripts/benchmark_group_memory.py verify \
  evals/storage-inventory-2026-10-04/group-memory/freeze \
  evals/storage-inventory-2026-10-04/group-memory/rules
```

Browser/global OS cache/unattributed kernel-driver allocations and unobserved startup/shutdown/short-stage peaks remain excluded. These runs do not supply genuine scans, manual semantic or approved-quality judgments, or full-release closure. The [method](../../../docs/group-memory.md), [contract](../../../docs/v1-release-contract.md) and [backlog](../../../docs/backlog.md) preserve those requirements.
