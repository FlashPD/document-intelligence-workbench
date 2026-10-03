from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("release_report", Path(__file__).resolve().parents[1] / "scripts/render_release_reports.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleaseReportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        (self.directory / "predictions").mkdir()
        self.docs = []
        for index, seconds in enumerate((1, 9, 30)):
            id = f"fixture-{index}"
            self.docs.append({"id": id, "header": {"total": {"fp": index, "fn": index}},
                              "gold_rows": 2, "predicted_rows": 2, "exact_rows": 2 - index,
                              "failure_type": "ModelUnavailable" if index == 0 else None})
            (self.directory / "predictions" / f"{id}.json").write_text(json.dumps({"runtime_seconds": {"model": seconds}}))
        (self.directory / "report.json").write_text(json.dumps({"documents": self.docs, "summary": {"documents_scheduled": 3}}))

    def test_percentiles_include_failed_calls_and_case_selection_is_declared(self):
        data = MODULE.supplementary_evidence(self.directory, "model")
        self.assertEqual((data["documents"], data["sum_seconds"], data["p50_seconds"], data["p95_seconds"]), (3, 40, 9, 30))
        self.assertEqual([c["id"] for c in data["representative_cases"]], ["fixture-0", "fixture-2", "fixture-1"])
        self.assertIsNone(data["peak_sampled_server_rss_bytes"])

    def test_missing_invalid_or_incomplete_timings_cannot_publish(self):
        path = self.directory / "predictions/fixture-0.json"
        for value in (None, -1, float("nan"), "3"):
            path.write_text(json.dumps({"runtime_seconds": {"model": value}}))
            with self.subTest(value=value), self.assertRaises(ValueError):
                MODULE.supplementary_evidence(self.directory, "model")
        (self.directory / "report.json").write_text(json.dumps({"documents": self.docs, "summary": {"documents_scheduled": 4}}))
        with self.assertRaises(ValueError):
            MODULE.supplementary_evidence(self.directory, "model")

    def test_validation_and_small_test_subsets_cannot_be_labeled_release(self):
        invoice = {"split": "test", "summary": {"documents_scheduled": 180}}
        receipt = {"split": "test", "documents": 100}
        MODULE.require_release_scope(invoice, receipt)
        for changed in ({"split": "validation", "documents": 100}, {"split": "test", "documents": 12}):
            with self.assertRaises(ValueError):
                MODULE.require_release_scope(invoice, changed)

    def test_standalone_report_escapes_failure_ids_and_shows_timing_boundaries(self):
        summary = {"header_fields": {"total": {"f1": .5}}, "header_macro_f1": .5,
                   "row_exact": {"f1": .5}, "eligible_exact_rows": {"f1": .5},
                   "documents_processed": 3, "documents_scheduled": 3, "failures_by_type": {}}
        comparison = {"status": "regression", "delta_model_minus_rules": {"total": 0}, "paired_95_intervals": {"total": [0, 0]}}
        invoice = {"summary": summary, "baseline_summary": summary, "comparison": comparison, "scope": "Fixture only"}
        receipt = {**comparison, "baseline": summary, "model": summary, "documents": 3, "scope": "Fixture only"}
        systems = MODULE.supplementary_evidence(self.directory, "model")
        systems["representative_cases"][0]["id"] = "<script>alert('fixture')</script>"
        rendered = MODULE.render(invoice, receipt, {"Fixture model": systems})
        self.assertIn("Measured stage timings", rendered)
        self.assertIn("Nearest-rank percentiles", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
