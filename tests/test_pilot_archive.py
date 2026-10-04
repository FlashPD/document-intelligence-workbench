"""Portable archival tests on explicitly automated, disposable pilot fixtures."""

import importlib.util
import contextlib
import io
import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from docwork.intake import IntakeStore
from docwork.pilot_bundle import prepare_pilot, report_pilot
from docwork.review_pilot import ReviewPilot

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pilot_archive", ROOT / "scripts/archive_review_pilot.py")
ARCHIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARCHIVE)


class PilotArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "project"
        self.root.mkdir()
        for name in ("datasets/invoices-v1/manifest.json", "evals/invoice-freeze-2026-10-03/development/report.json"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, path)
        self.source = self.root / "artifacts/session"
        protocol = prepare_pilot(ROOT, ROOT / "datasets/invoices-v1/manifest.json",
                                 ROOT / "evals/invoice-freeze-2026-10-03/development", self.source)
        self.store = IntakeStore(self.source / "review.sqlite", self.source / "objects")
        self.now = 10.0
        self.pilot = ReviewPilot(self.source, self.store, clock=lambda: self.now)
        self.addCleanup(self.pilot.close)
        for doc in protocol["documents"]:
            document = doc["document_id"]
            trial = self.pilot.start(document, "automated-archive-fixture")["trials"][-1]["id"]
            for issue in self.store.get(document)["issues"]:
                if issue["blocking"]:
                    self.store.acknowledge(document, 1, issue["code"], issue["path"],
                                           "Automated fixture preserves printed suggestion", "automated-archive-fixture")
            self.store.approve(document, 1, "automated-archive-fixture")
            self.store.export(document, "json")
            self.now += 30
            self.pilot.event(trial, "finish", uuid.uuid4().hex)
        self.report = self.root / "artifacts/report.json"
        self.report.write_text(json.dumps(report_pilot(self.root, self.source), indent=2) + "\n")
        self.output = self.root / "evals/pilot"

    def archive(self):
        self.pilot.close()
        return ARCHIVE.archive_pilot(self.root, self.source, self.report, self.output)

    def test_active_server_cannot_be_archived(self):
        with self.assertRaisesRegex(ValueError, "Stop the pilot server"):
            ARCHIVE.archive_pilot(self.root, self.source, self.report, self.output)
        self.assertFalse(self.output.exists())

    def test_portable_archive_reproduces_report_after_original_session_is_removed(self):
        before = {p.name: p.read_bytes() for p in (self.source / name for name in ARCHIVE.SESSION_FILES)}
        result = self.archive()
        self.assertEqual((result["pilot_status"], result["completed"]), ("complete", 6))
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.source / name for name in ARCHIVE.SESSION_FILES)})
        self.assertEqual(self.report.read_bytes(), (self.output / "report.json").read_bytes())
        shutil.rmtree(self.source)
        relocated = self.root.parent / "different-checkout"
        shutil.copytree(self.root, relocated)
        self.assertEqual(ARCHIVE.verify_archive(relocated, relocated / "evals/pilot"), result)

    def test_restored_session_reproduces_original_approvals_exports_and_timing(self):
        self.archive()
        restored = self.root / "artifacts/restored"
        ARCHIVE.restore_session(self.output, restored)
        self.assertEqual(report_pilot(self.root, restored), json.loads(self.report.read_text()))
        self.assertTrue((restored / "review.sqlite").is_file())
        self.assertTrue((restored / "objects").is_dir())

    def test_mismatched_report_cannot_replace_the_original_result(self):
        self.pilot.close()
        report = json.loads(self.report.read_text())
        report["timing"]["completed_only_active_median_seconds"] = 1
        self.report.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "Supplied report"):
            ARCHIVE.archive_pilot(self.root, self.source, self.report, self.output)
        self.assertFalse(self.output.exists())

    def test_tampered_and_unlisted_archive_files_fail_verification(self):
        self.archive()
        path = self.output / "report.json"
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "inventory"):
            ARCHIVE.verify_archive(self.root, self.output)
        path.write_bytes(original)
        (self.output / "extra.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "inventory"):
            ARCHIVE.verify_archive(self.root, self.output)

    def test_archive_refuses_existing_output_and_symlink_files(self):
        self.archive()
        with self.assertRaisesRegex(ValueError, "new output"):
            ARCHIVE.archive_pilot(self.root, self.source, self.report, self.output)
        (self.output / "link").symlink_to(self.report)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            ARCHIVE.verify_archive(self.root, self.output)

    def test_incomplete_outcomes_are_preserved_but_cannot_pass_the_release_gate(self):
        self.pilot.close()
        timing_path = self.source / "timing.json"
        timing = json.loads(timing_path.read_text())
        trial = timing["trials"][-1]
        trial["status"] = "ABANDONED"
        trial["events"][-1]["kind"] = "abandon"
        trial.pop("final")
        timing_path.write_text(json.dumps(timing))
        self.report.write_text(json.dumps(report_pilot(self.root, self.source)))
        result = self.archive()
        self.assertEqual((result["pilot_status"], result["completed"], result["scheduled"]), ("incomplete", 5, 6))
        with patch("sys.argv", ["archive_review_pilot.py", "verify", str(self.output), "--require-complete"]), \
                patch.object(ARCHIVE, "verify_archive", return_value=result), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exited:
                ARCHIVE.main()
        self.assertEqual(exited.exception.code, 2)
