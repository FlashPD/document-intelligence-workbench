# Bounded document intake

The intake prototype streams a local file into a quarantine directory, rejects files over 20 MB, checks the extension, declared MIME type, and file signature, and checks PNG/JPEG header dimensions against the 20-megapixel limit. It stores valid bytes under their SHA-256 hash and creates a separate document and queued job for each submission. Up to 20 files can be submitted through the library's batch method. A duplicate hash shares one stored object but never shares a job, review record, or approval.

The database and object store are local. Submission metadata and the queued job are committed in one SQLite transaction after the stored bytes pass a checksum check. A failed transaction can leave an unreferenced object file; `intake reconcile` audits these and unreferenced rendered pages. Jobs use 120-second leases with renewal every 30 seconds during parsing and extraction. An expired worker cannot publish a candidate or mark another worker's reclaimed job as failed because completion and failure require its current fencing token. Candidate insertion, rendered-page metadata, review readiness, and job completion share one transaction. A crash stops renewal; the expired job can then be reclaimed by the next `process-one` call. [Parser checkpoints](parser-recovery.md) preserve verified pages before extraction and let a retry avoid repeating OCR. Live recovery coverage targets a host worker exit after parsing and a refused loopback model connection; other interruption points remain unverified.

The `docwork-parser:v3` image decodes and re-encodes each page, applies image EXIF rotation, runs English Tesseract with automatic orientation detection (PSM 1, falling back to PSM 3 if orientation detection fails), and emits numbered PNGs with canonical OCR spans. OCR boxes remain on the displayed raster, which may still appear sideways even when text is recognized correctly. PDFs are limited to ten pages; the container has a 600-second document timeout. The worker starts it without network, with a read-only root, no capabilities, an unprivileged user, bounded CPU/memory/PIDs, a read-only original mount, and a scratch output mount. The host checks exact output filenames, regular files, sizes, checksums, page dimensions, page numbers, and span geometry before extraction. A failed parser job can be retried without inheriting review decisions. Docker and a locally built image are required. The worker never implicitly pulls a missing image. The [live verification](parser-verification.md) now exercises PNG/PDF/JPEG inputs, rejection, retry, review/export, and the runtime restrictions on this host.

The [resource-failure runbook](parser-resources.md) records real deadline/OOM probes and successful retries. `PARSER_TIMEOUT` confirms timeout removal; `PARSER_KILLED` identifies exit 137 without assuming OOM. `PARSER_CLEANUP_FAILED` means removal could not be confirmed: restore Docker connectivity and inspect the exact job container before retrying. Failures do not publish a candidate or approval.

## Try the upload-to-review CLI

For the automatic browser worker, bounded batches, cancellation, reprocessing, deletion and persisted storage limits, start with the [lifecycle runbook](lifecycle.md). CLI commands below explicitly process a single item; the live browser no longer needs a manual processing trigger.

With Docker Desktop running, build the parser image once:

The [pinned-build and upgrade guide](parser-build.md) records the exact base, Debian snapshot, package/wheel/language identities and two-rebuild verification. Building is the explicit download step; uploaded-document processing never downloads dependencies.

```sh
make parser-build
make parser-smoke
make parser-verify OUTPUT=artifacts/parser-verification-fresh.json
```

```sh
PYTHONPATH=src python3.12 -m docwork.cli intake submit samples/clean.png --mime image/png
```

The response contains a `document_id`, `status: RECEIVED`, and a `QUEUED` job. By default, the database is `artifacts/review.sqlite` and originals are below `artifacts/intake/objects/`. Check the state with:

```sh
PYTHONPATH=src python3.12 -m docwork.cli intake status YOUR_DOCUMENT_ID
PYTHONPATH=src python3.12 -m docwork.cli intake process-one
PYTHONPATH=src python3.12 -m docwork.cli intake page YOUR_DOCUMENT_ID
PYTHONPATH=src python3.12 -m docwork.cli intake page YOUR_DOCUMENT_ID --number 2
PYTHONPATH=src python3.12 -m docwork.cli review show YOUR_DOCUMENT_ID
```

