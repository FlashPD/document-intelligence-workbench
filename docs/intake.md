# Bounded document intake

The intake prototype streams a local file into a quarantine directory, rejects files over 20 MB, checks the extension, declared MIME type, and file signature, and checks PNG/JPEG header dimensions against the 20-megapixel limit. It stores valid bytes under their SHA-256 hash and creates a separate document and queued job for each submission. Up to 20 files can be submitted through the library's batch method. A duplicate hash shares one stored object but never shares a job, review record, or approval.

The database and object store are local. Submission metadata and the queued job are committed in one SQLite transaction after the stored bytes pass a checksum check. A failed transaction can leave an unreferenced object file; a later reconciliation step will remove those orphans. Jobs use 120-second leases with renewal every 30 seconds during parsing and extraction. An expired worker cannot publish a candidate or mark another worker's reclaimed job as failed because completion and failure require its current fencing token. Candidate insertion, rendered-page metadata, review readiness, and job completion share one transaction. A crash stops renewal; the expired job can then be reclaimed by the next `process-one` call. This is deterministic contract coverage; a real interrupted Docker run remains to be exercised.

The parser image decodes and re-encodes each page, applies image EXIF rotation, runs English Tesseract, and emits numbered PNGs with canonical OCR spans. PDFs are limited to ten pages; the container has a 600-second document timeout. The worker starts it without network, with a read-only root, no capabilities, an unprivileged user, bounded CPU/memory/PIDs, a read-only original mount, and a scratch output mount. The host checks exact output filenames, regular files, sizes, checksums, page dimensions, page numbers, and span geometry before extraction. A failed parser job can be retried without inheriting review decisions. Docker and the image are required; the daemon was unavailable for a real-container run on this host.

## Try the upload-to-review CLI

With Docker Desktop running, build the parser image once:

```sh
make parser-build
make parser-smoke
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

`process-one` claims one queued job. Run it again for the next document. The `page` command reports a checked page PNG path for evidence inspection; omit `--number` for page 1. Review, approval, and export commands are documented in [review.md](review.md). If the job fails, inspect its `error_code` with `intake status`, then use `intake retry YOUR_DOCUMENT_ID` after fixing the cause.

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

## Current boundary

The default is a narrow English OCR baseline. The parser now handles PDFs of up to ten pages, and the host merges page-level extraction into one record. It takes the first observed header value for each field and raises a blocking issue when later pages show a different labeled value. The model profile uses the same canonical spans and flags conflicting per-page model values; it is not yet a measured layout-model comparison. The container uses a versioned Python base and pinned Pillow, while Debian OCR/Poppler package versions are not yet locked. A real Docker execution, PDF/JPEG cases, resource-failure drills, and a security review remain before claiming an arbitrary-document workflow. The CLI still assumes a trusted local operator and has no authenticated browser interface.
