# Bounded document intake

The intake prototype streams a local file into a quarantine directory, rejects files over 20 MB, checks the extension, declared MIME type, and file signature, and checks PNG/JPEG header dimensions against the 20-megapixel limit. It stores valid bytes under their SHA-256 hash and creates a separate document and queued job for each submission. Up to 20 files can be submitted through the library's batch method. A duplicate hash shares one stored object but never shares a job, review record, or approval.

The database and object store are local. Submission metadata and the queued job are committed in one SQLite transaction after the stored bytes pass a checksum check. A failed transaction can leave an unreferenced object file; a later reconciliation step will remove those orphans. Jobs use leases and incrementing fencing tokens, so an expired worker cannot publish a candidate after another worker has claimed the same job. Candidate insertion, rendered-page metadata, review readiness, and job completion share one transaction.

The parser image decodes and re-encodes one page, applies image EXIF rotation, runs English Tesseract, and emits a PNG with canonical OCR spans. The worker starts it without network, with a read-only root, no capabilities, an unprivileged user, bounded CPU/memory/PIDs, a read-only original mount, and a scratch output mount. The host checks exact output filenames, regular files, sizes, checksums, page dimensions, and span geometry before extraction. A failed parser job can be retried without inheriting review decisions. Docker and the image are required; the daemon was unavailable for a real-container run on this host.

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
PYTHONPATH=src python3.12 -m docwork.cli review show YOUR_DOCUMENT_ID
```

`process-one` claims one queued job. Run it again for the next document. The `page` command reports the checked page PNG path for evidence inspection; review, approval, and export commands are documented in [review.md](review.md). If the job fails, inspect its `error_code` with `intake status`, then use `intake retry YOUR_DOCUMENT_ID` after fixing the cause.

## Current boundary

This is a single-page, English OCR baseline. Intake permits PDFs of up to ten pages, but the parser marks a multi-page job `MULTIPAGE_NOT_SUPPORTED` until the canonical page and review schema support multiple pages. The container uses a versioned Python base and pinned Pillow, while Debian OCR/Poppler package versions are not yet locked. A real Docker execution, PDF/JPEG cases, resource-failure drills, and a security review remain before claiming an arbitrary-document workflow. The CLI still assumes a trusted local operator and has no authenticated browser interface.
