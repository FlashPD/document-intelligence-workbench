import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("evaluation_status", Path(__file__).resolve().parents[1] / "scripts/evaluation_status.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class EvaluationStatusTests(unittest.TestCase):
    def save_predictions(self, root, predictions):
        (root / "predictions").mkdir()
        ledger = {}
        for name, prediction in predictions.items():
            path = root / "predictions" / name
            path.write_text(json.dumps(prediction))
            ledger[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (root / "completed.json").write_text(json.dumps(ledger))

    def test_progress_verifies_completed_failure_without_claiming_process_liveness(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "predictions").mkdir()
            prediction = root / "predictions/a.json"
            prediction.write_text('{"record":null,"failure_type":"ModelUnavailable"}')
            (root / "completed.json").write_text(json.dumps({"a.json": hashlib.sha256(prediction.read_bytes()).hexdigest()}))
            (root / "sessions/0001").mkdir(parents=True)
            status = MODULE.progress(root, 180)
            self.assertEqual((status["completed"], status["failed"]), (1, 1))
            self.assertEqual(status["sessions_without_runtime"], ["0001"])
            self.assertIn("does not establish a live process", status["process_state"])
            self.assertIsNotNone(status["last_ledger_update_utc"])
            prediction.write_text("{}")
            with self.assertRaisesRegex(ValueError, "ledger"):
                MODULE.progress(root, 180)

    def test_empty_and_unsafe_ledgers_remain_distinct_from_completed_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            status = MODULE.progress(root, 100)
            self.assertEqual(status["completed"], 0)
            self.assertFalse(status["report_written"])
            (root / "completed.json").write_text('{"../secret.json":"bad"}')
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                MODULE.progress(root, 100)

    def test_frozen_schedule_and_timing_include_failures_without_scoring_partial_quality(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "freeze.json").write_text(json.dumps({"document_ids": list("abcdef")}))
            self.save_predictions(root, {f"{name}.json": {
                "record": None if name == "e" else {}, "failure_type": "ModelUnavailable" if name == "e" else None,
                "runtime_seconds": {"model": seconds}} for name, seconds in zip("abcde", [10, 20, 30, 40, 150])})
            status = MODULE.progress(root, 180)
            self.assertEqual((status["completed"], status["scheduled"], status["remaining"]), (5, 6, 1))
            self.assertEqual(status["failure_types"], {"ModelUnavailable": 1})
            timing = status["model_stage_timing"]
            self.assertEqual((timing["total_seconds"], timing["p50_seconds"], timing["p95_seconds"]), (250, 30, 150))
            self.assertEqual(timing["remaining_stage_seconds_estimate"], 50)
            self.assertFalse(status["report_written"])
            self.assertNotIn("summary", status)

    def test_receipt_schedule_and_sparse_timing_do_not_invent_an_estimate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "run.json").write_text('{"document_ids":["a","b"]}')
            self.save_predictions(root, {"a.json": {"record": {}, "runtime_seconds": {"model": 20}}})
            status = MODULE.progress(root, 100)
            self.assertEqual(status["scheduled"], 2)
            self.assertIsNone(status["model_stage_timing"]["remaining_stage_seconds_estimate"])
            (root / "run.json").write_text('{"document_ids":["b"]}')
            with self.assertRaisesRegex(ValueError, "scheduled documents"):
                MODULE.progress(root, 100)

    def test_rejects_invalid_schedules_timings_and_overfull_ledgers(self):
        for ids in (["a", "a"], ["../a"], [1], []):
            with self.subTest(ids=ids), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "freeze.json").write_text(json.dumps({"document_ids": ids}))
                with self.assertRaisesRegex(ValueError, "schedule"):
                    MODULE.progress(root, 100)
        for seconds in (-1, float("nan"), float("inf"), True, "10"):
            with self.subTest(seconds=seconds), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.save_predictions(root, {"a.json": {"record": {}, "runtime_seconds": {"model": seconds}}})
                with self.assertRaisesRegex(ValueError, "timing"):
                    MODULE.progress(root, 100)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "completed.json").write_text('{"a.json":"hash","b.json":"hash"}')
            with self.assertRaisesRegex(ValueError, "scheduled documents"):
                MODULE.progress(root, 1)

    def test_coordinator_identity_check_handles_reused_pid_permissions_and_keeps_arguments_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            launch = Path(temporary) / "launch.json"
            command = ["/usr/bin/python3", "/repo/scripts/complete_release_evaluations.py", "--invoice-run", "invoice"]
            launch.write_text(json.dumps({"pid": 123, "command": command}))
            for result, expected in (
                (subprocess.CompletedProcess([], 0, " ".join(command), ""), "running"),
                (subprocess.CompletedProcess([], 0, "/usr/bin/other --api-key secret", ""), "identity_mismatch"),
                (subprocess.CompletedProcess([], 1, "", ""), "not_running"),
                (subprocess.CompletedProcess([], 1, "", "Operation not permitted"), "inspection_unavailable"),
                (subprocess.CompletedProcess([], 0, "", ""), "identity_mismatch"),
            ):
                with self.subTest(expected=expected), patch.object(MODULE.subprocess, "run", return_value=result):
                    status = MODULE.runner_status(launch, inspect=True)
                self.assertEqual(status["state"], expected)
                self.assertNotIn("secret", json.dumps(status))
                self.assertNotIn("command", status)
            with patch.object(MODULE.subprocess, "run") as run:
                self.assertEqual(MODULE.runner_status(launch)["state"], "not_inspected")
                run.assert_not_called()
            with patch.object(MODULE.subprocess, "run", side_effect=subprocess.TimeoutExpired("ps", 5)):
                self.assertEqual(MODULE.runner_status(launch, inspect=True)["state"], "inspection_unavailable")
            launch.write_text(json.dumps({"pid": -1, "command": command}))
            with self.assertRaisesRegex(ValueError, "launch identity"):
                MODULE.runner_status(launch, inspect=True)

    def test_macos_framework_launcher_accepts_only_its_own_app_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            framework = root / "Python.framework/Versions/3.12"
            executable = framework / "bin/python3.12"
            app_binary = framework / "Resources/Python.app/Contents/MacOS/Python"
            for path in (executable, app_binary):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            arguments = ["/repo/scripts/complete_release_evaluations.py", "--invoice-run", "invoice"]
            launch = root / "launch.json"
            launch.write_text(json.dumps({"pid": 123, "command": [str(executable), *arguments]}))
            for binary, expected in ((app_binary, "running"),
                                     (root / "other/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python", "identity_mismatch")):
                result = subprocess.CompletedProcess([], 0, " ".join([str(binary), *arguments]), "")
                with patch.object(MODULE.subprocess, "run", return_value=result):
                    self.assertEqual(MODULE.runner_status(launch, inspect=True)["state"], expected)
