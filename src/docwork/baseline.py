"""Simple deterministic invoice parser over canonical OCR lines."""

from __future__ import annotations

import re
from dataclasses import replace

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

BASELINE_VERSION = "ocr-rules-v0.2"

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
AMOUNT_ONLY = re.compile(r"^\d+\.\d{2}$")


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
        if fields["supplier_name"].value is None and line.lower() != "invoice":
            fields["supplier_name"] = observed(re.sub(r"\s+INVOICE$", "", line, flags=re.I), span)
    return InvoiceRecord(CONTRACT_VERSION, fields, _extract_rows(page))


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


def _extract_rows(page: DocumentPage) -> tuple[LineItem, ...]:
    amount_spans = [span for span in page.spans if span.box and AMOUNT_ONLY.fullmatch(span.text.strip())]
    used_amounts: set[str] = set()
    candidates: list[tuple[float, LineItem]] = []
    for span in page.spans:
        line = span.text.strip()
        match = ROW.fullmatch(line)
        if match:
            description, quantity, unit_price, line_total = match.groups()
            total = observed(line_total, span)
        else:
            match = ROW_WITHOUT_TOTAL.fullmatch(line)
            if match is None or span.box is None:
                continue
            description, quantity, unit_price = match.groups()
            center = (span.box.top + span.box.bottom) / 2
            same_row = [
                candidate for candidate in amount_spans
                if candidate.id not in used_amounts
                and candidate.box is not None
                and candidate.box.left > span.box.right
                and abs((candidate.box.top + candidate.box.bottom) / 2 - center) <= .015
            ]
            if same_row:
                right = min(same_row, key=lambda candidate: candidate.box.left)
                used_amounts.add(right.id)
                total = observed(right.text.strip(), right)
            else:
                total = missing("rightmost_amount_not_observed")
        row = LineItem(
            row_id="pending",
            description=observed(description, span),
            quantity=observed(quantity, span),
            unit_price=observed(unit_price, span),
            line_total=total,
            tax=missing("not_explicit_on_row"),
        )
        candidates.append((span.box.top if span.box else float(len(candidates)), row))
    return tuple(replace(row, row_id=f"row-{index:03d}") for index, (_, row) in enumerate(sorted(candidates, key=lambda pair: pair[0]), start=1))
