"""Post-pilot maintenance must preserve outcomes and invalidate approval."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from docwork.intake import IntakeStore
from docwork.pilot_bundle import report_pilot
from docwork.review import ReviewConflict

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pilot_corrections", ROOT / "scripts/correct_pilot_records.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PostPilotCorrectionTests(unittest.TestCase):
    def test_corrections_preserve_original_trial_scores_and_require_new_approval(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session"
            result = MODULE.prepare(ROOT, session)
            original = json.loads((ROOT / MODULE.ARCHIVE_PATH / "report.json").read_text())
            self.assertEqual(report_pilot(ROOT, session), original)
            self.assertEqual(original["completed_only_quality"]["all_required_exact"]["correct"], 5)
            self.assertEqual(original["completed_only_quality"]["row_exact"]["tp"], 19)
            self.assertEqual(result["draft_quality"]["all_required_exact"]["correct"], 6)
            self.assertEqual(result["draft_quality"]["row_exact"]["tp"], 20)
            store = IntakeStore(session / "review.sqlite", session / "objects")
            for correction in result["corrections"]:
                document = correction["document_id"]
                self.assertEqual(correction["revision"], correction["previous_revision"] + 1)
                self.assertIsNotNone(store.get(document, correction["previous_revision"])["approval"])
                self.assertIsNone(store.get(document)["approval"])
                for format in ("json", "csv"):
                    with self.assertRaises(ReviewConflict):
                        store.export(document, format)

    def test_report_reproduces_and_tampered_corrections_cannot_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = MODULE.prepare(ROOT, directory / "session")
            report = directory / "report.json"
            report.write_text(json.dumps(result))
            self.assertEqual(MODULE.verify(ROOT, report)["status"], "verified")
            result["corrections"][0]["after"] = "Another value"
            report.write_text(json.dumps(result))
            with self.assertRaisesRegex(ValueError, "does not reproduce"):
                MODULE.verify(ROOT, report)
