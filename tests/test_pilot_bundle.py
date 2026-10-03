from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from docwork.intake import IntakeStore
from docwork.pilot_bundle import prepare_pilot, report_pilot, verify_pilot_setup
from docwork.review import ReviewConflict
from docwork.review_pilot import ReviewPilot

ROOT = Path(__file__).resolve().parents[1]


class PilotBundleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "pilot"
        self.protocol = prepare_pilot(ROOT, ROOT / "datasets/invoices-v1/manifest.json",
                                      ROOT / "evals/invoice-freeze-2026-10-03/development",
                                      self.directory, document_ids=("inv-f02-02",))
        self.store = IntakeStore(self.directory / "review.sqlite", self.directory / "objects")
        self.now = 10.0
        self.pilot = ReviewPilot(self.directory, self.store, clock=lambda: self.now)
        self.addCleanup(self.pilot.close)

    def complete(self):
        doc_id = self.protocol["documents"][0]["document_id"]
        trial_id = self.pilot.start(doc_id, "fixture-test")["trials"][0]["id"]
        self.store.approve(doc_id, 1, "fixture-test")
        self.store.export(doc_id, "json")
        self.now = 40
        self.pilot.event(trial_id, "finish", uuid.uuid4().hex)
        return doc_id

    def test_preparation_freezes_replay_and_report_never_invents_human_time(self):
        verify_pilot_setup(ROOT, self.directory, require_current_source=True)
        doc_id = self.protocol["documents"][0]["document_id"]
        self.assertEqual(self.store.get(doc_id)["extraction"]["profile"], "replay_ocr_rules")
        self.assertTrue(self.store.page_image_path(doc_id).is_file())
        report = report_pilot(ROOT, self.directory)
        self.assertEqual((report["status"], report["completed"], report["scheduled"]), ("incomplete", 0, 1))
        self.assertIsNone(report["timing"]["completed_only_active_median_seconds"])
        self.assertIsNone(report["completed_only_quality"])

    def test_completed_fixture_report_checks_approval_export_and_final_quality(self):
        self.complete()
        report = report_pilot(ROOT, self.directory)
        self.assertEqual((report["status"], report["completed"]), ("complete", 1))
        self.assertEqual(report["timing"]["completed_only_active_median_seconds"], 30)
        self.assertEqual(report["completed_only_quality"]["documents_scheduled"], 1)

    def test_replaced_export_refuses_pilot_report(self):
        doc_id = self.complete()
        manifest = self.store.export(doc_id, "json")
        Path(manifest["files"][0]["path"]).write_text("corrupt")
        with self.assertRaises(ReviewConflict):
            report_pilot(ROOT, self.directory)

    def test_setup_refuses_overwrite_and_source_snapshot_changes(self):
        with self.assertRaises(ValueError):
            prepare_pilot(ROOT, ROOT / "datasets/invoices-v1/manifest.json",
                          ROOT / "evals/invoice-freeze-2026-10-03/development", self.directory)
        snapshot = json.loads((self.directory / "source_snapshot.json").read_text())
        snapshot["ui/app.js"] += "\nchanged"
        (self.directory / "source_snapshot.json").write_text(json.dumps(snapshot))
        with self.assertRaises(ValueError):
            verify_pilot_setup(ROOT, self.directory)

    def test_report_recomputes_record_hash_instead_of_trusting_database_column(self):
        self.complete()
        doc_id = self.protocol["documents"][0]["document_id"]
        with self.store._connect() as db:
            record = self.store.get(doc_id)["record"]
            record["fields"]["total"]["value"] = "9999.00"
            db.execute("UPDATE revisions SET record_json=? WHERE document_id=? AND revision=1", (json.dumps(record), doc_id))
        with self.assertRaisesRegex(ValueError, "initial candidate"):
            report_pilot(ROOT, self.directory)
