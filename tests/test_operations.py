import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docwork.intake import IntakeStore
from docwork.local_model import LocalModelConfig
from docwork.operations import Readiness, snapshot
from docwork.storage_budget import StorageLimitExceeded
from docwork.worker import process_one
from test_worker import parser_result

ROOT = Path(__file__).resolve().parents[1]


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        directory = Path(self.temp.name)
        self.store = IntakeStore(directory / "review.sqlite", directory / "objects")
        self.server = SimpleNamespace(store=self.store, supervisor=None, model_config=None)

    def tearDown(self):
        self.temp.cleanup()

    def submit(self):
        return self.store.submit(io.BytesIO((ROOT / "samples/clean.png").read_bytes()),
                                 "private-company-secret.png", "image/png")

    def test_durable_stage_coverage_retry_and_privacy(self):
        doc = self.submit()
        queued = snapshot(self.store)
        self.assertEqual(queued["jobs"], {"QUEUED": 1})
        self.assertIsNotNone(queued["oldest_queue_age_seconds"])
        process_one(self.store, "worker", runner=lambda *args, **kw: (_ for _ in ()).throw(ValueError("secret")))
        self.store.retry(doc)
        process_one(self.store, "worker", runner=lambda source, mime, output, claim, image: parser_result(output, source))
        view = snapshot(IntakeStore(self.store.database, self.store.object_root))
        self.assertEqual(view["failure_codes"], {"PARSER_OUTPUT_INVALID": 1})
        self.assertEqual(view["retry_or_reprocess_count"], 1)
        self.assertEqual(view["timings"]["processing"]["count"], 2)
        self.assertEqual(view["timings"]["parsing"]["count"], 2)
        self.assertEqual(view["timings"]["extracting"]["count"], 1)
        self.assertEqual(view["timings"]["checking"]["count"], 1)
        self.assertEqual(view["review_wait"]["count"], 1)
        serialized = json.dumps(view)
        for value in (doc, "private-company", "Aster", "worker", str(self.store.database)):
            self.assertNotIn(value, serialized)
        self.store.request_delete(doc)
        self.store.run_deletions()
        self.assertEqual(snapshot(self.store)["timings"]["processing"]["count"], 0)

    def test_stale_stage_writer_cannot_add_events_and_legacy_timing_is_not_invented(self):
        doc = self.submit()
        claim = self.store.claim("worker")
        self.store.set_stage(claim, "PARSING")
        self.store.set_stage(claim, "PARSING")
        with self.store._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM review_events WHERE kind='processing_stage'").fetchone()[0]
        self.assertEqual(count, 1)
        self.store.fail(claim, "PARSER_TIMEOUT")
        with self.assertRaises(ValueError):
            self.store.set_stage(claim, "SECRET")
        from docwork.review import ReviewConflict
        with self.assertRaises(ReviewConflict):
            self.store.set_stage(claim, "EXTRACTING")
        with self.store._connect() as db:
            db.execute("DELETE FROM review_events WHERE kind='processing_stage'")
            db.execute("UPDATE processing_attempts SET error_code='PRIVATE_SUPPLIER'")
        view = snapshot(self.store)
        self.assertEqual(view["failure_codes"], {"OTHER": 1})
        self.assertEqual(view["timings"]["parsing"]["count"], 0)
        self.assertEqual(view["timings"]["processing"]["count"], 1)

    def test_readiness_separates_model_failure_from_rules_and_caches_probes(self):
        self.server.model_config = LocalModelConfig("http://127.0.0.1:1", "private-model", api_key="secret-key")
        readiness = Readiness(self.server)
        with patch("urllib.request.OpenerDirector.open", side_effect=OSError("secret-key")) as probe:
            view = readiness.view()
            self.assertTrue(view["ready"])
            self.assertEqual(view["model"], "unavailable")
            self.assertEqual(view, readiness.view())
            self.assertEqual(probe.call_count, 1)
        self.assertNotIn("secret", json.dumps(view))

    def test_storage_worker_and_parser_failures_are_not_ready(self):
        with patch.object(self.store.storage_budget, "check", side_effect=StorageLimitExceeded("INSUFFICIENT_DISK_SPACE")):
            self.assertFalse(Readiness(self.server).view()["ready"])
        self.server.supervisor = SimpleNamespace(status=lambda: {"running": True}, last_error=None)
        with patch("docwork.operations._docker_image_id", side_effect=OSError("private path")):
            view = Readiness(self.server).view()
            self.assertFalse(view["ready"])
            self.assertEqual(view["parser"], "unavailable")
        self.server.supervisor = SimpleNamespace(status=lambda: {"running": False}, last_error=None)
        with patch("docwork.operations._docker_image_id", return_value="image"):
            self.assertFalse(Readiness(self.server).view()["ready"])


if __name__ == "__main__":
    unittest.main()
