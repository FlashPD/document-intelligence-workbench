import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("release_checkout", Path(__file__).resolve().parents[1] / "scripts/verify_release_checkout.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CleanCheckoutTests(unittest.TestCase):
    def test_empty_skipped_and_failed_contract_suites_cannot_pass_packaging(self):
        for code, log in ((0, "\nRan 0 tests in 0s\n\nOK\n"),
                          (0, "\nRan 1 test in 0s\n\nOK (skipped=1)\n"),
                          (1, "\nRan 1 test in 0s\n\nFAILED\n")):
            with self.subTest(log=log):
                self.assertFalse(MODULE.command_passed("contracts", subprocess.CompletedProcess([], code, "", log)))
        self.assertTrue(MODULE.command_passed("contracts", subprocess.CompletedProcess([], 0, "", "\nRan 1 test in 0s\n\nOK\n")))

    def test_nonignored_snapshot_includes_current_changes_and_excludes_its_own_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("README.md", "new.py", "evals/output/report.json"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture")
            with patch.object(MODULE.subprocess, "check_output", return_value=b"README.md\0new.py\0evals/output/report.json\0README.md\0"):
                paths = MODULE.snapshot_files(root, root / "evals/output")
            self.assertEqual([path.name for path in paths], ["README.md", "new.py"])

    def test_snapshot_rejects_runtime_assets_path_escape_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "file.py").write_text("fixture")
            (root / "link.py").symlink_to(root / "file.py")
            for name in ("../file.py", "artifacts/model.gguf", ".venv/python", "link.py"):
                with self.subTest(name=name), patch.object(MODULE.subprocess, "check_output", return_value=f"{name}\0".encode()):
                    with self.assertRaises(ValueError):
                        MODULE.snapshot_files(root, root / "evals/output")

    def test_report_cannot_overwrite_inputs_or_an_existing_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "evals/existing").mkdir(parents=True)
            for output in (root / "src", root / "evals/existing", root / "evals/../src"):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    MODULE.verify(root, output, "evals/model")
