from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from unittest.mock import patch

from docwork.local_model import LocalModelConfig, ModelUnavailable
from docwork.review import ReviewBlocked, ReviewConflict
from docwork.worker import ParserFailure, _docker_image_id, parser_cache_key, process_one
import test_worker
from test_worker import SAMPLE, model_output, parser_result

IDENTITY = "sha256:" + "1" * 64
OTHER_IDENTITY = "sha256:" + "2" * 64


class ParserCheckpointTests(unittest.TestCase):
    setUp = test_worker.WorkerTests.setUp
    submit = test_worker.WorkerTests.submit

    def run_job(self, *, identity=IDENTITY, model=False, request=None, reparse=False):
        def runner(source, mime, output, claim, *, image):
            self.parser_calls += 1
            parser_result(output, source)

        return process_one(self.store, "checkpoint-worker", runner=runner, parser_identity=identity,
                           reparse=reparse, extractor="span_llm" if model else "ocr_rules",
                           model_config=LocalModelConfig("http://127.0.0.1:8080", "new-model") if model else None,
                           model_request=request)

    def prepare_failed_model(self):
        self.parser_calls = 0
        document_id = self.submit()

        def unavailable(config, payload):
            self.assertEqual(list((self.store.object_root / "quarantine").iterdir()), [])
            raise ModelUnavailable("offline")

        self.run_job(model=True, request=unavailable)
        self.assertEqual(self.store.status(document_id)["job"]["error_code"], "MODEL_UNAVAILABLE")
        self.assertIsNotNone(self.store.status(document_id)["parser_checkpoint"])
        with self.assertRaises(ReviewBlocked):
            self.store.get(document_id)
        return document_id

    def test_model_retry_after_reopening_reuses_ocr_and_creates_fresh_candidate(self):
        document_id = self.prepare_failed_model()
        self.store = type(self.store)(self.store.database, self.store.object_root)
        self.store.retry(document_id)
        self.run_job(model=True, request=lambda config, payload: model_output())
        self.assertEqual(self.parser_calls, 1)
        detail = self.store.get(document_id)
        self.assertEqual(detail["current_revision"], 1)
        self.assertEqual(detail["extraction"]["model_id"], "new-model")
        self.assertEqual(detail["decisions"], [])
        self.assertIsNone(detail["approval"])
        self.assertEqual(detail["record"]["fields"]["total"]["value"], "270.00")
        kinds = [event["kind"] for event in self.store.history(document_id)]
        self.assertEqual(kinds.count("parser_checkpoint_saved"), 1)
        self.assertEqual(kinds.count("parser_checkpoint_reused"), 1)
        self.assertEqual(kinds.count("candidate_created"), 1)
        self.assertEqual(list((self.store.object_root / "quarantine").iterdir()), [])

    def test_switching_extractor_reuses_only_parsing(self):
        document_id = self.prepare_failed_model()
        self.store.retry(document_id)
        self.run_job()
        self.assertEqual(self.parser_calls, 1)
        self.assertEqual(self.store.get(document_id)["extraction"]["profile"], "ocr_rules")

    def test_changed_image_invalidates_checkpoint(self):
        document_id = self.prepare_failed_model()
        old_key = self.store.status(document_id)["parser_checkpoint"]["cache_key"]
        self.store.retry(document_id)
        self.run_job(identity=OTHER_IDENTITY)
        self.assertEqual(self.parser_calls, 2)
        checkpoint = self.store.status(document_id)["parser_checkpoint"]
        self.assertNotEqual(checkpoint["cache_key"], old_key)
        self.assertEqual(checkpoint["parser_identity"], OTHER_IDENTITY)

    def test_checkpoint_key_binds_source_mime_image_and_host_contract(self):
        source = hashlib.sha256(SAMPLE).hexdigest()
        key = parser_cache_key(source, "image/png", IDENTITY)
        self.assertEqual(key, parser_cache_key(source, "image/png", IDENTITY))
        self.assertNotEqual(key, parser_cache_key("0" * 64, "image/png", IDENTITY))
        self.assertNotEqual(key, parser_cache_key(source, "image/jpeg", IDENTITY))
        self.assertNotEqual(key, parser_cache_key(source, "image/png", OTHER_IDENTITY))
        with patch("docwork.worker.CHECKPOINT_VERSION", "next-contract"):
            self.assertNotEqual(key, parser_cache_key(source, "image/png", IDENTITY))
        with patch("docwork.worker.Path.read_bytes", return_value=b"changed host code"):
            self.assertNotEqual(key, parser_cache_key(source, "image/png", IDENTITY))

    def test_corrupt_manifest_is_explicit_and_reparse_refreshes_it(self):
        document_id = self.prepare_failed_model()
        with self.store._connect() as db:
            db.execute("UPDATE parser_checkpoints SET result_json='{}' WHERE document_id=?", (document_id,))
        self.store.retry(document_id)
        self.run_job()
        self.assertEqual(self.parser_calls, 1)
        self.assertEqual(self.store.status(document_id)["job"]["error_code"], "PARSER_CHECKPOINT_INVALID")
        self.store.retry(document_id)
        self.run_job(reparse=True)
        self.assertEqual(self.parser_calls, 2)
        self.assertEqual(self.store.status(document_id)["status"], "REVIEW_READY")

    def test_hash_matching_manifest_is_still_revalidated(self):
        document_id = self.prepare_failed_model()
        with self.store._connect() as db:
            encoded = db.execute("SELECT result_json FROM parser_checkpoints WHERE document_id=?", (document_id,)).fetchone()[0]
            result = json.loads(encoded)
            result["pages"][0]["page"]["width_px"] = 1
            encoded = json.dumps(result)
            db.execute("UPDATE parser_checkpoints SET result_json=?,result_sha256=? WHERE document_id=?",
                       (encoded, hashlib.sha256(encoded.encode()).hexdigest(), document_id))
        self.store.retry(document_id)
        self.run_job()
        self.assertEqual(self.store.status(document_id)["job"]["error_code"], "PARSER_CHECKPOINT_INVALID")
        self.assertEqual(self.parser_calls, 1)

    def test_missing_corrupt_symlink_and_unsafe_render_never_reach_extraction(self):
        for damage in ("missing", "corrupt", "symlink", "unsafe_reference"):
            with self.subTest(damage=damage):
                self.setUp()
                document_id = self.prepare_failed_model()
                with self.store._connect() as db:
                    relative = db.execute("SELECT image_relpath FROM parser_checkpoint_pages WHERE document_id=?",
                                          (document_id,)).fetchone()[0]
                    if damage == "unsafe_reference":
                        db.execute("UPDATE parser_checkpoint_pages SET image_relpath='../outside' WHERE document_id=?",
                                   (document_id,))
                path = self.store.object_root / relative
                if damage == "missing":
                    path.unlink()
                elif damage == "corrupt":
                    path.write_bytes(b"changed")
                elif damage == "symlink":
                    path.unlink()
                    path.symlink_to(self.store.object_path(document_id))
                self.store.retry(document_id)
                with patch("docwork.worker.extract_pages") as extract:
                    self.run_job(model=True)
                extract.assert_not_called()
                self.assertEqual(self.store.status(document_id)["job"]["error_code"], "PARSER_CHECKPOINT_INVALID")
                self.assertEqual(self.parser_calls, 1)

    def test_reconciliation_protects_checkpoint_renders_before_candidate(self):
        document_id = self.prepare_failed_model()
        report = self.store.reconcile(prune=True, min_age_seconds=0)
        self.assertEqual(report["referenced"], 2)
        self.assertEqual(report["orphans"], [])
        self.assertEqual(report["removed"], [])
        self.assertFalse(report["prune_blocked"])
        self.store.retry(document_id)
        self.run_job()
        self.assertEqual(self.parser_calls, 1)

    def test_original_integrity_is_checked_even_on_cache_hit(self):
        document_id = self.prepare_failed_model()
        self.store.object_path(document_id).write_bytes(b"changed")
        self.store.retry(document_id)
        self.run_job()
        self.assertEqual(self.store.status(document_id)["job"]["error_code"], "SOURCE_INTEGRITY_FAILED")
        self.assertEqual(self.parser_calls, 1)

    def test_expired_worker_cannot_publish_or_read_checkpoint(self):
        document_id = self.submit()
        old = self.store.claim("old-worker")
        with patch("docwork.intake.time.time", return_value=old.lease_until + 1):
            new = self.store.claim("new-worker")
        for operation in (
            lambda: self.store.save_parser_checkpoint(old, "key", IDENTITY, b"{}", (SAMPLE,)),
            lambda: self.store.load_parser_checkpoint(old, "key"),
            lambda: self.store.record_parser_reuse(old, "key"),
        ):
            with self.assertRaises(ReviewConflict):
                operation()
        self.assertIsNone(self.store.status(document_id)["parser_checkpoint"])
        self.assertEqual(self.store.status(document_id)["job"]["fence"], new.fence)

    def test_failed_checkpoint_write_rolls_back_metadata(self):
        self.parser_calls = 0
        document_id = self.submit()
        with patch("docwork.intake._atomic_write", side_effect=OSError("disk failure")):
            self.run_job()
        self.assertIsNone(self.store.status(document_id)["parser_checkpoint"])
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM parser_checkpoint_pages").fetchone()[0], 0)
        self.store.retry(document_id)
        self.run_job()
        self.assertEqual(self.parser_calls, 2)

    def test_duplicate_submission_does_not_inherit_checkpoint_or_approval(self):
        first = self.prepare_failed_model()
        self.store.retry(first)
        self.run_job()
        self.store.approve(first, 1, "reviewer")
        second = self.submit()
        self.assertIsNone(self.store.status(second)["parser_checkpoint"])
        self.run_job()
        self.assertEqual(self.parser_calls, 2)
        with self.assertRaises(ReviewConflict):
            self.store.export(second, "json")

    def test_image_inspection_fails_without_pull_and_validates_identity(self):
        cases = (
            (subprocess.CompletedProcess([], 1, b"", b"No such image: absent"), "PARSER_IMAGE_MISSING"),
            (subprocess.CompletedProcess([], 1, b"", b"Cannot connect to Docker"), "PARSER_UNAVAILABLE"),
            (subprocess.CompletedProcess([], 0, b"mutable-tag", b""), "PARSER_IDENTITY_INVALID"),
        )
        for completed, code in cases:
            with self.subTest(code=code), patch("docwork.worker.shutil.which", return_value="docker"), \
                    patch("docwork.worker.subprocess.run", return_value=completed) as run:
                with self.assertRaises(ParserFailure) as failure:
                    _docker_image_id("absent")
                self.assertEqual(failure.exception.code, code)
                self.assertEqual(run.call_args.args[0][:3], ["docker", "image", "inspect"])
                self.assertEqual(run.call_count, 1)

    def test_production_runner_receives_resolved_image_id(self):
        self.submit()
        received = []

        def runner(source, mime, output, claim, *, image):
            received.append(image)
            parser_result(output, source)

        with patch("docwork.worker._docker_image_id", return_value=IDENTITY), \
                patch("docwork.worker._docker_run", side_effect=runner):
            process_one(self.store, "worker", image="mutable-tag")
        self.assertEqual(received, [IDENTITY])


if __name__ == "__main__":
    unittest.main()
