# Full v1 release readiness review

Review date: October 4, 2026, America/Chicago. This working-source review organizes the fifteen [contract gates](v1-release-contract.md); **full v1 is pending**. Covered gates still need applicable final-source/commit confirmation. The experimental portfolio audit and isolated checkout checks cover subsets of these requirements. No commit, tag or publication is created by this review.

| Gate | Current decision | Evidence and remaining action |
|---|---|---|
| G01 Scope | Defined | [ADR](adr/0001-local-v1-scope.md), contract and maintained backlog. Confirm consistency during final review. |
| G02 Supervision | Covered | [Lifecycle](lifecycle.md), durable profiles/attempts, serial claims, live upload/progress and shutdown checks. Confirm applicable source. |
| G03 Cancellation/reprocess | Covered | Lifecycle fencing, owned cleanup, revision/checkpoint and old-export preservation. Confirm applicable source. |
| G04 Deletion/budgets | Covered | Resumable cleanup, shared objects, logical deletion, quota/free-space refusal. Preserve documented guard/external-copy limits. |
| G05 Reviewer authority | Covered | [Access](access.md), server OS principal, forged-actor and capability rejection, authenticated artifacts. Confirm applicable source. |
| G06 Semantic evidence | Partial | [Study protocol](production-study.md) and geometry/reference UI exist. Inspect and complete the declared production target sample; publish wrong/ambiguous/absent support. |
| G07 Approval/export | Covered | Revision/approval binding, stale rejection, immutable JSON/CSV, browser and [stage-recovery](stage-recovery.md) evidence. Confirm applicable source. |
| G08 Adversarial boundaries | Covered for declared cases | [Adversarial evidence](adversarial.md); retain failures, controlled cases and verifier drift. Confirm applicability without claiming general resistance. |
| G09 Restart/recovery | Covered for declared cases | Twelve stage drills plus timeout/OOM, lifecycle and portable restore. Short leases/controlled parser/power-loss exclusions remain visible. |
| G10 Frozen comparisons | Covered | [180-invoice/100-receipt comparisons](heldout-model-comparison.md), failures/masks/uncertainty and retained default. Audit relevant source and preserve original predictions. |
| G11 Production inputs | Partial | Tooling diagnostic exists. Select at least eight permissioned cases including two genuine scanner captures, source-label before prediction, run both extractors and assess approved quality separately. |
| G12 Performance/memory | Partial | Complete historical cold/warm/queue schedules; [group accounting](group-memory.md) improves VM/shared/driver coverage. Both corrected-source schedules complete: 34 rules/14 model, warm P95 1.994/88.500 seconds. Review startup/shutdown/stage gaps and excluded memory before closure. |
| G13 Operations | Covered | [Authenticated operations](operations.md), persistent stage/queue/failure metrics and distinct parser/model readiness. Confirm applicable source. |
| G14 Setup/upgrade | Covered for recorded configuration | [Parser rebuild/upgrade](parser-build.md), restore and [fresh model setup](model-setup.md). Host prerequisites/daemon boundaries remain explicit. Confirm final-source/tag setup. |
| G15 Presentation/release | Partial | [Written case study](engineering-case-study.md), existing captioned fixture demo and runbooks. [Continuous narrated workflow](narrated-demo.md) passes seven checks/nine scenes on current source. Cards/guides are reconciled; the refreshed eleven-check portfolio audit and twelve-check checkout pass. Close remaining gates and verify requested commit and separate published-tag clone. |

## Sequence to release

1. Review the retained complete group-memory schedules and their coverage/exclusions. Keep G12 partial wherever peak/boundary coverage remains unavailable; any acceptance/scope change needs an explicit documented decision rather than relabeling a sampled maximum.
2. Acquire permitted scanner originals, freeze source-inspected labels and the complete production protocol, then run both variants. Complete manual semantic/geometry and separate approved-quality assessments. The user's unavailable scanner inputs remain a dependency; synthetic degradation cannot satisfy acquisition.
3. Review the retained continuous narrated upload, source inspection, correction, approval, export and recovery walkthrough and reconciled current guides/cards. Explain model regression and retained pilot errors using the written case study.
4. Review every gate against relevant source/configuration/input hashes and retained evidence. Run required final-source checks, addressing any implementation drift with new evidence. Passing a subset audit does not override missing human/input/memory evidence.
5. Close G01–G14 and G15’s prepublication presentation/source/runtime/audit checks; verify the working tree and requested exact release commit. After separately requested publication, verify a separate published-tag clone/demo to finish G15. Retain each identity separately; full v1 closure waits for the postpublication check. Until then keep the current experimental status and historical release unchanged.

The [initial fifteen-gate evidence index](../evals/release-gate-review-2026-10-04/README.md) binds the exact contract requirements and nineteen report references, with hashes checked before retention. It records `not_release_ready` and concrete gaps against its original contract snapshot, before the later publication-sequencing clarification; declared subset passes do not prove complete gates. The corrected rules schedule is complete and the fourteen-model process is still active at that historical review; both workloads have since completed with current-source portable verification.

The [updated gate index](../evals/release-gate-review-2026-10-04/refresh/report.json) records completed corrected-source timing, native integrations, setup/upgrade, all twelve checkout checks/409 tests and the eleven-check portfolio audit. It binds twenty-four report references and all 52 current application/UI files, with a fresh contract snapshot. Status remains `not_release_ready`: G06/G11 actual scan/manual evidence, G12 unavailable peak/boundary acceptance and requested final identity/published-clone verification remain open. Earlier indices and outcomes are preserved.

The [backlog](backlog.md) is authoritative for the next active task. This document is a concrete review worksheet, not a release certificate or a new scope decision. The independent manual-versus-assisted study is required only for productivity/time-saved claims; no such claim is made.
