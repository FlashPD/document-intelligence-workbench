"""Recorded evidence checks only: no model server, downloads, or fresh inference."""

import json
import unittest
from pathlib import Path

from docwork.comparison import compare_files
from docwork.evidence import verify_model_evidence

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evals" / "local-model-instruct-2026-10-02"
SPAN_V2 = ROOT / "evals" / "local-model-span-v2-2026-10-02"


class RecordedModelTests(unittest.TestCase):
    def test_original_predictions_reproduce_report_and_rejected_candidate(self):
        checked = verify_model_evidence(EVIDENCE / "model", ROOT)
        self.assertEqual(checked["status"], "verified")
        self.assertEqual(checked["documents"], 12)
        comparison = compare_files(EVIDENCE / "baseline.json", EVIDENCE / "model" / "report.json",
                                   ROOT, allow_changes=("extractor",))
        self.assertEqual(comparison, json.loads((EVIDENCE / "comparison.json").read_text()))
        self.assertEqual(comparison["status"], "regression")
        self.assertIn("Regression: line_total_exact_by_order", comparison["reasons"])

    def test_span_v2_predictions_reproduce_passing_development_gate(self):
        checked = verify_model_evidence(SPAN_V2 / "model", ROOT)
        self.assertEqual(checked["status"], "verified")
        self.assertEqual(checked["documents"], 12)
        comparison = compare_files(SPAN_V2 / "baseline.json", SPAN_V2 / "model" / "report.json",
                                   ROOT, allow_changes=("extractor",))
        self.assertEqual(comparison, json.loads((SPAN_V2 / "comparison.json").read_text()))
        self.assertEqual(comparison["status"], "pass")
        self.assertEqual(comparison["metrics"]["line_total_exact_by_order"]["candidate"], 17 / 18)
        prompt_comparison = compare_files(EVIDENCE / "model" / "report.json",
                                          SPAN_V2 / "model" / "report.json", ROOT,
                                          allow_changes=("extractor",))
        self.assertEqual(prompt_comparison, json.loads((SPAN_V2 / "prompt-comparison.json").read_text()))


if __name__ == "__main__":
    unittest.main()
