from __future__ import annotations

import json
import unittest
import uuid

from docwork.review_pilot import PILOT_VERSION, ReviewPilot
import test_web
from docwork.access import Principal


class PilotWebTests(unittest.TestCase):
    def setUp(self):
        self.helper = test_web.WebTests()
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.helper.server.access.reviewer = Principal("author", "reviewer")
        self.helper.cookie = "docwork_session=test-token"
        self.store = self.helper.store
        page, record = test_web.candidate()
        self.doc_id = self.store.ingest("a" * 64, "fixture.png", page, record)
        directory = self.store.database.parent
        (directory / "protocol.json").write_text(json.dumps({"pilot_version": PILOT_VERSION, "idle_cutoff_seconds": 60,
            "documents": [{"document_id": self.doc_id, "initial_record_hash": self.store.get(self.doc_id)["record_hash"]}]}))
        self.helper.server.review_pilot = ReviewPilot(directory, self.store)
        self.addCleanup(self.helper.server.review_pilot.close)

    def test_sources_and_mutations_require_timing_and_same_origin(self):
        h = self.helper
        self.assertEqual(h.call("GET", f"/api/documents/{self.doc_id}")[0], 422)
        self.assertEqual(h.call("GET", f"/api/documents/{self.doc_id}/page")[0], 422)
        start = {"document_id": self.doc_id, "actor": "author"}
        self.assertEqual(h.call("POST", "/api/pilot/start", start, headers={"Origin": "https://example.org"})[0], 403)
        self.assertEqual(h.call("POST", "/api/pilot/start", start)[0], 201)
        self.assertEqual(h.call("GET", f"/api/documents/{self.doc_id}")[0], 200)
        self.assertEqual(h.call("POST", "/api/demo/seed", {"fixture": "clean"})[0], 422)

    def test_pause_blocks_edits_and_finish_cannot_bypass_export_policy(self):
        h = self.helper
        _, view, _ = h.call("POST", "/api/pilot/start", {"document_id": self.doc_id, "actor": "author"})
        trial_id = view["trials"][0]["id"]
        event = lambda kind: h.call("POST", "/api/pilot/event", {"trial_id": trial_id, "kind": kind, "event_id": uuid.uuid4().hex})
        self.assertEqual(event("finish")[0], 422)
        self.assertEqual(event("pause")[0], 200)
        self.assertEqual(h.call("POST", f"/api/documents/{self.doc_id}/edit", {
            "revision": 1, "path": "fields.total", "value": "270.00", "actor": "author"})[0], 422)
        self.assertEqual(self.store.get(self.doc_id)["revision"], 1)
        self.assertEqual(event("resume")[0], 200)
        self.assertEqual(event("abandon")[0], 200)
        self.assertEqual(h.call("POST", "/api/pilot/start", {"document_id": self.doc_id, "actor": "author"})[0], 409)
