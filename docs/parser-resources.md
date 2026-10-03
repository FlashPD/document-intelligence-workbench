# Parser resource failures — October 3, 2026

The [live verification report](../evals/parser-resources-2026-10-03/report.json) records 16 passing Docker checks, including two resource faults followed by successful retries of the unchanged source through the normal parser. The deterministic suite passes 210 tests. The report identifies the parser image, Docker and host versions, source hashes, actual Docker lifecycle events, and per-check durations.

## What was exercised

| Fault | Injection and independent evidence | Outcome |
|---|---|---|
| Parser deadline | A Python sleep probe inside the parser image, with an eight-second supervisor deadline; a marker proves the container started, and Docker records kill and destroy events | `PARSER_TIMEOUT`; the owned container and scratch directory are removed |
| Cgroup memory exhaustion | A Python allocation probe with a 64 MiB memory limit and zero swap; the probe records the effective cgroup limits, and Docker records an `oom` event and exit code 137 | `PARSER_KILLED`; Docker automatically removes the container, and the worker removes scratch files |

Both probes run through the real `_docker_run` supervisor and durable processing queue. The test changes the container entrypoint to a known fault probe; the original remains a read-only mounted fictional invoice. Network denial, the read-only root, unprivileged user, capability/PID/CPU restrictions, and output mount follow the production command. Production retains its 1 GiB memory limit, Docker's default swap setting, fixed parser entrypoint, and 600-second deadline.

Each failed attempt leaves revision zero, no imported pages, no parser checkpoint, and no exportable record. The source checksum is unchanged. A reviewer explicitly retries the job; the fencing token advances, the real parser processes the same invoice, and attempt two reaches `REVIEW_READY` with invoice number `AST-1001`. It has no approval. Existing checks also verify version-bound exports, duplicate approval isolation, checkpoint reuse, abrupt host worker recovery, and portable backup restoration.

These probes establish fault handling under the stated test settings. They do not measure natural invoice memory requirements, prove that a particular invoice exceeds the production limit, exercise machine power loss, or establish recovery at every interruption point. Docker's event history is required for the OOM assertion; exit 137 alone is insufficient. The live suite fails if a probe does not actually start, a relevant event is missing, cleanup cannot be confirmed, or the real-parser retry fails.

## Error codes and recovery

| Code | Meaning | Action |
|---|---|---|
| `PARSER_TIMEOUT` | The deadline expired and the owned container was removed or had already disappeared | Inspect the document and local runtime before an explicit retry |
| `PARSER_KILLED` | The container exited with code 137; OOM and external SIGKILL are both possible | Inspect Docker resource/events evidence; the product does not claim a confirmed OOM |
| `PARSER_CLEANUP_FAILED` | The deadline expired, but forced removal failed or exceeded its own ten-second deadline | Restore Docker connectivity and verify that this job's container is stopped before retrying |

An ordinary parser rejection retains its specific code, such as `PDF_PAGE_LIMIT` or `IMAGE_DECODE_FAILED`. Unrecognized nonzero exits remain `PARSER_FAILED`. Failures stay in the queue and require an explicit retry; no candidate is silently substituted.

The timeout cleanup names only the claimed container: `docwork-<first 16 characters of job ID>-<fencing token>`. Obtain those values from `docwork intake status <document_id>` or the document status API. If cleanup fails, inspect that exact container with `docker container inspect <container_name>`. Cleanup errors are now persisted rather than escaping as an unhandled subprocess timeout or being ignored.

The [queue-release refresh](../evals/queue-release-parser-2026-10-03/report.json) repeats all 16 checks against the rebuilt current image after the replay and queue-benchmark additions. It passes with unchanged inputs; the original reports remain historical measurements. The separate [20-document queue](parser-queue.md) measures ordinary serial processing rather than fault probes. The current deterministic suite passes 263 tests.

## Reproduce

Start Docker Desktop and build the current image. From the repository root:

```sh
make parser-build
PYTHONPATH=src python3.12 -m unittest discover -s tests -p 'container_resources.py' -v
make parser-verify OUTPUT=artifacts/parser-resources-fresh.json
```

The focused command runs both probes and their real-parser retries. The full command adds all existing Docker workflow/recovery/restoration checks and saves a report at a new path. A skipped or failed check, changed source/image, or existing output path cannot publish passing evidence. No model inference or dataset download is needed. Temporary databases, probe output, and exports are removed after the checks; the saved JSON report remains.