`process-one` confirms cleanup of expired work, then claims one queued job using its persisted profile by default. Run it again for the next document. The `page` command reports a checked page PNG path for evidence inspection; omit `--number` for page 1. Review, approval, and export commands are documented in [review.md](review.md). If the job fails, inspect its `error_code` and `parser_checkpoint` with `intake status`, then use `intake retry YOUR_DOCUMENT_ID` after fixing the cause and confirming any parser cleanup. Matching checkpoints are reused automatically; `process-one --reparse` explicitly refreshes parsing. See the [recovery guide](parser-recovery.md) and [lifecycle runbook](lifecycle.md) for attempt identity, cancellation and restart behavior.

## Audit stored artifacts

```sh
PYTHONPATH=src python3.12 -m docwork.cli intake reconcile
PYTHONPATH=src python3.12 -m docwork.cli intake reconcile --prune
```

The first command reports referenced files with missing or incorrect bytes, malformed references, and unreferenced files. It changes nothing. `--prune` removes only unreferenced originals and rendered pages at least 24 hours old. It protects renders referenced by parser checkpoints, including failed model jobs. It never scans upload quarantine or exports, and it leaves every referenced file in place. Any missing, corrupt, or malformed reference blocks all pruning and makes the command exit with code 2; fix the integrity problem before retrying. Use `--min-age-seconds` to change the age threshold when recovering a known interrupted write. The database lock keeps a concurrent submission, checkpoint write, or job completion from publishing a reference during the audit.

To process the next queued document with an experimental local span model, run a local chat completion server on loopback with schema-constrained JSON output support. For a separately obtained local GGUF, [llama-server documents these flags and the schema response format](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md):

```sh
llama-server -m /absolute/path/to/model.gguf --host 127.0.0.1 --port 8080 --ctx-size 8192 --alias local-invoice
```

Then process the queued document:

```sh
PYTHONPATH=src python3.12 -m docwork.cli intake process-one --extractor span_llm --model-endpoint http://127.0.0.1:8080 --model-id local-invoice
```

The model receives bounded OCR spans, not original files or review credentials. Each page gets one extraction request and at most one schema-repair request. Unknown evidence IDs and value mismatches become blocking review issues; unusable output, context overflow, an API rejection, or an unavailable endpoint fails the job explicitly. The endpoint must be a numeric loopback address. The saved model ID from this generic endpoint command is the server's declared alias, not a verified weights hash. The pinned managed evaluation below adds verified model/runtime identity. The extractor does not yet use Docling layout output.

To score the model on the same 12 self-authored development images as the OCR rules baseline, run:

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-development-model --model-endpoint http://127.0.0.1:8080 --model-id local-invoice
```

This writes `artifacts/development-model.json`. All 12 images remain in the denominator, including OCR and model failures. OCR and model time are recorded separately. This is development data used for engineering choices, not a held-out quality claim.

## Pinned local model evaluation

The Apple Silicon profile in [config/model-mac-instruct.json](../config/model-mac-instruct.json) pins Qwen3-4B-Instruct-2507 in Q4_K_M GGUF and llama.cpp b11149 by revision, SHA-256, and size. Model and runtime license URLs are included. The model is an Unsloth conversion of the [upstream Qwen model](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507); its conversion recipe has not been independently reproduced. The runtime flags are tied to the [pinned llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/d2e54583c7452353eb35d40431281f6ee984332f/tools/server/README.md).

Explicitly download the assets once, then verify and evaluate offline:

```sh
make models-fetch
make models-verify
PYTHONPATH=src python3.12 -m docwork.cli eval-local-model --output-dir artifacts/model-run-001
```

`models-fetch` downloads approximately 2.50 GB of weights, an 11 MB runtime archive, and their license texts. Existing files are verified and reused; a mismatched file causes an error instead of being overwritten. Alternatively, place already-downloaded files at the profile's filenames under `artifacts/models/` and `artifacts/runtime/`, then run `models-verify`. There are no imports or paths into another project.

`eval-local-model` verifies the assets, extracts the pinned runtime into a temporary directory, starts a server on an available numeric loopback port with an ephemeral API key, and waits for the expected model alias. It uses one 8192-token slot, requests full GPU offload, disables agent tools/web UI, and enables runtime offline mode. Startup has a 120-second limit; each extraction request has a 150-second limit and the existing single schema-repair allowance. The child is terminated on success, failure, or interruption. Temporary runtime files are removed; the original verified weights/archive remain cached.

Use a **new output directory** for each run. It contains the profile, extraction source snapshot, server log, original model candidates and source spans under `predictions/`, and a `report.json` with artifact hashes. Failed documents remain in all scoring denominators. The managed command exits with code 2 if any document fails processing; quality regressions are checked separately with `eval-compare`. An early startup failure leaves `failure.json` and diagnostics rather than a successful report.

Verify the saved files and reproduce their scores without loading a model:

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-verify artifacts/model-run-001
```

