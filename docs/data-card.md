# Document Intelligence Workbench data card

The project evaluates fictional invoices separately from public receipt images. Synthetic held-out families test variation within an author-created generator; they do not establish real vendor accuracy. Receipt labels use their own adapter and scoring rules. The [complete paired evaluations](heldout-model-comparison.md) account for all 180 test invoices and 100 official test receipts, including four invoice and five receipt model failures.

## Self-authored invoice corpus

The [committed manifest](../datasets/invoices-v1/manifest.json) binds all assets and labels by SHA-256. [Corpus verification](invoice-corpus.md) checks images, PDFs, geometry, arithmetic, treatments, parent links, and split isolation.

| Property | Frozen corpus |
|---|---|
| Documents | 540, with 180 development / 180 calibration / 180 test |
| Layout families | 18, with six exclusive families per split |
| Family composition | 25 base documents and five degraded derivatives |
| Multi-page documents | 90 two-page PDFs with committed PNG previews |
| Degraded documents | 90: blur, contrast reduction, JPEG compression, two-degree skew, or sideways rotation |
| Printed total conflicts | 72, labeled as document inconsistencies |
| Ambiguous dates | 42, explicitly excluded from eligible date scoring |

Names, invoice numbers, amounts, and rows are fictional, authored for this project. The generator supplies labels and source geometry. It has a limited English vocabulary and controlled formatting; shared generation patterns can make test families easier than unfamiliar documents. Derivatives stay with their parents, and families do not cross splits. Development informed extraction changes; calibration rejected the spatial candidate. Test settings were frozen before extraction, and the original test predictions remain unchanged after an explicitly recorded bootstrap reporting correction.

The [default test run](invoice-heldout-run.md) used verified PNG previews, including previews of PDF pages. It measured extraction after host OCR, rather than production PDF parsing. Every scheduled document is scored, including failures and unmatched rows. The paired model run performs fresh inference on the exact saved OCR inputs; this controls OCR differences but cannot measure whether a model compensates for a different parser or OCR engine. Rules/model header macro F1 is 0.9981/0.9766, and exact-row F1 is 0.9423/0.7985. The model was not promoted. Its prompt/profile selection used the earlier 12-document development study without a full-corpus model calibration run; the rules test report was already published before the model freeze.

## CORD receipts

The [pinned dataset configuration](../config/cord-v2.json) records `naver-clova-ix/cord-v2`, revision `7f0115a4b758a71d6473b8d085751692da2fef98`, original Parquet checksums, and CC BY 4.0 attribution: Park et al., *CORD: A Consolidated Receipt Dataset for Post-OCR Parsing*, Document Intelligence Workshop at NeurIPS, 2019, NAVER CLOVA. This repository records the adapter and predictions; source receipt data are explicitly downloaded into ignored `artifacts/`.

The adapter imports the official 100 validation and 100 test receipts. The 800 official training rows are not imported. Project validation serves as development evidence; the initial model smoke uses the first 12 validation rows in source order. Test settings must be sealed before all 100 test receipts can be scored. Missing labels are masked, rather than treated as empty values. Top-level menu items and released receipt amount fields are scored; modifiers/submenus, void items, payment information, and store labels are excluded.

The [100-receipt rules validation report](../evals/cord-validation-2026-10-03/ocr-rules/report.json) processes all receipts but achieves total F1 0.1273 and eligible exact-row F1 0.0774. The [completed test comparison](../evals/cord-heldout-2026-10-03/comparison.json) records rules/model total F1 0.2435/0.1651 and eligible exact-row F1 0.0957/0.0204. Rules produce 100/100 records and the model 95/100; the failed model cases remain in the score. Both variants show a substantial domain gap for the English OCR prototype. These are separate receipt diagnostics, not invoice quality or full CORD hierarchical parsing results. CORD is public; exposure in model pretraining has not been audited, so held-out project tuning does not imply model pretraining independence.

## Scoring and claim boundaries

Invoice headers use normalized exact matching, with decimal equivalence for amounts and explicit missing-versus-zero handling. The frozen maximum-weight row matcher assigns duplicates independently of source order and reports unmatched rows. Fuzzy description matching and exact-row/amount correctness are reported separately. Receipt scoring applies its released-label masks. The [scoring contract](release-evaluation.md) defines the full eligibility, matching, failure, and compatibility rules.

Paired invoice uncertainty groups derivatives with parents within the six fixed test families. Receipt intervals resample within their official split. Neither interval represents arbitrary unseen vendors. Valid evidence IDs and substring alignment are reported separately from semantic attribution; label boxes do not prove every model-selected region is correct. Timing on saved OCR is stage timing, not end-to-end upload or human review latency.

The six-case [author review pilot](review-pilot.md) uses declared development cases and recorded default suggestions. It measures assisted review effort and final eligible quality only. Author familiarity, fixed selection, activity heuristics, unauthenticated labels, and lack of a manual-entry comparison limit interpretation. Automated browser clicks are excluded from human results. No time-saved, productivity, real-invoice accuracy, or unattended-processing claim is supported.
