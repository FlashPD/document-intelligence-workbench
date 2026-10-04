# Production-input validation and semantic evidence protocol

B04/B05 use a separate bounded study without changing the original synthetic/CORD comparisons or author pilot. [The runner](../scripts/production_study.py) freezes originals, labels, permission/acquisition declarations, parser image, model profile and implementation before extraction. [The study library](../src/docwork/production_study.py) checks the schedule, original candidates, scoring, semantic sample and separate approved results. This is tooling for [G06/G11](v1-release-contract.md); neither gate is complete until its actual inputs and assessments exist.

## Input and label protocol

Production mode requires **at least eight cases, including two actual scanner captures**, collectively covering clean, degraded, rotated and multi-page originals. Each case can cover multiple treatments. The maximum is twenty cases under the product's 20 MiB/file and ten-page limits. Include English invoices with inspectable headers and ordered rows. A physical scan of a fictional invoice may measure capture robustness, but must retain its self-authored-content disclosure; it does not establish unfamiliar-vendor accuracy. Artificial blur, JPEG degradation and rotation are synthetic treatments, not scanner acquisition.

Before either extractor runs, record each original's SHA-256, source-family group, declared page count, capture kind, treatments, permission reference and redistribution decision. Actual scans need an acquisition reference (scanner/capture history or the dataset's documented provenance). Declare labels' author, source-inspection method, source hash and completion before predictions. These declarations are local attestations, not forensic proof of acquisition or independently verified annotation quality.

Label all ten headers and all five row fields using text/Decimal strings or null for absent values. Required headers that cannot be resolved need a named exclusion reason. Exclusions remain visible; the existing frozen scoring contract handles them. Inspect labels against the originals, retain printed inconsistent totals, and resolve uncertainty before freezing. Do not derive gold from either study extractor. Use source-family groups for related captures; this small diagnostic reports a declared sample and does not estimate unseen-vendor generalization.

A spec JSON contains `study_id` and ordered `documents`. Each document has:

```json
{
  "id": "capture-001",
  "asset": "inputs/capture-001.pdf",
  "sha256": "REPLACE_WITH_64_HEX_DIGITS",
  "family_group": "source-family-001",
  "capture_kind": "scanner",
  "capture_reference": "Describe the actual capture or documented dataset provenance",
  "page_count": 2,
  "treatments": ["degraded", "multi_page"],
  "permission": {
    "basis": "owner_permission",
    "reference": "Describe permission for this local study",
    "redistribution": false
  },
  "annotation": {
    "author": "Source inspector",
    "method": "source_inspection",
    "source_sha256": "REPLACE_WITH_THE_SAME_HASH",
    "before_predictions": true
  },
  "fields": {
    "supplier_name": "Printed supplier",
    "invoice_number": "PRINTED-ID",
    "issue_date": "2026-09-01",
    "due_date": null,
    "currency": "USD",
    "subtotal": "10.00",
    "tax": null,
    "discount": null,
    "shipping": null,
    "total": "10.00"
  },
  "field_exclusions": {},
  "line_items": [{
    "description": "Printed description",
    "quantity": "1",
    "unit_price": "10.00",
    "line_total": "10.00",
    "tax": null
  }]
}
```

This is a format example, not a labeled real invoice or a complete production selection. Assets are relative to the spec directory; traversal and symlink paths are rejected. Keep private inputs/results under ignored `artifacts/`. The redistribution declaration is metadata, not automatic publication enforcement. Do not move private inputs, rendered pages, source text, labels or reports into `evals/` without permission to publish them.

## Freeze and run

Build the parser with `make parser-build` if needed. A freeze requires the installed immutable parser image; it does not decode original PDFs on the host. Actual intake/parsing still goes through the isolated product boundary.

```sh
PYTHONPATH=src python3.12 scripts/production_study.py freeze \
  artifacts/permissioned-spec/spec.json --mode production \
  --output-dir artifacts/production-freeze-001
PYTHONPATH=src python3.12 scripts/production_study.py run \
  artifacts/production-freeze-001 --variant ocr_rules \
  --output-dir artifacts/production-rules-001
PYTHONPATH=src python3.12 scripts/production_study.py run \
  artifacts/production-freeze-001 --variant span_llm \
  --output-dir artifacts/production-model-001
```

Use new output paths. Keep implementation/UI, parser image and profile unchanged between variants; changes require a new study identity. The model branch verifies already-cached pinned assets and owns its runtime, with no implicit downloads. Run one model workload at a time.

Each branch creates a separate persisted workbench alongside its immutable evidence directory: `production-rules-001-workbench/` or `production-model-001-workbench/`. It submits originals through authenticated HTTP to the normal supervised serial worker and records initial unapproved candidates and verified page bytes. Neither labels nor audit judgments enter extraction. Both variants parse fresh uploads; this studies the production workflow rather than reusing saved OCR. A case's 1,800-second diagnostic deadline includes processing. Late work is cancelled and remains failed. Interrupted/global-startup runs remain incomplete and cannot be scored as complete; make a new run after resolving the cause. Completed per-document failures remain in the denominator, including rejected uploads and mismatched declared page counts.

