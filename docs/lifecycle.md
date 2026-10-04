# Processing and document lifecycle

The live local workbench processes uploads automatically and serially. B02 adds durable processing profiles, per-file batch outcomes, cancellation, explicit reprocessing, tracked deletion and storage growth guards. These capabilities support the [v1 contract](v1-release-contract.md); reviewer identity, new scan studies and final release acceptance remain separate [backlog](backlog.md) work.

## Start and submit

Build the isolated parser once with `make parser-build`, then run `make dev` and open its printed session URL. Select one to twenty PDF, PNG or JPEG files and an extractor before **Upload**. Each file remains bounded to 20 MiB; the parser retains ten-page and twenty-million-pixel bounds. Accepted files queue immediately; requests do not wait for extraction. Rejected files retain individual outcomes rather than silently disappearing from a batch. The latest batch and recent deletion statuses reappear after reloading the browser.

The server owns one supervised worker. Durable SQLite claims prevent another workbench worker from processing simultaneously. Stages are `QUEUED`, `PARSING`, `EXTRACTING`, `CHECKING` and terminal outcomes; cancellation and deletion have explicit pending states. `/api/runtime` exposes worker liveness and a content-free error token. Model readiness and complete timing metrics remain B06 work.

`ocr_rules` remains the default. `make dev-model` verifies cached pinned assets and supplies an ephemeral authenticated model endpoint. Select **Local span model** before upload or reprocessing. Each job and processing attempt persist its extractor, model alias, request timeout and output-token limit. Endpoints, API keys and review session credentials are excluded. A queued model profile that is unavailable after restart fails with `MODEL_UNAVAILABLE`; it cannot silently switch to rules. Replay and timed author-pilot servers never start live supervision.

## Cancel and restart

**Cancel processing** cancels queued work immediately and marks active work `CANCELLING` until the owned operation unwinds. The production parser removes only the container named for that job and fence. The model transport interrupts its own loopback client socket; it never terminates a shared model server. Server-side inference cancellation depends on the server's disconnect behavior. No later page request, schema repair or candidate publication can pass the cancelled claim.

Ctrl+C stops the owned worker before closing the server. Unfinished work returns to the queue, preserving a verified checkpoint when available. Abrupt exit is different: wait for the recorded lease to expire, then startup maintenance confirms removal of the old parser and cleans its scratch directory before requeueing. Fencing denies stale publication. A failed removal leaves `CLEANUP_REQUIRED` and blocks further serial processing; restore Docker connectivity before recovery. Cleanup failure cannot be bypassed by retry/reprocess.

## Reprocess and review history

**Reprocess** creates a new attempt using the selected configuration. **Render and OCR again** bypasses the parser checkpoint; otherwise only a verified checkpoint with matching input, parser image and parser settings is reused. Reprocessing preserves earlier records, edits, approvals, source spans, rendered pages and export bytes. A completed extraction creates a new revision and attempt identity; that candidate needs a fresh approval. New edits/approvals/exports are blocked while replacement processing is pending or failed. Existing historical downloads remain available until deletion.

The API supports `GET /api/documents/ID?revision=N`, revision-specific page images and `GET /api/documents/ID/attempts`. Reprocessing is `POST /api/documents/ID/reprocess` with a current integer `revision`, an `extractor` and boolean `reparse`. Stale revisions conflict. Failed attempts remain explicit; fix the cause and retry instead of replacing their outcomes.

## Delete local data

**Delete document** confirms removal of that document's local source, renders, checkpoints, revisions, review events, decisions, approvals and exports. Access is revoked as soon as the durable deletion request commits. Active work must settle or be fenced and its container cleanup confirmed before unlinking mounted files. Deletion resumes on startup and remains `PENDING` with `DELETION_RETRY_REQUIRED` when a file or cleanup operation fails. Inspect `/api/deletions/ID` or the browser's deletion list; a vanished queue row alone is not proof of completion.

Deletion preserves originals/renders shared by another document, including historical revision sources. After the final reference is gone, it removes those files. A completed tombstone contains only the opaque document ID, timestamps, status and an empty manifest; batch slots retain a content-free `DELETED` outcome. Unsafe paths or symlinks are refused rather than followed.

