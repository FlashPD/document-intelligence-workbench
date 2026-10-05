# Stage recovery evidence — October 4, 2026

Reviewed automated fictional-fixture observations for G09. The [runbook](../../docs/stage-recovery.md) defines the complete fault schedule and limits. Application, parser, extraction, review, approval and export implementation bytes are unchanged by this task.

| Evidence | Result and scope |
|---|---|
| [Final run](final/report.json) | All twelve declared process-crash boundaries pass on the installed pinned parser image. Source/build/test inventory and input remain unchanged throughout the run. |
| [Initial run](initial/report.json) | All twelve pass with the earlier verifier, before additional snapshot/export-preservation checks and test inventory binding. Its original source snapshot/report remain historical; current saved verification targets the final bundle. |
| [Active parser observation](final/parser_active/container-at-crash.json) | Container is independently inspected while its declared isolated sleep probe is running; SIGKILL terminates the host worker. Recovery preserves its live lease, later removes the owned container/scratch and runs real OCR. |
| [Checkpoint write boundary](final/checkpoint_render_written/evidence.json) | Real content-addressed render exists before SQLite commit; no partial checkpoint metadata/candidate survives. Fresh parsing recovers the same render. |
| [Extraction restart](final/rules_extracted/resume.json) | New OS process runs the normal supervisor and reuses committed OCR with zero parser calls. |
| [Committed review](final/review_committed/evidence.json) | New revision/value survives; stale edits and unapproved current exports are refused; original JSON remains byte-identical. |
| [Partial CSV](final/csv_first_file/evidence.json) | First file survives, but no uncommitted manifest becomes downloadable; retry preserves bytes and publishes once. |
| [Isolated checkout](checkout/report.json) | All nine offline checks and **393 deterministic tests pass**, including the retained stage archive. All 3,990 copied source/evidence files remain stable during verification. Source snapshot precedes checkout-evidence retention and the documentation-only result update; committed/tagged/published-source verification remains pending. |

```sh
PYTHONPATH=src python3.12 scripts/verify_stage_recovery.py \
  --verify evals/stage-recovery-2026-10-04/final
make stage-recovery-verify OUTPUT=artifacts/stage-recovery-new
make release-checkout OUTPUT=artifacts/stage-recovery-checkout-new
```

The parser identity is `sha256:09c23c3cf290412c5ae523c636c098d7f11c6a17a6a6ca6261e27419bfb6485c`. Each case retains before/interrupted/recovered SQLite logical snapshots, boundary markers, fault logs and assertions. Processing cases also retain new-process restart markers/logs; export cases retain verified bytes. Both runs use the same self-authored fictional PNG. No records from the original invoice/CORD comparisons or author pilot are changed.

Eight focused offline tests exercise actual subprocess edit/approval/export crashes on injected saved OCR and reject archive corruption, incomplete schedules, source/input substitutions, mutable image identities, extra files and symlinks. A checkout regression check requires the new archive when its verifier is included in the checked tree; older committed releases preserve their original eight-command schedule.

An initial preflight stopped before any drill because the sandbox source glob included `__pycache__` as a file. The inventory now filters regular files. That attempt produced no passing report; its empty output directory remains local at `artifacts/stage-recovery-2026-10-04-001`. The initial and final successful runs are retained independently without rewriting either report.

Faults are abrupt process exits at twelve declared boundaries, with shortened real eight-second leases and 0.25-second heartbeats. They do not establish machine power-loss durability, every crash point, real native-model restart, natural invoice OOM, genuine-scan quality, human review quality or controlled performance. Fixture approvals are automated trusted-operator actions. G09 coverage combines these bounded cases with separately recorded resource, restore, cancellation and deletion checks; full v1 remains pending under the [contract](../../docs/v1-release-contract.md).
