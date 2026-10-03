# Live parser verification — October 3, 2026

The later [checkpoint recovery run](parser-recovery.md) extends this suite to 12 passing checks with abrupt host worker exit and model transport failure evidence. The original ten-check results below remain historical.

The real Docker upload-to-review boundary now passes ten integration checks on this Mac. The [recorded evidence](../evals/parser-verification-2026-10-03/report.json) identifies the image, host architecture, Docker versions, source and fixture hashes, each check's outcome and duration, runtime restrictions, package versions, and OCR asset hashes. This closes the earlier missing-container-execution milestone. It uses fictional fixtures and `ocr_rules`; it does not establish arbitrary-invoice quality or complete the architecture plan's release criteria.

| Check | Observed result |
|---|---|
| Clean PNG upload | Review-ready, correct invoice number and row amount, no validation issues |
| Two-page PDF upload | Both pages rendered; extracted row cites page 2 |
| JPEG with EXIF rotation | Re-encoded upright at 1800 × 2200; correct invoice number, no issues |
| Malformed PDF | `PDF_INFO_FAILED`; no candidate or imported pages |
| Eleven-page PDF | `PDF_PAGE_LIMIT`; no candidate or imported pages |
| Truncated PNG with valid header | `IMAGE_DECODE_FAILED`; no candidate or imported pages |
| Missing image and retry | `PARSER_IMAGE_MISSING`, then review-ready on attempt 2 after retry |
| Duplicate uploads | Shared stored original, separate candidates and approvals; first approval cannot authorize second export |
| Conflicting total, review, export, reopen | Unresolved conflict blocks approval; explicit fixture acknowledgment permits JSON/CSV; reopening preserves idempotent exports; a new edit invalidates current approval while the historical export remains intact |
| Container policy and runtime | Live configuration inspection and an executed probe confirm the restrictions below; image source hashes match this checkout |

The fixture reviewer is a test actor. Acknowledgment and approval in this suite do not approve a user's documents. Reopening the SQLite store tests persisted state between connections, rather than killing a worker or recovering from a machine crash. Successful and rejected processing checks also verify removal of their quarantine scratch files.

## Runtime identity and isolation

Docker Engine was 29.7.2. The locally built Linux/arm64 image ID was `sha256:c18a3a96d002ebf36ea22be9602f27de0f2decbd0744d3060aee98d559c531a0`. It contains Python 3.12.12, Pillow 11.3.0, Tesseract `5.3.0-2`, Poppler utilities `22.12.0-2+deb12u3`, and English/orientation data `1:4.1.0-2`. Host benchmark runs use a different Tesseract version; their quality numbers must not be attributed to this image.

The worker and runtime probe share one container-command builder. The probe inspects the created container and then runs under that configuration: UID/GID 65534, all capabilities dropped, effective capability mask zero, `NoNewPrivs: 1`, read-only root, read-only original mount, and only an output directory plus a 64 MB `/tmp` writable. Network mode is `none`; loopback is the only interface with the UP flag and the IPv4 route table is empty. Docker Desktop also exposes inactive tunnel interfaces, which do not imply an available route. The inspected limits are 1 GiB RAM, two CPUs, and 64 PIDs. These verify configured limits, not deliberate OOM/PID/CPU exhaustion behavior or protection against a container-engine vulnerability.

The production worker now uses `--pull never`. A missing local image fails explicitly without an implicit registry download; `make parser-build` is the explicit setup step. The image is locally built and has no registry digest. Debian repositories and the Python base tag remain moving build inputs, so the report records the actual installed versions and image ID without claiming an identical future rebuild. The live probe rejects a stale image whose Python source differs from the checkout.

## Reproduce

Start Docker Desktop, then run from the repository:

```sh
make parser-build
make parser-smoke
make parser-verify OUTPUT=artifacts/parser-verification-fresh.json
```

`parser-smoke` prints the checks. `parser-verify` runs those same checks and writes evidence to a **new** path, exiting 2 on failures, skips, missing dependencies, changed source files, or an image change during the run. Existing reports are never overwritten. It uses only self-authored fixtures; the JPEG is generated with the image's Pillow so the host needs no additional Python dependency. Docker containers, uploads, and exports are temporary, while the requested report persists. The report records local observations; it is not a signed attestation or a standalone offline evidence verifier.

Alongside this run, `make test` passed 120 deterministic checks and `make smoke-ocr` passed four host OCR checks. Container verification remains an explicit local command; the current CI workflow runs deterministic checks only.

## Remaining release work

Genuine scans, encrypted-PDF rejection, deliberate parser timeout/OOM, interruption during parsing/export, Linux bind-mount permissions, a real model through the container/browser path, and a recorded browser demo still need live evidence. A host worker exit after a verified parse now has [checkpoint recovery evidence](parser-recovery.md); this is a narrower claim than recovery at every stage or after machine power loss. The calibration row-recall gap needs development work before freezing release settings. The held-out invoice split and CORD receipt evaluation remain unscored, and no human review-time pilot has been run. The first release's claims must stay within the workflow, data, and hardware actually measured.
