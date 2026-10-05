# ADR 0001 Local v1 scope

Status: adopted for implementation on October 3, 2026. Full acceptance remains pending under the [v1 contract](../v1-release-contract.md). Work is tracked in the [backlog](../backlog.md).

## Context

The [September 23 architecture plan](../../arch_plan/document-intelligence-workbench-plan.md) specifies a broader stack than `v0.1.0-experimental`. The working product already preserves evidence, revisions, approvals and exports, and publishes complete paired extraction comparisons. Its portfolio audit deliberately leaves full architecture acceptance open. Framework replacement alone would not close the lifecycle, identity, evidence or performance gaps.

## Decision

Release v1 as a local, single-user invoice workbench on the measured macOS arm64 profile. Retain the implemented stack and one serial worker. All approval/export authority stays outside extraction. Keep `ocr_rules` as the default and the pinned `span_llm` as an optional bounded profile/comparison. A model quality win is not required; a regressing candidate cannot become the default.

| Original design | v1 decision | Consequence |
|---|---|---|
| FastAPI/Pydantic/Typer and React/TypeScript/Vite | Keep standard-library HTTP, Python contracts/CLI, HTML/CSS/JavaScript | Enforce validation, identity, revision semantics and browser acceptance directly. Framework migration is optional. |
| Docling conversion and `layout_llm` | Keep Poppler/Tesseract canonical spans with `ocr_rules` versus `span_llm` | A shared-OCR text-model experiment does not establish Docling or layout-model performance. A layout adapter needs a separate future evaluation. |
| Dedicated table/cell objects | Keep pages/spans and ordered invoice rows | Preserve row/page provenance and visible evidence limits. Table/cell contracts are deferred with the layout adapter. |
| SQLite, artifact store, durable worker | Keep SQLite/WAL, local content-addressed files, fencing/checkpoints; add supervision and lifecycle controls | Cancellation, reprocessing, deletion and restart coherence are mandatory; no distributed queue. |
| Distinct worker/reviewer credentials | Establish reviewer identity on the server and separate processing permissions | Typed names cannot authorize review. Keep authenticated artifacts; enterprise multi-user identity is outside v1. |
| Proposed `/v1` and evaluation-service APIs | Keep `/api` and the `docwork` CLI; document lifecycle/status routes as implemented | Behavioral contracts matter; exact planned route names and an HTTP evaluation service are deferred. |
| 4 GiB parser cap and 20 GB artifact allowance | Keep current 1 GiB parser cap, two CPUs, 64 PIDs and 600-second deadline; persist a 20 GiB workbench growth budget and 256 MiB disk reserve | Inventory covers SQLite/WAL, originals, derived pages, quarantine and local exports. The separate 1 GiB original-object quota remains; model/runtime/Docker assets and external backups are outside this budget. See the lifecycle runbook. |
| JSON/OpenTelemetry and optional dashboards | Require local stage timing, status, readiness and content-free metrics | Operational visibility is mandatory; external telemetry infrastructure is optional. |
| PC/CUDA and document VLM | Defer both | No PC/GPU or VLM quality/offload claims in v1. Mac model evidence is profile-specific. |
| Synthetic invoices, separate CORD, author pilot | Preserve frozen archives/limits; add genuine scan/production and semantic evidence studies | Receipts remain diagnostic. Independent manual-versus-assisted testing is conditional on productivity claims. |

## Consequences

V1 requires all mandatory contract gates on the final relevant source. Publication follows G01–G14 and G15’s prepublication evidence; the separate published-tag clone/demo finishes G15 afterward. Full v1 closure waits for that verification. This sequencing does not remove any acceptance requirement. The experimental audit remains a subset. Lifecycle/identity work must preserve existing revisions, fencing, checkpoints and immutable exports.

Keep the original plan unchanged as historical design. This ADR supersedes its framework/converter choices, proposed interface names and optional placement for local v1 while preserving isolation, review authority, recovery, measurement and reproducibility requirements. Later scope changes must explain their effect on acceptance and claims rather than silently removing a difficult gate.

Permitted claims concern a measured local invoice workflow and extractor selection. Arbitrary-vendor accuracy, receipt product support, validated time saved, unattended approval, invoice authenticity, hosted/multi-user readiness, and unmeasured GPU/VLM behavior are outside this release.
