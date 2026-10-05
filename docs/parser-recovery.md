# Parser checkpoints and worker recovery

The worker now commits verified canonical OCR and page renders before starting extraction. A model outage leaves a failed, retryable document with a parser checkpoint and no review candidate. A retry can resume extraction from the checkpoint, avoiding another PDF render and OCR run. The document still needs a new candidate, validation, human approval, and a version-bound export.

## Checkpoint identity and integrity

The cache key covers the original SHA-256, declared media type, immutable Docker image ID, parser protocol version, checkpoint version, and hashes of the host importer and contract sources. The image identifies the installed renderer, OCR code, language assets, and fixed parser configuration. The worker inspects the selected local image tag and runs the resolved image ID, so changing a tag during processing cannot change that attempt's parser. Inspection never pulls an image. A changed image or host contract causes fresh parsing. The local image must remain available even for a checkpoint hit; Docker unavailability does not silently substitute a different parser.

The checkpoint stores the parser manifest in SQLite with its SHA-256 and references the existing content-addressed render store. Before reuse, the worker verifies original bytes, manifest bytes, each render's path/type/size/hash, page sequence, and the complete canonical parser contract, including geometry and source membership. A corrupt matching checkpoint fails with `PARSER_CHECKPOINT_INVALID` before extraction. It is never treated as an empty successful result.

Checkpoint writes and reuse events require a live fencing token. Checkpoint references commit in the same transaction; a failed write rolls back metadata and can leave only an unreferenced render. `intake reconcile` protects checkpoint renders even when extraction has failed and no candidate exists. Scratch output is removed before the model is called. Checkpoints are scoped to individual submissions: duplicate originals do not inherit checkpoints, candidates, decisions, or approvals.

Only parsing is cached. A model, prompt, or extractor change reruns extraction and validation from the verified pages. An explicit switch from `span_llm` to `ocr_rules` on an unreviewed retry is supported; no automatic fallback occurs. Injected library runners do not cache results unless their caller explicitly supplies an immutable parser identity. CLI/browser processing always resolves the real local image.

## Resume and inspect

After a `MODEL_UNAVAILABLE` failure, start the intended model endpoint and requeue the document:

```sh
PYTHONPATH=src python3.12 -m docwork.cli intake status YOUR_DOCUMENT_ID
PYTHONPATH=src python3.12 -m docwork.cli intake retry YOUR_DOCUMENT_ID
PYTHONPATH=src python3.12 -m docwork.cli intake process-one \
  --extractor span_llm --model-endpoint http://127.0.0.1:8080 --model-id local-invoice
PYTHONPATH=src python3.12 -m docwork.cli review history YOUR_DOCUMENT_ID
```

`intake status` reports `parser_checkpoint` with its cache key, image ID, and creation time. Review history records `parser_checkpoint_saved` and `parser_checkpoint_reused` without OCR text. The job's attempts/fence distinguish retries; `current_revision` remains zero until extraction succeeds. Checkpoint pages are not exposed as an approved record or as review-ready pages.

To refresh parsing on a queued job, including after a manifest integrity failure:

```sh
PYTHONPATH=src python3.12 -m docwork.cli intake retry YOUR_DOCUMENT_ID
PYTHONPATH=src python3.12 -m docwork.cli intake process-one --reparse
```

Supply the model options again to retain `span_llm`; the command above explicitly selects the default rules extractor. `--reparse` bypasses the saved result and replaces its metadata only after a new verified parse. It does not overwrite corrupt content-addressed render files: restore those bytes from a trusted backup before retrying, and use `intake reconcile` to find affected references. A failed refresh leaves the previous checkpoint in place. Editing or re-extracting a reviewed document remains outside this retry interface.

If a worker exits without marking a job failed, run `process-one` after its lease expires; do not call `retry` on a processing job. The normal lease is 120 seconds with renewal every 30 seconds. Reclaim increments the fencing token and reruns extraction from a matching checkpoint.

## Live verification scope

The [final October 3, 2026 report](../evals/parser-recovery-2026-10-03-final/report.json) passes all 12 checks and confirms unchanged source/image inputs. The deterministic suite passes 148 tests, including 14 new checkpoint tests. The [initial report](../evals/parser-recovery-2026-10-03/report.json) is retained: its short-lease crash probe failed because the heartbeat kept its original default interval. The worker now explicitly passes the same lease/heartbeat constants to the renewal context, and the final probe verifies real renewal and expiry with the shortened lease.

Run the workflow suite with the current image and a fresh evidence path:

```sh
make parser-build
make parser-verify OUTPUT=artifacts/parser-recovery-fresh.json
```

Two checks extend the original ten container checks:

- A real two-page PDF is parsed in Docker. The real model HTTP adapter connects to a bound, non-listening loopback port and fails with `MODEL_UNAVAILABLE`. Reopening SQLite and explicitly selecting rules on retry preserves both pages and page-2 row evidence. A guard makes any second Docker parser invocation fail the test. The new candidate can be approved and exported.
- A separate host worker process parses a PNG in Docker, commits its checkpoint, and calls `os._exit(73)` at the first model-request callback. It leaves a processing job, no candidate, and no parser scratch files. A replacement worker waits for a shortened **real** two-second lease, reclaims with a new fence, and creates the correct rules candidate. A guard again forbids a parser rerun. There is no real model inference in this check.

These exercise a host process exit at the parser/extractor boundary and model transport failure. They do not establish recovery during parsing, machine power loss, filesystem durability under power loss, export interruption, or a real model restart. A crash during parsing can still leave quarantine scratch; reconciliation deliberately excludes quarantine. There is no partial-page checkpointing: an interruption before a complete verified manifest requires parsing again. Retention/cleanup of completed checkpoints, parser resource-exhaustion drills, and real-model upload evidence remain release work.

The later [stage-recovery drills](stage-recovery.md) separately verify owned active-parser cleanup after lease expiry, render-before-checkpoint-commit rollback, completed rules extraction and candidate-transaction recovery, review/approval commits and partial JSON/CSV exports. Those new reports extend the bounded process-crash coverage without changing these historical outcomes or establishing machine power-loss/real-model-restart durability.
