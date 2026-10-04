# Full release backlog

Last updated: October 4, 2026, America/Chicago.

This is the handoff for completing the local invoice workbench for a senior AI engineer portfolio. The published baseline is `v0.1.0-experimental`, commit `6c2b3c524baee68e8598408e415bdc7a043f3d73`. The full release must satisfy the [v1 contract](v1-release-contract.md); passing the existing portfolio audit alone is insufficient.

**Current position:** B01 and B02 are complete. **Next: B03, reviewer identity and permissions.** B02 delivers supervised processing, durable profiles, batches, cancellation, versioned reprocessing, restartable deletion and persisted storage guards. B03 through B07 remain open; B08 is conditional on productivity claims. This working tree is uncommitted; no new release has been published.

## Baseline already delivered

The experimental release includes upload/review/export, immutable revisions and approved exports, isolated parser checks, leased and fenced processing, verified parser checkpoints, portable backups, offline replay, paired 180-invoice and 100-receipt comparisons, and a six-case author pilot. Rules remain the default after the model regression; original pilot results retain two errors. The [experimental overview](portfolio-candidate.md) links retained evidence.

On October 3, the exact published tag passed 298 deterministic tests and all eight isolated checkout checks. The local report is `artifacts/full-release-assessment-tag-001/report.json`. Reproduce it with `make release-checkout REF=v0.1.0-experimental OUTPUT=artifacts/baseline-check-fresh`, choosing a new directory. This checks packaging and saved evidence, not fresh OCR, inference, browser behavior, or human participation.

## Release workstreams

**Done** means the item's deliverables are complete; **In progress** means active work; **Todo** means remaining work; **Blocked** needs a named dependency or missing input. Completing a planning item does not implement its planned capabilities.

| ID | Workstream | Status | Depends on | Completion criteria and gates |
|---|---|---|---|---|
| B01 | Define the final v1 contract | Done | Experimental baseline | Document supported scope, architecture decisions, acceptance gates, evidence rules, and maintained handoff. G01. |
| B02 | Processing and document lifecycle | Done | B01 | Supervised worker, bounded batches/progress, cancellation, reprocessing, resumable deletion, artifact budget and disk guard; cover stale workers/shared objects. G02–G04 and lifecycle coverage for G09; final-source gate closure remains B07. |
| B03 | Reviewer identity and permissions | Todo | B01; coordinate with B02 | Server-established identity, worker/reviewer permission separation, authenticated artifacts, and rejection of forged actors. G05, G08. |
| B04 | Production document validation | Todo | B02, B03 | Freeze permitted genuine scan/multi-page inputs, labels and protocol; run production uploads with both extractors; retain failures and score original suggestions separately from approved results. G10, G11. |
| B05 | Semantic evidence audit | Todo | B04 inputs/protocol | Audit a declared sample of critical headers/rows for semantic support and geometry; publish wrong, ambiguous and absent citations and fix misleading evidence presentation. G06. |
| B06 | Performance and operations | Todo | B02; final runtime configuration | Freeze workloads/budgets first; publish controlled cold/warm timing, serial throughput, total-memory methodology, failure/recovery results, readiness and content-free metrics. G09, G12, G13. |
| B07 | Reproducible final release and presentation | Todo | B02–B06 | Pin parser builds, verify upgrades/restoration/setup, reconcile guides, refresh final-source evidence, close all gates, record narrated demo/case study, and verify release commit and published tag. G07–G10, G14, G15. |
| B08 | Independent manual versus assisted study | Todo, conditional | Stable B02–B05 | Matched difficulty, counterbalanced order, independent participants, immutable timing and final quality. Required for productivity/time-saved claims; otherwise retain author-pilot limitations. |

## B01 completion record

