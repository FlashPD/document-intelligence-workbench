import importlib.util
import contextlib
import hashlib
import io
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


class CommittedCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.git("init", "--quiet", "--template=")
        self.git("config", "user.name", "Checkout test")
        self.git("config", "user.email", "checkout@example.invalid")
        self.git("config", "core.hooksPath", "/dev/null")
        self.git("config", "tag.gpgsign", "false")
        self.write("README.md", "committed source\n")
        self.write("run.sh", "#!/bin/sh\nexit 0\n").chmod(0o755)
        self.write(".gitignore", "artifacts/\n")
        self.write(".gitattributes", "README.md export-ignore\nrun.sh export-subst\n")
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.git("-c", "tag.gpgsign=false", "tag", "candidate")

    def git(self, *arguments):
        return subprocess.run(["git", *arguments], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout

    def write(self, name, contents):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def test_commit_verification_ignores_dirty_index_working_tree_and_moving_tag(self):
        self.write("README.md", "later committed version\n")
        self.git("add", "README.md")
        self.git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Later fixture")
        self.write("README.md", "staged local change\n")
        self.git("add", "README.md")
        self.write("README.md", "unstaged local change\n")
        (self.root / "run.sh").unlink()
        self.write("untracked.py", "local helper\n")
        self.write("artifacts/model.gguf", "local model\n")
        real_run = subprocess.run
        checked = []

        def run(command, **kwargs):
            if command[0] == "git":
                return real_run(command, **kwargs)
            checkout = kwargs["cwd"]
            self.assertEqual((checkout / "README.md").read_text(), "committed source\n")
            self.assertTrue((checkout / "run.sh").stat().st_mode & 0o111)
            self.assertFalse((checkout / "untracked.py").exists())
            self.assertFalse((checkout / ".git").exists())
            self.assertFalse((checkout / "artifacts").exists())
            self.assertEqual(kwargs["env"]["PYTHONPATH"], str(checkout / "src"))
            # Move the originally supplied name while verification runs.
            real_run(["git", "tag", "-f", "candidate", "HEAD"], cwd=self.root, check=True, capture_output=True)
            checked.append(command)
            return subprocess.CompletedProcess(command, 0, "", "\nRan 1 test in 0s\n\nOK\n")

        output = self.root / "artifacts/report"
        with patch.object(MODULE.subprocess, "run", side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            report = MODULE.verify(self.root, output, ref="candidate")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["git_source"]["commit"], self.commit)
        self.assertEqual(report["mode"], "committed-git-tree")
        self.assertEqual(len(checked), 8)
        self.assertTrue(report["inputs_unchanged"])
        self.assertTrue(report["checkout_inputs_unchanged"])
        self.assertEqual(report["input_sha256"]["README.md"], hashlib.sha256(b"committed source\n").hexdigest())
        self.assertEqual(self.git("diff", "--cached", "--name-only").strip(), "README.md")

    def test_commit_snapshot_preserves_binary_blob_and_ignores_export_attributes(self):
        data = bytes(range(256)) * 4096 + b"\n\0tail"
        (self.root / "binary.bin").write_bytes(data)
        self.git("add", "binary.bin")
        self.git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Binary fixture")
        source, files = MODULE.committed_files(self.root, "HEAD")
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            hashes = MODULE.copy_committed_files(self.root, files, checkout)
            self.assertEqual((checkout / "binary.bin").read_bytes(), data)
            self.assertEqual((checkout / "README.md").read_text(), "committed source\n")
            self.assertEqual(set(hashes), {entry["path"] for entry in files})
            self.assertEqual(source["tree"], self.git("rev-parse", "HEAD^{tree}").strip())

    def test_unknown_or_option_like_ref_does_not_create_output(self):
        for ref in ("missing-tag", "--help"):
            with self.subTest(ref=ref), self.assertRaises(subprocess.CalledProcessError):
                MODULE.verify(self.root, self.root / "artifacts/report", ref=ref)
            self.assertFalse((self.root / "artifacts/report").exists())

    def test_committed_symlink_submodule_and_runtime_data_are_rejected(self):
        for name, mode in (("link", "120000"), ("vendor", "160000"), ("artifacts/model.gguf", "100644")):
            with self.subTest(name=name):
                self.git("reset", "--quiet", "--hard", self.commit)
                oid = self.commit if mode == "160000" else self.git("rev-parse", "HEAD:README.md").strip()
                self.git("update-index", "--add", "--cacheinfo", f"{mode},{oid},{name}")
                self.git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Forbidden fixture")
                with self.assertRaises(ValueError):
                    MODULE.committed_files(self.root, "HEAD")

    def test_a_command_cannot_mutate_source_and_still_pass(self):
        real_run = subprocess.run

        def run(command, **kwargs):
            if command[0] == "git":
                return real_run(command, **kwargs)
            (kwargs["cwd"] / "README.md").write_text("changed during verification\n")
            return subprocess.CompletedProcess(command, 0, "", "\nRan 1 test in 0s\n\nOK\n")

        with patch.object(MODULE.subprocess, "run", side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            report = MODULE.verify(self.root, self.root / "artifacts/report", ref="HEAD")
        self.assertEqual(report["status"], "failed")
        self.assertFalse(report["checkout_inputs_unchanged"])

    def test_failed_committed_contracts_remain_failed_with_retained_logs(self):
        real_run = subprocess.run

        def run(command, **kwargs):
            if command[0] == "git":
                return real_run(command, **kwargs)
            return subprocess.CompletedProcess(command, 1, "", "committed fixture failure\n")

        output = self.root / "artifacts/report"
        with patch.object(MODULE.subprocess, "run", side_effect=run), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            report = MODULE.verify(self.root, output, ref="HEAD")
        self.assertEqual(report["status"], "failed")
        self.assertTrue(all(check["status"] == "failed" for check in report["checks"]))
        self.assertEqual((output / "contracts.log").read_text(), "committed fixture failure\n")
