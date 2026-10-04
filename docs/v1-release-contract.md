# Local v1 release contract

Last updated: October 4, 2026, America/Chicago. Scope is adopted; B01/B02/B03 are complete and **full v1 acceptance is pending**. [ADR 0001](adr/0001-local-v1-scope.md) records design decisions; the [backlog](backlog.md) is the current handoff. The [original plan](../arch_plan/document-intelligence-workbench-plan.md) remains historical design.

## Supported product

V1 is a local single-user English invoice workbench. The reference live profile is macOS arm64 on the recorded Apple M1 with 16 GiB RAM, Python 3.12, Docker CPU parsing, and optional pinned Qwen3/llama.cpp. Python-only offline checks/replay are separately reproducible; Linux CI does not verify a Linux live model profile.

The required workflow is bounded upload, automatic serial processing, source inspection, correction or issue acknowledgment, current-revision approval, and immutable JSON/CSV download. Cancellation, explicit reprocessing, portable recovery and tracked local deletion are included. Every export needs human approval. `ocr_rules` stays the default; optional `span_llm` may regress without replacing it.

PDF/PNG/JPEG originals are bounded to 20 MiB per file, ten pages, twenty million decoded pixels per image/page and twenty files per batch. Malformed, spoofed, encrypted and out-of-limit inputs receive explicit outcomes. Keep network-denied unprivileged parsing, validated canonical OCR input to models, explicit setup downloads and no runtime cloud fallback.

Retain the invoice schema, Decimal amounts, missing-versus-zero distinctions, observed-versus-computed totals, ordered rows and provenance. Unsupported language/locale, ambiguous dates/currencies, incomplete arithmetic and missing evidence remain visible review concerns. CORD is an offline receipt diagnostic, not a supported receipt product.

## Acceptance gates

All fifteen gates are mandatory. **Defined** records a completed scope decision. **Covered** identifies baseline behavior/evidence to verify against final source. **Partial** identifies coverage plus a gap. **Open** means the deliverable is absent. None means full v1 already passes; final closure requires linked evidence and limitations.

