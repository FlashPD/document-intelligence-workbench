# Invoice corpus development baseline — October 2, 2026

The first full split run scores all 180 development invoices in the [frozen synthetic corpus](invoice-corpus.md). It uses host Tesseract 5.4.1 on the corpus's hash-verified PNG page previews, then the deterministic `ocr_rules` extractor and [release scorer](release-evaluation.md). Python was 3.12.12 on the Apple M1 / 16 GB Mac. Multi-page PDF invoices contribute both preview pages, but this run does **not** exercise PDF rendering, container isolation, or the upload worker. The test and calibration splits were not run.

The [v0.2 evidence](../evals/invoice-development-2026-10-02/ocr-rules-v0.2/report.json) exposed an OCR-order bug: Tesseract frequently produced a clipped glyph at the upper-left page corner before the printed supplier name. Version `ocr-rules-v0.3` ignores that corner span when choosing the supplier. The [v0.3 evidence](../evals/invoice-development-2026-10-02/ocr-rules-v0.3/report.json) is a fresh run on the same development images. Both bundles include all 180 original predictions, source hashes, timings, issue codes, and scored reports.

| Measure | `ocr-rules-v0.2` | `ocr-rules-v0.3` |
|---|---:|---:|
| Processed documents | 180/180 | 180/180 |
| Header macro F1, ten fields | 0.9093 | 0.9738 |
| Supplier-name F1 | 0.3222 | 0.9667 |
| All required fields exact, eligible documents | 43/166 | 145/166 |
| Exact line-item F1 | 0.9688 | 0.9688 |
| Row detection F1 | 0.9727 | 0.9727 |
| Sum of serial OCR times | 105.588 s | 107.498 s |

The 14 documents with an ambiguous printed issue date are excluded from the all-required-exact denominator by the frozen label policy. The scorer retains every other document and every failed extraction in its denominators. Numeric values use Decimal equivalence; row matching is order independent and preserves duplicates. The elapsed sums are serial stage timings, not controlled throughput or a speed comparison.

### Remaining errors

With Tesseract's default PSM 3, version v0.3 recovers 174/180 supplier names. All six remaining supplier errors are the deliberately rotated 90-degree derivatives. Those six documents also yield no exact rows and no required-field-complete records. The other prominent error is the multi-page continuation family: Tesseract often reads `F06` invoice identifiers as `FO6` or `FO06`. The extractor preserves the observed OCR text rather than guessing the intended digit. Invoice-number exactness is 159/180 overall, including the six rotated failures. Exact row matching finds 496 of 522 gold rows, with six predicted rows not exact.

On the development labels, validation flags all 24 injected `TOTAL_MISMATCH` cases with no false total-mismatch flags, and all 14 ambiguous-date cases with no false ambiguous-date flags. This is a narrow synthetic diagnostic. It does not measure whether validation detects arbitrary real-world errors. `TOTAL_NOT_CHECKED` appears on 72 documents because at least one arithmetic component is absent or unreadable; it must not be counted as a successful check.

### Reproduce and audit

Use a new output directory for a fresh run. An interrupted run can continue with `--resume` if the manifest and runtime identity still match.

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-run-invoices \
  --ocr-psm 3 --output-dir artifacts/invoice-development-psm3-fresh
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-invoice-run \
  evals/invoice-development-2026-10-02/ocr-rules-v0.3
```

The verifier rechecks every asset hash and prediction source hash, rescoring all 180 documents and checking the saved report, prediction hashes, timings, and issue counts. The committed manifest SHA-256 is `a39a99cf5543888ebd7b9af5a42a18ac82e4ff6baf2534b8cd6d16ca9c76f61c`. A live rerun also requires local Tesseract English OCR. The recorded evidence is development data from the same author as the extractor; it does not establish held-out or real-invoice performance. A later release run must use the isolated PDF parser and freeze choices before scoring the test families.

## Orientation-aware OCR follow-up — October 3, 2026

The [PSM 1 run](../evals/invoice-development-2026-10-03/ocr-rules-v0.3-psm1/report.json) repeats all 180 development documents with Tesseract's automatic orientation and page segmentation. Rules, corpus, scorer, and serial hardware are the same as the v0.3 PSM 3 run above; only the OCR mode changes. Every prediction is saved under the linked run directory and passes `eval-verify-invoice-run`.

| Measure | PSM 3 | PSM 1 |
|---|---:|---:|
| Processed documents | 180/180 | 180/180 |
| Header macro F1 | 0.9738 | 0.9911 |
| All required fields exact, eligible documents | 145/166 | 150/166 |
| Exact line-item F1 | 0.9688 | 0.9817 |
| Exact rows on six rotated documents | 0/16 | 13/16 |
| Sum of serial OCR times | 107.498 s | 136.929 s |

The five additional required-field-complete documents are rotated derivatives. No previously correct required-field document or exact row regressed in this paired development run. One rotated document still has a wrong invoice number, and three rotated rows are not exact. The continuation-family `F06`/`FO6` recognition error remains. This is development tuning on six related rotated examples, not a guarantee that orientation detection works on arbitrary scans. Tesseract PSM 1 is now the default for new OCR jobs; the parser image/version moved to `docwork-parser:v3` / `container-tesseract-v3`. A PSM 1 failure on sparse text falls back to PSM 3. The saved run had no such OCR failures.

The parser's page raster remains in its decoded orientation while PSM 1 recognizes sideways text and returns boxes on that raster. A reviewer can inspect source overlays, but the page may still appear sideways. Automatic display rotation and a real Docker/PDF run remain open. The Docker daemon was unavailable on this host; a direct trusted-PNG parser invocation and the rotated OCR smoke check passed, but they do not establish container isolation or PDF behavior.

For a fresh run with the current OCR default:

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-run-invoices \
  --output-dir artifacts/invoice-development-orientation-fresh
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-invoice-run \
  evals/invoice-development-2026-10-03/ocr-rules-v0.3-psm1
```
