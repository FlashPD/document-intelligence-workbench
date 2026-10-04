"""Synthetic verifier contracts; these fixtures are never live setup evidence."""
from __future__ import annotations

import hashlib
import json
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_model_setup as setup
sys.path.pop(0)


class SetupEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.profile = json.loads((ROOT / "config/model-mac-instruct.json").read_text())
        self.report = {"report_version": "fresh-model-setup-v1", "status": "passed",
                       "setup_mode": "download", "started_without_artifacts": True,
                       "inputs_unchanged": True, "checkout_inputs_unchanged": True,
                       "runtime_scratch_removed": True, "checks": [],
                       "setup_assets": {key: {field: self.profile[key][field] for field in ("sha256", "size_bytes")}
                                        for key in ("model", "runtime")}}
        for name in setup.CHECKS:
            offline = name != "explicit_setup"
            self.report["checks"].append({"id": name, "status": "passed", "returncode": 0,
                                         "offline_policy": offline,
                                         "command": ["/usr/bin/sandbox-exec", "-f", "fixture.sb", "python"] if offline else ["python"]})
            (self.directory / f"{name}.log").write_text("synthetic test log\n")
        (self.directory / "network_policy.log").write_text(json.dumps({
            "loopback": "passed", "parent_external_denial": "passed", "child_external_denial": "passed"}) + "\n")
        (self.directory / "missing_assets.log").write_text("Missing assets refused; no runtime/download artifacts created\n")
        (self.directory / "offline.sb").write_text(setup.POLICY)
        source = {"scripts/verify_model_setup.py": "synthetic verifier", "src/docwork/web.py": "synthetic workflow"}
        self.report["input_sha256"] = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in source.items()}
        (self.directory / "source_snapshot.json").write_text(json.dumps(source))
        (self.directory / "profile.json").write_text(json.dumps(self.profile))
        (self.directory / "workflow").mkdir()
        (self.directory / "workflow/profile.json").write_text(json.dumps(self.profile))
        (self.directory / "workflow/report.json").write_text(json.dumps({
            "source_sha256": {"src/docwork/web.py": self.report["input_sha256"]["src/docwork/web.py"]}}))
        self.workflow_verify = patch("docwork.workflow_evidence.verify_workflow_evidence", return_value={"status": "verified"})
        self.workflow_verify.start()
        self.addCleanup(self.workflow_verify.stop)
        self.seal()

    def seal(self):
        self.report["artifacts"] = {str(path.relative_to(self.directory)): setup.digest(path)
                                    for path in self.directory.rglob("*") if path.is_file() and path.name != "report.json"}
        self.report["artifacts"]["workflow/report.json"] = setup.digest(self.directory / "workflow/report.json")
        (self.directory / "report.json").write_text(json.dumps(self.report))

    def test_complete_download_and_transfer_modes_are_distinct(self):
        self.assertEqual(setup.verify_saved(self.directory)["setup_mode"], "download")
        self.report["setup_mode"] = "verified-local-transfer"
        check = self.report["checks"][2]
        check.update(offline_policy=True, command=["/usr/bin/sandbox-exec", "-f", "fixture.sb", "python"])
        self.seal()
        self.assertEqual(setup.verify_saved(self.directory)["setup_mode"], "verified-local-transfer")

    def test_incomplete_failed_or_unrestricted_schedule_is_rejected(self):
        original = json.dumps(self.report)
        for change in (lambda r: r["checks"].pop(),
                       lambda r: r["checks"][0].update(returncode=1),
                       lambda r: r["checks"][-2].update(offline_policy=False),
                       lambda r: r["checks"][-2].update(command=["python"]),
                       lambda r: r.update(runtime_scratch_removed=False)):
            self.report = json.loads(original)
            change(self.report)
            self.seal()
            with self.assertRaises(ValueError):
                setup.verify_saved(self.directory)

    def test_tampered_artifact_extra_file_and_symlink_are_rejected(self):
        log = self.directory / "offline_assets.log"
        log.write_text("tampered")
        with self.assertRaisesRegex(ValueError, "checksum"):
            setup.verify_saved(self.directory)
        self.seal()
        extra = self.directory / "unexpected.txt"
        extra.write_text("extra")
        with self.assertRaisesRegex(ValueError, "inventory"):
            setup.verify_saved(self.directory)
        extra.unlink()
        outside = self.directory.parent / (self.directory.name + "-outside.txt")
        outside.write_text("outside")
        self.addCleanup(outside.unlink)
        log.unlink()
        log.symlink_to(outside)
        self.seal()
        with self.assertRaisesRegex(ValueError, "path or checksum"):
            setup.verify_saved(self.directory)

    def test_rehashed_policy_or_source_substitution_cannot_pass(self):
        (self.directory / "offline.sb").write_text("(version 1)(allow default)")
        self.seal()
        with self.assertRaisesRegex(ValueError, "policy differs"):
            setup.verify_saved(self.directory)
        (self.directory / "offline.sb").write_text(setup.POLICY)
        workflow = self.directory / "workflow/report.json"
        workflow.write_text(json.dumps({"source_sha256": {"src/docwork/web.py": "0" * 64}}))
        self.seal()
        with self.assertRaisesRegex(ValueError, "fresh source"):
            setup.verify_saved(self.directory)

    def test_missing_snapshot_or_profile_identity_cannot_pass(self):
        snapshot = self.directory / "source_snapshot.json"
        original = snapshot.read_text()
        snapshot.write_text(json.dumps({"src/docwork/web.py": "synthetic workflow"}))
        self.seal()
        with self.assertRaisesRegex(ValueError, "omits"):
            setup.verify_saved(self.directory)
        snapshot.write_text(original)
        self.report["setup_assets"]["model"]["sha256"] = "0" * 64
        self.seal()
        with self.assertRaisesRegex(ValueError, "assets differ"):
            setup.verify_saved(self.directory)

    def test_timeout_interrupts_only_owned_wrapper_before_session_cleanup(self):
        process = Mock(pid=23456)
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 1), 0]
        process.poll.return_value = None
        with patch.object(setup.subprocess, "Popen", return_value=process) as start, \
                patch.object(setup.os, "killpg") as kill:
            with self.assertRaises(subprocess.TimeoutExpired):
                setup.run_process(["fixture"], cwd=self.directory, env={}, stream=None, timeout=1)
        self.assertTrue(start.call_args.kwargs["start_new_session"])
        process.send_signal.assert_called_once_with(signal.SIGINT)
        self.assertEqual([call.kwargs["timeout"] for call in process.wait.call_args_list], [1, 30])
        kill.assert_called_once_with(process.pid, signal.SIGKILL)

    def test_unresponsive_child_and_user_interrupt_cannot_skip_owned_cleanup(self):
        for error in (subprocess.TimeoutExpired("fixture", 1), KeyboardInterrupt()):
            process = Mock(pid=23456)
            process.poll.return_value = None
            process.wait.side_effect = [error, subprocess.TimeoutExpired("fixture", 30), 0]
            with patch.object(setup.subprocess, "Popen", return_value=process), \
                    patch.object(setup.os, "killpg") as kill:
                with self.assertRaises(type(error)):
                    setup.run_process(["fixture"], cwd=self.directory, env={}, stream=None, timeout=1)
            self.assertTrue(all(call.args == (process.pid, signal.SIGKILL) for call in kill.call_args_list))


if __name__ == "__main__":
    unittest.main()