| Gate | Requirement and completion check | Current coverage | Workstream |
|---|---|---|---|
| G01 | Document scope/substitutions, map original requirements, and maintain current/next work in the backlog. | Defined by this contract and ADR | B01 |
| G02 | Return upload/batch requests without waiting for extraction. Supervise one automatic worker, persist job profile, enforce bounds, expose queue/stage/errors, and verify exclusive claims, shutdown and restart. | Covered by B02 lifecycle/HTTP tests and source-bound live Chrome/Docker run; final-source confirmation remains B07 | B02 |
| G03 | Cancel/fence owned work without later publication. Reprocess with a new extraction identity/configuration, preserve old revisions/exports, invalidate approval for new candidates, and reuse only valid checkpoints. | Covered by B02 historical-source/export tests, cancellation/transport checks and live reprocess/owned-container stop; final-source confirmation remains B07 | B02 |
| G04 | Restartable tracked deletion revokes access and removes local originals, rendered pages, checkpoints, review/candidate data and exports after active work stops. Shared objects survive until unreferenced. Enforce total artifact budget/free-space reserve; disclose external retention. | Covered by B02 restart/shared-deletion/quota/disk/partial-write tests and live deletion; logical deletion and guard limits disclosed; final-source confirmation remains B07 | B02 |
| G05 | Server-established reviewer identity authorizes edits/decisions/approval; forged actors and processing credentials cannot approve. Preserve session/origin checks and authenticated artifacts; document trusted-local-operator boundary. | Covered by B03 server OS-account principal, forged-actor/capability/download tests and source-bound real HTTP/Chrome reports; trusted CLI boundary documented in [local access](access.md); final-source confirmation remains B07 | B03 |
| G06 | Resolve field sources or visibly disclose evidence limits. Verify crop/rotation/multi-page geometry and publish a predeclared semantic attribution sample with wrong-field, ambiguous and absent support. Fix misleading precision; IDs/substring alignment alone are insufficient. | Partial: B05 adds frozen critical-header/row sample, complete manual-assessment validation and explicit citation/geometry limits in the UI; actual semantic judgments on declared production inputs remain pending | B05 |
| G07 | Preserve suggestions through edits; reject stale edits/approvals and unresolved blocking issues. Bind exports to current approved revisions, preserve retry bytes, prevent duplicate approval inheritance, and verify JSON/formula-safe CSV. | Covered by existing tests/browser/runtime evidence | B07 |
| G08 | Exercise malformed/oversized/encrypted inputs, symlink/path attempts, parser network/resources, escaped UI/CSV and model-directed document instructions. Extraction instructions cannot gain review/export authority. Retain applicable deterministic/live checks. | Partial: B03 rejects forged actors and processing/model review authority; substantial parser/UI/CSV controls exist; comprehensive final-source adversarial coverage remains B07 | B03, B07 |
| G09 | Interrupt parsing/extraction/review/export and restart without lost committed edits or stale authority. Verify restore, timeout/OOM cleanup, fencing and new cancellation/deletion recovery; partial artifacts cannot appear successful. | Partial: B02 adds stop/shutdown/expired ownership, deletion resume, cancellation-preserving restore and export-write failure coverage; full final-source stage/resource drills remain B06/B07 | B02, B06, B07 |
| G10 | Account for all 180 original test invoices and 100 CORD receipts in separate two-variant reports with failures, masks, uncertainty and stage boundaries. Verify archives; extraction/scoring changes need new frozen source-bound comparisons. Reject default promotion of regressing candidates. | Covered by experimental comparisons; final-source applicability remains | B04, B07 |
| G11 | Freeze permitted genuine scan and clean/degraded/rotated/multi-page originals, labels and protocol, then run production uploads with rules and pinned real model. Publish original versus approved quality separately and retain all scheduled failures. | Partial: B04 freezes permission/labels/source/profile and separately scores original versus approved quality; five-case fictional production-runner diagnostic exists; genuine scanner selection and completed production study remain pending | B04 |
| G12 | Run the controlled workload below; publish cold/warm end-to-end/stage P50/P95, serial throughput, memory methods/budgets and failures. Meet rules clean-page latency target or retain experimental labeling until an explicit scope/implementation revision. | Partial: both profiles have historical cold/warm evidence; the pinned image now passes all 34 rules uploads, warm P95 1.707 s and the serial queue. Complete pinned-image model timing and whole-application/unified-memory peak coverage remain pending; [new evidence](../evals/reproducibility-2026-10-04/README.md), [historical evidence](../evals/operations-2026-10-04/README.md) | B06, B07 |
| G13 | Show queue age/stage/failures/retry state; expose service/storage readiness and model availability separately. Export content-free stage timing/failure counts; separate queue, processing and human wait. No external stack required. | Covered by B06 authenticated readiness/metrics, fenced persistent stage events, live browser status and real loopback checks; wall-clock/retention/coverage limits in [operations](operations.md). Final-source confirmation remains B07 | B06 |
| G14 | Pin parser base/packages/language/Python inputs and model/runtime/data identities. Verify explicit fresh-checkout setup followed by offline runtime, prior-version database upgrade and portable restore preserving export bytes. Document disk/host requirements. | Partial: [parser base/snapshot/package/wheel/language pins](parser-build.md), two uncached arm64 rebuilds, sixteen pinned-image parser checks and prior-release database upgrade/portable export preservation now pass. Model/runtime/data identities remain pinned. Final fresh-checkout explicit setup and offline runtime confirmation remain | B07 |
| G15 | Reconcile guides/cards/README, record continuous narrated workflow and case study, retain final-source tests/live parser/model/browser/audit/gate evidence, and verify a separate published-tag clone/demo. All mandatory gates close before publication. | Partial: experimental packaging/presentation exists; full-v1 evidence pending | B07 |

The [review](../tests/test_review.py), [intake](../tests/test_intake.py), [worker](../tests/test_worker.py), [web](../tests/test_web.py), [backup](../tests/test_backup.py) tests and [checkout verifier](../scripts/verify_release_checkout.py) are the deterministic foundation. The [experimental overview](portfolio-candidate.md) links runtime evidence. Source presence or historical passes cannot satisfy missing final-source behavior.