Parser/image/profile/source drift, omitted/duplicate cases, changed originals/labels, revised or approved candidate snapshots, mismatched renders and incomplete owned-model shutdown are rejected by offline checks. All-scheduled-failure runs can be accounted for without being mistaken for successful extraction. Reports are hash-bound local evidence, not signed independent attestations. Diagnostic timing is not G12 controlled performance evidence.

## Original versus approved quality

```sh
PYTHONPATH=src python3.12 scripts/production_study.py score \
  artifacts/production-freeze-001 artifacts/production-rules-001 \
  --output artifacts/production-rules-score-001.json
```

Repeat for the model run. This reuses the existing header/exact-row/cell scorer, accounts for every scheduled input and preserves original predictions. A run may complete with poor quality; completion does not promote a model or authorize exports. Record both variants' results and failures, with permission/acquisition limits and representative errors.

To review candidates, start the ordinary server on their persisted workbench:

```sh
PYTHONPATH=src python3.12 -m docwork.cli serve \
  --db artifacts/production-rules-001-workbench/review.sqlite \
  --objects artifacts/production-rules-001-workbench/objects
```

Use the server-established reviewer to inspect/correct/acknowledge/approve current revisions. Avoid reprocessing these records; a new extraction belongs to a new study, and the approved snapshot rejects replacement candidates. Then collect approved quality separately:

```sh
PYTHONPATH=src python3.12 scripts/production_study.py approved \
  artifacts/production-freeze-001 artifacts/production-rules-001 \
  --workbench artifacts/production-rules-001-workbench \
  --output artifacts/production-approved-001.json
```

Collection verifies the original candidate and current approval's source/record/revision/decision hashes. It never creates approval. Unapproved cases are explicitly `NotApproved`; failed extractions remain failures. Compare approved quality and coverage separately from original quality. This is neither a human-time study nor independent proof of human participation, and corrections never replace frozen extractor predictions.

## Semantic and geometry audit

The predeclared sample includes **all five required headers plus the first and last gold row's description and line total** for every scheduled case in each variant. A one-row case contributes that row once. Frozen row matching locates candidate rows; it does not establish semantic truth. Unmatched rows, missing values, exclusions and extraction failures remain explicit sample entries. Extra predicted rows are counted in extraction metrics but are outside this declared semantic sample. Publish that sampling limit.

```sh
PYTHONPATH=src python3.12 scripts/production_study.py audit-template \
  artifacts/production-freeze-001 artifacts/production-rules-001 \
  --output artifacts/production-audit-template-001.json
```

Copy the template to a new assessment file. Inspect the original and saved rendered page for **every** target, including all cited pages. Fill only `auditor`, `semantic_status`, `geometry_status`, `inspected_pages` and `rationale`; do not edit bound values, references or targets.

| Axis | Allowed classifications |
|---|---|
| Semantic support | `supported`, `wrong_field`, `ambiguous`, `absent_support`, `no_citation`, `extraction_failed` |
| Geometry | `aligned_line_region`, `imprecise`, `wrong_page_or_region`, `unavailable`, `not_applicable` |

`supported` means the cited source region supports the field's meaning and value. A matching digit or valid reference alone is insufficient. `wrong_field` covers a citation to another amount/date/entity; `ambiguous` covers unresolved alternatives; `absent_support` covers present references without support. Missing citations cannot become supported, unknown references cannot establish support, and absent geometry cannot become aligned. Extraction failures must remain failures. Line boxes can be geometrically aligned while semantically wrong; record both axes independently.

```sh
PYTHONPATH=src python3.12 scripts/production_study.py audit-report \
  artifacts/production-freeze-001 artifacts/production-rules-001 \
  artifacts/production-audit-assessment-001.json \
  --output artifacts/production-semantic-report-001.json
```

Repeat for the model. Every target needs a classification and rationale; incomplete/reordered/altered samples are rejected. Include source/page references and representative wrong/ambiguous/absent cases with any presentation fix and its verification. The UI now describes highlights as cited OCR lines, distinguishes reviewer corrections/computed values, and discloses missing references/geometry. It does not call a citation semantically verified.

## Tooling diagnostic and data acquisition status

`prepare-diagnostic` copies five **known fictional development** originals with existing generator labels. It is an offline way to test study setup, not the genuine-scan selection:

```sh
PYTHONPATH=src python3.12 scripts/production_study.py prepare-diagnostic \
  --output-dir artifacts/production-diagnostic-spec-001
PYTHONPATH=src python3.12 scripts/production_study.py freeze \
  artifacts/production-diagnostic-spec-001/spec.json --mode diagnostic \
  --output-dir artifacts/production-diagnostic-freeze-001
```

Run/score/audit using that freeze as above. Diagnostic mode cannot satisfy the eight-case/two-scan production gate, and preparing an assessment template is not a completed semantic audit. Reviewed tooling evidence is linked from the [backlog](backlog.md).

As of October 4, no permissioned genuine scanner originals have been selected. The [published MIDD record](https://zenodo.org/records/5113009) lists `IOB.rar` annotations and no PDF/image download; it therefore does not yet supply originals for this workflow. [DocILE's official access page](https://docile.rossum.ai/) requires registration for research data, and its [repository](https://github.com/rossumai/docile) documents a token-based download. Neither dataset has been downloaded or substituted with synthetic degradation. Obtain suitable originals and review their permission/labels before a production freeze. No new vendor-generalization, time-saved or unattended-approval claim is made.
