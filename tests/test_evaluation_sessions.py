"""Abrupt session loss cannot disappear from release lifecycle/memory claims."""

import json
import tempfile
import unittest
from pathlib import Path

from docwork.evaluation_sessions import audit_sessions, latest_session_stopped
from docwork.model_runtime import file_hash


class EvaluationSessionsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.session = self.root / "sessions/0001"
        self.session.mkdir(parents=True)
        (self.session / "server.log").write_text("Abrupt fixture log")
        (self.root / "freeze.json").write_text("{}")
        (self.root / "predictions").mkdir()
        (self.root / "predictions/a.json").write_text('{"record":null,"failure_type":"ModelUnavailable"}')

    def observe_interruption(self):
        data = {"version": "model-session-interruption-v1", "session": "0001",
                "observed_at_utc": "2026-10-03T20:51:11+00:00",
                "process_observation": "no_project_model_or_evaluation_process_observed",
                "shutdown_complete": None, "peak_sampled_rss_bytes": None,
                "server_log_sha256": file_hash(self.session / "server.log"),
                "freeze_sha256": file_hash(self.root / "freeze.json"),
                "completed_predictions": {"a.json": file_hash(self.root / "predictions/a.json")},
                "limitations": "Exit reason and runtime measurements unavailable; local observation only."}
        (self.session / "interruption.json").write_text(json.dumps(data))
        return data

    def test_unrecorded_session_cannot_disappear_behind_later_runtime(self):
        second = self.root / "sessions/0002"
        second.mkdir()
        (second / "server.log").write_text("Finished later session")
        (second / "runtime.json").write_text('{"shutdown_complete":true}')
        self.assertTrue(latest_session_stopped(self.root))
        with self.assertRaisesRegex(ValueError, "no runtime metadata"):
            audit_sessions(self.root)
        self.observe_interruption()
        result = audit_sessions(self.root)
        self.assertEqual(result["status"], "recorded_with_interruptions")
        self.assertEqual(result["recorded_runtime_sessions"], ["0002"])
        self.assertEqual(result["interrupted_sessions"][0]["completed_predictions"], 1)
        self.assertIn("RSS and shutdown metadata are unavailable", result["memory_coverage"])

    def test_missing_runtime_is_not_a_live_process_signal(self):
        self.assertFalse(latest_session_stopped(self.root))
        self.observe_interruption()
        self.assertEqual(audit_sessions(self.root)["recorded_runtime_sessions"], [])
        (self.session / "runtime.json").write_text('{"shutdown_complete":false}')
        with self.assertRaisesRegex(ValueError, "contradictory shutdown"):
            audit_sessions(self.root)

    def test_observation_cannot_invent_shutdown_memory_or_unsafe_predictions(self):
        original = self.observe_interruption()
        for name, value in (("shutdown_complete", True), ("peak_sampled_rss_bytes", 1234),
                            ("server_log_sha256", "0" * 64), ("completed_predictions", {"../secret.json": "0" * 64})):
            changed = {**original, name: value}
            (self.session / "interruption.json").write_text(json.dumps(changed))
            with self.subTest(name=name), self.assertRaises(ValueError):
                audit_sessions(self.root)

    def test_changed_completed_failure_and_log_are_rejected(self):
        self.observe_interruption()
        prediction = self.root / "predictions/a.json"
        saved = prediction.read_bytes()
        prediction.write_text("{}")
        with self.assertRaisesRegex(ValueError, "prediction changed"):
            audit_sessions(self.root)
        prediction.write_bytes(saved)
        (self.session / "server.log").write_text("changed")
        with self.assertRaisesRegex(ValueError, "observation is invalid"):
            audit_sessions(self.root)


if __name__ == "__main__":
    unittest.main()
