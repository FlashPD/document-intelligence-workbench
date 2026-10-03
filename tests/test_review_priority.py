from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from docwork.baseline import extract_invoice
from docwork.contracts import Box, DocumentPage, TextSpan
from docwork.intake import IntakeStore
from docwork.priority_evaluation import priority_report, verify_priority_report, write_priority_report
from docwork.review_priority import PRIORITY_VERSION, score_review_priority

ROOT = Path(__file__).resolve().parents[1]
RECORDED_RUN = ROOT / "evals/invoice-development-2026-10-03/ocr-rules-v0.3-psm1"
CALIBRATION_RUN = ROOT / "evals/invoice-calibration-2026-10-03/ocr-rules-v0.3-psm1"
MANIFEST = ROOT / "datasets/invoices-v1/manifest.json"


def _candidate(total: str):
    lines = (
        "Aster Studio LLC", "INVOICE", "Invoice Number: AST-1001", "Date: 2026-09-12",
        "Due Date: 2026-10-12", "Currency: USD", "Research workshop 2 125.00 250.00",
        "Subtotal: 250.00", "Tax: 20.00", "Discount: 0.00", "Shipping: 0.00",
        f"Total: {total}",
    )
    page = DocumentPage(1, 1000, 1000, tuple(
        TextSpan(f"s{index}", 1, line, Box(.1, .02 + index * .07, .9, .05 + index * .07), "fixture")
        for index, line in enumerate(lines)
    ))
    return page, extract_invoice(page)


class ReviewPriorityTests(unittest.TestCase):
    def test_points_are_explainable_and_unknown_issues_remain_visible(self):
        issues = ["TOTAL_NOT_CHECKED", {"code": "REQUIRED_MISSING", "path": "fields.total"},
                  {"code": "NEW_PARSER_WARNING", "path": "page.1"}]
        result = score_review_priority(issues)
        self.assertEqual(result["version"], PRIORITY_VERSION)
        self.assertEqual(result["points"], 14)
        self.assertEqual([item["code"] for item in result["signals"]],
                         ["REQUIRED_MISSING", "NEW_PARSER_WARNING", "TOTAL_NOT_CHECKED"])
        self.assertEqual(score_review_priority([])["points"], 0)
        self.assertEqual(score_review_priority([], failed=True)["points"], 100)
        with self.assertRaises(ValueError):
            score_review_priority([{"path": "fields.total"}])

    def test_queue_priority_changes_with_revision_without_erasing_source_issue(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            store = IntakeStore(root / "review.sqlite", root / "objects")
            clean_page, clean_record = _candidate("270.00")
            clean = store.ingest(hashlib.sha256(b"clean").hexdigest(), "clean.png", clean_page, clean_record)
            conflict_page, conflict_record = _candidate("275.00")
            conflict = store.ingest(hashlib.sha256(b"conflict").hexdigest(), "conflict.png", conflict_page, conflict_record)
            self.assertEqual(store.list_documents()[0]["id"], conflict)
            self.assertEqual(store.get(conflict)["review_priority"]["points"], 5)
            self.assertEqual(store.get(clean)["review_priority"]["points"], 0)
            store.edit(conflict, 1, "fields.total", "270.00", "reviewer")
            self.assertEqual(store.get(conflict)["review_priority"]["points"], 0)
            self.assertEqual(store.get(conflict, 1)["review_priority"]["points"], 5)
            store.approve(clean, 1, "reviewer")
            self.assertEqual(store.status(clean)["status"], "APPROVED")
            self.assertEqual(store.list_documents()[-1]["id"], clean)
            store.edit(clean, 1, "fields.total", "270.00", "reviewer")
            self.assertEqual(store.status(clean)["status"], "REVIEW_READY")
            self.assertTrue(all("issues_json" not in item for item in store.list_documents()))

    def test_recorded_development_curve_is_reproducible_and_counts_unknown_labels(self):
        report = priority_report(MANIFEST, RECORDED_RUN)
        self.assertEqual(report["documents_scheduled"], 180)
        self.assertEqual(report["curve"][-1]["accepted"], 180)
        self.assertEqual(report["curve"][-1]["critical_label_unknown"], 14)
        self.assertEqual(report["curve"][-1]["critical_errors"], 16)
        self.assertEqual(report["injected_issue_detection"]["TOTAL_MISMATCH"]["flagged"], 24)
        self.assertGreater(report["curve"][0]["critical_error_parent_bootstrap_p95"], 0)
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "priority.json"
            self.assertEqual(write_priority_report(MANIFEST, RECORDED_RUN, path), report)
            self.assertEqual(verify_priority_report(MANIFEST, RECORDED_RUN, path)["status"], "verified")
            changed = json.loads(path.read_text())
            changed["curve"][0]["critical_errors"] += 1
            path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                verify_priority_report(MANIFEST, RECORDED_RUN, path)

    def test_calibration_keeps_its_own_labels_and_surfaces_unchecked_totals(self):
        report = priority_report(MANIFEST, CALIBRATION_RUN)
        self.assertEqual(report["run_identity"]["split"], "calibration")
        self.assertEqual(report["curve"][0]["accepted"], 33)
        self.assertGreater(report["curve"][0]["critical_error_upper_95_independent_docs"], .01)
        totals = report["injected_issue_detection"]["TOTAL_MISMATCH"]
        self.assertEqual((totals["expected"], totals["flagged"], totals["missed"]), (24, 11, 13))
        self.assertEqual(totals["not_checked_when_missed"], 13)


if __name__ == "__main__":
    unittest.main()
