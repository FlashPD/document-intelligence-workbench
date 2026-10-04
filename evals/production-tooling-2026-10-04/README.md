# Production-study tooling diagnostic

This October 4 diagnostic uses five **known, self-authored fictional development invoices**, explicitly declared synthetic. It verifies production-study tooling, not genuine scanner acquisition, unseen-vendor accuracy, human review or full v1 acceptance. All originals permit redistribution. Historical evaluations and pilot outcomes remain unchanged.

The [freeze](freeze/protocol.json) binds ordered originals, generator labels, permission declarations, runtime/UI source, pinned model profile and immutable parser image before either variant. Extraction receives originals, without gold labels. [Rules](rules/report.json) and [model](model/report.json) preserve initial unapproved candidates and rendered pages; all scheduled failures remain in scoring.

| Check | Recorded result |
|---|---|
| [Deterministic tests](tests.log) | 347 passed |
| [Chrome replay controls](browser/report.json) | 16 passed after evidence-limit presentation changes |
| [Rules original suggestions](rules-score.json) | 5/5 processed; required headers exact on 5/5; header macro F1 1.0; exact-row F1 0.9412 |
| [Pinned-model original suggestions](model-score.json) | 3/5 processed; 2 `MODEL_UNAVAILABLE` failures retained; required headers exact on 3/5; header macro F1 0.7167; exact-row F1 0.6923 |
| Semantic assessments | [Rules](rules-audit-template.json) and [model](model-audit-template.json): 43 targets each, all unassessed |
| Separate approved-quality snapshots | [Rules](rules-approval-snapshot.json): five `NotApproved`; [model](model-approval-snapshot.json): three `NotApproved` plus two original extraction failures. No approvals created. |

The model runtime's shutdown is recorded as complete. The error category alone does not establish whether those requests failed through timeout or endpoint unavailability. Diagnostic per-document timings and sampled model-process RSS are not controlled whole-application performance evidence.

Offline reproduction, with a new output path:

```sh
PYTHONPATH=src python3.12 scripts/production_study.py score \
  evals/production-tooling-2026-10-04/freeze \
  evals/production-tooling-2026-10-04/model \
  --output artifacts/production-model-score-fresh.json
```

Use the [runbook](../../docs/production-study.md) for a new frozen study and real parser/model runs. The separate persisted diagnostic workbenches stay under ignored `artifacts/`; approval snapshots are point-in-time evidence and do not reconstruct a live database. G06/G11 remain partial until declared production inputs, complete source-inspected semantic assessments and separately collected approved quality exist.
