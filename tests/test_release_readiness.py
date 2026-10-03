from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.cli import main
from docwork.model_runtime import file_hash
from docwork.release_readiness import (
    DEFAULTS, DOCS, PARSER_CHECKS, audit_release, inventory, render_readiness,
    run_contracts, safe_path, source_status, verify_browser_report, verify_parser_report,
)

ROOT = Path(__file__).resolve().parents[1]


class ReleaseReadinessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "repo"
        self.root.mkdir()
        for name in ("src/docwork/component.py", "tests/test_component.py", "sandbox/Dockerfile",
                     "scripts/verify_parser.py", "samples/clean.png", "samples/conflicting-total.png",
                     "pyproject.toml", "Makefile", *DOCS):
            self.write(name, "fixture\n", raw=True)

    def write(self, name, value, *, raw=False):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if raw else json.dumps(value))
        return path

    def parser_report(self):
        hashes = inventory(self.root, [self.root / name for name in (
            "src/docwork/component.py", "tests/test_component.py", "sandbox/Dockerfile",
            "scripts/verify_parser.py", "samples/clean.png", "samples/conflicting-total.png")])
        return {"report_version": "parser-verification-v1", "status": "passed", "inputs_unchanged": True,
                "source_sha256": hashes, "image": {"Id": "sha256:" + "1" * 64},
                "scope": "Disposable fixtures", "checks": [{"id": name, "status": "passed"} for name in sorted(PARSER_CHECKS)]}

    def audit(self, **kwargs):
        output = self.root / "artifacts/audit"
        with patch("docwork.release_readiness.run_contracts", return_value=({"status": "passed", "tests": 3}, "test log\n")):
            return audit_release(self.root, output, **kwargs)

    def test_missing_evidence_stays_pending_and_outputs_are_bound(self):
        report = self.audit()
        self.assertEqual(report["status"], "pending")
        checks = {c["id"]: c for c in report["checks"]}
        self.assertEqual(checks["invoice_model_comparison"]["status"], "pending")
        self.assertEqual(checks["author_review_pilot"]["status"], "pending")
        self.assertEqual(checks["demo_recording"]["status"], "pending")
        directory = self.root / "artifacts/audit"
        self.assertEqual(json.loads((directory / "report.json").read_text()), report)
        self.assertEqual(file_hash(directory / "tests.log"), report["test_log_sha256"])
        self.assertIn("not exhaustive architecture acceptance", (directory / "index.html").read_text())

    def test_partial_model_run_is_never_scored_or_hashed_as_quality(self):
        self.write(DEFAULTS["invoice_model"] + "/completed.json", {"fixture.json": "0" * 64})
        self.write(DEFAULTS["invoice_model"] + "/predictions/fixture.json", {"record": {}})
        with patch("docwork.release_readiness.verify_invoice_model") as verify:
            report = self.audit()
        verify.assert_not_called()
        self.assertFalse(any(name.startswith(DEFAULTS["invoice_model"]) for name in report["input_sha256"]))

    def test_parser_skipped_missing_duplicate_or_failed_checks_cannot_pass(self):
        original = self.parser_report()
        variants = []
        changed = json.loads(json.dumps(original))
        changed["checks"][0]["status"] = "skipped"
        variants.append(changed)
        changed = json.loads(json.dumps(original))
        changed["checks"].pop()
        variants.append(changed)
        changed = json.loads(json.dumps(original))
        changed["checks"].append(changed["checks"][0])
        variants.append(changed)
        for report in variants:
            path = self.write("parser.json", report)
            with self.subTest(report=report), self.assertRaises(ValueError):
                verify_parser_report(self.root, path)
        path = self.write("parser.json", original)
        self.assertEqual(verify_parser_report(self.root, path)["status"], "passed")

    def test_source_drift_and_new_sources_require_a_fresh_run(self):
        path = self.write("parser.json", self.parser_report())
        self.write("src/docwork/component.py", "changed", raw=True)
        self.write("src/docwork/new.py", "new", raw=True)
        result = verify_parser_report(self.root, path)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["recorded_run"], "verified")
        self.assertEqual(result["source_drift"], ["src/docwork/component.py"])
        self.assertEqual(result["unrecorded_sources"], ["src/docwork/new.py"])

    def test_changed_evidence_is_invalid_and_other_checks_continue(self):
        self.write(DEFAULTS["manifest"], {})

        def mutation(path):
            path.write_text('{"changed":true}')
            return {"documents": 540}

        with patch("docwork.release_readiness.verify_synthetic_corpus", side_effect=mutation):
            report = self.audit()
        self.assertEqual(report["status"], "invalid")
        self.assertIn("Evidence changed", next(c for c in report["checks"] if c["id"] == "invoice_corpus")["note"])
        self.assertTrue(any(c["id"] == "demo_recording" for c in report["checks"]))

    def test_malformed_report_is_invalid_instead_of_pending(self):
        self.write(DEFAULTS["parser"], "{broken", raw=True)
        report = self.audit()
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(next(c for c in report["checks"] if c["id"] == "parser_reliability")["status"], "invalid")

    def test_small_invoice_or_receipt_comparisons_cannot_complete_release_evidence(self):
        baseline = DEFAULTS["baseline"]
        self.write(baseline + "/report.json", {"split": "test", "summary": {"documents_scheduled": 12}})
        receipts = DEFAULTS["receipts"]
        self.write(receipts + "/comparison.json", {})
        self.write(DEFAULTS["receipt_manifest"], {})
        with patch("docwork.release_readiness.verify_heldout", return_value={"documents": 12}), \
                patch("docwork.release_readiness.verify_receipt_comparison", return_value={"split": "test", "documents": 12}):
            report = self.audit()
        checks = {c["id"]: c["status"] for c in report["checks"]}
        self.assertEqual(checks["invoice_baseline"], "invalid")
        self.assertEqual(checks["receipt_model_comparison"], "invalid")

    def test_a_measured_model_regression_is_publishable_without_promotion(self):
        baseline, model = DEFAULTS["baseline"], DEFAULTS["invoice_model"]
        summary = {"documents_scheduled": 180}
        self.write(baseline + "/report.json", {"split": "test", "summary": summary})
        self.write(model + "/report.json", {"split": "test", "summary": summary, "comparison": {"status": "regression"}})
        self.write(DEFAULTS["manifest"], {})
        with patch("docwork.release_readiness.verify_heldout", return_value={"documents": 180}), \
                patch("docwork.release_readiness.verify_invoice_model", return_value={"documents": 180}), \
                patch("docwork.release_readiness.verify_synthetic_corpus", return_value={"documents": 540}):
            report = self.audit()
        result = next(c for c in report["checks"] if c["id"] == "invoice_model_comparison")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["comparison_status"], "regression")
        self.assertIn("not default promotion", result["note"])

    def test_paths_symlinks_and_existing_outputs_are_rejected(self):
        (self.root / "link").symlink_to(self.root / "src", target_is_directory=True)
        for path in ("../outside.json", "/tmp/outside.json", "link/docwork/component.py"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_path(self.root, path)
        with self.assertRaisesRegex(ValueError, "Source hash"):
            source_status(self.root, {}, set())
        self.audit()
        with self.assertRaisesRegex(ValueError, "new output"):
            self.audit()
        with self.assertRaisesRegex(ValueError, "outside its input"):
            audit_release(self.root, self.root / "src/audit")

    def test_browser_evidence_cannot_be_relabeled_as_human_time_or_lose_screenshot(self):
        source = ROOT / DEFAULTS["browser"]
        report = json.loads((source / "report.json").read_text())
        directory = self.root / "browser"
        directory.mkdir()
        (directory / "review.png").write_bytes((source / "review.png").read_bytes())
        self.assertEqual(verify_browser_report(ROOT, source)["status"], "passed")
        report["human_timing_measurement"] = True
        (directory / "report.json").write_text(json.dumps(report))
        with self.assertRaises(ValueError):
            verify_browser_report(ROOT, directory)
        report["human_timing_measurement"] = False
        (directory / "report.json").write_text(json.dumps(report))
        (directory / "review.png").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            verify_browser_report(ROOT, directory)

    def test_nested_directory_symlink_cannot_disappear_from_inventory(self):
        directory = self.root / "bundle"
        directory.mkdir()
        (directory / "hidden").symlink_to(self.root / "src", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            inventory(self.root, [directory])

    def test_new_workflow_evidence_can_replace_historical_paths(self):
        self.write("fresh-workflow/report.json", {"source_sha256": {}})
        self.write("fresh-browser/report.json", {})
        self.write("fresh-parser/report.json", self.parser_report())
        with patch("docwork.release_readiness.verify_workflow_evidence", return_value={"fixtures": 2}), \
                patch("docwork.release_readiness.source_status", return_value={"status": "passed"}), \
                patch("docwork.release_readiness.verify_browser_report", return_value={"status": "passed"}) as browser:
            report = self.audit(workflow_directory="fresh-workflow", browser_directory="fresh-browser",
                                parser_report="fresh-parser/report.json")
        checks = {c["id"]: c["status"] for c in report["checks"]}
        self.assertEqual(checks["real_model_workflow"], "passed")
        self.assertEqual(checks["browser_workflow"], "passed")
        self.assertIn("fresh-parser/report.json", report["input_sha256"])
        self.assertEqual(browser.call_args.args[1], (self.root / "fresh-browser").resolve())

    def test_only_complete_supplied_checklist_can_return_evidence_complete(self):
        self.write(DEFAULTS["manifest"], {})
        for name in ("baseline", "invoice_model"):
            self.write(DEFAULTS[name] + "/report.json", {"split": "test", "summary": {"documents_scheduled": 180},
                                                       "comparison": {"status": "regression"}})
        self.write(DEFAULTS["receipt_manifest"], {})
        self.write(DEFAULTS["receipts"] + "/comparison.json", {})
        self.write(DEFAULTS["parser"], self.parser_report())
        self.write(DEFAULTS["workflow"] + "/report.json", {"source_sha256": {}})
        self.write(DEFAULTS["browser"] + "/report.json", {})
        from docwork.pilot_bundle import DOCUMENTS
        self.write("pilot/protocol.json", {"participant": "project_author", "mode": "assisted_only",
                                          "documents": [{"corpus_id": name} for name in DOCUMENTS]})
        self.write("pilot/timing.json", {})
        self.write("pilot/source_snapshot.json", {})
        self.write("demo.mp4", "fixture-only; media contents are manually reviewed", raw=True)
        with patch("docwork.release_readiness.verify_synthetic_corpus", return_value={"documents": 540}), \
                patch("docwork.release_readiness.verify_heldout", return_value={"documents": 180}), \
                patch("docwork.release_readiness.verify_invoice_model", return_value={"documents": 180}), \
                patch("docwork.release_readiness.verify_receipt_comparison", return_value={"split": "test", "documents": 100, "baseline": {}, "model": {}}), \
                patch("docwork.release_readiness.verify_workflow_evidence", return_value={"fixtures": 2}), \
                patch("docwork.release_readiness.source_status", return_value={"status": "passed"}), \
                patch("docwork.release_readiness.verify_browser_report", return_value={"status": "passed"}), \
                patch("docwork.release_readiness.report_pilot", return_value={"status": "complete", "scheduled": 6, "completed": 6}):
            report = self.audit(pilot_directory="pilot", demo_recording="demo.mp4")
        self.assertEqual(report["status"], "evidence_complete")
        self.assertTrue(all(c["status"] == "passed" for c in report["checks"]))

    def test_empty_failed_skipped_and_timed_out_contract_runs_cannot_pass(self):
        for code, output, expected in ((0, "Ran 3 tests in 1s\n\nOK\n", "passed"),
                                       (0, "Ran 0 tests in 1s\n\nOK\n", "invalid"),
                                       (0, "Ran 3 tests in 1s\n\nOK (skipped=1)\n", "invalid"),
                                       (1, "Ran 3 tests in 1s\n\nFAILED\n", "invalid")):
            with self.subTest(code=code, output=output), \
                    patch("docwork.release_readiness.subprocess.run", return_value=subprocess.CompletedProcess([], code, "", output)):
                result, _ = run_contracts(self.root)
                self.assertEqual(result["status"], expected)
        with patch("docwork.release_readiness.subprocess.run", side_effect=subprocess.TimeoutExpired("tests", 300)):
            self.assertEqual(run_contracts(self.root)[0]["status"], "invalid")

    def test_html_escapes_untrusted_evidence_and_requires_no_remote_assets(self):
        report = {"status": "pending", "created_at_utc": "fixture", "scope": "fixture",
                  "checks": [{"id": "<script>", "status": "invalid", "note": "<img onerror=alert(1)>"}]}
        rendered = render_readiness(report)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;img", rendered)
        self.assertNotIn("https://", rendered)

    def test_cli_exit_codes_and_summary_preserve_pending_vs_invalid(self):
        for status, code in (("evidence_complete", 0), ("pending", 1), ("invalid", 2)):
            stream = io.StringIO()
            with self.subTest(status=status), contextlib.redirect_stdout(stream), \
                    patch("docwork.release_readiness.audit_release", return_value={"status": status, "checks": []}):
                self.assertEqual(main(["release-check", "--output-dir", "artifacts/new-audit"]), code)
            self.assertEqual(json.loads(stream.getvalue())["status"], status)


if __name__ == "__main__":
    unittest.main()
