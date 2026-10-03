from __future__ import annotations

import json
import unittest

from docwork.receipt_comparison import compare_receipts, verify_receipt_comparison
import test_receipt_run


class ReceiptComparisonTests(unittest.TestCase):
    def setUp(self):
        helper = test_receipt_run.ReceiptRunTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.helper = helper

    def test_shared_ocr_pair_is_audited_and_bootstrapped(self):
        h = self.helper
        h.run_variant("rules")
        h.run_variant("model", variant="span_llm", ocr_run=h.root / "rules")
        report = compare_receipts(h.manifest, h.root / "rules", h.root / "model", h.root / "comparison.json")
        self.assertEqual(report["documents"], 2)
        self.assertEqual(report["delta_model_minus_rules"]["row_detection_f1"], 0)
        self.assertEqual(report["paired_95_intervals"]["eligible_exact_row_f1"], [0, 0])
        self.assertEqual(json.loads((h.root / "comparison.json").read_text()), report)

    def test_unpaired_subsets_cannot_form_a_headline_comparison(self):
        h = self.helper
        h.run_variant("rules")
        h.run_variant("model", variant="span_llm", ocr_run=h.root / "rules", limit=1)
        with self.assertRaisesRegex(ValueError, "compatible paired"):
            compare_receipts(h.manifest, h.root / "rules", h.root / "model", h.root / "invalid.json")
        self.assertFalse((h.root / "invalid.json").exists())

    def test_portfolio_export_rejects_changed_metrics_with_unchanged_input_hashes(self):
        h = self.helper
        h.run_variant("test-rules")
        h.run_variant("test-model", variant="span_llm", ocr_run=h.root / "test-rules")
        report = compare_receipts(h.manifest, h.root / "test-rules", h.root / "test-model", h.root / "comparison.json")
        self.assertEqual(verify_receipt_comparison(h.manifest, h.root / "test-rules", h.root / "test-model",
                                                 h.root / "comparison.json"), report)
        report["delta_model_minus_rules"]["eligible_exact_row_f1"] = 1.0
        (h.root / "comparison.json").write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "deterministic rescoring"):
            verify_receipt_comparison(h.manifest, h.root / "test-rules", h.root / "test-model", h.root / "comparison.json")


if __name__ == "__main__":
    unittest.main()