## Original acceptance mapping

| Original plan section 12 criterion | v1 gates and explicit scope |
|---|---|
| Complete Mac CPU-parser/real-model upload and review | G02, G05, G07, G11, G14; Mac required, PC optional |
| Verified evidence or visible unavailability | G06; semantic support is separate from reference existence |
| Auditable corrections and current approved exports | G03, G07 |
| Coherent interruptions and preserved corrections | G03, G04, G09 |
| Complete invoice/receipt two-variant reports | G10; shared-OCR span model replaces Docling layout variant under ADR 0001 |
| Honest objectives and experimental labeling when unmet | G10–G12 and claim rules below |
| Recorded demo and clean reproduction after setup | G14, G15 |
| Evidence for optional GPU/VLM claims | Deferred under ADR; no such v1 claims |

Original sections 3.2–3.6 and 8 supply isolation, evidence, state, identity and deletion requirements in G02–G09. Sections 7 and 9 supply lifecycle/operational requirements in G02–G04 and G12–G13. Section 5 supplies split hygiene and study limits in G10–G11 and the claim rules. Frameworks, interface names, table/cell objects and external telemetry are reconciled explicitly in the ADR.

## Controlled performance workload

The clean-page target is **warm upload-to-review-ready P95 no greater than 60 seconds for rules** on the reference Mac. This makes the original feasibility objective explicit for the product default. Optional-model timing is separate; it is not promised to meet 60 seconds.

Before measurement, B06 freezes fixture selection, input/source/configuration/image/model hashes, machine-load policy, memory accounting/sampling, resource budgets and cache policy. Run at least ten warm clean single-page uploads and three cold launches per profile, using new attempts. Disclose checkpoint reuse; do not mix saved-OCR timing with fresh parsing. Measure startup separately and include it in a labeled cold workflow total.

Run a declared twenty-document serial rules queue with PNG/PDF originals and multi-page cases. Account for every item and retry/failure; separate queue from processing and confirm one active job. Measure model workloads separately without unrelated inference competing for the Mac.

Memory evidence covers the application, parser/container or Docker VM, and model process, with sampling coverage and shared/unified-memory accounting. Do not double-count RSS or call model RSS a whole-application peak. Report host pressure/headroom and enforced parser/artifact budgets. Unavailable peak coverage remains pending.

## Quality and claim rules

Retain the original synthetic development objectives of required-header macro F1 at least 0.90 and line-item amount exact-match accuracy at least 0.85 with their original definitions/denominators; exact-row F1 is not a substitute for an amount metric. The recorded model-promotion gate permits at most 0.02 absolute regression on its declared metrics. The current model failed and remains optional. Synthetic quality does not establish real-scan/vendor quality.

G11/G06 protocols must predeclare selection, cases, annotations, sample counts, eligibility and acceptance policy. Scan results are a bounded diagnostic; broader claims need independent data and validated objectives. Original test families/CORD test outputs have been inspected. Repetitions are not unseen tests; tuning prompted by their errors needs new development/calibration data and a new sealed test set for new generalization claims. Corrections never replace original extractor predictions.

The completed author pilot supports narrow assisted-review findings and retains two errors. **B08 is conditional:** complete an independent counterbalanced manual-versus-assisted study before productivity/time-saved claims. It is unnecessary for claims limited to workflow, extraction and assisted effort with disclosed pilot limitations. No risk threshold authorizes unattended export.

## Closing the release

Each gate needs its result, relevant source commit/hashes, configuration/input identities, command or human protocol, artifact inventory, failures and limitations. Update gate status and backlog together. Preserve archived outcomes; separate new measurements. Documentation edits alone do not require fresh inference when extraction code/inputs are unchanged; relevant implementation changes need applicable runtime evidence before closure.

Existing `release-check` and `release-checkout` are subset checks. B07 must retain a complete gate review alongside them; portfolio `evidence_complete` does not certify these fifteen gates. Verify working source before commit, committed source afterward, then a separate published-tag clone. Publication is a later explicitly requested action; this contract creates neither a release nor a release-date promise.
