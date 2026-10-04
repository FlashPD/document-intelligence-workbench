from __future__ import annotations

import hashlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.backup import create_backup, restore_backup, verify_backup
from docwork.cli import main
from docwork.intake import IntakeStore
from docwork.review import ReviewConflict
from docwork.worker import process_one
from test_worker import LINES, SAMPLE, parser_result

IDENTITY = "sha256:" + "1" * 64


class BackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.store = IntakeStore(self.root / "source" / "review.sqlite", self.root / "source" / "intake")
        self.bundle = self.root / "backup"
        self.restored = self.root / "restored"

    def submit(self):
        return self.store.submit(io.BytesIO(SAMPLE), "clean.png", "image/png")

    def backup(self):
        return create_backup(self.store.database, self.store.object_root, self.bundle)

    def processed(self):
        document = self.submit()
        process_one(self.store, "worker", runner=lambda source, mime, output, claim, image:
                    parser_result(output, source, lines_by_page=(LINES, ("Continuation",))),
                    parser_identity=IDENTITY)
        return document

    def restored_store(self):
        return IntakeStore(self.restored / "database.sqlite", self.restored / "intake")

    def test_portable_restore_preserves_revisions_decisions_approvals_and_export_bytes(self):
        document = self.processed()
        first_approval = self.store.approve(document, 1, "reviewer")
        self.store.export(document, "json")
        self.store.edit(document, 1, "fields.total", "275.00", "reviewer")
        self.store.acknowledge(document, 2, "TOTAL_MISMATCH", "fields.total", "Verified printed amount", "reviewer")
        self.store.approve(document, 2, "reviewer")
        exports = {format: self.store.export(document, format) for format in ("json", "csv")}
        expected = {Path(file["path"]).name: Path(file["path"]).read_bytes()
                    for export in exports.values() for file in export["files"]}
        duplicate = self.processed()
        before = self.store.get(document)
        history = self.store.history(document)
        self.backup()
        # Later live writes must not appear in the snapshot.
        self.store.edit(document, 2, "fields.invoice_number", "LATER", "reviewer")
        result = restore_backup(self.bundle, self.restored)
        self.assertEqual(result["requeued_jobs"], 0)
        restored = self.restored_store()
        self.assertEqual(restored.get(document), before)
        self.assertEqual(restored.history(document), history)
        self.assertEqual(restored.get(document, 1)["approval"]["approval_hash"], first_approval["approval_hash"])
        for format in ("json", "csv"):
            for file in restored.export(document, format)["files"]:
                self.assertTrue(Path(file["path"]).is_relative_to(self.restored))
                self.assertEqual(restored.exported_file(document, 2, format, Path(file["path"]).name)[0],
                                 expected[Path(file["path"]).name])
        self.assertEqual(restored.page_image_path(document, 2).read_bytes(), SAMPLE)
        self.assertEqual(restored.object_path(document).read_bytes(), SAMPLE)
        with self.assertRaises(ReviewConflict):
            restored.export(duplicate, "json")
        restored.edit(document, 2, "fields.total", "270.00", "reviewer")
        with self.assertRaises(ReviewConflict):
            restored.export(document, "json")
        audit = restored.reconcile()
        self.assertFalse(audit["missing"] or audit["corrupt"] or audit["metadata_errors"])

    def test_restore_fences_active_worker_and_reuses_parser_checkpoint(self):
        document = self.submit()
        old_claim = self.store.claim("interrupted-worker")
        # Build a real canonical checkpoint using the existing parser test fixture.
        output = self.root / "parser-output"
        output.mkdir()
        parser_result(output, self.store.object_path(document))
        from docwork.worker import parser_cache_key
        key = parser_cache_key(hashlib.sha256(SAMPLE).hexdigest(), "image/png", IDENTITY)
        self.store.save_parser_checkpoint(old_claim, key, IDENTITY,
                                          (output / "result.json").read_bytes(), (SAMPLE,))
        before = self.store.status(document)
        self.backup()
        result = restore_backup(self.bundle, self.restored)
        self.assertEqual(result["requeued_jobs"], 1)
        restored = self.restored_store()
        with restored._connect() as db:
            job = dict(db.execute("SELECT * FROM jobs WHERE document_id=?", (document,)).fetchone())
        self.assertEqual(job["status"], "QUEUED")
        self.assertIsNone(job["lease_until"])
        self.assertIsNone(job["worker_id"])
        self.assertGreater(job["fence"], old_claim.fence)
        self.assertEqual(job["attempts"], before["job"]["attempts"])
        with self.assertRaises(ReviewConflict):
            restored.fail(old_claim, "STALE_WORKER")
        with patch("docwork.worker._docker_run", side_effect=AssertionError("Parser must not rerun")):
            process_one(restored, "new-worker", parser_identity=IDENTITY,
                        runner=lambda *args, **kwargs: self.fail("Parser must not rerun"))
        self.assertEqual(restored.get(document)["current_revision"], 1)
        self.assertIsNone(restored.get(document)["approval"])
        self.assertIn("backup_restored", [event["kind"] for event in restored.history(document)])
        self.assertEqual(self.store.status(document), before)

    def test_snapshot_excludes_quarantine_orphans_and_later_concurrent_submission(self):
        self.submit()
        (self.store.object_root / "quarantine" / "partial-upload").write_bytes(b"scratch")
        orphan = self.store.object_root / "renders" / "aa" / ("a" * 64 + ".png")
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b"orphan")
        started, finished = threading.Event(), threading.Event()
        errors = []

        def submit_later():
            started.set()
            try:
                self.submit()
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        from docwork.backup import _copy
        threads = []

        def copying(*args, **kwargs):
            if not threads:
                thread = threading.Thread(target=submit_later)
                threads.append(thread)
                thread.start()
                self.assertTrue(started.wait(2))
                self.assertFalse(finished.wait(.1))
            return _copy(*args, **kwargs)

        with patch("docwork.backup._copy", side_effect=copying):
            result = self.backup()
        threads[0].join(5)
        self.assertTrue(finished.is_set())
        self.assertEqual(errors, [])
        self.assertEqual(result["counts"]["documents"], 1)
        self.assertEqual(len(self.store.list_documents()), 2)
        self.assertEqual(result["files"], 2)  # SQLite plus the single referenced original.
        self.assertFalse((self.bundle / "intake" / "quarantine").exists())

    def test_corrupt_or_missing_source_never_publishes_backup(self):
        document = self.submit()
        source = self.store.object_path(document)
        for remove in (False, True):
            with self.subTest(remove=remove):
                if remove:
                    source.unlink()
                else:
                    source.write_bytes(b"corrupt")
                with self.assertRaises(ValueError):
                    self.backup()
                self.assertFalse(self.bundle.exists())

    def test_corrupt_checkpoint_is_rejected(self):
        self.processed()
        with self.store._connect() as db:
            db.execute("UPDATE parser_checkpoints SET result_json='{}'")
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            self.backup()

    def test_tampered_bundle_and_extra_files_refuse_restore(self):
        self.processed()
        self.backup()
        original = (self.bundle / "database.sqlite").read_bytes()
        (self.bundle / "database.sqlite").write_bytes(b"bad")
        with self.assertRaises(ValueError):
            restore_backup(self.bundle, self.restored)
        self.assertFalse(self.restored.exists())
        (self.bundle / "database.sqlite").write_bytes(original)
        (self.bundle / "unexpected.txt").write_text("extra")
        with self.assertRaisesRegex(ValueError, "unlisted"):
            verify_backup(self.bundle)

    def test_traversal_and_symlink_are_rejected_even_with_matching_hashes(self):
        self.submit()
        self.backup()
        manifest_path = self.bundle / "manifest.json"
        original = manifest_path.read_text()
        manifest = json.loads(original)
        manifest["files"][0]["path"] = "../source/review.sqlite"
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            verify_backup(self.bundle)
        manifest_path.write_text(original)
        artifact = self.bundle / manifest["files"][1]["path"]
        external = self.root / "external"
        artifact.rename(external)
        artifact.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            verify_backup(self.bundle)

    def test_existing_destinations_are_never_overwritten(self):
        self.submit()
        self.backup()
        digest = hashlib.sha256((self.bundle / "manifest.json").read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, "new directory"):
            self.backup()
        self.assertEqual(hashlib.sha256((self.bundle / "manifest.json").read_bytes()).hexdigest(), digest)
        self.restored.mkdir()
        (self.restored / "keep").write_text("keep")
        with self.assertRaises(ValueError):
            restore_backup(self.bundle, self.restored)
        self.assertEqual((self.restored / "keep").read_text(), "keep")

    def test_database_inventory_cannot_omit_a_referenced_artifact(self):
        self.submit()
        self.backup()
        manifest_path = self.bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        file = manifest["files"].pop()
        (self.bundle / file["path"]).unlink()
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "database references"):
            verify_backup(self.bundle)

    def test_cli_create_verify_restore_and_missing_database(self):
        self.submit()
        with patch("builtins.print"), patch("docwork.cli.baseline_fixture", side_effect=AssertionError("Backup must not run OCR")):
            self.assertEqual(main(["backup", "create", "--db", str(self.store.database),
                                   "--objects", str(self.store.object_root), "--output", str(self.bundle)]), 0)
            self.assertEqual(main(["backup", "verify", str(self.bundle)]), 0)
            self.assertEqual(main(["backup", "restore", str(self.bundle), "--output", str(self.restored)]), 0)
        missing = self.root / "missing.sqlite"
        with self.assertRaises(SystemExit) as exc, patch("sys.stderr", new=io.StringIO()):
            main(["backup", "create", "--db", str(missing), "--output", str(self.root / "bad")])
        self.assertEqual(exc.exception.code, 2)
        self.assertFalse(missing.exists())

    def test_failed_job_keeps_its_error_and_requires_explicit_retry(self):
        document = self.submit()
        claim = self.store.claim("worker")
        self.store.fail(claim, "MODEL_UNAVAILABLE")
        before = self.store.status(document)
        self.backup()
        result = restore_backup(self.bundle, self.restored)
        self.assertEqual(result["requeued_jobs"], 0)
        restored = self.restored_store()
        self.assertEqual(restored.status(document), before)
        self.assertIsNone(restored.claim("worker"))
        restored.retry(document)
        self.assertEqual(restored.claim("worker").document_id, document)

    def test_corrupt_export_prevents_backup_and_custom_export_root_is_supported(self):
        document = self.processed()
        self.store.export_root = self.root / "custom-exports"
        self.store.approve(document, 1, "reviewer")
        exported = self.store.export(document, "json")
        create_backup(self.store.database, self.store.object_root, self.bundle,
                      export_root=self.store.export_root)
        restore_backup(self.bundle, self.restored)
        self.assertEqual(self.restored_store().exported_file(document, 1, "json", "invoice.json")[0],
                         Path(exported["files"][0]["path"]).read_bytes())
        Path(exported["files"][0]["path"]).write_text("corrupt")
        with self.assertRaises(ValueError):
            create_backup(self.store.database, self.store.object_root, self.root / "corrupt-backup",
                          export_root=self.store.export_root)
        self.assertFalse((self.root / "corrupt-backup").exists())

    def test_copy_failure_never_publishes_or_changes_live_data(self):
        document = self.processed()
        before = self.store.get(document)
        with patch("docwork.backup._copy", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.backup()
        self.assertFalse(self.bundle.exists())
        self.assertEqual(self.store.get(document), before)
        self.backup()
        with patch("docwork.backup._copy", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                restore_backup(self.bundle, self.restored)
        self.assertFalse(self.restored.exists())
        self.assertEqual(verify_backup(self.bundle)["status"], "verified")


if __name__ == "__main__":
    unittest.main()
