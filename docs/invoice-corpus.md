# Self-authored invoice corpus v1

The [frozen manifest](../datasets/invoices-v1/manifest.json) contains 540 fictional invoices. Six layout families and 180 documents belong to each of development, calibration, and test. Each family has 25 base documents and five degraded derivatives of its first five bases. Vendor names, layout families, and identical asset bytes stay within one split. The corpus contains 90 two-page PDFs with repeated table headers and 450 single-page PNGs; the PDFs also have numbered PNG page previews for geometry checks. There are 90 degraded documents, 72 intentionally conflicting totals, and 42 ambiguous printed dates.

The generator is [generate_invoice_corpus.py](../scripts/generate_invoice_corpus.py). It uses only fictional names and self-drawn page elements. The committed files are the evaluation inputs: regenerating on another font or Pillow build can change pixels. The manifest records the generator version, seed, Pillow version, and font SHA-256. It also stores field and row source boxes in normalized top-left page coordinates, source and page hashes, split, layout family, treatment, parent ID, and expected issue codes. No gold labels are provided to the extractor.

## Verify and use

```sh
PYTHONPATH=src python3.12 -m docwork.cli eval-verify-corpus
```

This checks every asset hash; split, vendor, and byte-content isolation; exact 180/180/180 and family counts; derivative links; row and total arithmetic; page preview dimensions; and field/row boxes. The current manifest SHA-256 is `a39a99cf5543888ebd7b9af5a42a18ac82e4ff6baf2534b8cd6d16ca9c76f61c`.

The renderer requires Pillow 9.1.1 and the recorded Arial font. On the generation host, the installed Pillow binary was x86_64, so it ran with `arch -x86_64 python3 scripts/generate_invoice_corpus.py`. PDF creation and modification dates are fixed to avoid time-dependent hashes. Representative single-page, two-page, and rotated documents reproduced their committed hashes on a second render. The command refuses to overwrite an existing corpus directory. For a new version, render to a new `--output-dir`, inspect it, then freeze it with a new dataset ID. The committed corpus can be verified and scored with Python 3.12 without Pillow.

Before freezing, the author visually inspected `inv-f01-01`, `inv-f04-04`, both pages of `inv-f06-01`, `inv-f07-01`, `inv-f13-01`, `inv-f18-30` page 2, and rotated `inv-f01-30`. This is a fixed sample, not independent label review. The verifier checks geometry bounds and image sizes but cannot prove that every box tightly covers its printed value. A real parser and OCR run on the PDF cases remains necessary.

## Label and scoring policy

Visible optional fields can be absent; missing is distinct from printed zero. Rows preserve duplicates and source order. A `TOTAL_MISMATCH` label means the printed total exceeds the displayed arithmetic by exactly 5.00; all five arithmetic components are printed on those documents, so the check is possible. For the 42 `AMBIGUOUS_DATE` cases, the printed issue date uses a slash format that does not uniquely establish month/day order. The generator's intended ISO date remains in the manifest for traceability, but `field_exclusions.issue_date` removes it from exact date and all-required-field scoring. The issue itself remains eligible for validation-issue evaluation.

This is synthetic data created by the same author as the pipeline. Its layouts and degradation rules do not establish performance on arbitrary real invoices. Development families may guide extraction changes; calibration families may guide thresholds. A [calibration OCR/rules run](invoice-calibration-run.md) has been scored without changing the extractor or triage weights. Hold test families unchanged until the extractor, scorer, and settings are frozen. The [release scoring contract](release-evaluation.md) defines how saved predictions are measured; there is no held-out quality report yet.
