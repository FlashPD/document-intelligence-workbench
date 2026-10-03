from __future__ import annotations

import unittest
from dataclasses import replace

from docwork.spatial_baseline import extract_invoice
from docwork.contracts import Box, DocumentPage, TextSpan
from docwork.validation import validate_invoice


def span(name, text, left, right, top=.4, bottom=.415):
    return TextSpan(name, 1, text, Box(left, top, right, bottom), "fixture")


def page(*spans):
    return DocumentPage(1, 1200, 1600, tuple(spans))


class SpatialRowTests(unittest.TestCase):
    def test_shuffled_columns_recover_row_with_cell_specific_evidence(self):
        source = page(span("money", "61.00 122.00", .65, .9),
                      span("quantity", "2", .5, .52),
                      span("description", "Technical review", .1, .4))
        row = extract_invoice(source).line_items[0]
        self.assertEqual((row.description.value, row.quantity.value, row.unit_price.value, row.line_total.value),
                         ("Technical review", "2", "61.00", "122.00"))
        self.assertEqual(row.description.evidence_ids, ("description",))
        self.assertEqual(row.quantity.evidence_ids, ("quantity",))
        self.assertEqual(row.unit_price.evidence_ids, ("money",))
        self.assertEqual(row.line_total.evidence_ids, ("money",))
        self.assertFalse(any(i.code.startswith("EVIDENCE_") for i in validate_invoice(extract_invoice(source), source)))

    def test_duplicate_descriptions_and_close_rows_keep_multiplicity(self):
        source = page(span("d2", "Field interview", .1, .4, .42, .43),
                      span("m1", "2 40.00 80.00", .5, .9, .4, .41),
                      span("d1", "Field interview", .1, .4, .4, .41),
                      span("m2", "3 40.00 120.00", .5, .9, .42, .43))
        rows = extract_invoice(source).line_items
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.line_total.value for row in rows], ["80.00", "120.00"])
        self.assertEqual([row.row_id for row in rows], ["row-001", "row-002"])

    def test_absent_quantity_stays_missing_and_requires_review(self):
        for prices in ((span("pair", "40.00 80.00", .65, .9),),
                       (span("price", "40.00", .65, .7), span("total", "80.00", .8, .9))):
            with self.subTest(prices=prices):
                source = page(span("description", "Field interview", .1, .4), *prices)
                record = extract_invoice(source)
                row = record.line_items[0]
                self.assertIsNone(row.quantity.value)
                self.assertEqual(row.quantity.missing_reason, "quantity_not_observed")
                self.assertEqual((row.unit_price.value, row.line_total.value), ("40.00", "80.00"))
                self.assertIn("INVALID_ROW_AMOUNT", [i.code for i in validate_invoice(record, source)])

    def test_description_across_fragments_has_joined_provenance(self):
        source = page(span("first", "Technical", .1, .2),
                      span("second", "review 2 40.00 80.00", .25, .9))
        record = extract_invoice(source)
        self.assertEqual(record.line_items[0].description.value, "Technical review")
        self.assertEqual(record.line_items[0].description.evidence_ids, ("first", "second"))
        self.assertNotIn("EVIDENCE_MISMATCH", [i.code for i in validate_invoice(record, source)])
        forged = replace(record.line_items[0], description=replace(record.line_items[0].description, value="Invented review"))
        self.assertIn("EVIDENCE_MISMATCH", [i.code for i in validate_invoice(replace(record, line_items=(forged,)), source)])

    def test_nearby_amount_from_another_row_is_not_borrowed(self):
        source = page(span("left", "Technical review 2 40.00", .1, .7, .4, .41),
                      span("wrong", "80.00", .8, .9, .42, .43))
        row = extract_invoice(source).line_items[0]
        self.assertIsNone(row.line_total.value)

    def test_unrelated_extra_citation_is_not_accepted(self):
        source = page(span("row", "Technical review 2 40.00 80.00", .1, .9),
                      span("unrelated", "Other document text", .1, .4, .6, .62))
        record = extract_invoice(source)
        row = record.line_items[0]
        forged = replace(row, description=replace(row.description, evidence_ids=("row", "unrelated")))
        issues = validate_invoice(replace(record, line_items=(forged,)), source)
        self.assertIn("EVIDENCE_MISMATCH", [i.code for i in issues])

    def test_header_label_and_value_can_be_split(self):
        source = page(span("label", "Subtotal:", .1, .3), span("value", "80.00", .4, .6))
        record = extract_invoice(source)
        self.assertEqual(record.fields["subtotal"].value, "80.00")
        self.assertEqual(record.fields["subtotal"].evidence_ids, ("value",))
        self.assertEqual(record.line_items, ())

    def test_decimal_quantity_and_missing_total_are_not_reinterpreted(self):
        source = page(span("left", "Technical review 1.50", .1, .6), span("price", "40.00", .65, .8))
        row = extract_invoice(source).line_items[0]
        self.assertEqual(row.quantity.value, "1.50")
        self.assertEqual(row.unit_price.value, "40.00")
        self.assertIsNone(row.line_total.value)

    def test_sideways_split_headers_allow_grouping_without_changing_source_boxes(self):
        upright = page(span("dh", "Description", .1, .3, .3, .32),
                       span("ah", "Amount", .8, .9, .3, .32),
                       span("d", "Technical review", .1, .4),
                       span("q", "2", .5, .52), span("m", "40.00 80.00", .65, .9))
        for clockwise in (True, False):
            def rotate(box):
                return (Box(1-box.bottom, box.left, 1-box.top, box.right) if clockwise else
                        Box(box.top, 1-box.right, box.bottom, 1-box.left))
            rotated = DocumentPage(1, 1600, 1200, tuple(replace(s, box=rotate(s.box)) for s in upright.spans))
            original = tuple(s.box for s in rotated.spans)
            row = extract_invoice(rotated).line_items[0]
            self.assertEqual(row.description.value, "Technical review")
            self.assertEqual(row.line_total.value, "80.00")
            self.assertEqual(tuple(s.box for s in rotated.spans), original)

    def test_sideways_complete_rows_without_header_anchors_keep_ocr_order(self):
        source = page(span("r1", "First task 2 40.00 80.00", .4, .415, .1, .9),
                      span("r2", "Second task 3 40.00 120.00", .45, .465, .1, .9))
        self.assertEqual([row.description.value for row in extract_invoice(source).line_items], ["First task", "Second task"])


if __name__ == "__main__":
    unittest.main()
