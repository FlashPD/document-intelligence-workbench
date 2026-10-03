# Phase 0 feasibility log

Recorded September 30, 2026. This is an initial development spike, not a completed Phase 0 exit.

## Local preflight

| Item | Observed |
|---|---|
| Host | MacBook Pro (MacBookPro17,1), Apple M1, 16 GB RAM |
| OS | macOS 26.6.2, arm64 |
| Free disk before development run | About 43 GiB |
| Python used | 3.12.12 |
| OCR | Tesseract 5.4.1, `eng`, `osd`, `snum` installed |
| Docker | CLI installed; daemon unavailable during this run |
| Local language model | No model configured or tested in this spike |
| Optional PC | OS, driver, runtime, and model fit not yet recorded |

The generated PNGs are self-authored. Their hashes are `925a3b1858f6b322330ba16148cbd9e498117e89979182fe897af60a90e1a106` (`clean.png`) and `13f2eef374c009a698a31e17639417d0b970df0feca6329e1053a59e8be331d3` (`conflicting-total.png`). The optional fixture generator uses Pillow and a macOS system font; the PNGs are committed, so running the spike does not require Pillow.

## What was measured

`make demo-baseline` ran fresh OCR and deterministic rules on each one-page PNG. Both produced ten header fields and one row with span IDs and normalized line boxes. The clean invoice produced no validation issues. The conflicting invoice retained observed total `275.00` and raised `TOTAL_MISMATCH` against computed `270.00`. OCR took 0.509 seconds for the clean image and 0.521 seconds for the conflicting image on this run. These are two same-layout fixtures created alongside the parser; they do not establish extraction accuracy, robustness, or generalization.

The deterministic contract checks pass without OCR. Three separate OCR smoke checks pass against the committed fixtures. Results are local to this hardware and Tesseract installation.

A separate [12-document development report](development-baseline.md) now measures the conventional baseline across six author-created layout families. Its manifest contains all expected fields, rows, image hashes, and source boxes; the runner checks crop and rotation transforms before scoring. The development set is used to change the baseline and is not held-out evidence.

## Contract and current limits

- Invoice header fields follow the plan's ten-field set; required fields for this spike are supplier, invoice number, issue date, currency, and total. Missing values have explicit reasons, and monetary values are strings parsed with `Decimal` for checks.
- Each observed value cites an OCR line span ID. The validator checks ID existence and case-insensitive substring alignment. Geometry is a normalized top-left line rectangle on the image supplied to OCR. This does not yet prove field-level semantic support or provide a precise box around each value within a line.
- The baseline recognizes explicit USD, ISO dates, dot-decimal amounts, and a simple single-line item pattern. Supplier selection assumes the first non-title line. It is deliberately a narrow conventional baseline, not an invoice parser for arbitrary layouts.
- Total reconciliation assumes `subtotal + tax - discount + shipping`, with all components explicitly observed and a 0.01 tolerance. Missing components leave the total check incomplete; totals are never silently changed.
- The CLI only reads PNGs under this repository's `samples/` directory. It does not accept user uploads or process untrusted originals. Crop and right-angle rotation transforms are implemented and tested for labels, but the OCR adapter does not yet correct an input page's orientation. Docker parser isolation, PDF handling, tables, multiple pages, and crash recovery remain to be built.

## Next implementation milestone

1. Add a genuine scanned development invoice. The current blur/low-contrast image is synthetic degradation, and the sideways image intentionally reveals the missing orientation correction.
2. Bring up the fixed, network-denied parser container and normalize its page/spans/table output. Verify orientation, geometry, and resource-limit behavior before enabling arbitrary uploads.
3. Pin the Python/OCR/layout runtime and explicit model assets; test one local structured extractor and compare it with this baseline on the same development set. Record peak memory and per-stage latency.
4. Add persistence, review revisions, approvals, and immutable export as the first product slice after the feasibility work.

The acceptance targets and larger evaluation plan remain in the architecture document. No held-out quality or review-time claim is made here.

## October 2 update: real model feasibility

The [recorded local-model comparison](development-baseline.md#span-invoice-v2-follow-up--october-2-2026) verifies pinned Qwen3 4B Instruct and llama.cpp assets, real Apple M1 Metal offload, all 12 development predictions, per-stage timing, and sampled process memory. The revised prompt reaches 17/18 line totals and 108/120 headers on this tuned development set; the deterministic baseline remains the default pending held-out evidence and latency work. An offline verifier reconstructs the saved scores and checks the evidence bundle.

Persistence, review/export, queued intake, and multi-page parser integration have also been implemented since the original September 30 spike. The earlier list describes the initial state. Phase 0 is still not fully exited: genuine scan evidence, real container verification, and pinned OCR assets remain open, and the current local-model extraction quality needs improvement. No held-out or human-time claim is made.

## October 3 update: live Docker boundary

The [parser verification report](parser-verification.md) records ten passing live-container checks on this host, including two-page PDF processing, JPEG EXIF rotation, rejected inputs, runtime restrictions, retry, independent duplicate approvals, and review/export across reopening the SQLite store. Earlier Docker-unavailable entries describe their original runs. Genuine scans, pinned build inputs, resource-failure/recovery drills, and broader extraction evidence remain open; this update does not mark the whole project release-ready.