This is logical local deletion, not forensic secure erasure of SQLite pages, WAL, filesystem snapshots or an SSD. Downloaded exports, portable backups, archived evaluation evidence and independently copied data have separate retention and require their own deletion. Pending deletion must finish before a portable backup can be created. Restore preserves cancellation and historical export bytes; it cannot operate containers belonging to the source workbench.

## Storage limits

The default artifact growth budget is **20 GiB**, with **256 MiB free space reserved** on affected filesystems. Inventory covers database/WAL/SHM, originals, rendered/checkpoint pages, parser/upload quarantine and local exports. Shared hard links count once. The existing **1 GiB original-object quota** also remains enforced. Model weights, runtime binaries, Docker images/VM storage, evaluation archives and external backups are outside this workbench budget.

Configure and persist the policy when starting a workbench:

```sh
PYTHONPATH=src python3.12 -m docwork.cli serve \
  --max-artifact-mib 20480 --disk-reserve-mib 256
```

Omitted flags retain the database's policy across restarts and review CLI exports. Growth checks reject uploads/imports/exports with `STORAGE_QUOTA_EXCEEDED` or `INSUFFICIENT_DISK_SPACE` (HTTP 507). Metadata guards reserve conservative SQLite/WAL overhead. Real parsing checks room for its bounded output before creating scratch; checkpoint imports and final writes are checked separately. This application guard is not an operating-system filesystem quota and cannot constrain unrelated disk writers. Inventory reports actual bytes and unsafe entries via `/api/storage` or `intake storage`.

Uploads and atomic artifact writes remove their temporary files on failure; a failed multi-file CSV export removes newly written parts and never registers a successful manifest. Crash recovery removes owned parser scratch. A crash between artifact publication and reference commit can leave an unreferenced object/render; use the existing checked `intake reconcile` workflow, whose default age threshold is 24 hours. Referenced historical renders remain protected. Deletion also inventories unregistered files in the document's local export directory.

## CLI operations

CLI submission persists the same profile; it queues work without starting a daemon. Use the live server for automatic processing, or explicitly process one item. Replace `ID` and `N` with a document ID and its current revision:

```sh
PYTHONPATH=src python3.12 -m docwork.cli intake submit samples/clean.png --mime image/png
PYTHONPATH=src python3.12 -m docwork.cli intake process-one
PYTHONPATH=src python3.12 -m docwork.cli intake status ID
PYTHONPATH=src python3.12 -m docwork.cli intake cancel ID
PYTHONPATH=src python3.12 -m docwork.cli intake reprocess ID --revision N --extractor ocr_rules --reparse
PYTHONPATH=src python3.12 -m docwork.cli intake attempts ID
PYTHONPATH=src python3.12 -m docwork.cli intake delete ID
PYTHONPATH=src python3.12 -m docwork.cli intake deletion-status ID
PYTHONPATH=src python3.12 -m docwork.cli intake resume-deletions
PYTHONPATH=src python3.12 -m docwork.cli intake storage
```

`process-one` honors the queued profile by default. Explicit `--extractor` overrides are retained for trusted diagnostic/manual commands and record the effective attempt configuration. A model job also needs its matching loopback `--model-endpoint` and `--model-id`; credentials remain ephemeral. The browser uses its server-owned profile and rejects endpoint overrides. Direct `ReviewServer` construction defaults to manual instrumentation for legacy verification scripts; the public `serve` command enables live supervision.

## Verification

Run `make test` for deterministic lifecycle, HTTP, checkpoint, backup and archive compatibility checks. Tests cover a twenty-file serial batch across two supervisors, shutdown/restart, cancellation/fencing, shared deletion, interrupted cleanup, historical sources/exports, quota/disk refusal and partial writes. Injected parser/model fixtures are explicitly separate from runtime evidence.

With Docker and Chrome installed, `make lifecycle-verify OUTPUT=artifacts/lifecycle-fresh-001` uses a new disposable workbench, native browser upload/reprocess/cancel/delete controls and real Docker/Tesseract on a fictional invoice. It retains screenshots, input/parser/source hashes and exact outcomes. It runs no model inference and measures neither human productivity nor the B06 performance gate. The retained B02 run is linked in the backlog; existing experimental archives remain unchanged.
