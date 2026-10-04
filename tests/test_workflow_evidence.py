"""Recorded workflow integrity checks; no Docker, HTTP sockets, or model process."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from docwork.workflow_evidence import verify_workflow_evidence

EVIDENCE = Path(__file__).resolve().parents[1] / "evals/real-model-upload-2026-10-03"


class WorkflowEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "evidence"
        shutil.copytree(EVIDENCE, self.directory)

    def test_recorded_real_model_http_workflow_verifies_offline(self):
        self.assertEqual(verify_workflow_evidence(EVIDENCE)["status"], "verified")

    def test_saved_model_or_shutdown_identity_cannot_be_relabelled(self):
        path = self.directory / "report.json"
        original = json.loads(path.read_text())
        for key, value in (("model_sha256", "0" * 64), ("shutdown_complete", False)):
            changed = json.loads(json.dumps(original))
            changed["managed_runtime"][key] = value
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "model/runtime identity differs"):
                verify_workflow_evidence(self.directory)

    def test_export_tampering_and_unexpected_artifacts_are_rejected(self):
        extra = self.directory / "unexpected.txt"
        extra.write_text("not in the recorded bundle")
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            verify_workflow_evidence(self.directory)
        extra.unlink()
        path = self.directory / "exports/clean/json/invoice.json"
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "checksum or path differs"):
            verify_workflow_evidence(self.directory)

    def test_missing_fixture_and_failed_reports_cannot_pass(self):
        path = self.directory / "report.json"
        report = json.loads(path.read_text())
        report["checks"] = report["checks"][:1]
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "fixture coverage differs"):
            verify_workflow_evidence(self.directory)
        report["status"] = "failed"
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "completed successful run"):
            verify_workflow_evidence(self.directory)


if __name__ == "__main__":
    unittest.main()
