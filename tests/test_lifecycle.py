from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.backup import create_backup, restore_backup
from docwork.intake import IntakeStore, JobStopped, processing_profile
from docwork.local_model import LocalModelConfig, ModelUnavailable
from docwork.review import ReviewConflict, ReviewStore
from docwork.storage_budget import StorageLimitExceeded
from docwork.supervisor import WorkerSupervisor
from docwork.worker import process_one
from test_worker import SAMPLE, LINES, parser_result, model_output

IDENTITY = "sha256:" + "a" * 64


def wait_for(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("Timed out waiting for processing state")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = IntakeStore(self.root / "review.sqlite", self.root / "objects", disk_reserve_bytes=0)

    def submit(self, **kwargs):
        return self.store.submit(io.BytesIO(SAMPLE), "clean.png", "image/png", **kwargs)

    def run_job(self, **kwargs):
        def runner(source, mime, output, claim, *, image):
            parser_result(output, source)
        return process_one(self.store, "test-worker", runner=kwargs.pop("runner", runner),
                           parser_identity=IDENTITY, **kwargs)

    def processor(self, store, worker, **kwargs):
        def runner(source, mime, output, claim, *, image):
            parser_result(output, source)
        return process_one(store, worker, runner=runner, parser_identity=IDENTITY, **kwargs)

    def test_profiles_survive_restart_and_missing_model_never_falls_back(self):
        profile = processing_profile("span_llm", model_id="pinned-model", timeout_seconds=150)
        doc = self.submit(profile=profile)
        self.store = IntakeStore(self.root / "review.sqlite", self.root / "objects")
        self.run_job(honor_job_profile=True)
        self.assertEqual(self.store.status(doc)["job"]["error_code"], "MODEL_UNAVAILABLE")
        attempt = self.store.attempts(doc)[0]
        self.assertEqual(json.loads(attempt["profile_json"]), profile)
        self.assertEqual(attempt["status"], "FAILED")
        self.assertNotIn("endpoint", attempt["profile_json"])

    def test_persisted_model_limits_and_credentials_stay_separate(self):
        profile = processing_profile("span_llm", model_id="pinned-model", timeout_seconds=45, max_output_tokens=512)
        doc = self.submit(profile=profile)
        seen = []
        def request(config, payload):
            seen.append((config.timeout_seconds, config.max_output_tokens, config.api_key))
            return model_output()
        self.run_job(honor_job_profile=True, model_config=LocalModelConfig("http://127.0.0.1:8080", "pinned-model", api_key="private-key"), model_request=request)
        self.assertEqual(seen, [(45, 512, "private-key")])
        self.assertNotIn("private-key", self.store.attempts(doc)[0]["profile_json"])
        self.assertEqual(self.store.get(doc)["extraction"]["attempt_id"], self.store.attempts(doc)[0]["id"])

    def test_reprocessing_preserves_sources_exports_and_requires_new_approval(self):
        doc = self.submit()
        self.run_job()
        original = self.store.get(doc)
        self.store.approve(doc, 1, "reviewer")
        export = self.store.export(doc, "json")
        before = Path(export["files"][0]["path"]).read_bytes()
        self.store.reprocess(doc, reparse=True, expected_revision=1)
        for operation in (lambda: self.store.export(doc, "json"), lambda: self.store.approve(doc, 1, "reviewer"),
                          lambda: self.store.edit(doc, 1, "fields.invoice_number", "NEW", "reviewer")):
            with self.assertRaises(ReviewConflict):
                operation()
        new_lines = tuple(line.replace("AST-1001", "AST-2002") for line in LINES)
        def updated(source, mime, output, claim, *, image):
            parser_result(output, source, lines_by_page=(new_lines,))
        self.run_job(runner=updated, honor_job_profile=True)
        current = self.store.get(doc)
        self.assertEqual(current["revision"], 2)
        self.assertEqual(current["record"]["fields"]["invoice_number"]["value"], "AST-2002")
        self.assertEqual(self.store.get(doc, 1)["record"], original["record"])
        self.assertEqual(self.store.get(doc, 1)["pages"], original["pages"])
        self.assertIsNone(current["approval"])
        with self.assertRaises(ReviewConflict):
            self.store.export(doc, "json")
        self.assertEqual(self.store.exported_file(doc, 1, "json", "invoice.json")[0], before)
        self.store.approve(doc, 2, "reviewer")
        self.store.export(doc, "csv")
        self.assertEqual(Path(export["files"][0]["path"]).read_bytes(), before)
        self.assertEqual(len(self.store.attempts(doc)), 2)

    def test_failed_reprocessing_can_retry_without_losing_prior_record(self):
        doc = self.submit()
        self.run_job()
        old = self.store.get(doc)
        self.store.reprocess(doc, profile=processing_profile("span_llm", model_id="missing"), expected_revision=1)
        self.run_job(honor_job_profile=True)
        self.assertEqual(self.store.get(doc)["record"], old["record"])
        self.store.retry(doc)
        self.assertEqual(self.store.status(doc)["current_revision"], 1)
        self.assertEqual(json.loads(self.store.status(doc)["job"]["profile_json"])["model_id"], "missing")

    def test_cancelled_queue_is_never_claimed(self):
        cancelled, next_doc = self.submit(), self.submit()
        self.store.cancel(cancelled)
        self.run_job()
        self.assertEqual(self.store.status(cancelled)["job"]["status"], "CANCELLED")
        self.assertEqual(self.store.status(next_doc)["status"], "REVIEW_READY")

    def test_active_cancellation_fences_publication_and_cleans_scratch(self):
        doc = self.submit()
        entered, release = threading.Event(), threading.Event()
        def runner(source, mime, output, claim, *, image):
            entered.set()
            self.assertTrue(release.wait(3))
            parser_result(output, source)
        errors = []
        def run():
            try:
                self.run_job(runner=runner)
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(entered.wait(2))
        self.store.cancel(doc)
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        status = self.store.status(doc)
        self.assertEqual((status["status"], status["current_revision"]), ("CANCELLED", 0))
        self.assertEqual(list((self.root / "objects/quarantine").iterdir()), [])

    def test_two_supervisors_process_a_batch_with_one_active_job(self):
        batch = self.store.create_batch(20)
        docs = [self.submit(batch_id=batch, batch_position=i) for i in range(20)]
        lock, active, peak = threading.Lock(), [0], [0]
        def processor(store, worker, **kwargs):
            def runner(source, mime, output, claim, *, image):
                with lock:
                    active[0] += 1
                    peak[0] = max(peak[0], active[0])
                try:
                    time.sleep(.01)
                    parser_result(output, source)
                finally:
                    with lock:
                        active[0] -= 1
            return process_one(store, worker, runner=runner, parser_identity=IDENTITY, **kwargs)
        supervisors = [WorkerSupervisor(self.store, processor=processor, cleanup=lambda name: None) for _ in range(2)]
        for supervisor in supervisors:
            self.addCleanup(supervisor.close)
            supervisor.start()
        wait_for(lambda: all(self.store.status(doc)["status"] == "REVIEW_READY" for doc in docs), timeout=8)
        self.assertEqual(peak[0], 1)
        self.assertEqual(len(self.store.batch_status(batch)["items"]), 20)

    def test_background_shutdown_requeues_owned_unfinished_work(self):
        doc = self.submit()
        entered = threading.Event()
        def processor(store, worker, **kwargs):
            def runner(source, mime, output, claim, *, image):
                entered.set()
                self.assertTrue(kwargs["stop_event"].wait(3))
                parser_result(output, source)
            return process_one(store, worker, runner=runner, parser_identity=IDENTITY, **kwargs)
        supervisor = WorkerSupervisor(self.store, processor=processor, cleanup=lambda name: None)
        self.addCleanup(supervisor.close)
        supervisor.start()
        self.assertTrue(entered.wait(2))
        supervisor.close()
        self.assertEqual(self.store.status(doc)["job"]["status"], "QUEUED")
        self.run_job(honor_job_profile=True)
        self.assertEqual(self.store.status(doc)["status"], "REVIEW_READY")

    def test_batch_errors_and_duplicate_slots_remain_accounted_for(self):
        for count in (0, 21, True):
            with self.assertRaises(ValueError):
                self.store.create_batch(count)
        batch = self.store.create_batch(2)
        doc = self.submit(batch_id=batch, batch_position=0)
        self.store.reject_batch_item(batch, 1)
        with self.assertRaises(ReviewConflict):
            self.submit(batch_id=batch, batch_position=0)
        self.assertEqual(self.store.batch_status(batch)["items"][1]["status"], "REJECTED")
        self.store.request_delete(doc)
        self.store.run_deletions()
        self.assertEqual(self.store.batch_status(batch)["items"][0]["status"], "DELETED")

    def test_deletion_retains_shared_content_until_last_reference(self):
        first, second = self.submit(), self.submit()
        self.run_job()
        self.run_job()
        original = self.store.object_path(first)
        rendered = self.store.page_image_path(first)
        self.store.approve(first, 1, "reviewer")
        export = self.store.export(first, "json")
        self.store.request_delete(first)
        for read in (lambda: self.store.get(first), lambda: self.store.history(first),
                     lambda: self.store.exported_file(first, 1, "json", "invoice.json")):
            with self.assertRaises(KeyError):
                read()
        self.assertEqual(self.store.run_deletions(), 1)
        self.assertTrue(original.exists())
        self.assertTrue(rendered.exists())
        self.assertFalse(Path(export["files"][0]["path"]).exists())
        self.assertEqual(self.store.get(second)["revision"], 1)
        self.store.request_delete(second)
        self.store.run_deletions()
        self.assertFalse(original.exists())
        self.assertFalse(rendered.exists())
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT manifest_json FROM deletion_jobs WHERE document_id=?", (first,)).fetchone()[0], "{}")

    def test_interrupted_deletion_resumes_from_manifest(self):
        doc = self.submit()
        self.run_job()
        self.store.request_delete(doc)
        original_delete = self.store._delete_file
        calls = []
        def interrupted(*args, **kwargs):
            calls.append(args)
            if len(calls) == 2:
                raise OSError("simulated interrupted delete")
            original_delete(*args, **kwargs)
        with patch.object(self.store, "_delete_file", side_effect=interrupted):
            self.assertEqual(self.store.run_deletions(), 0)
        self.assertEqual(self.store.deletion_status(doc)["status"], "PENDING")
        self.store = IntakeStore(self.root / "review.sqlite", self.root / "objects")
        self.assertEqual(self.store.run_deletions(), 1)
        self.assertEqual(self.store.deletion_status(doc)["status"], "DELETED")

    def test_pending_deletion_cannot_be_ported_with_foreign_container_ownership(self):
        doc = self.submit()
        self.store.request_delete(doc)
        with self.assertRaisesRegex(ValueError, "Finish pending"):
            create_backup(self.store.database, self.store.object_root, self.root / "backup")

    def test_deletion_waits_for_live_claim_and_retries_unconfirmed_cleanup(self):
        doc = self.submit()
        claim = self.store.claim("stalled")
        self.store.request_delete(doc)
        self.assertEqual(self.store.run_deletions(cleanup=lambda name: None), 0)
        with self.store._connect() as db:
            db.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (claim.job_id,))
        with patch("docwork.worker.remove_owned_container", side_effect=RuntimeError("not confirmed")):
            self.assertEqual(self.store.run_deletions(), 0)
        self.assertEqual(self.store.deletion_status(doc)["error_code"], "DELETION_RETRY_REQUIRED")
        self.assertEqual(self.store.run_deletions(cleanup=lambda name: None), 1)
        with self.assertRaises(ReviewConflict):
            self.store.check_claim(claim)

    def test_deletion_rejects_path_tampering_and_symlinks(self):
        doc = self.submit()
        self.store.request_delete(doc)
        outside = self.root / "unrelated.txt"
        outside.write_text("preserve")
        with self.store._connect() as db:
            db.execute("UPDATE deletion_jobs SET manifest_json=? WHERE document_id=?",
                       (json.dumps({"intake": ["objects/../../unrelated.txt"], "exports": [], "containers": []}), doc))
        self.assertEqual(self.store.run_deletions(), 0)
        self.assertEqual(outside.read_text(), "preserve")

    def test_revision_render_history_survives_reconcile_and_portable_restore(self):
        doc = self.submit()
        self.run_job()
        old_render = self.store.page_image_path(doc, revision=1)
        self.store.reprocess(doc, reparse=True)
        def updated(source, mime, output, claim, *, image):
            parser_result(output, source)
            raster = output / "page-0001.png"
            raster.write_bytes(SAMPLE + b"new-render")
            manifest = json.loads((output / "result.json").read_text())
            import hashlib
            manifest["pages"][0]["page_sha256"] = hashlib.sha256(raster.read_bytes()).hexdigest()
            (output / "result.json").write_text(json.dumps(manifest))
        self.run_job(runner=updated, honor_job_profile=True)
        self.assertNotEqual(self.store.page_image_path(doc), old_render)
        self.assertEqual(self.store.reconcile(prune=True, min_age_seconds=0)["removed"], [])
        create_backup(self.store.database, self.store.object_root, self.root / "backup")
        report = restore_backup(self.root / "backup", self.root / "restored")
        restored = IntakeStore(Path(report["database"]), Path(report["objects"]))
        self.assertEqual(restored.page_image_path(doc, revision=1).read_bytes(), SAMPLE)
        self.assertEqual(restored.get(doc, 1)["pages"], self.store.get(doc, 1)["pages"])

    def test_budget_and_disk_refusal_leave_no_submission_or_quarantine(self):
        self.store.storage_budget.maximum = self.store.storage_budget.inventory()["used_bytes"] + 1
        with self.assertRaises(StorageLimitExceeded):
            self.submit()
        self.assertEqual(self.store.list_documents(), [])
        self.assertEqual(list((self.root / "objects/quarantine").iterdir()), [])
        self.store.storage_budget.maximum = 20 * 1024**3
        with patch("docwork.storage_budget.shutil.disk_usage", return_value=type("Disk", (), {"free": 0})()):
            with self.assertRaises(StorageLimitExceeded):
                self.submit()
        self.assertEqual(self.store.list_documents(), [])

    def test_checkpoint_quota_failure_publishes_no_metadata_or_render(self):
        doc = self.submit()
        claim = self.store.claim("worker")
        self.store.storage_budget.maximum = self.store.storage_budget.inventory()["used_bytes"] + 100
        with self.assertRaises(StorageLimitExceeded):
            self.store.save_parser_checkpoint(claim, "cache", IDENTITY, b"{}", (SAMPLE,))
        self.assertIsNone(self.store.status(doc)["parser_checkpoint"])
        self.assertFalse((self.root / "objects/renders").exists())

    def test_review_cli_inherits_persisted_budget_for_exports(self):
        doc = self.submit()
        self.run_job()
        self.store.approve(doc, 1, "reviewer")
        with self.store._connect() as db:
            db.execute("UPDATE storage_policy SET max_artifact_bytes=1")
        review = ReviewStore(self.store.database)
        with self.assertRaises(StorageLimitExceeded):
            review.export(doc, "json")
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exports").fetchone()[0], 0)

    def test_invalid_profile_cannot_store_credentials_or_unbounded_requests(self):
        for profile in ({"extractor": "span_llm", "model_id": "model", "api_key": "private"},
                        {"extractor": "span_llm", "model_id": "model", "timeout_seconds": 999},
                        {"extractor": "span_llm", "model_id": "model", "timeout_seconds": True}):
            with self.assertRaises(ValueError):
                self.submit(profile=profile)

    def test_restore_preserves_inflight_cancellation(self):
        doc = self.submit()
        self.store.claim("worker")
        self.store.cancel(doc)
        create_backup(self.store.database, self.store.object_root, self.root / "backup")
        report = restore_backup(self.root / "backup", self.root / "restored")
        restored = IntakeStore(Path(report["database"]), Path(report["objects"]))
        self.assertEqual(restored.status(doc)["status"], "CANCELLED")
        self.assertEqual(report["requeued_jobs"], 0)
        self.assertIsNone(restored.claim("restored-worker"))

    def test_cleanup_blocks_retry_and_serial_claims_until_confirmed(self):
        doc, waiting = self.submit(), self.submit()
        claim = self.store.claim("worker")
        self.store.cleanup_failed(claim)
        with self.assertRaises(ReviewConflict):
            self.store.retry(doc)
        with self.assertRaises(ReviewConflict):
            self.store.reprocess(doc)
        self.assertIsNone(self.store.claim("other"))
        self.store.recover_stops(cleanup=lambda name: None)
        self.assertEqual(self.store.claim("other").document_id, waiting)

    def test_expired_background_claim_requires_confirmed_cleanup(self):
        doc = self.submit()
        claim = self.store.claim("crashed")
        scratch = self.store.object_root / "quarantine" / f"parse-{claim.job_id[:16]}-{claim.fence}-orphan"
        scratch.mkdir()
        (scratch / "private-ocr.txt").write_text("fixture")
        with self.store._connect() as db:
            db.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (claim.job_id,))
        self.assertIsNone(self.store.claim("new", reclaim_expired=False))
        with self.assertRaises(ReviewConflict):
            self.store.check_claim(claim)
        removed = []
        self.store.recover_stops(cleanup=removed.append)
        self.assertEqual(removed, [f"docwork-{claim.job_id[:16]}-{claim.fence}"])
        self.assertFalse(scratch.exists())
        self.assertEqual(self.store.claim("new", reclaim_expired=False).document_id, doc)

    def test_deletion_does_not_follow_a_symlink(self):
        doc = self.submit()
        path = self.store.object_path(doc)
        outside = self.root / "outside.png"
        outside.write_bytes(SAMPLE)
        path.unlink()
        path.symlink_to(outside)
        self.store.request_delete(doc)
        self.assertEqual(self.store.run_deletions(), 0)
        self.assertEqual(outside.read_bytes(), SAMPLE)
        self.assertEqual(self.store.deletion_status(doc)["status"], "PENDING")

    def test_partial_export_write_never_registers_or_leaves_temporary_files(self):
        doc = self.submit()
        self.run_job()
        self.store.approve(doc, 1, "reviewer")
        with patch("docwork.review.os.link", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.store.export(doc, "json")
        self.assertFalse(list(self.store.export_root.rglob(".writing-*")))
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM exports").fetchone()[0], 0)

    def test_second_csv_failure_cleans_the_first_file(self):
        doc = self.submit()
        self.run_job()
        self.store.approve(doc, 1, "reviewer")
        from docwork.review import _atomic_write
        calls = []
        def write(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise OSError("disk full")
            return _atomic_write(path, data)
        with patch("docwork.review._atomic_write", side_effect=write):
            with self.assertRaises(OSError):
                self.store.export(doc, "csv")
        self.assertFalse(list(self.store.export_root.rglob("*.csv")))

    def test_unexpected_extractor_exception_is_an_explicit_failure(self):
        doc = self.submit()
        def broken(*args, **kwargs):
            raise RuntimeError("private document details must not escape")
        self.run_job(runner=broken)
        self.assertEqual(self.store.status(doc)["job"]["error_code"], "PROCESSING_FAILED")
        self.assertEqual(self.store.status(doc)["current_revision"], 0)

    def test_storage_budget_also_guards_approval_and_issue_decisions(self):
        clean = self.submit()
        self.run_job()
        conflict = self.submit()
        def conflicting(source, mime, output, claim, *, image):
            parser_result(output, source, lines_by_page=(tuple(line.replace("Total: 270.00", "Total: 275.00") for line in LINES),))
        self.run_job(runner=conflicting)
        issue = next(item for item in self.store.get(conflict)["issues"] if item["blocking"])
        self.store.storage_budget.maximum = self.store.storage_budget.inventory()["used_bytes"] + 1
        with self.assertRaises(StorageLimitExceeded):
            self.store.approve(clean, 1, "reviewer")
        with self.assertRaises(StorageLimitExceeded):
            self.store.acknowledge(conflict, 1, issue["code"], issue["path"], "keep printed value", "reviewer")
        self.assertIsNone(self.store.get(clean)["approval"])
        self.assertEqual(self.store.get(conflict)["decisions"], [])


if __name__ == "__main__":
    unittest.main()
