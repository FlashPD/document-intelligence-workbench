# Full release backlog

Last updated: October 4, 2026, America/Chicago.

This is the handoff for completing the local invoice workbench for a senior AI engineer portfolio. The published baseline is `v0.1.0-experimental`, commit `6c2b3c524baee68e8598408e415bdc7a043f3d73`. The full release must satisfy the [v1 contract](v1-release-contract.md); passing the existing portfolio audit alone is insufficient.

**Current position:** B01, B02 and B03 are complete. **Active: B04/B05, frozen production-input tooling and semantic evidence audit.** B03 adds server-established local reviewer authority and separate processing permissions. B04 through B07 remain open; B08 is conditional on productivity claims. B01/B02 were committed in `97e582a`; B03 and the CI evidence refresh were committed in `d9f4e67`; B04/B05 remain working-tree changes. No full v1 has been published.

## Baseline already delivered

The experimental release includes upload/review/export, immutable revisions and approved exports, isolated parser checks, leased and fenced processing, verified parser checkpoints, portable backups, offline replay, paired 180-invoice and 100-receipt comparisons, and a six-case author pilot. Rules remain the default after the model regression; original pilot results retain two errors. The [experimental overview](portfolio-candidate.md) links retained evidence.

On October 3, the exact published tag passed 298 deterministic tests and all eight isolated checkout checks. The local report is `artifacts/full-release-assessment-tag-001/report.json`. Reproduce it with `make release-checkout REF=v0.1.0-experimental OUTPUT=artifacts/baseline-check-fresh`, choosing a new directory. This checks packaging and saved evidence, not fresh OCR, inference, browser behavior, or human participation.

## Release workstreams

**Done** means the item's deliverables are complete; **In progress** means active work; **Todo** means remaining work; **Blocked** needs a named dependency or missing input. Completing a planning item does not implement its planned capabilities.

| ID | Workstream | Status | Depends on | Completion criteria and gates |
|---|---|---|---|---|
| B01 | Define the final v1 contract | Done | Experimental baseline | Document supported scope, architecture decisions, acceptance gates, evidence rules, and maintained handoff. G01. |
| B02 | Processing and document lifecycle | Done | B01 | Supervised worker, bounded batches/progress, cancellation, reprocessing, resumable deletion, artifact budget and disk guard; cover stale workers/shared objects. G02–G04 and lifecycle coverage for G09; final-source gate closure remains B07. |
| B03 | Reviewer identity and permissions | Done | B01; coordinate with B02 | Server-established identity, worker/reviewer permission separation, authenticated artifacts, and rejection of forged actors. G05, G08. |
| B04 | Production document validation | In progress | B02, B03 | Freeze permitted genuine scan/multi-page inputs, labels and protocol; run production uploads with both extractors; retain failures and score original suggestions separately from approved results. G10, G11. |
| B05 | Semantic evidence audit | In progress | B04 inputs/protocol | Audit a declared sample of critical headers/rows for semantic support and geometry; publish wrong, ambiguous and absent citations and fix misleading evidence presentation. G06. |
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

At B02 completion, remaining release work included reviewer authority (now completed by B03); B04/B05 add production-input and semantic evidence studies; B06 measures timing/readiness/memory. B07 must refresh final-source parser/model/browser/audit evidence (including source snapshot inventories for the new modules), verify upgrade/setup and close all gates. Local deletion is logical, not forensic erasure; cancelling the model disconnects only the owned request, with server computation cancellation dependent on its runtime. The storage guard is not an OS quota and excludes unrelated disk writers.

## B03 completion record

Read the [local access guide](access.md). [LocalAccess](../src/docwork/access.py) establishes `local:OS_ACCOUNT` through the OS account database and issues separate ephemeral reviewer/processing capabilities. HTTP edits, issue decisions, approvals and pilot starts obtain the actor from that principal; forged client actors return `403`. Processing capabilities permit only batch/upload/process requests and cannot retrieve pages/exports or acquire review authority through an accompanying reviewer cookie. Model credentials do not authorize the workbench. The browser displays the established identity read-only. Existing historical labels/approval hashes remain unchanged; CLI and in-process store methods retain the documented trusted-local-operator boundary.

