# Bounded document intake

The intake prototype streams a local file into a quarantine directory, rejects files over 20 MB, checks the extension, declared MIME type, and file signature, and checks PNG/JPEG header dimensions against the 20-megapixel limit. It stores valid bytes under their SHA-256 hash and creates a separate document and queued job for each submission. Up to 20 files can be submitted through the library's batch method. A duplicate hash shares one stored object but never shares a job, review record, or approval.

The database and object store are local. Submission metadata and the queued job are committed in one SQLite transaction after the stored bytes pass a checksum check. A failed transaction can leave an unreferenced object file; a later reconciliation step will remove those orphans. Jobs use leases and incrementing fencing tokens, so an expired worker cannot publish a candidate after another worker has claimed the same job. Candidate insertion, rendered-page metadata, review readiness, and job completion share one transaction.

The parser image decodes and re-encodes each page, applies image EXIF rotation, runs English Tesseract, and emits numbered PNGs with canonical OCR spans. PDFs are limited to ten pages. The worker starts it without network, with a read-only root, no capabilities, an unprivileged user, bounded CPU/memory/PIDs, a read-only original mount, and a scratch output mount. The host checks exact output filenames, regular files, sizes, checksums, page dimensions, page numbers, and span geometry before extraction. A failed parser job can be retried without inheriting review decisions. Docker and the image are required; the daemon was unavailable for a real-container run on this host.

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

The model receives bounded OCR spans, not original files or review credentials. Each page gets one extraction request and at most one schema-repair request. Unknown evidence IDs and value mismatches become blocking review issues; unusable output, context overflow, an API rejection, or an unavailable endpoint fails the job explicitly. The endpoint must be a numeric loopback address. The saved model ID is the server's declared alias, not a verified weights hash. The model profile is unmeasured on a real runtime and does not yet use Docling layout output.

To score the model on the same 12 self-authored development images as the OCR rules baseline, run:

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-development-model --model-endpoint http://127.0.0.1:8080 --model-id local-invoice
```

This writes `artifacts/development-model.json`. All 12 images remain in the denominator, including OCR and model failures. OCR and model time are recorded separately. This is development data used for engineering choices, not a held-out quality claim.

## Current boundary

The default is a narrow English OCR baseline. The parser now handles PDFs of up to ten pages, and the host merges page-level extraction into one record. It takes the first observed header value for each field and raises a blocking issue when later pages show a different labeled value. The model profile uses the same canonical spans and flags conflicting per-page model values; it is not yet a measured layout-model comparison. The container uses a versioned Python base and pinned Pillow, while Debian OCR/Poppler package versions are not yet locked. A real Docker execution, PDF/JPEG cases, resource-failure drills, and a security review remain before claiming an arbitrary-document workflow. The CLI still assumes a trusted local operator and has no authenticated browser interface.
