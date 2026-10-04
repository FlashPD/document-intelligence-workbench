from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.contracts import CONTRACT_VERSION, HEADER_FIELDS
from docwork.evidence import verify_model_evidence
from docwork.local_model import LocalModelConfig, _request
from docwork.model_runtime import (
    _download, _extract_runtime, _stop_process, fetch_assets, load_profile,
    managed_server, run_managed_evaluation, verify_assets,
)

ROOT = Path(__file__).resolve().parents[1]


class ModelRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = load_profile(ROOT / "config/model-mac-instruct.json")

    def seed_assets(self):
        for key, folder in (("model", "models"), ("runtime", "runtime")):
            content = key.encode()
            asset = self.profile[key]
            asset.update(size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
            directory = self.root / "artifacts" / folder
            directory.mkdir(parents=True)
            path = directory / asset["filename"]
            path.write_bytes(content)
            path.with_name(path.name + ".LICENSE").write_text("Test license")

    def test_verify_rejects_same_size_tampering_and_fetch_does_not_overwrite(self):
        self.seed_assets()
        paths = verify_assets(self.root, self.profile)
        paths["model"].write_bytes(b"wrong")
        with self.assertRaises(ValueError):
            verify_assets(self.root, self.profile)
        with patch("docwork.model_runtime.urllib.request.urlopen") as download:
            with self.assertRaises(ValueError):
                fetch_assets(self.root, self.profile)
            download.assert_not_called()
        self.assertEqual(paths["model"].read_bytes(), b"wrong")

    def test_fetch_verified_local_assets_is_offline(self):
        self.seed_assets()
        with patch("docwork.model_runtime.urllib.request.urlopen") as download:
            result = fetch_assets(self.root, self.profile)
            download.assert_not_called()
        self.assertEqual(result["status"], "verified")

    def test_download_checksum_failure_leaves_no_partial_asset(self):
        class Response(io.BytesIO):
            url = "https://example.com/asset"
        target = self.root / "model.gguf"
        with patch("docwork.model_runtime.urllib.request.urlopen", return_value=Response(b"wrong")):
            with self.assertRaises(ValueError):
                _download("https://example.com/asset", target, 5, "0" * 64)
        self.assertFalse(target.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_profile_rejects_path_escape_and_unbounded_execution(self):
        for section, key, value in (("model", "filename", "../escape"),
                                    ("runtime", "directory", ".."),
                                    ("inference", "parallel", 8),
                                    ("inference", "timeout_seconds", 9999)):
            profile = json.loads((ROOT / "config/model-mac-instruct.json").read_text())
            profile[section][key] = value
            path = self.root / "bad-profile.json"
            path.write_text(json.dumps(profile))
            with self.subTest(key=key), self.assertRaises(ValueError):
                load_profile(path)

    def test_archive_cannot_escape_or_replace_server_with_symlink(self):
        for name, link in (("../escape", None), ("runtime/escape", "/tmp/outside"),
                           ("runtime/llama-server", "another-file")):
            archive = self.root / "runtime.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                entry = tarfile.TarInfo(name)
                if link:
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = link
                bundle.addfile(entry)
            with tempfile.TemporaryDirectory(dir=self.root) as extracted:
                with self.subTest(name=name), self.assertRaises((ValueError, tarfile.FilterError)):
                    _extract_runtime(archive, Path(extracted), "runtime")
            self.assertFalse((self.root / "escape").exists())

    def test_process_cleanup_escalates_only_for_owned_unresponsive_child(self):
        class Process:
            terminated = False
            killed = False
            def poll(self):
                return None
            def terminate(self):
                self.terminated = True
            def wait(self, timeout):
                if not self.killed:
                    raise subprocess.TimeoutExpired("test", timeout)
                return 0
            def kill(self):
                self.killed = True
        process = Process()
        _stop_process(process)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)

    def test_api_key_is_sent_only_in_header_and_not_repr(self):
        config = LocalModelConfig("http://127.0.0.1:8080", "test", api_key="secret-test-key")
        self.assertNotIn("secret-test-key", repr(config))
        with patch("docwork.local_model.urllib.request.build_opener") as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}).encode()
            _request(config, {"model": "test"})
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer secret-test-key")
            self.assertNotIn(b"secret-test-key", request.data)
        with self.assertRaises(ValueError):
            LocalModelConfig("http://127.0.0.1:8080", "test", api_key="key\r\nheader")

    def test_managed_server_stops_owned_child_on_body_and_startup_failure(self):
        self.seed_assets()
        executable = self.root / "test-server"
        executable.write_bytes(b"test executable")
        class Process:
            pid = 12345
            returncode = None
            def poll(self):
                return self.returncode
            def terminate(self):
                self.returncode = -15
            def wait(self, timeout):
                return self.returncode
        for startup_failure in (False, True):
            process = Process()
            log = self.root / "server.log"
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch("docwork.model_runtime.platform.system", return_value="Darwin"))
                stack.enter_context(patch("docwork.model_runtime.platform.machine", return_value="arm64"))
                stack.enter_context(patch("docwork.model_runtime._extract_runtime", return_value=executable))
                stack.enter_context(patch("docwork.model_runtime.secrets.token_urlsafe", return_value="ephemeral-secret"))
                sock = stack.enter_context(patch("docwork.model_runtime.socket.socket"))
                sock.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 8123)
                stack.enter_context(patch("docwork.model_runtime.subprocess.run", return_value=
                                         subprocess.CompletedProcess([], 0, "1024\n", "")))
                opener = stack.enter_context(patch("docwork.model_runtime.urllib.request.build_opener"))
                opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps({
                    "data": [{"id": self.profile["inference"]["model_id"]}]
                }).encode()
                def start(command, **kwargs):
                    self.assertIn("--offline", command)
                    self.assertIn("--cors-origins", command)
                    self.assertNotIn("--cors-origin", command)
                    kwargs["stdout"].write(b"log contains ephemeral-secret")
                    kwargs["stdout"].flush()
                    return process
                stack.enter_context(patch("docwork.model_runtime.subprocess.Popen", side_effect=start))
                if startup_failure:
                    opener.return_value.open.side_effect = OSError("not ready")
                    stack.enter_context(patch("docwork.model_runtime.time.monotonic", side_effect=[0, 121]))
                with self.assertRaises(RuntimeError):
                    with managed_server(self.root, self.profile, log) as (config, metadata):
                        self.assertEqual(config.api_key, "ephemeral-secret")
                        self.assertNotIn("ephemeral-secret", json.dumps(metadata))
                        raise RuntimeError("evaluation failed")
                self.assertEqual(process.returncode, -15)
                self.assertNotIn(b"ephemeral-secret", log.read_bytes())
                self.assertFalse(list((self.root / "artifacts" / "runtime").glob("docwork-model-*")))

    def test_managed_run_saves_failed_predictions_and_shutdown_before_report(self):
        config = LocalModelConfig("http://127.0.0.1:8080", "test", api_key="not-in-artifacts")
        runtime = {"shutdown_complete": False, "model_sha256": self.profile["model"]["sha256"],
                   "runtime_archive_sha256": self.profile["runtime"]["sha256"]}
        @contextlib.contextmanager
        def server(root, profile, log):
            log.write_text("test runtime")
            try:
                yield config, runtime
            finally:
                runtime["shutdown_complete"] = True
        calls = []
        def fixture(path, actual_config):
            self.assertIs(actual_config, config)
            calls.append(path)
            if len(calls) == 1:
                raise TimeoutError("test failure")
            return {"python_version": "test", "tesseract_version": "test",
                    "page": {"spans": []}, "issues": [], "runtime_seconds": {"ocr": 0, "model": 0},
                    "record": {"schema_version": CONTRACT_VERSION, "line_items": [],
                               "fields": {key: {"value": None, "evidence_ids": []} for key in HEADER_FIELDS}}}
        output = self.root / "run"
        with patch("docwork.model_runtime.managed_server", server), contextlib.redirect_stdout(io.StringIO()):
            report = run_managed_evaluation(ROOT, ROOT / "config/model-mac-instruct.json", output, fixture)
        self.assertEqual(report["summary"]["documents_scheduled"], 12)
        self.assertEqual(report["summary"]["documents_processed"], 11)
        self.assertEqual(report["summary"]["failures_by_type"], {"TimeoutError": 1})
        self.assertTrue(report["managed_runtime"]["shutdown_complete"])
        self.assertEqual(len(list((output / "predictions").glob("*.json"))), 12)
        self.assertEqual(verify_model_evidence(output, ROOT)["status"], "verified")
        for name, digest in report["artifacts"].items():
            data = (output / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
            self.assertNotIn(b"not-in-artifacts", data)
        original = (output / "report.json").read_bytes()
        with self.assertRaises(FileExistsError):
            run_managed_evaluation(ROOT, ROOT / "config/model-mac-instruct.json", output, fixture)
        self.assertEqual((output / "report.json").read_bytes(), original)
        prediction = output / "predictions" / "dev-01-02.json"
        prediction.write_text(prediction.read_text() + " ")
        with self.assertRaises(ValueError):
            verify_model_evidence(output, ROOT)
        saved = json.loads(prediction.read_text())
        saved["runtime_seconds"]["model"] = 7
        prediction.write_text(json.dumps(saved))
        report["artifacts"]["predictions/dev-01-02.json"] = hashlib.sha256(prediction.read_bytes()).hexdigest()
        (output / "report.json").write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "do not reproduce"):
            verify_model_evidence(output, ROOT)


if __name__ == "__main__":
    unittest.main()
