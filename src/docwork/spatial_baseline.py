"""Experimental spatial invoice parser; calibration rejected default promotion."""

from __future__ import annotations

import re
from dataclasses import replace
from .spatial_lines import SpatialLine, spatial_lines

from .contracts import (
    CONTRACT_VERSION,
    HEADER_FIELDS,
    DocumentPage,
    FieldValue,
    InvoiceRecord,
    LineItem,
    TextSpan,
    missing,
)

BASELINE_VERSION = "ocr-rules-v0.4"

LABELS = {
    "invoice_number": re.compile(r"^invoice\s*(?:number|no\.?|#)\s*:\s*(.+)$", re.I),
    "issue_date": re.compile(r"^(?:issue\s*)?date\s*:\s*(.+)$", re.I),
    "due_date": re.compile(r"^due\s*date\s*:\s*(.+)$", re.I),
    "currency": re.compile(r"^currency\s*:\s*(.+)$", re.I),
    "subtotal": re.compile(r"^subtotal\s*:\s*(.+)$", re.I),
    "tax": re.compile(r"^tax\s*:\s*(.+)$", re.I),
    "discount": re.compile(r"^discount\s*:\s*(.+)$", re.I),
    "shipping": re.compile(r"^shipping\s*:\s*(.+)$", re.I),
    "total": re.compile(r"^total\s*:\s*(.+)$", re.I),
}
ROW = re.compile(r"^(.+?)\s+(\d+(?:\.\d+)?)\s+(\d+\.\d{2})\s+(\d+\.\d{2})$")
ROW_WITHOUT_TOTAL = re.compile(r"^(.+?)\s+(\d+(?:\.\d+)?)\s+(\d+\.\d{2})$")
ROW_WITHOUT_QUANTITY = re.compile(r"^(.+?)\s+(\d+\.\d{2})\s+(\d+\.\d{2})$")
AMOUNT_ONLY = re.compile(r"^\d+\.\d{2}$")


def _separate_prices(line: SpatialLine, match: re.Match) -> bool:
    description_ids = set(line.observed(match, 1).evidence_ids)
    price_ids = set(line.observed(match, 2).evidence_ids + line.observed(match, 3).evidence_ids)
    return not description_ids & price_ids and all(
        AMOUNT_ONLY.fullmatch(token) for span in line.spans if span.id in price_ids for token in span.text.split()
    )


def observed(value: str, span: TextSpan) -> FieldValue:
    return FieldValue(value=value.strip(), raw=span.text, evidence_ids=(span.id,))


def extract_invoice(page: DocumentPage) -> InvoiceRecord:
    fields = {name: missing() for name in HEADER_FIELDS}
    for span in page.spans:
        line = span.text.strip()
        if not line:
            continue
        matched = False
        for name, pattern in LABELS.items():
            match = pattern.fullmatch(line)
            if match:
                if fields[name].value is None:
                    fields[name] = observed(match.group(1), span)
                matched = True
                break
        if matched:
            continue
        # Tesseract can emit a clipped page-corner glyph before the actual
        # printed header. A source box touching both top and left edges is
        # unreliable as a supplier name.
        corner_artifact = span.box is not None and span.box.top <= .005 and span.box.left <= .005
        if fields["supplier_name"].value is None and line.lower() != "invoice" and not corner_artifact:
            fields["supplier_name"] = observed(re.sub(r"\s+INVOICE$", "", line, flags=re.I), span)
    lines = spatial_lines(page)
    # Keep directly observed labels first; recover split labels/values only
    # when the field is absent. Never replace a complete observed value.
    for line in lines:
        if len(line.spans) < 2:
            continue
        for name, pattern in LABELS.items():
            match = pattern.fullmatch(line.text)
            if match and fields[name].value is None:
                fields[name] = line.observed(match)
    return InvoiceRecord(CONTRACT_VERSION, fields, _extract_rows(lines))


def extract_invoice_pages(pages: tuple[DocumentPage, ...]) -> InvoiceRecord:
    """Apply the narrow baseline per page, retaining source order and page spans."""
    if not pages:
        raise ValueError("At least one page is required")
    fields = {name: missing() for name in HEADER_FIELDS}
    rows: list[LineItem] = []
    for page in pages:
        candidate = extract_invoice(page)
        for name in HEADER_FIELDS:
            if fields[name].value is None and candidate.fields[name].value is not None:
                fields[name] = candidate.fields[name]
        for row in candidate.line_items:
            rows.append(replace(row, row_id=f"row-{len(rows) + 1:03d}"))
    return InvoiceRecord(CONTRACT_VERSION, fields, tuple(rows))


def _extract_rows(lines: tuple[SpatialLine, ...]) -> tuple[LineItem, ...]:
    candidates: list[LineItem] = []
    for line in lines:
        if any(pattern.fullmatch(line.text) for pattern in LABELS.values()):
            continue
        match = ROW.fullmatch(line.text)
        if match:
            description, quantity, unit_price, total = (line.observed(match, index) for index in range(1, 5))
        else:
            match = ROW_WITHOUT_QUANTITY.fullmatch(line.text)
            # A separate description and two observed prices form a partial
            # row. A single-span two-number line is ambiguous with decimal
            # quantity + unit price, so retain the existing interpretation.
            if match and len(line.spans) >= 2 and _separate_prices(line, match):
                description = line.observed(match, 1)
                quantity = missing("quantity_not_observed")
                unit_price, total = line.observed(match, 2), line.observed(match, 3)
            else:
                match = ROW_WITHOUT_TOTAL.fullmatch(line.text)
                if match is None:
                    continue
                description, quantity, unit_price = (line.observed(match, index) for index in range(1, 4))
                total = missing("rightmost_amount_not_observed")
        row = LineItem(
            row_id="pending",
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            line_total=total,
            tax=missing("not_explicit_on_row"),
        )
        candidates.append(row)
    return tuple(replace(row, row_id=f"row-{index:03d}") for index, row in enumerate(candidates, start=1))
