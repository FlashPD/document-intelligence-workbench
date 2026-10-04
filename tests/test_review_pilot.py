from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import uuid
from unittest.mock import patch
from pathlib import Path

from docwork.intake import IntakeStore
from docwork.review import ReviewBlocked, ReviewConflict
from docwork.review_pilot import PILOT_VERSION, ReviewPilot, durations
from test_web import candidate


class ReviewPilotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = IntakeStore(self.root / "review.sqlite", self.root / "objects")
        page, record = candidate()
        self.ids = [self.store.ingest("a" * 64, "fixture.png", page, record) for _ in range(2)]
        protocol = {"pilot_version": PILOT_VERSION, "idle_cutoff_seconds": 60,
                    "documents": [{"document_id": d, "initial_record_hash": self.store.get(d)["record_hash"]}
                                  for d in self.ids]}
        (self.root / "protocol.json").write_text(json.dumps(protocol))
        self.now = 100.0
        self.pilot = ReviewPilot(self.root, self.store, clock=lambda: self.now)
        self.addCleanup(self.pilot.close)

    def start(self):
        return self.pilot.start(self.ids[0], "author")["trials"][0]["id"]

    def event(self, trial_id, kind):
        return self.pilot.event(trial_id, kind, uuid.uuid4().hex)

    def finish_record(self):
        self.store.edit(self.ids[0], 1, "fields.total", "270.00", "author")
        self.store.approve(self.ids[0], 2, "author")
        self.store.export(self.ids[0], "json")

    def test_active_idle_and_paused_intervals_sum_to_elapsed(self):
        trial_id = self.start()
        self.now = 115
        self.event(trial_id, "interaction")
        self.now = 205
        self.event(trial_id, "pause")
        self.now = 245
        self.event(trial_id, "resume")
        self.finish_record()
        self.now = 260
        trial = self.event(trial_id, "finish")["trials"][0]
        self.assertEqual((trial["active_seconds"], trial["idle_seconds"], trial["paused_seconds"], trial["elapsed_seconds"]),
                         (90, 30, 40, 160))
        self.assertEqual(trial["final"]["revision"], 2)
        self.assertEqual(trial["final"]["corrections"], 1)
        exported, _ = self.store.exported_file(self.ids[0], 2, "json", "invoice.json")
        self.assertEqual(trial["final"]["export_sha256"], hashlib.sha256(exported).hexdigest())

    def test_finish_requires_current_approval_and_existing_export(self):
        trial_id = self.start()
        with self.assertRaisesRegex(ReviewBlocked, "Approve"):
            self.event(trial_id, "finish")
        self.store.edit(self.ids[0], 1, "fields.total", "270.00", "author")
        self.store.approve(self.ids[0], 2, "author")
        with self.assertRaisesRegex(ReviewBlocked, "export"):
            self.event(trial_id, "finish")
        self.store.export(self.ids[0], "json")
        self.assertEqual(self.event(trial_id, "finish")["trials"][0]["status"], "COMPLETE")

    def test_pause_rejects_mutations_and_different_reviewer(self):
        trial_id = self.start()
        with self.assertRaises(ReviewConflict):
            self.pilot.guard(self.ids[0], "other")
        self.event(trial_id, "pause")
        with self.assertRaises(ReviewBlocked):
            self.pilot.guard(self.ids[0], "author")
        with self.assertRaises(ReviewConflict):
            self.event(trial_id, "interaction")
        self.event(trial_id, "resume")
        self.pilot.guard(self.ids[0], "author")

    def test_order_fresh_candidate_and_single_trial_are_enforced(self):
        with self.assertRaises(ReviewConflict):
            self.pilot.start(self.ids[1], "author")
        with self.assertRaises(ReviewBlocked):
            self.pilot.guard_view(self.ids[0])
        trial_id = self.start()
        self.pilot.guard_view(self.ids[0])
        with self.assertRaises(ReviewConflict):
            self.pilot.start(self.ids[1], "author")
        self.event(trial_id, "abandon")
        with self.assertRaises(ReviewConflict):
            self.pilot.start(self.ids[0], "author")
        self.store.edit(self.ids[1], 1, "fields.total", "270.00", "author")
        with self.assertRaisesRegex(ReviewConflict, "changed"):
            self.pilot.start(self.ids[1], "author")

    def test_event_retransmission_does_not_double_count_time(self):
        trial_id = self.start()
        event_id = uuid.uuid4().hex
        self.now = 130
        first = self.pilot.event(trial_id, "pause", event_id)
        self.now = 200
        second = self.pilot.event(trial_id, "pause", event_id)
        self.assertEqual(first, second)
        self.assertEqual(len(second["trials"][0]["events"]), 2)
        with self.assertRaises(ReviewConflict):
            self.pilot.event(trial_id, "resume", event_id)

    def test_restart_preserves_interrupted_trial_in_denominator(self):
        trial_id = self.start()
        self.now = 125
        self.event(trial_id, "interaction")
        self.pilot.close()
        restarted = ReviewPilot(self.root, self.store, clock=lambda: 0)
        self.addCleanup(restarted.close)
        view = restarted.view()
        self.assertEqual(view["scheduled"], 2)
        self.assertEqual(view["trials"][0]["status"], "ABANDONED")
        self.assertEqual(view["trials"][0]["elapsed_seconds"], 25)
        self.assertEqual(view["next_document"]["document_id"], self.ids[1])

    def test_changed_protocol_and_invalid_clock_are_rejected(self):
        data = json.loads((self.root / "protocol.json").read_text())
        data["idle_cutoff_seconds"] = 30
        (self.root / "protocol.json").write_text(json.dumps(data))
        self.pilot.close()
        with self.assertRaisesRegex(ValueError, "different protocol"):
            ReviewPilot(self.root, self.store)
        for offset in (-1, float("nan"), float("inf")):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                durations([{"kind": "interaction", "offset_seconds": offset}], 60)

    def test_two_servers_cannot_overwrite_the_same_session(self):
        with self.assertRaisesRegex(ReviewConflict, "Another pilot server"):
            ReviewPilot(self.root, self.store)

    def test_disk_failure_keeps_last_committed_timing_and_retry_records_once(self):
        trial_id = self.start()
        before = (self.root / "timing.json").read_bytes()
        self.now = 125
        event_id = uuid.uuid4().hex
        with patch("docwork.review_pilot.tempfile.mkstemp", side_effect=OSError("disk full")), self.assertRaises(OSError):
            self.pilot.event(trial_id, "pause", event_id)
        self.assertEqual((self.root / "timing.json").read_bytes(), before)
        self.assertEqual(self.pilot.view()["trials"][0]["status"], "RUNNING")
        self.assertEqual(self.pilot.event(trial_id, "pause", event_id)["trials"][0]["active_seconds"], 25)
