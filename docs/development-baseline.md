# Development baseline: `ocr-rules-v0.2`

Measured September 30, 2026 on the Apple M1 MacBook Pro with 16 GB RAM, Python 3.12.12, and Tesseract 5.4.1 English OCR. The [development manifest](../datasets/development-v0.json) SHA-256 is `f49e349a900e3d09612d00179027d10730f91786a059707f77ebb57f9589e60d`. The [recorded run](../evals/development-baseline-v0.2.json) contains per-document scores. Reproduce it with `make eval-development`; the fresh result is written to `artifacts/development-baseline.json`.

This is author-created development data: 12 fictional, one-page PNG invoices from six layout families, two pages per family. One page has a deliberately inconsistent total; one has synthetic blur and low contrast; one is cropped and rotated. Three representative pages were visually inspected, and the manifest's box transforms were checked mechanically. It is not a real scanned corpus, and these layouts were used to tune the baseline.

| Diagnostic | Result |
|---|---:|
| Documents processed | 12 / 12 |
| Exact header values | 100 / 120 |
| Exact required header values | 48 / 60 |
| All five required fields correct | 8 / 12 documents |
| Exact line totals, matched by source order | 17 / 18 |
| Correct row count | 11 / 12 documents |
| Mean header source-box IoU, all labeled slots | 0.381 |
| Injected total conflicts detected | 1 / 1 |
| False total-conflict warnings | 0 / 11 |
| Sum of OCR time across 12 serial pages | About 5.3 seconds |

Exact header scoring compares canonical strings, with no fuzzy normalization. Missing predictions and processing failures count as errors. Line-total matching uses source order for this small diagnostic; the planned release evaluator will use a frozen row matcher. Box IoU compares the whole OCR line rectangle with the generated value-only box, using zero for missing evidence. A span ID or overlap alone does not prove semantic support.

The first regex-only pass found 4/18 line totals because Tesseract separated many rightmost amounts into their own spans. A development change joined spans on the same page row using vertical position, yielding 17/18. The remaining missing row is on the sideways page; the current OCR adapter does not rotate it upright. The two-column family also omits colons and merges header text across columns, leaving required fields missing. The low-contrast page renders dates as `MM/DD/YYYY`, which the strict parser flags as ambiguous rather than silently guessing a locale.

These measurements show feasibility and specific failure modes only. They do not establish accuracy on held-out synthetic invoices, real invoices, or CORD receipts. No local language model, isolated parser, review workflow, or human-time comparison has been measured yet.
