# Process footprint and controlled performance — October 4, 2026

This B06 continuation adds native charged-memory and Linux VM observations and completes the pinned-image rules/model timing schedules. **Full v1 remains pending.** Read the [operations method](../../docs/operations.md), [backlog](../../docs/backlog.md), [release contract](../../docs/v1-release-contract.md) and [standalone view](index.html).

| Observation | Result | Evidence |
|---|---|---|
| Controlled rules | 34/34 ready, warm P50/P95 1.630/1.868 s, cold workflow 1.921/1.934 s | [Report](performance-rules/report.json), [raw measurements](performance-rules/measurements.json), [offline verification](performance-rules/offline-verification.json) |
| Serial rules queue | 24 pages in 42.109 s, 34.197 pages/minute; durable attempt intervals do not overlap | [Stage metrics](performance-rules/warm-metrics.json) |
| Controlled optional model | 14/14 ready, warm P50/P95 81.735/93.473 s, cold workflow 109.692/114.210 s; four owned model runtimes stop cleanly | [Report](performance-model/report.json), [raw measurements](performance-model/measurements.json), [offline verification](performance-model/offline-verification.json) |
| Source-refresh pinned parser | All sixteen live runtime/recovery checks pass; dependency pins unchanged | [Report](parser/report.json), [test log](parser/tests.log), [build log](parser/build.log) |
| Memory counter preflight | Native ABI matches public SDK; touching 64 MiB increases charged footprint by 67,174,528 bytes; bounded VM observer samples and removes cleanly | [Report](preflight/report.json), [ABI probe](preflight/abi.c), [exact historical helper](preflight/observer_source_snapshot.py) |
| Deterministic checks before final checkout | 367 pass before the additional early-exit check; all eight focused memory checks subsequently pass | [Original full-suite log](tests-before-final-checkout.log), [current test source](../../tests/test_memory_accounting.py) |
| Isolated working-source checkout | All eight checks pass, including all 368 deterministic tests, saved-model/pilot/correction evidence and offline replay | [Report](checkout/report.json), [contracts log](checkout/contracts.log) |

Both schedules use the [same freeze](performance-freeze/protocol.json) and immutable parser `sha256:09c23c3cf290412c5ae523c636c098d7f11c6a17a6a6ca6261e27419bfb6485c`. The [source snapshot](source_snapshot.json) matches every frozen source hash and includes relevant test source. No extraction/scoring algorithm changes; original invoice/CORD predictions, pilot errors and draft corrections remain unchanged. Prior dependency rebuilds, upgrades, model approval/export and browser observations remain in the [earlier reproducibility bundle](../reproducibility-2026-10-04/README.md) with their own source/image identities.

Cold trials use new Python/model processes with OS/Docker caches retained. One separately counted warmup precedes ten warm uploads; rules also run twenty queued PNG/PDF originals. Every upload has one fresh attempt, no checkpoint reuse or retry. The normal HTTP supervisor processes them. Instrumentation and ordinary interactive/background activity are declared in the freeze. No tests, builds, browser verification, upgrade probes or unrelated inference run during controlled timing. Idle sleep is inhibited and wall/monotonic divergence checked. Optional-model latency has no sixty-second objective. These known fictional inputs measure workflow timing, not extraction correctness, real-scan robustness or human effort.

## Memory boundaries and coverage

| Profile | Host samples | Container samples | Guest samples | Unavailable/exited process reads | Guest used incl. cache (GiB) | Guest total less available (GiB) | Observer cgroup peak (MiB) |
|---|---|---|---|---|---|---|---|
| rules | 218 | 28 | 255 | 7 | 1.480 | 0.996 | 10.867 |
| model | 3726 | 541 | 4812 | 51 | 1.370 | 0.885 | 12.297 |

Raw [rules memory](performance-rules/memory.json), [model memory](performance-model/memory.json) and derived [per-launch sampling windows/gaps](sampling-coverage.json) retain exact observations and errors. Host/container/guest sampling errors are recorded independently from unavailable/exited native process reads. All eight observers have samples, remain alive until owned teardown and are confirmed removed; no observer is counted as a parser worker. Short cold lifetimes and gaps do not establish continuous peak coverage.

Model maximum observed **single-process** charged footprint is **1.257 GiB**, and its maximum observed lifetime high-water is **1.257 GiB**. These are not whole-application peaks, simultaneous totals, exclusive GPU allocation or complete clean-file residency. Docker lifetime counters may include work before this run. Guest usage includes daemon/kernel/cache/observer memory and nests parser working sets. The **host-resident VM/driver accounting and application-exclusive union remain pending**. Nothing adds guest occupancy, backend/model footprint, RSS or parser working sets together. Host free/speculative counters and pressure queries remain separate from reclaimable RAM.

Native `proc_pid_rusage` works without root. The root-only `footprint` command was unavailable, and a read-only check of Docker's separate virtualization process did not expose full guest occupancy through its native footprint. A [separate post-run diagnostic](post-run-vm-diagnostic.json) retains that process's counters and selected VM settings; it is not simultaneous workload/peak evidence. The recorded preflight helper predates the later early-exit guard; its SHA-256 is preserved, not replaced by the final helper. Earlier disposable observer setup exposed an asynchronous create/attach race and a case-sensitive missing-object assumption; the current implementation corrects them and the disposable observer was removed. These diagnostic observations do not count as controlled timing or whole-memory acceptance.

## Reproduce and verify

Use Python 3.12, running Docker Desktop and explicit [pinned model setup](../../docs/intake.md#pinned-local-model-evaluation). Follow the [freeze/run commands](../../docs/operations.md#frozen-benchmark) with new artifact directories. Do not reuse this historical freeze after a source/image/configuration change. Saved reports verify offline without model downloads or inference:

```sh
PYTHONPATH=src python3.12 scripts/benchmark_performance.py verify \
  evals/memory-performance-2026-10-04/performance-freeze \
  evals/memory-performance-2026-10-04/performance-rules
PYTHONPATH=src python3.12 scripts/benchmark_performance.py verify \
  evals/memory-performance-2026-10-04/performance-freeze \
  evals/memory-performance-2026-10-04/performance-model
```

[Legacy rules verification](historical-rules-verification.json) preserves the older summary semantics and reports source drift; it does not rewrite the historical bundle. Bundle hashes support local integrity checks, not independent attestation. Genuine scanner inputs/manual assessments, complete memory coverage, final explicit fresh setup/adversarial checks, narrated presentation and release commit/tag verification remain open. Nothing here publishes a release.

The checkout verifies 3,654 stable source/evidence files before the subsequent documentation-only result update, checkout retention and inventory refresh. It is a working-source check, separate from explicit fresh model setup, new committed-tree verification, hosted CI or published-tag evidence. The [pre-final-update link check](document-links-before-final-update.json) checks 222 local links; the final documentation changes receive a separate link/diff/inventory check without repeating inference or the suite.
