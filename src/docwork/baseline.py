"""Simple deterministic invoice parser over canonical OCR lines."""

from __future__ import annotations

import re

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


def observed(value: str, span: TextSpan) -> FieldValue:
    return FieldValue(value=value.strip(), raw=span.text, evidence_ids=(span.id,))


def extract_invoice(page: DocumentPage) -> InvoiceRecord:
    fields = {name: missing() for name in HEADER_FIELDS}
    line_items: list[LineItem] = []
    for span in page.spans:
        line = span.text.strip()
        if not line:
            continue
        if fields["supplier_name"].value is None and line.lower() != "invoice":
            fields["supplier_name"] = observed(line, span)
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
        match = ROW.fullmatch(line)
        if match and not line.lower().startswith("description"):
            description, quantity, unit_price, line_total = match.groups()
            line_items.append(
                LineItem(
                    row_id=f"row-{len(line_items) + 1:03d}",
                    description=observed(description, span),
                    quantity=observed(quantity, span),
                    unit_price=observed(unit_price, span),
                    line_total=observed(line_total, span),
                    tax=missing("not_explicit_on_row"),
                )
            )
    return InvoiceRecord(CONTRACT_VERSION, fields, tuple(line_items))
