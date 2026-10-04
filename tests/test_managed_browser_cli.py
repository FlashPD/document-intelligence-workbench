from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.cli import main
from docwork.local_model import LocalModelConfig

ROOT = Path(__file__).resolve().parents[1]


class ManagedBrowserCliTests(unittest.TestCase):
    def test_cli_owns_config_and_saves_shutdown_metadata_on_success_and_browser_failure(self):
        for failure in (False, True):
            with self.subTest(browser_failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                config = LocalModelConfig("http://127.0.0.1:8080", "pinned-model", api_key="private-key")
                runtime = {"shutdown_complete": False}

                @contextlib.contextmanager
                def owned_server(project, profile, log):
                    self.assertEqual(project, root)
                    log.write_text("Owned test server")
                    try:
                        yield config, runtime
                    finally:
                        runtime["shutdown_complete"] = True

                with patch("docwork.cli.__file__", str(root / "src/docwork/cli.py")), \
                        patch("docwork.model_runtime.managed_server", side_effect=owned_server), \
                        patch("docwork.web.serve", side_effect=RuntimeError("browser failure") if failure else None) as serve, \
                        contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    args = ["serve", "--model-profile", str(ROOT / "config/model-mac-instruct.json")]
                    if failure:
                        with self.assertRaises(SystemExit) as exited:
                            main(args)
                        self.assertEqual(exited.exception.code, 2)
                    else:
                        self.assertEqual(main(args), 0)
                self.assertIs(serve.call_args.kwargs["model_config"], config)
                session = next((root / "artifacts/model-sessions").iterdir())
                self.assertTrue(json.loads((session / "runtime.json").read_text())["shutdown_complete"])
                self.assertEqual(json.loads((session / "profile.json").read_text())["profile"], "docwork-mac-instruct-v1")
                self.assertNotIn("private-key", (session / "runtime.json").read_text())


if __name__ == "__main__":
    unittest.main()
