# Bounded document intake

The intake prototype streams a local file into a quarantine directory, rejects files over 20 MB, checks the extension, declared MIME type, and file signature, and checks PNG/JPEG header dimensions against the 20-megapixel limit. It stores valid bytes under their SHA-256 hash and creates a separate document and queued job for each submission. Up to 20 files can be submitted through the library's batch method. A duplicate hash shares one stored object but never shares a job, review record, or approval.

The database and object store are local. Submission metadata and the queued job are committed in one SQLite transaction after the stored bytes pass a checksum check. A failed transaction can leave an unreferenced object file; a later reconciliation step will remove those orphans. Jobs use leases and incrementing fencing tokens, so an expired worker cannot publish a candidate after another worker has claimed the same job. Candidate insertion, review readiness, and job completion share one transaction.

## Try the current CLI

```sh
PYTHONPATH=src python3.12 -m docwork.cli intake submit samples/clean.png --mime image/png
```

The response contains a `document_id`, `status: RECEIVED`, and a `QUEUED` job. By default, the database is `artifacts/review.sqlite` and originals are below `artifacts/intake/objects/`. Check the state with:

```sh
PYTHONPATH=src python3.12 -m docwork.cli intake status YOUR_DOCUMENT_ID
```

The CLI does not start a worker. The Docker daemon was unavailable during this implementation, so an isolated PDF/image parser and its output validation still need to be built and exercised before queued documents can advance to review. Header checks do not prove that a PDF is unencrypted or well formed, or that image pixels decode safely. The existing `review seed` command continues to OCR only committed, trusted fixtures.
