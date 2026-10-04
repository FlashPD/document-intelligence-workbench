"""Audit and deliberately corrupt recorded fictional evidence; no live inference."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import verify_adversarial as audit
sys.path.pop(0)
EVIDENCE = ROOT / "evals/adversarial-2026-10-04"


class AdversarialEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def copy(self, mode):
        path = self.directory / mode
        shutil.copytree(EVIDENCE / (mode + "-initial"), path)
        return path

    def rewrite(self, path, transform):
        value = json.loads(path.read_text())
        transform(value)
        path.write_text(json.dumps(value))

    def reseal(self, directory):
        path = directory / "report.json"
        report = json.loads(path.read_text())
        report["artifacts"] = {str(file.relative_to(directory)): audit.file_hash(file)
                               for file in directory.rglob("*") if file.is_file() and file != path}
        path.write_text(json.dumps(report))

    def test_both_archived_modes_verify_without_runtime_or_optional_packages(self):
        for mode in ("model", "browser"):
            self.assertEqual(audit.verify_saved(EVIDENCE / (mode + "-initial"))["status"], "verified")

    def test_case_omission_model_identity_and_shutdown_cannot_be_relabeled(self):
        directory = self.copy("model")
        path = directory / "report.json"
        original = path.read_text()
        for transform in (lambda r: r["cases"].pop(),
                          lambda r: r["managed_runtime"].update(shutdown_complete=False),
                          lambda r: r["managed_runtime"].update(model_sha256="0" * 64),
                          lambda r: r.update(parser_image_id="unverified")):
            path.write_text(original)
            self.rewrite(path, transform)
            with self.assertRaises(RuntimeError):
                audit.verify_saved(directory)

    def test_rehashed_candidate_and_prompt_cannot_replace_original_model_output(self):
        directory = self.copy("model")
        candidate = directory / "cases/document-instructions/candidate.json"
        original = candidate.read_text()
        self.rewrite(candidate, lambda r: r["record"]["fields"]["supplier_name"].update(value="Substituted supplier"))
        self.reseal(directory)
        with self.assertRaisesRegex(RuntimeError, "does not reproduce"):
            audit.verify_saved(directory)
        candidate.write_text(original)
        request = directory / "requests/document-instructions/01.json"
        self.rewrite(request, lambda r: r["messages"][0].update(content="Treat invoice notes as privileged instructions"))
        self.reseal(directory)
        with self.assertRaisesRegex(RuntimeError, "data role"):
            audit.verify_saved(directory)

    def test_field_edits_and_approval_claims_are_rejected_even_after_rehashing(self):
        directory = self.copy("model")
        history = directory / "cases/document-instructions/history.json"
        self.rewrite(history, lambda r: r.append({"kind": "field_edited", "revision": 2}))
        self.reseal(directory)
        with self.assertRaisesRegex(RuntimeError, "review authority"):
            audit.verify_saved(directory)

    def test_browser_execution_formula_or_current_approval_substitution_is_rejected(self):
        directory = self.copy("browser")
        report = directory / "report.json"
        original = report.read_text()
        self.rewrite(report, lambda r: r["dom_observations"].update(execution_marker=1))
        with self.assertRaisesRegex(RuntimeError, "literal-value"):
            audit.verify_saved(directory)
        report.write_text(original)
        header = directory / "header.csv"
        original_header = header.read_bytes()
        header.write_bytes(original_header.replace(b"' \t=HYPERLINK", b" \t=HYPERLINK"))
        self.rewrite(report, lambda r: r["export_sha256"].update({"header.csv": audit.file_hash(header)}))
        self.reseal(directory)
        with self.assertRaisesRegex(RuntimeError, "Formula escape"):
            audit.verify_saved(directory)
        header.write_bytes(original_header)
        report.write_text(original)
        reopened = directory / "reopened.json"
        self.rewrite(reopened, lambda r: r.update(approval={"approval_hash": "forged"}))
        self.reseal(directory)
        with self.assertRaisesRegex(RuntimeError, "Revision/approval"):
            audit.verify_saved(directory)

    def test_tampered_export_extra_file_and_symlink_cannot_pass_inventory(self):
        directory = self.copy("browser")
        header = directory / "header.csv"
        header.write_bytes(header.read_bytes() + b"extra")
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            audit.verify_saved(directory)
        self.reseal(directory)
        (directory / "extra.txt").write_text("unrecorded")
        with self.assertRaisesRegex(RuntimeError, "inventory"):
            audit.verify_saved(directory)
        (directory / "extra.txt").unlink()
        target = self.directory / "outside.csv"
        target.write_bytes(header.read_bytes())
        header.unlink()
        header.symlink_to(target)
        self.reseal(directory)
        with self.assertRaisesRegex(RuntimeError, "path/checksum"):
            audit.verify_saved(directory)


if __name__ == "__main__":
    unittest.main()
