from __future__ import annotations

import unittest
from pathlib import Path

from docwork.baseline import extract_invoice
from docwork.cli import baseline_fixture
from docwork.ocr import png_dimensions, tesseract_page
from docwork.validation import validate_invoice

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
CORPUS = Path(__file__).resolve().parents[1] / "datasets" / "invoices-v1" / "assets"


class FixtureIntegrationTests(unittest.TestCase):
    def test_ocr_boxes_and_clean_invoice(self) -> None:
        page = tesseract_page(SAMPLES / "clean.png")
        self.assertEqual((page.width_px, page.height_px), png_dimensions(SAMPLES / "clean.png"))
        self.assertTrue(page.spans)
        self.assertTrue(all(span.box is not None for span in page.spans))
        record = extract_invoice(page)
        self.assertEqual(record.fields["invoice_number"].value, "AST-1001")
        self.assertEqual(record.fields["total"].value, "270.00")
        self.assertEqual(record.line_items[0].line_total.value, "250.00")
        self.assertEqual(validate_invoice(record, page), ())

    def test_conflicting_total_retains_observation_and_flags_issue(self) -> None:
        page = tesseract_page(SAMPLES / "conflicting-total.png")
        record = extract_invoice(page)
        self.assertEqual(record.fields["total"].value, "275.00")
        issues = validate_invoice(record, page)
        self.assertEqual([issue.code for issue in issues], ["TOTAL_MISMATCH"])
        self.assertIn("270.00", issues[0].detail)

    def test_fixture_boundary_rejects_external_file(self) -> None:
        with self.assertRaises(ValueError):
            baseline_fixture(Path(__file__))

    def test_orientation_aware_ocr_keeps_boxes_on_rotated_page(self) -> None:
        path = CORPUS / "inv-f01-30.png"
        page = tesseract_page(path)
        self.assertEqual((page.width_px, page.height_px), png_dimensions(path))
        record = extract_invoice(page)
        self.assertEqual(record.fields["supplier_name"].value, "Aster Studio LLC")
        self.assertEqual(record.fields["invoice_number"].value, "F01-005")
        self.assertEqual(record.line_items[0].line_total.value, "25.00")
        cited = {span.id: span for span in page.spans}
        for field in (record.fields["supplier_name"], record.fields["invoice_number"],
                      record.line_items[0].line_total):
            self.assertTrue(field.evidence_ids)
            self.assertTrue(all(cited[span_id].box is not None for span_id in field.evidence_ids))


if __name__ == "__main__":
    unittest.main()
