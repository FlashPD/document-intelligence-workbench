"""Real subprocess/WAL fault checks on injected saved OCR, without Docker."""
from __future__ import annotations

import io
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.intake import IntakeStore
from docwork.review import ReviewConflict
from docwork.worker import process_one
from test_worker import SAMPLE, parser_result

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_stage_recovery as recovery
sys.path.pop(0)


class StageRecoveryTests(unittest.TestCase):
    def prepare(self, case):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        store = recovery.open_store(folder)
        document = store.submit(io.BytesIO(SAMPLE), "fictional.png", "image/png")
        def runner(source, media, output, claim, *, image):
            parser_result(output, source)
        process_one(store, "fixture", runner=runner)
        if not case.startswith("approval_"):
            store.approve(document, 1, recovery.ACTOR)
            store.export(document, "csv" if case == "json_transaction" else "json")
        marker, log = folder / "marker.json", folder / "child.log"
        process, stream = recovery.launch(folder, document, case, "unused", marker, log)
        try:
            result = process.wait(timeout=15)
        finally:
            recovery.stop_session(process)
            stream.close()
        self.assertEqual(result, recovery.EXIT_CODE, log.read_text())
        self.assertTrue(json.loads(marker.read_text())["boundary_reached"])
        reopened = recovery.open_store(folder)
        with reopened._connect() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        return reopened, document, json.loads(marker.read_text())

    def test_real_process_exit_rolls_back_edit_or_preserves_commit_and_stale_guard(self):
        for case in ("review_transaction", "review_committed"):
            with self.subTest(case=case):
                store, document, marker = self.prepare(case)
                detail = store.get(document)
                committed = case.endswith("committed")
                self.assertEqual(detail["revision"], 2 if committed else 1)
                self.assertEqual(detail["record"]["fields"]["invoice_number"]["value"],
                                 recovery.EDIT_VALUE if committed else "AST-1001")
                self.assertEqual(detail["approval"] is None, committed)
                self.assertEqual(sum(event["kind"] == "field_edited" for event in store.history(document)), int(committed))
                if committed:
                    with self.assertRaises(ReviewConflict):
                        store.edit(document, 1, "fields.invoice_number", "STALE", recovery.ACTOR)
                    with self.assertRaises(ReviewConflict):
                        store.export(document, "csv")
                self.assertTrue(store.exported_file(document, 1, "json", "invoice.json")[0])

    def test_real_process_exit_rolls_back_approval_or_preserves_idempotent_commit(self):
        for case in ("approval_transaction", "approval_committed"):
            with self.subTest(case=case):
                store, document, _ = self.prepare(case)
                committed = case.endswith("committed")
                self.assertEqual(store.get(document)["approval"] is not None, committed)
                first = store.approve(document, 1, recovery.ACTOR)
                self.assertEqual(store.approve(document, 1, recovery.ACTOR), first)
                self.assertEqual(sum(event["kind"] == "approved" for event in store.history(document)), 1)

    def test_partial_export_is_not_downloadable_and_retry_preserves_written_bytes(self):
        for case in ("csv_first_file", "csv_transaction", "csv_committed", "json_transaction"):
            with self.subTest(case=case):
                store, document, _ = self.prepare(case)
                interrupted = recovery.snapshot(store, document)
                kind = "json" if case.startswith("json_") else "csv"
                if case != "csv_committed":
                    self.assertTrue(recovery.deny_export(store, document, kind, 1))
                first = store.export(document, kind)
                self.assertEqual(store.export(document, kind), first)
                for name, sha in interrupted["export_files"].items():
                    self.assertEqual(recovery.digest(store.export_root / name), sha)
                after = recovery.snapshot(store, document)
                self.assertEqual(after["counts"]["exports"], 2)
                self.assertEqual(sum(event["kind"] == "exported" for event in after["history"]), 2)

    def test_empty_failed_missing_and_reordered_saved_schedules_cannot_verify(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = {"report_version": "stage-recovery-v1", "status": "passed", "inputs_unchanged": True,
                      "lease_seconds": recovery.LEASE_SECONDS,
                      "parser_image_id": "sha256:" + "a" * 64,
                      "checks": [{"id": case, "status": "passed"} for case in recovery.CHECKS]}
            for change in (lambda r: r.update(status="failed"), lambda r: r.update(inputs_unchanged=False),
                           lambda r: r.update(checks=[]), lambda r: r["checks"].pop(),
                           lambda r: r["checks"].reverse(), lambda r: r["checks"][0].update(status="failed")):
                modified = json.loads(json.dumps(report))
                change(modified)
                recovery.write_json(directory / "report.json", modified)
                with self.assertRaises(ValueError):
                    recovery.verify_saved(directory)

    def archive_header(self, directory):
        # Synthetic archive header for negative integrity checks; no live claims.
        names = ("src/docwork/intake.py", "src/docwork/review.py", "src/docwork/worker.py",
                 "src/docwork/lifecycle.py", "src/docwork/supervisor.py", "scripts/verify_stage_recovery.py",
                 "tests/test_stage_recovery.py", "sandbox/Dockerfile", "sandbox/parser-build-lock.json", ".dockerignore")
        sources = {name: "synthetic test snapshot" for name in names}
        recovery.write_json(directory / "source_snapshot.json", sources)
        (directory / "input.png").write_bytes(b"synthetic input")
        report = {"report_version": "stage-recovery-v1", "status": "passed", "inputs_unchanged": True,
                  "lease_seconds": recovery.LEASE_SECONDS, "parser_image_id": "sha256:" + "a" * 64,
                  "checks": [{"id": case, "status": "passed"} for case in recovery.CHECKS],
                  "source_sha256": {name: hashlib.sha256(value.encode()).hexdigest() for name, value in sources.items()}}
        report["source_sha256"]["samples/clean.png"] = recovery.digest(directory / "input.png")
        return report

    def seal(self, directory, report):
        report["artifacts"] = {str(path.relative_to(directory)): recovery.digest(path)
                               for path in directory.rglob("*") if path.is_file() and path.name != "report.json"}
        recovery.write_json(directory / "report.json", report)

    def test_archive_rejects_tampering_extra_files_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = self.archive_header(directory)
            self.seal(directory, report)
            (directory / "input.png").write_bytes(b"substituted bytes")
            with self.assertRaisesRegex(ValueError, "inventory/checksum"):
                recovery.verify_saved(directory)
            self.seal(directory, report)
            (directory / "extra.txt").write_text("extra")
            with self.assertRaisesRegex(ValueError, "inventory/checksum"):
                recovery.verify_saved(directory)
            (directory / "extra.txt").unlink()
            (directory / "link").symlink_to(directory / "input.png")
            with self.assertRaisesRegex(ValueError, "Symlink"):
                recovery.verify_saved(directory)

    def test_archive_rejects_rehashed_source_substitution_and_missing_runtime_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = self.archive_header(directory)
            snapshot = directory / "source_snapshot.json"
            sources = json.loads(snapshot.read_text())
            sources["src/docwork/worker.py"] = "changed"
            recovery.write_json(snapshot, sources)
            self.seal(directory, report)
            with self.assertRaisesRegex(ValueError, "Source snapshot differs"):
                recovery.verify_saved(directory)
            del sources["src/docwork/worker.py"]
            recovery.write_json(snapshot, sources)
            self.seal(directory, report)
            with self.assertRaisesRegex(ValueError, "omits"):
                recovery.verify_saved(directory)

    def test_archive_rejects_rehashed_input_substitution_and_mutable_image_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            report = self.archive_header(directory)
            (directory / "input.png").write_bytes(b"changed input")
            self.seal(directory, report)
            with self.assertRaisesRegex(ValueError, "Input differs"):
                recovery.verify_saved(directory)
            report["parser_image_id"] = "docwork-parser:v3"
            self.seal(directory, report)
            with self.assertRaisesRegex(ValueError, "immutable parser"):
                recovery.verify_saved(directory)

    def test_owned_session_cleanup_does_not_target_existing_host_processes(self):
        with patch.object(recovery.os, "killpg", side_effect=ProcessLookupError) as kill:
            from unittest.mock import Mock
            process = Mock(pid=12345)
            recovery.stop_session(process)
            kill.assert_called_once_with(process.pid, recovery.signal.SIGKILL)
            process.wait.assert_called_once_with(timeout=10)


if __name__ == "__main__":
    unittest.main()
