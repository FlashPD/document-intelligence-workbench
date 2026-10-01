from __future__ import annotations

import unittest
from dataclasses import replace

from docwork.baseline import extract_invoice
from docwork.contracts import Box, DocumentPage, FieldValue, TextSpan
from docwork.validation import validate_invoice


def page_with_total(total: str) -> DocumentPage:
    lines = (
        "Aster Studio LLC",
        "INVOICE",
        "Invoice Number: AST-1001",
        "Date: 2026-09-12",
        "Due Date: 2026-10-12",
        "Currency: USD",
        "Description Qty Unit Price Line Total",
        "Research workshop 2 125.00 250.00",
        "Subtotal: 250.00",
        "Tax: 20.00",
        "Discount: 0.00",
        "Shipping: 0.00",
        f"Total: {total}",
    )
    spans = tuple(
        TextSpan(f"s{index}", 1, line, Box(.1, .02 + index * .06, .8, .05 + index * .06), "fixture")
        for index, line in enumerate(lines)
    )
    return DocumentPage(1, 1000, 1000, spans)


class ContractTests(unittest.TestCase):
    def test_normalized_box_rejects_invalid_geometry(self) -> None:
        with self.assertRaises(ValueError):
            Box(.8, .2, .1, .4)
        with self.assertRaises(ValueError):
            Box(0, -.1, .5, .5)

    def test_missing_and_zero_are_distinct(self) -> None:
        with self.assertRaises(ValueError):
            FieldValue(value=None, raw=None)
        self.assertEqual(FieldValue(value="0.00", raw="0.00").value, "0.00")

    def test_clean_record_and_provenance(self) -> None:
        page = page_with_total("270.00")
        record = extract_invoice(page)
        self.assertEqual(record.fields["invoice_number"].value, "AST-1001")
        self.assertEqual(record.fields["invoice_number"].evidence_ids, ("s2",))
        self.assertEqual(record.line_items[0].line_total.value, "250.00")
        self.assertEqual(validate_invoice(record, page), ())

    def test_observed_conflict_is_not_rewritten(self) -> None:
        page = page_with_total("275.00")
        record = extract_invoice(page)
        self.assertEqual(record.fields["total"].value, "275.00")
        self.assertEqual([issue.code for issue in validate_invoice(record, page)], ["TOTAL_MISMATCH"])

    def test_unknown_and_misaligned_evidence_are_flagged(self) -> None:
        page = page_with_total("270.00")
        record = extract_invoice(page)
        fields = dict(record.fields)
        fields["supplier_name"] = FieldValue("Other supplier", "Aster Studio LLC", ("s0",))
        fields["invoice_number"] = FieldValue("A-1", "A-1", ("made-up",))
        issues = validate_invoice(replace(record, fields=fields), page)
        self.assertIn("EVIDENCE_MISMATCH", {issue.code for issue in issues})
        self.assertIn("EVIDENCE_UNKNOWN", {issue.code for issue in issues})

    def test_missing_currency_is_not_inferred_from_amounts(self) -> None:
        page = page_with_total("270.00")
        page = replace(page, spans=tuple(span for span in page.spans if span.text != "Currency: USD"))
        record = extract_invoice(page)
        self.assertIsNone(record.fields["currency"].value)
        self.assertIn("REQUIRED_MISSING", {issue.code for issue in validate_invoice(record, page)})

    def test_ambiguous_date_is_reported(self) -> None:
        page = page_with_total("270.00")
        spans = tuple(replace(span, text="Date: 12/09/2026") if span.text.startswith("Date:") else span for span in page.spans)
        page = replace(page, spans=spans)
        record = extract_invoice(page)
        self.assertIn("AMBIGUOUS_DATE", {issue.code for issue in validate_invoice(record, page)})

    def test_split_rightmost_row_amount_uses_spatial_evidence(self) -> None:
        page = page_with_total("270.00")
        spans = tuple(span for span in page.spans if span.id != "s7") + (
            TextSpan("row-left", 1, "Research workshop 2 125.00", Box(.1, .44, .65, .47), "fixture"),
            TextSpan("row-right", 1, "250.00", Box(.8, .44, .9, .47), "fixture"),
        )
        page = replace(page, spans=spans)
        record = extract_invoice(page)
        self.assertEqual(len(record.line_items), 1)
        self.assertEqual(record.line_items[0].line_total.value, "250.00")
        self.assertEqual(record.line_items[0].line_total.evidence_ids, ("row-right",))
        self.assertEqual(validate_invoice(record, page), ())


if __name__ == "__main__":
    unittest.main()
