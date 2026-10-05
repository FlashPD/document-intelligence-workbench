# Process interruption and transaction recovery

The [stage verifier](../scripts/verify_stage_recovery.py) exercises twelve declared process-crash boundaries for [G09](v1-release-contract.md). It uses independent disposable workbenches and a self-authored fictional invoice, the installed immutable Docker parser, real OCR/rules, SQLite WAL and new OS processes. These are automated reliability checks; fixture approvals are not human study results.

```sh
make stage-recovery-verify OUTPUT=artifacts/stage-recovery-fresh
PYTHONPATH=src python3.12 scripts/verify_stage_recovery.py \
  --verify artifacts/stage-recovery-fresh
```

Use a new output directory. Python 3.12, Docker Desktop and the pinned parser image from `make parser-build` are prerequisites for the live command. Saved verification needs Python only; it neither starts Docker nor performs OCR/inference. No files, datasets or models are downloaded. The focused offline tests use injected saved OCR and real subprocess exits, separately from live parser evidence:

```sh
PYTHONPATH=src python3.12 -m unittest discover -s tests -p test_stage_recovery.py -v
```

## Fault schedule and recovery assertions

| Boundary | Fault and recorded observations | Required result |
|---|---|---|
| Active isolated parser | A sleep entrypoint writes a startup marker under the production container policy; inspect confirms the container is running, then SIGKILL stops its host worker | Preserve the live lease; after actual expiry the replacement supervisor removes the exact old container/scratch and performs fresh real parsing |
| Render written before checkpoint commit | Abrupt exit after the real atomic render write while the checkpoint transaction is open | No checkpoint/page metadata or candidate survives; the content-addressed orphan is observed and reused by the fresh parse without corruption |
| Rules extraction completed | Run real rules on committed container OCR, then exit before candidate publication | Reuse the verified checkpoint without invoking another parser; no partial record or approval |
| Candidate transaction | Exit after candidate/revision/event writes but before the transaction commits | Roll back the candidate atomically, reuse committed OCR and publish exactly one new candidate |
| Edit transaction | Exit after the edit event is inserted, before commit | Preserve the prior record/revision/approval and historical export |
| Committed edit | Exit after the edit method returns | Preserve the new value, revision, source and audit event; reject a stale edit and export without new approval |
| Approval transaction | Exit after approval/event writes, before commit | No approval survives; explicit retry creates one approval |
| Committed approval | Exit after approval returns | Preserve the exact approval and event; retries remain idempotent |
| First CSV file | Exit after writing the header, before the second file and manifest | No CSV is downloadable; retry retains the header bytes and commits both files once |
| CSV transaction | Exit after both files and export event are written, before commit | No manifest/event survives; retry preserves both files and commits once |
| Committed CSV | Exit after export returns | Preserve the manifest, event and bytes; retry/download checksums match |
| JSON transaction | Exit after JSON file/event writes, before commit, with an existing CSV baseline | No JSON is downloadable; retry retains its bytes and the prior CSV exports |

Except for the active-parser SIGKILL, faults call `os._exit(73)`: Python `finally` blocks and SQLite context exits do not run. Processing probes use an **eight-second lease and 0.25-second heartbeat**, preserving real time rather than editing expiry timestamps. Before expiry, another claim and cleanup cannot steal ownership. After expiry, the stale claim is rejected. A new process starts the normal serial supervisor; attempt history must show `ABANDONED` followed by `COMPLETE`, an advancing fence and no inherited approval. Valid checkpoint recovery forbids any parser invocation; a missing checkpoint requires exactly one fresh parser call.

Every case reopens SQLite and verifies integrity. The verifier records before/interrupted/recovered logical snapshots, exact boundary markers, child logs, restart markers and recovered export bytes. Source/build/test and original hashes bind the run; source/image drift, an omitted boundary, incomplete cleanup or an existing output directory cannot count as success. Temporary stores and only their owned process sessions/containers are removed after each drill. Reports retain failures and stop the schedule on the first failed case; reruns need a new path.

Saved verification checks the complete schedule, artifact inventory, symlink refusal, source/input snapshots, immutable parser identity, state assertions and retained export checksums. It checks locally recorded evidence, not a signed independent attestation. Keep the report and its entire directory together when copying it.

## Evidence and limits

[October 4 reviewed evidence](../evals/stage-recovery-2026-10-04/README.md) records the final schedule and verification. Earlier passing verifier output is kept separately from the final run. An initial preflight encountered a Python cache directory in the sandbox inventory and stopped before any fault drill; filtering the inventory to files corrected that tooling assumption.

These bounded process interruptions complement the [timeout/OOM drills](parser-resources.md), [checkpoint/portable restoration](parser-recovery.md), [cancellation/deletion lifecycle](lifecycle.md) and deterministic review/export tests. They do not establish machine power-loss durability, every possible instruction-level crash point, natural invoice OOM behavior, a real native-model restart, genuine-scan quality, human approval accuracy or controlled latency. Active-parser interruption uses a declared sleep probe, followed by a normal real-parser retry. The optional model's transport, setup/shutdown and saved-checkpoint recovery remain separately identified in their existing reports.

G09 can be covered for these declared process/resource/lifecycle cases while full v1 remains pending. Final release commit/tag applicability, the other mandatory gates and the [backlog](backlog.md) still govern publication.