This checks each artifact hash, the pipeline source fingerprint, profile/model/runtime identity, completed server shutdown, original document hashes, and all per-document scores reconstructed from saved predictions. It is an integrity check on recorded evidence, not a new inference run or a cryptographic attestation from the model server.

The report binds the model and runtime hashes to the child process that served the requests; the generic `eval-development-model` command still records only a user-selected server alias. The managed server's sampled RSS is reported separately from quality scores. One-second RSS samples do not establish peak GPU allocation or total application memory. Documents run serially after model readiness, with no explicit warmup; the first request is cold. Runtime logs record whether GPU layers actually loaded. Real invoice data is not used by these commands; they accept only the committed, fictional development PNGs.

Here, “cold” means the first generation in a new server process. OS file caches and Metal compilation caches are not cleared, and later requests may reuse prompt-cache state. This is an exploratory feasibility timing protocol, not a controlled cold/warm latency benchmark.

Compare the managed model run with a fresh baseline:

```sh
make eval-development
PYTHONPATH=src python3.12 -m docwork.cli eval-compare artifacts/development-baseline.json artifacts/model-run-001/report.json --allow-change extractor
```

See the [development report](development-baseline.md#span-invoice-v2-follow-up--october-2-2026) for measured results and limitations. The pinned profile currently targets Apple Silicon only; Linux/Windows/PC inference needs a separately verified runtime profile.

## Portable backup and restoration

The workbench can snapshot its SQLite database and all referenced originals, rendered pages, parser checkpoints, and historical JSON/CSV exports. Stop the browser and processing worker before a routine backup: creation holds a database write lock while copying the referenced files, so concurrent writes wait and a long copy can exceed their ten-second lock timeout. SQLite's backup API includes committed WAL data; copying the database file alone is insufficient.

```sh
PYTHONPATH=src python3.12 -m docwork.cli backup create \
  --db artifacts/review.sqlite --objects artifacts/intake \
  --output artifacts/backups/workbench-001
PYTHONPATH=src python3.12 -m docwork.cli backup verify artifacts/backups/workbench-001
PYTHONPATH=src python3.12 -m docwork.cli backup restore artifacts/backups/workbench-001 \
  --output artifacts/restored-workbench-001
PYTHONPATH=src python3.12 -m docwork.cli serve \
  --db artifacts/restored-workbench-001/database.sqlite \
  --objects artifacts/restored-workbench-001/intake
```

Both creation and restoration require a **new destination directory**. They stage files, check every copied checksum, and publish only a verified result. An existing destination is refused. Missing/corrupt originals, renders, checkpoints, or exports fail creation rather than produce a partial successful backup. Restoring a corrupt, incomplete, or symlink-containing bundle fails without publishing a workbench. The commands exit 2 on failure. A custom source export directory can be supplied with `backup create --exports /absolute/path/to/exports`.

The bundle contains `database.sqlite`, `manifest.json`, and only referenced files under `intake/` and `exports/`. The verifier checks SQLite integrity and foreign keys, supported schema, exact artifact inventory, database references, sizes, SHA-256 checksums, checkpoint payload hashes, and record counts. It rejects traversal paths and symlink artifacts. Duplicate originals/renders are copied once. Quarantine scratch files, orphan artifacts, model weights, configuration, runtime logs, and session credentials are excluded. The backup does contain invoice data and reviewer history; its directory is private to the creating user. It is neither encrypted nor a signed attestation. A person able to rewrite both a bundle and its hashes can replace its contents.

Restoration relocates export paths and storage policy while preserving every export byte, revision, correction, issue decision, approval hash and review event. A subsequent edit or new extraction still needs a new approval. Failed jobs retain their errors and require explicit retry. Interrupted processing becomes `QUEUED` with a newer fence; pending cancellation becomes `CANCELLED`. Restored attempts cannot operate source-workbench containers. Verified checkpoints and historical revision renders remain available. Finish pending deletions before creating a backup. Restore runs no parsing or inference and changes no source workbench. Start only the restored copy when recovering; independent copies can duplicate work outside the single-node boundary.

Legacy trusted sample seeds have records and OCR spans but no intake-stored originals. Their backup count explicitly reports `documents_without_stored_original`; viewing those sample images still requires the repository fixtures. Uploaded originals are included. The command accepts the current intake/review schema, not arbitrary older or future SQLite schemas, and performs no migration. Model/runtime assets and the parser image must be set up separately on a fresh machine.

The deterministic suite covers moved-directory restoration, historical/current approvals and exports, duplicate submission isolation, WAL snapshots with a concurrent writer, corrupt/missing inputs, disk-copy failures, and restored checkpoint reuse. The [live Docker restoration report](../evals/backup-restoration-2026-10-03/report.json) adds an actual parsed two-page PDF and an actual host worker exit after checkpointing. Reproduce the entire parser/recovery/restoration suite with `make parser-build` and `make parser-verify OUTPUT=artifacts/parser-restoration-fresh.json`. The saved report records assertions and hashes; temporary test databases and backup bundles are removed. These checks do not establish power-loss durability, interrupted directory publication, cross-version migration, or backups on arbitrary filesystems.

## Current boundary

The default remains a narrow English OCR baseline. The parser handles PDFs of up to ten pages, and the host merges page-level extraction into one record. It takes the first observed header value for each field and raises a blocking issue when later pages show a different labeled value. The model profile uses the same canonical spans and flags conflicting per-page values; it is a shared-OCR span comparison, not a layout-model comparison.

The [parser build](parser-build.md) now pins its base, Debian snapshots, PDF/OCR/language packages and Pillow wheel hashes. [Fresh model setup](model-setup.md) records explicit acquisition and verified transfer followed by restricted offline inference/review/export. Live parser/resource/recovery checks and the [five-case production-runner diagnostic](production-study.md#tooling-diagnostic-and-data-acquisition-status), including rotated and multi-page model uploads, retain their own scopes and failures. Browser review uses a [server-established OS-account identity](access.md) with separate processing permissions; CLI/store calls retain the trusted-local-operator boundary. The [lifecycle guide](lifecycle.md) covers automatic processing, cancellation, reprocessing, tracked deletion and total-storage guards.

The [adversarial run](adversarial.md) adds valid encrypted-PDF refusal, model-directed invoice notes, literal browser markup and formula-safe CSV. Its eighteen-check parser rerun passes; an initial short-lease probe failure remains preserved. Permissioned genuine scans, manual semantic/approved-quality assessments, whole-application memory coverage, remaining stage-recovery drills and final committed/tagged-source confirmation remain pending under the [v1 contract](v1-release-contract.md). Existing fixture evidence does not establish arbitrary-vendor accuracy or complete release acceptance.