Validation on October 4:

- **335 deterministic tests passed**, including forged actors, capability precedence/restart expiry, model credentials, authenticated downloads, stale revisions and pilot guards. [Retained test log](../evals/access-2026-10-04/tests.log). Command: `make test`.
- **Ten real loopback HTTP checks passed** with disposable fictional recorded candidates. [Source-bound report](../evals/access-2026-10-04/http-report.json). Command: `make access-verify OUTPUT=artifacts/access-fresh`.
- **Sixteen Chrome replay controls and ten pilot controls passed**, including the read-only established identity. [Replay report](../evals/access-2026-10-04/replay-browser/report.json), [pilot report](../evals/access-2026-10-04/pilot-browser/report.json). Commands are in the access guide. No fresh OCR/model inference, human participation or productivity measurement is claimed.

Remaining: final-source G05/G08 confirmation is B07; comprehensive G08 parser/model adversarial coverage remains open. Possession of a local session capability delegates the server account; it is not independent proof of the person reviewing, enterprise authentication, or isolation from arbitrary trusted host code.

## CI repair — October 4

The [failed Actions run](https://github.com/FlashPD/document-intelligence-workbench/actions/runs/37180170364) tested commit `97e582a`: seven checkout checks passed and `post_pilot_corrections` failed. The October 3 report bound older intake/review source, so its strict reproduction check correctly rejected implementation drift after B02. Preserve that historical report.

The [new correction report](../evals/post-pilot-corrections-2026-10-04.json) reproduces the same two unapproved corrections with current lifecycle/storage dependencies included in the source inventory. `verify_release_checkout.py` now selects this report. Strict source/result equality, original-pilot preservation and approval invalidation remain enforced. `PYTHONPATH=src python3.12 scripts/correct_pilot_records.py verify evals/post-pilot-corrections-2026-10-04.json` passes. **All eight isolated working-source checkout checks now pass**, including all 335 deterministic tests. Command: `make release-checkout OUTPUT=artifacts/access-ci-checkout-2026-10-04-001`; [retained report](../evals/access-checkout-2026-10-04/report.json). This report binds the checked source snapshot before the subsequent documentation-only handoff update/evidence retention, not a new committed or published tree. The repair is now committed in `d9f4e67`; hosted CI still needs a run after the commit is pushed. The user's HTTPS push failed with HTTP 400; a read-only `git ls-remote origin refs/heads/main` check still found `97e582a`, so the failed attempt did not update the remote branch. The exact commit also passes all eight isolated checkout checks (335 tests): `make release-checkout REF=d9f4e67 OUTPUT=artifacts/reviewer-commit-checkout-2026-10-04-001`; [retained committed-tree report](../evals/reviewer-commit-checkout-2026-10-04/report.json). Publication remains a separately requested action.

## B04/B05 tooling — in progress

The [production study runbook](production-study.md) defines a separate predeclared bounded diagnostic. [Study contracts](../src/docwork/production_study.py) and [runner](../scripts/production_study.py) freeze ordered originals, complete labels/exclusions, permission/acquisition declarations, model profile, immutable parser image and runtime/UI source before extraction. Production mode requires at least eight cases and two genuine scanner captures, covering clean/degraded/rotated/multi-page input. Diagnostic mode is explicitly separate. Source, input, render or profile drift; revised/approved candidate substitution; incomplete runs and omitted cases cannot pass offline checks. Extraction receives originals, not labels. All scheduled document failures remain scored.

Original and approved quality remain separate. Approved snapshots never create approval, verify current approval hashes and reject replacement extractions. The semantic template freezes all five required headers and first/last gold-row description/line-total targets, including absent candidates and failures. Complete assessments need semantic and geometry classifications, inspected pages and rationales; an absent/unknown citation or unavailable geometry cannot become supported/aligned. The UI now labels cited OCR lines, distinguishes corrections/calculations and discloses missing references/geometry.

Validation in this turn:

- **347 deterministic tests passed**; new study tests cover gate eligibility, provenance, rejected/omitted/interrupted runs, label isolation, approval separation and complete manual assessment. Local log: `artifacts/production-study-tests-2026-10-04-001/tests.log`.
- **Live rules tooling diagnostic:** all five known fictional development originals reached review-ready through authenticated supervised HTTP and real Docker/Tesseract, including a rotated PNG and two-page PDF. Frozen selection: `artifacts/production-diagnostic-freeze-2026-10-04-001`; run: `artifacts/production-diagnostic-rules-2026-10-04-001`. Scores retain original suggestions; the generated **43-target** semantic sheet remains unassessed. The separate approval snapshot reports all five records as `NotApproved`; no human review or approval was created.
- **Sixteen Chrome controls passed** after the evidence presentation change: [retained browser report](../evals/production-tooling-2026-10-04/browser/report.json).
- **All eight isolated working-source checkout checks passed**, including 347 tests and the refreshed correction check: `make release-checkout OUTPUT=artifacts/production-tooling-checkout-2026-10-04-001`; [retained report](../evals/production-tooling-checkout-2026-10-04/report.json). It binds the source/evidence before this documentation-only result-retention update, not a committed tree or hosted Actions run.
- **Live pinned-model tooling diagnostic:** all five scheduled inputs are accounted for: three review-ready candidates (including the rotated PNG and two-page PDF) and two `MODEL_UNAVAILABLE` failures. Required headers are exact on 3/5 scheduled documents; header macro F1 is 0.7167 and exact-row F1 is 0.6923 with failures retained. The owned runtime shut down completely. Its 43-target semantic sheet remains unassessed; the separate snapshot records three `NotApproved` cases and retains two extraction failures. [Reviewed diagnostic bundle](../evals/production-tooling-2026-10-04/README.md) preserves the freeze, both runs, scores, audit templates, approval snapshots, browser evidence and test log. This is known-development tooling evidence, not new held-out, genuine-scan or semantic-audit evidence.

Remaining: no permissioned genuine scanner originals have been selected, and no manual semantic judgments or new approved-quality study has been recorded. B04 and B05 are **not done**. The runbook records the examined official acquisition options: MIDD's listed bundle supplies annotations rather than originals; DocILE requires research access registration. Do not substitute synthetic degradation for physical capture or relabel an automated trial as human review. Historical invoice/CORD predictions, scoring, pilot outcomes and unapproved correction reports are unchanged.

**Next concrete action:** acquire permitted scanner originals (or physical scanner captures of explicitly self-authored invoices), label them from source before predictions, and freeze the complete production selection with acquisition/permission references. Run both variants on new uploads, preserve every failure, then inspect all declared source/render targets and collect approved quality separately. B06 can independently freeze controlled timing/readiness/memory work; B07 retains final-source evidence and eventual tag verification.

## Maintaining the handoff

After each task, update status, changed files, checks, remaining gaps, and next action. Link retained evidence or give exact commands/local output paths. Ignored reports alone cannot provide portable final-release proof. Update gate status in the contract alongside the backlog; scope changes also update the ADR. Missing runtime or human evidence stays pending even when implementation tests pass. Preserve historical study outcomes.

## Work outside v1

Docling/layout-table extraction, FastAPI/React migration, PC/CUDA inference, a VLM, and an external telemetry stack are optional under ADR 0001. Receipt productization, handwriting, ERP/payment workflows, fine-tuning, hosted deployment, and multi-user operation are outside v1. Optional experiments require frozen comparisons before adoption or claims.
