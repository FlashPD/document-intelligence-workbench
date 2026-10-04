# Local operations and controlled performance

B06 adds authenticated readiness and content-free metrics to the supervised local workbench. The [v1 contract](v1-release-contract.md) keeps controlled timing, complete memory accounting and final-source recovery evidence separate from implementation. No endpoint grants processing or review permission.

## Service status

With a reviewer session, `GET /healthz` returns service liveness. `GET /readyz` returns `200` when the database, artifact budget/free-space guard and required parser/worker are usable, or `503` when a required dependency needs attention. Database readiness obtains a write transaction and rolls it back. Storage readiness checks the current inventory, unsafe entries and disk reserve. Live mode checks the installed parser image and background worker. Replay does not require a parser or worker.

The configured model is probed separately through its loopback `/v1/models` endpoint with its server credential, no proxy and no redirects. An unavailable optional model does not make rules processing unavailable. A model response is availability evidence, not proof that inference will succeed. Probes are cached for five seconds; Docker inspection has a fifteen-second bound and the model probe a two-second timeout. Readiness is advisory and cannot reserve disk or prevent another process changing dependencies after a check.

`GET /metrics` returns JSON aggregates without document IDs, names, field values, source text, paths, actors, model IDs or credentials. Processing credentials and unauthenticated requests cannot read any of these endpoints. Counts cover current job statuses, active stages, retained attempt outcomes, known failure categories and retry/reprocess attempts. Unrecognized stored failure text becomes `OTHER`.

The live browser shows readiness, queued/active/failed counts, oldest known queue age, retries/reprocesses, parser status and model availability alongside storage usage. Cancellation/retry controls remain in the document workflow.

Run `make operations-verify OUTPUT=artifacts/operations-http-fresh` for real loopback checks of authentication, capability separation, privacy, retries, deletion and storage refusal. Its failure outcomes are injected; it does not run OCR/inference or establish performance.

## Timing and retention

Stage starts are persisted under the current claim/fence. Repeated starts of the same stage do not create duplicate boundaries. A stale worker cannot append a stage. Attempt start/end and stage timestamps produce nearest-rank P50/P95, counts and sums for queue wait, total processing, parsing, extracting and checking. Parsing includes Docker startup, OCR, checkpoint lookup/import and scratch cleanup; a reused checkpoint is not fresh OCR. Extracting includes model requests when selected. Checking includes validation and candidate persistence.

These operational intervals use UTC wall-clock timestamps. Missing, unfinished and backward intervals cannot create valid timing observations. Older attempts without stage events retain processing timing but have no invented stage timings. Counts make coverage visible. Metrics combine retained workloads and profiles and are not the controlled performance result.

Review wait is elapsed age since the latest completed extraction on a currently unapproved record. Later corrections can make a previously approved extraction unapproved again, so this age can include earlier approved intervals; it is not cumulative waiting, active human effort or time saved. The author pilot remains the separate historical effort study. Retries and reprocessing remain separate attempts, with failures in their original attempt counts. Deleting a document removes its timing/history; portable backups preserve the existing event/attempt tables without a new schema.

## Frozen benchmark

The [benchmark runner](../scripts/benchmark_performance.py) freezes the clean sample, twenty development PNG/PDF originals, current implementation/UI/script hashes, installed immutable parser image, pinned model profile, host identity, declared competing workloads, counts, budgets, cache policy and memory method **before measurement**. Changes require a new freeze and new output directories. It does not download assets.

```sh
PYTHONPATH=src python3.12 scripts/benchmark_performance.py freeze \
  --host-workloads 'Describe observed host activity and competing inference' \
  --output-dir artifacts/performance-freeze-fresh
PYTHONPATH=src python3.12 scripts/benchmark_performance.py run \
  artifacts/performance-freeze-fresh --variant ocr_rules \
  --output-dir artifacts/performance-rules-fresh
PYTHONPATH=src python3.12 scripts/benchmark_performance.py run \
  artifacts/performance-freeze-fresh --variant span_llm \
  --output-dir artifacts/performance-model-fresh
PYTHONPATH=src python3.12 scripts/benchmark_performance.py verify \
  artifacts/performance-freeze-fresh artifacts/performance-rules-fresh
```

Each profile uses three fresh Python/server launches and, for the model profile, fresh owned model processes. A fourth launch performs one separately accounted warmup and ten measured clean uploads. Rules also run the twenty-document bounded batch, containing sixteen PNGs and four two-page PDFs. Jobs are automatically processed by the normal serial supervisor. Every upload creates a new document and attempt, with no parser checkpoint reuse or retries. Cold workflow elapsed time includes launch, readiness probing and upload through the observed terminal status. Startup is reported separately. Cold means fresh application/model processes; the Docker VM/image and operating-system file caches remain warm. There is no cache purge or Docker reboot.

Upload-to-terminal observations include submission and polling; they are upper bounds on the instant of terminal publication. Queue observations begin before batch creation and include intake submission. Persisted attempt intervals separately verify nonoverlapping processing; sampled active counts are additional observations, not proof of exclusive execution by themselves. Stage summaries retain missing coverage. Every scheduled failure, including warmup, stays recorded. Failed cold startup or an interrupted run cannot publish a complete schedule. The warm rules target is P95 at most sixty seconds with all ten warm uploads review-ready. Optional-model timing has no sixty-second objective.

On macOS the runner owns `caffeinate -i` for its lifetime to inhibit idle sleep, then releases it. It does not prevent lid closure or forced sleep. Host samples record wall and monotonic elapsed times; divergence over five seconds invalidates controlled timing. A worker that recovers after a lease expires still has an extra attempt, which cannot satisfy this no-retry benchmark. Retain such runs separately.

## Memory methods and remaining coverage