- [x] Adopt the local v1 scope and explicit design substitutions in [ADR 0001](adr/0001-local-v1-scope.md).
- [x] Map implementation/evidence gaps to fifteen [acceptance gates](v1-release-contract.md#acceptance-gates).
- [x] Define required versus conditional claims, controlled performance workload, and evaluation/publication rules.
- [x] Link the backlog and contract from the README and current release guides; preserve the original plan and archived evidence.
- [x] Add [continuity instructions](../AGENTS.md) requiring future tasks to update this handoff.

Validation: repository-relative links/anchors and scope/gate references checked; `git diff --check` passed. This was documentation work; no fresh runtime measurement or implementation acceptance is claimed.

## B02 implementation sequence

| Task | Status | Deliverable and validation |
|---|---|---|
| B02.1 | Done | Live `serve` owns one serial background worker; jobs persist bounded extraction configuration and attempts. Upload/status return independently of extraction. Tests cover two supervisors, twenty queued items, shutdown, expired ownership and unavailable model profiles; replay/pilot stay separate. |
| B02.2 | Done | Browser submits up to twenty files with durable slots and explicit rejection outcomes. Cancel stops owned parser/client requests, drains active claims and prevents publication. Tests cover cancellation/fencing; live Chrome and a controlled Docker stop probe verify runtime behavior. |
| B02.3 | Done | Explicit reprocessing records a new attempt and candidate revision. Revision source snapshots retain prior spans, renders and extraction identity. Prior exports remain byte-identical; new approval is required. Checkpoint integrity/reuse tests and live reprocessing pass. |
| B02.4 | Done | Durable deletion revokes reads immediately, waits for active cleanup, resumes interrupted manifests and removes local history/artifacts. Tests cover shared objects, symlinks, stale workers and crash scratch; live deletion verifies last-reference removal. Content-free tombstones and external retention limits are documented. |
| B02.5 | Done | Persist 20 GiB workbench growth budget and 256 MiB free-space reserve across server/CLI restarts. Account for database/WAL, originals, renders, quarantine and exports; retain 1 GiB original-object cap. Tests cover quota/disk refusal, atomic-write cleanup and partial CSV failure. Model/runtime/Docker/external copies remain separate. |

## B02 completion record

Read the [lifecycle runbook](lifecycle.md) to use and recover the new workflow. Implementation adds [supervision](../src/docwork/supervisor.py), [lifecycle operations](../src/docwork/lifecycle.py), [cancellable model transport](../src/docwork/request_control.py) and [storage guards](../src/docwork/storage_budget.py), with migrations/source snapshots in intake/review, HTTP/CLI/browser controls and portable-backup updates. Pilot archive restore rebases the persisted object-store policy without changing archived evidence.

Validation on October 4:

- **330 deterministic tests passed**: `PYTHONPATH=src python3.12 -m unittest discover -s tests -p 'test_*.py' -v`. The retained [test log](../evals/lifecycle-2026-10-04/tests.log) includes lifecycle, HTTP, checkpoint, backup, archive and owned-request checks. Fixtures/injected runners are not fresh OCR/inference measurements.
- **Seven live lifecycle checks passed** after `make parser-build`: `make lifecycle-verify OUTPUT=artifacts/lifecycle-fresh-001` reproduces a new run. The [retained report](../evals/lifecycle-2026-10-04/report.json) binds current runtime/UI source hashes, input and immutable parser image. [Review-ready](../evals/lifecycle-2026-10-04/review-ready.png) and [reprocessed](../evals/lifecycle-2026-10-04/reprocessed.png) screenshots show native Chrome controls. Six checks exercise real Docker/Tesseract on a fictional invoice; the seventh is a separate controlled sleep probe confirming active owned-container removal. No model inference, real scans, human time or performance gate is claimed.
- **Sixteen existing replay browser controls passed**: `PYTHONPATH=src python3.12 scripts/verify_review_browser.py --skip-recording --demo-replay --output-dir artifacts/b02-replay-browser-001`. Local report: `artifacts/b02-replay-browser-001/report.json`. Replay runs no live supervisor.
- The stable isolated working-source checkout ran all **330 tests** and passed **seven of eight** checks: `make release-checkout OUTPUT=artifacts/b02-checkout-003`. Its `post_pilot_corrections` check rejects the old report's exact source binding after the implementation changed. The original report is preserved. Local report: `artifacts/b02-checkout-003/report.json`. The first checkout attempt also detected documentation edits during its run; it is not a passing release snapshot. B07 must retain new applicable source-bound correction evidence and repeat all checks on final source before publication.

Remaining release work: B03 establishes reviewer authority; B04/B05 add production-input and semantic evidence studies; B06 measures timing/readiness/memory. B07 must refresh final-source parser/model/browser/audit evidence (including source snapshot inventories for the new modules), verify upgrade/setup and close all gates. Local deletion is logical, not forensic erasure; cancelling the model disconnects only the owned request, with server computation cancellation dependent on its runtime. The storage guard is not an OS quota and excludes unrelated disk writers.

**Next concrete action for B03:** read [web.py](../src/docwork/web.py), [review.py](../src/docwork/review.py), [HTTP tests](../tests/test_web.py) and [review tests](../tests/test_review.py). Establish reviewer identity server-side, remove client-supplied actor authority, keep processing credentials unable to approve, preserve same-origin/session/artifact checks, and test forged actor requests. Document the trusted single-user CLI boundary separately. Update G05/G08 and this handoff after completion.

## Maintaining the handoff

After each task, update status, changed files, checks, remaining gaps, and next action. Link retained evidence or give exact commands/local output paths. Ignored reports alone cannot provide portable final-release proof. Update gate status in the contract alongside the backlog; scope changes also update the ADR. Missing runtime or human evidence stays pending even when implementation tests pass. Preserve historical study outcomes.

## Work outside v1

Docling/layout-table extraction, FastAPI/React migration, PC/CUDA inference, a VLM, and an external telemetry stack are optional under ADR 0001. Receipt productization, handwriting, ERP/payment workflows, fine-tuning, hosted deployment, and multi-user operation are outside v1. Optional experiments require frozen comparisons before adoption or claims.
