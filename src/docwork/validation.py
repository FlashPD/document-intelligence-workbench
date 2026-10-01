"""Evidence and invoice arithmetic checks. No automatic value correction."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from .contracts import DocumentPage, FieldValue, InvoiceRecord, REQUIRED_FIELDS, ValidationIssue

MONEY = re.compile(r"^-?\d+\.\d{2}$")
QUANTITY = re.compile(r"^\d+(?:\.\d+)?$")
TOLERANCE = Decimal("0.01")


def _decimal(value: FieldValue, *, money: bool) -> Decimal | None:
    if value.value is None:
        return None
    if not (MONEY if money else QUANTITY).fullmatch(value.value):
        return None
    try:
        return Decimal(value.value)
    except InvalidOperation:
        return None


def _all_values(record: InvoiceRecord):
    for name, field in record.fields.items():
        yield f"fields.{name}", field
    for row in record.line_items:
        for name in ("description", "quantity", "unit_price", "line_total", "tax"):
            yield f"line_items.{row.row_id}.{name}", getattr(row, name)


def validate_invoice(record: InvoiceRecord, page: DocumentPage) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    spans = {span.id: span for span in page.spans}
    for name in REQUIRED_FIELDS:
        if record.fields[name].value is None:
            issues.append(ValidationIssue("REQUIRED_MISSING", f"fields.{name}", "Required value was not observed"))
    for path, field in _all_values(record):
        if field.value is None:
            continue
        if not field.evidence_ids and field.origin == "observed":
            issues.append(ValidationIssue("EVIDENCE_MISSING", path, "Observed value has no source span"))
        for span_id in field.evidence_ids:
            span = spans.get(span_id)
            if span is None:
                issues.append(ValidationIssue("EVIDENCE_UNKNOWN", path, f"Unknown span ID: {span_id}"))
            elif field.origin == "observed" and field.value.casefold() not in span.text.casefold():
                issues.append(ValidationIssue("EVIDENCE_MISMATCH", path, "Value does not occur in cited span"))

    for name in ("subtotal", "tax", "discount", "shipping", "total"):
        field = record.fields[name]
        if field.value is not None and _decimal(field, money=True) is None:
            issues.append(ValidationIssue("INVALID_MONEY", f"fields.{name}", "Expected a decimal with two fractional digits"))
    currency = record.fields["currency"].value
    if currency is not None and currency != "USD":
        issues.append(ValidationIssue("UNSUPPORTED_CURRENCY", "fields.currency", "Phase 0 parser supports explicit USD only"))

    parsed_dates: dict[str, date] = {}
    for name in ("issue_date", "due_date"):
        value = record.fields[name].value
        if value is None:
            continue
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("date format must be ISO calendar format")
            parsed_dates[name] = date.fromisoformat(value)
        except ValueError:
            issues.append(ValidationIssue("AMBIGUOUS_DATE", f"fields.{name}", "Expected YYYY-MM-DD"))
    if all(name in parsed_dates for name in ("issue_date", "due_date")) and parsed_dates["due_date"] < parsed_dates["issue_date"]:
        issues.append(ValidationIssue("DATE_ORDER", "fields.due_date", "Due date precedes issue date"))

    if not record.line_items:
        issues.append(ValidationIssue("NO_LINE_ITEMS", "line_items", "No line-item rows were extracted"))
    row_totals: list[Decimal] = []
    for row in record.line_items:
        quantity = _decimal(row.quantity, money=False)
        unit_price = _decimal(row.unit_price, money=True)
        line_total = _decimal(row.line_total, money=True)
        if None in (quantity, unit_price, line_total):
            issues.append(ValidationIssue("INVALID_ROW_AMOUNT", f"line_items.{row.row_id}", "Row numbers could not be parsed"))
            continue
        assert quantity is not None and unit_price is not None and line_total is not None
        row_totals.append(line_total)
        if abs(quantity * unit_price - line_total) > TOLERANCE:
            issues.append(ValidationIssue("ROW_ARITHMETIC_MISMATCH", f"line_items.{row.row_id}", "Quantity times unit price differs from observed row total"))

    subtotal = _decimal(record.fields["subtotal"], money=True)
    if subtotal is not None and len(row_totals) == len(record.line_items) and row_totals:
        if abs(sum(row_totals) - subtotal) > TOLERANCE:
            issues.append(ValidationIssue("SUBTOTAL_MISMATCH", "fields.subtotal", "Observed subtotal differs from sum of observed rows"))
    terms = {name: _decimal(record.fields[name], money=True) for name in ("subtotal", "tax", "discount", "shipping", "total")}
    if all(value is not None for value in terms.values()):
        expected = terms["subtotal"] + terms["tax"] - terms["discount"] + terms["shipping"]
        if abs(expected - terms["total"]) > TOLERANCE:
            issues.append(ValidationIssue("TOTAL_MISMATCH", "fields.total", f"Observed total differs from computed {expected:.2f}"))
    else:
        issues.append(ValidationIssue("TOTAL_NOT_CHECKED", "fields.total", "A total component is missing or invalid", blocking=False))
    return tuple(issues)