The sampler observes application process-tree RSS and llama RSS separately, Docker backend RSS (including VM/support overhead), host load and free/speculative pages every quarter second where available. A separate one-second sampler queries Docker container working sets. Actual sampling timestamps, errors and missing component observations are retained. On macOS `memory_pressure -Q` is query-only. The runtime also records its own sampled model RSS and clean shutdown.

The parser working set is nested in Docker memory and is never added to Docker backend RSS. Process RSS may share physical pages; llama RSS omits some Metal/unified-memory ownership. Docker backend RSS does not establish exclusive VM allocation. Free plus speculative host pages are not total reclaimable/available RAM. Sampling can miss short peaks. The benchmark therefore publishes component observations and methodology, **not an aggregate whole-application peak**. G12 memory acceptance remains pending until appropriate unified-memory/VM accounting supplies the missing coverage; a configured memory limit is not measured peak usage.

The production parser enforces one GiB/two CPUs/64 PIDs and a six-hundred-second deadline. The workbench persists a twenty-GiB growth budget and a 256-MiB disk reserve, excluding model/runtime/Docker assets and external copies. Recorded parser timeout/OOM/recovery checks are linked from [resource drills](parser-resources.md); final-source comprehensive recovery remains B06/B07. No timing, memory, genuine-scan quality, human review or full-v1 completion follows merely from passing deterministic checks.

## October 4 measurement record

The later [pinned-image reproducibility run](parser-build.md) repeats the complete rules schedule after pinning and rebuilding dependencies: **34/34** uploads ready, warm P50/P95 **1.632/1.707 s**, cold workflow **1.874/1.912 s**, and **24 queued pages in 41.613 s**. [Raw reports](../evals/reproducibility-2026-10-04/README.md) bind the pinned immutable image. This supersedes the prior-image rules timing gap only. Whole-application memory and complete pinned-image model timing remain pending; the earlier results below retain their original configuration and outcomes.

The final rules run uses `artifacts/performance-freeze-2026-10-04-003` and `artifacts/performance-rules-2026-10-04-003`. All **34** scheduled uploads reached review-ready with one fresh attempt each: three cold-process clean uploads, one separately counted warmup, ten warm clean uploads and the twenty-document queue. Warm upload P50/P95 was **1.517/1.541 seconds**; cold workflow P50/P95 was **1.758/1.759 seconds**, including server launch and readiness probing. The rules latency objective passes on this sample. The queue produced **24 pages in 38.694 seconds** (**37.215 pages/minute**), with nonoverlapping persisted attempt intervals and one sampled active job. No test suite or browser verifier ran during this final timing run. Other interactive/background activity and sampler overhead are declared in the freeze.

The separate pinned-model run uses the same freeze and `artifacts/performance-model-2026-10-04-003`. All **14/14** uploads reached review-ready: three cold launches, one separately counted warmup and ten warm uploads. Warm upload P50/P95 was **65.964/66.785 seconds**; cold workflow P50/P95 was **88.081/90.591 seconds**. All four owned model runtimes shut down cleanly. Optional-model timing has no sixty-second objective, and successful candidate publication is not correctness or promotion evidence.

The [retained bundle](../evals/operations-2026-10-04/README.md) includes both timing reports, raw observations, frozen source/profile identities and preserved earlier runs. Rules/model runs collected **195/3,266 host samples** and **42/962 Docker observations**, with zero sampling errors; some short cold parser lifetimes have no working-set sample. Peak sampled application process-tree RSS was **61.562/63.047 MiB**, model process RSS **3.615 GiB** in the model run, and Docker backend RSS about **254.6 MiB**. These components are not summed. Docker reports `MemTotal` of 8,215,117,824 bytes. The report field `docker_vm_memory_limit_bytes` stores this daemon-reported total; it does not verify the configured VM cap or measure peak usage. Unified-memory/VM ownership and exact peak coverage remain pending.

The subsequent parser audit rejected the installed image's stale source inventory and found two obsolete live-test expectations for export refusal. The [first audit](../evals/operations-2026-10-04/parser-first-attempt/report.json) is preserved. The tests now assert `ReviewConflict` on failed/unreviewable export attempts while retaining all zero-revision/checkpoint, cleanup, Docker fault-event and successful-retry assertions. Rebuilding with cached base/OCR/Pillow layers changes only copied application source, producing `sha256:1882551df34e75e029bb64bdd9663b5843d4b11d87ba9073bab13ae6fc97dd8c`. The [rebuilt audit](../evals/operations-2026-10-04/parser-rebuilt/report.json) passes all sixteen checks. [Current-image lifecycle/status](../evals/operations-2026-10-04/lifecycle-current-image/report.json) passes eight checks; [real-model upload/review/export](../evals/operations-2026-10-04/model-workflow/report.json) passes both fictional fixtures and verifies offline.

Timing reports remain bound to the prior immutable image `sha256:ed27f389dffa98a267df581c6fce346c4690f111ef9293fa62ad3eecbd44f88b`. They are valid measurements of that frozen configuration, not timing acceptance for the rebuilt image. Pin final build inputs before repeating final-configuration timing. B06/G12 remain partial, and full v1 remains pending.

The first run, `artifacts/performance-rules-2026-10-04-001`, remains invalid as controlled evidence: all documents eventually completed, but one queue item had an abandoned attempt followed by a second attempt. A subsequent read-only host power-log inspection identified **151 seconds of idle sleep** from 09:37:38 to 09:40:09 America/Chicago. That inspection is retained separately as `sleep-inspection.json`; it does not alter the original report. The second run, `artifacts/performance-rules-2026-10-04-002`, passed its schedule and timing checks but overlapped deterministic tests, a workload not specifically declared in its freeze. It is diagnostic; use the third run for the controlled timing result. All earlier outcomes remain preserved.
