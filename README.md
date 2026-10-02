# Local Document Intelligence & Review Workbench

A local invoice workbench under development. The intended product extracts structured fields and line items, links each suggestion to page evidence, flags conflicts, and exports only after human review. See the [architecture plan](arch_plan/document-intelligence-workbench-plan.md).

## Current status

Phase 0 has a runnable `ocr_rules` spike for two demo PNGs and a separate 12-document, six-layout development set. It normalizes OCR line boxes, extracts header fields and line items, checks source references and arithmetic, and preserves an observed total when it conflicts with a computed total. A local [review and export slice](docs/review.md) stores candidate revisions in SQLite, records issue decisions, binds approvals to exact revisions, and writes JSON/CSV exports. [Bounded intake and a container worker](docs/intake.md) connect stored originals to reviewable image and multi-page PDF candidates. The [browser prototype](docs/browser.md) lets a reviewer switch pages, inspect OCR evidence, correct fields, acknowledge issues, approve revisions, and download exports. [Phase 0 notes](docs/phase0.md) record the preflight; the [development report](docs/development-baseline.md) records the measured baseline and its failure cases.

The fixture OCR command remains restricted to trusted repository samples. Uploaded originals are processed only by the fixed, network-denied Docker parser image. The worker supports up to ten PDF pages and uses Tesseract plus the deterministic baseline, with page-specific evidence in one invoice record. Processing leases renew during long parser and model calls; fenced completion and failure prevent a stale worker from changing a reclaimed job. An experimental `span_llm` profile sends canonical OCR spans and source boxes to a loopback model server. A [pinned Qwen3 4B / llama.cpp evaluation](docs/development-baseline.md#span-invoice-v2-follow-up--october-2-2026) measures all 12 development documents, with saved predictions, GPU-offload evidence, timings, and sampled process memory. The revised prompt passes the paired development gate against `ocr_rules` (108/120 exact headers and 17/18 exact line totals), though inference takes about 86 seconds per page and this tuned set cannot establish held-out quality. `ocr_rules` remains the default. The browser is a loopback-only prototype with an ephemeral session token, not an authenticated multi-user application. The Docker parser has not been run on this host because the Docker daemon is unavailable; its host-side validation and job handoff are covered by deterministic tests.

`docwork intake reconcile` audits stored originals and page renders, and can prune aged unreferenced files after checking referenced artifacts. See [the intake guide](docs/intake.md#audit-stored-artifacts).

The [frozen invoice scoring contract](docs/release-evaluation.md) adds order-independent duplicate-row matching, split checks, and hash-linked reports for the planned larger corpus. It has not produced held-out quality results yet.

## Run the spike

On macOS with Python 3.12 and Tesseract with English language data:

```sh
make doctor
make test
make smoke-ocr
make demo-baseline
make eval-development
make dev
```

`make dev` prints a one-time browser URL. Seed the trusted samples in the browser for an OCR-backed review demo without Docker. For uploaded documents, start Docker Desktop and run `make parser-build`, then follow [the intake guide](docs/intake.md). Run `make parser-smoke` to exercise the real container.

`make demo-baseline` and `make eval-development` write fresh results to ignored `artifacts/`. The development command verifies every committed image hash and label transform before scoring all 12 pages. The deterministic contract suite runs without Tesseract; `smoke-ocr` exercises real OCR on the committed demo fixtures. No model or dataset download is performed by these commands.

The optional `eval-development-model` command scores a configured loopback model on the same development images and counts failures. See [the intake guide](docs/intake.md) for the local endpoint setup and command.

For a reproducible Apple Silicon run, `make models-fetch` explicitly downloads the pinned model/runtime assets; `make models-verify` verifies their hashes. `docwork eval-local-model --output-dir artifacts/model-run-001` manages a temporary authenticated server and saves the evidence. `docwork eval-verify evals/local-model-instruct-2026-10-02/model` checks the recorded run offline without model assets. Use `PYTHONPATH=src python3.12 -m docwork.cli` in place of `docwork` when running directly from the checkout. See the [pinned model runbook](docs/intake.md#pinned-local-model-evaluation).

`make eval-repeatability` runs the development OCR baseline twice and writes an audited JSON comparison and a standalone HTML report to `artifacts/development-comparison.*`. The comparison includes paired intervals, per-layout scores, and failure counts. `eval-compare` rejects incompatible or incomplete reports and returns a nonzero exit code for regressions or unusable evidence. See the [comparison guide and measured repeatability check](docs/development-baseline.md#development-comparison-gate). A passing development gate does not establish release readiness or held-out accuracy.

See [review workflow](docs/review.md) for a complete fixture-to-export example.
See [intake status](docs/intake.md) for the current queued-document boundary.
See [browser guide](docs/browser.md) for the visual review prototype.
