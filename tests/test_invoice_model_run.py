from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from docwork.baseline import extract_invoice_pages
from docwork.invoice_model_run import run_invoice_model, verify_invoice_model
from docwork.local_model import ModelExtraction, ModelUnavailable, PROMPT_SHA256
from docwork.model_runtime import file_hash
import test_heldout
ROOT = test_heldout.ROOT


class InvoiceModelRunTests(unittest.TestCase):
    def setUp(self):
        helper = test_heldout.HeldoutTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.freeze_run()
        helper.evaluate()
        self.helper = helper
        self.output = helper.root / "model"
        self.profile = ROOT / "config/model-mac-instruct.json"
        self.dev = helper.root / "dev-model"
        self.dev.mkdir()
        (self.dev / "report.json").write_text(json.dumps({"extractor": {
            "prompt_sha256": PROMPT_SHA256, "profile_sha256": file_hash(self.profile)}}))
        self.pages = []

        @contextmanager
        def server(root, profile, log):
            log.write_text("fixture runtime")
            yield None, {"shutdown_complete": True, "model_sha256": profile["model"]["sha256"],
                         "runtime_archive_sha256": profile["runtime"]["sha256"], "inference": profile["inference"]}

        def extract(pages, config):
            self.pages.append(pages)
            return ModelExtraction(extract_invoice_pages(pages), ())

        for where, replacement in (("verify_model_evidence", lambda *args: {}),
                                   ("managed_server", server), ("extract_pages", extract)):
            mocking = patch(f"docwork.invoice_model_run.{where}", side_effect=replacement)
            mocking.start()
            self.addCleanup(mocking.stop)

    def run_model(self, resume=False):
        return run_invoice_model(self.helper.root, self.helper.manifest, self.helper.run_dir,
                                 self.dev, self.profile, self.output, resume=resume)

    def test_paired_run_uses_only_saved_ocr_and_verifies_offline(self):
        report = self.run_model()
        self.assertEqual(report["summary"]["documents_scheduled"], 1)
        self.assertEqual(report["comparison"]["delta_model_minus_rules"]["exact_row_f1"], 0)
        self.assertEqual(len(self.pages), 1)
        self.assertEqual(verify_invoice_model(self.helper.manifest, self.helper.run_dir, self.output)["status"], "verified")
        self.assertEqual(self.run_model(resume=True), report)
        self.assertEqual(len(self.pages), 1)

    def test_failed_model_is_scored_and_resumption_does_not_retry_completed_failure(self):
        with patch("docwork.invoice_model_run.extract_pages", side_effect=ModelUnavailable("offline")):
            report = self.run_model()
        self.assertEqual(report["summary"]["documents_processed"], 0)
        self.assertEqual(report["summary"]["failures_by_type"], {"ModelUnavailable": 1})
        self.run_model(resume=True)
        self.assertEqual(self.pages, [])

    def test_interrupted_run_keeps_completed_ledger_and_shutdown_evidence(self):
        with patch("docwork.invoice_model_run.extract_pages", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_model()
        self.assertFalse((self.output / "report.json").exists())
        self.assertTrue((self.output / "sessions/0001/runtime.json").exists())
        self.run_model(resume=True)
        self.assertEqual(len(self.pages), 1)
        self.assertEqual(len(list((self.output / "sessions").iterdir())), 2)

    def test_tampered_prediction_report_and_source_fail_audit(self):
        self.run_model()
        path = self.output / "predictions/invoice-test.json"
        original = path.read_text()
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "completion ledger"):
            verify_invoice_model(self.helper.manifest, self.helper.run_dir, self.output)
        path.write_text(original)
        path = self.output / "source_snapshot.json"
        snapshot = json.loads(path.read_text())
        snapshot["local_model.py"] += "changed"
        path.write_text(json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.run_model(resume=True)

    def test_changed_prompt_cannot_be_selected_from_test(self):
        with patch("docwork.invoice_model_run.PROMPT_SHA256", "different"):
            with self.assertRaisesRegex(ValueError, "development selection"):
                self.run_model()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
