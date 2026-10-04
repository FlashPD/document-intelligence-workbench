from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from docwork.cli import main
from docwork.comparison import compare_files, compare_reports, render_html
from docwork.contracts import CONTRACT_VERSION, REQUIRED_FIELDS
from docwork.evaluation import (
    REPORT_VERSION, SCORING_VERSION, evaluate_development, score_document, summarize_document_scores,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_BYTES = (ROOT / "datasets" / "development-v0.json").read_bytes()
MANIFEST = json.loads(MANIFEST_BYTES)
MANIFEST_HASH = hashlib.sha256(MANIFEST_BYTES).hexdigest()


def report_fixture() -> dict:
    # Synthetic metadata for gate unit tests only; never publish as a measured run.
    report = json.loads((ROOT / "evals" / "development-baseline-v0.2.json").read_text())
    report.update(report_version=REPORT_VERSION, scoring_version=SCORING_VERSION,
                  schema_version=CONTRACT_VERSION, split="development", evidence_kind="fresh",
                  extractor={"variant": "ocr_rules", "version": "ocr-rules-v0.2", "implementation_sha256": "0" * 64},
                  environment={"platform": "test"})
    for doc, gold in zip(report["documents"], MANIFEST["documents"]):
        doc["family_group"] = gold["family_group"]
    report["summary"] = summarize_document_scores(report["documents"])
    return report


def refresh(report: dict) -> None:
    for doc in report["documents"]:
        doc["required_all_exact"] = all(doc["header_exact"][key] for key in REQUIRED_FIELDS)
    report["summary"] = summarize_document_scores(report["documents"])


class ComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.baseline = report_fixture()
        self.candidate = copy.deepcopy(self.baseline)

    def compare(self, **options) -> dict:
        return compare_reports(self.baseline, self.candidate, MANIFEST, MANIFEST_HASH,
                               bootstrap_samples=100, **options)

    def test_identical_predictions_have_zero_paired_intervals_and_are_order_independent(self) -> None:
        self.candidate["documents"].reverse()
        result = self.compare()
        self.assertEqual(result["status"], "pass")
        for metric in result["metrics"].values():
            self.assertEqual(metric["delta"], 0)
            self.assertEqual(metric["delta_ci95"], [0, 0])
        self.assertEqual(result["bootstrap"]["groups"], 6)
        self.assertEqual(len(result["by_family"]), 6)

    def test_required_field_regression_and_seed_reproducibility(self) -> None:
        for doc in self.candidate["documents"]:
            doc["header_exact"]["total"] = False
        refresh(self.candidate)
        result = self.compare()
        self.assertEqual(result, self.compare())
        self.assertEqual(result["status"], "regression")
        self.assertIn("Regression: required_header_exact", result["reasons"])
        self.assertLess(result["metrics"]["required_header_exact"]["delta_ci95"][1], 0)

    def test_extra_failure_is_a_regression_and_remains_in_denominators(self) -> None:
        self.candidate["documents"][0] = score_document(MANIFEST["documents"][0], None)
        self.candidate["documents"][0]["failure_type"] = "ModelUnavailable"
        refresh(self.candidate)
        result = self.compare(max_regression=1)
        self.assertEqual(result["status"], "regression")
        self.assertIn("Regression: documents_processed", result["reasons"])
        self.assertEqual(result["summaries"]["candidate"]["required_header_exact"]["eligible"], 60)
        self.assertEqual(result["summaries"]["candidate"]["failures_by_type"], {"ModelUnavailable": 1})

    def test_all_failed_runs_cannot_pass(self) -> None:
        for report in (self.baseline, self.candidate):
            report["documents"] = [score_document(gold, None) for gold in MANIFEST["documents"]]
            refresh(report)
        self.assertEqual(self.compare()["status"], "unusable_evidence")

    def test_extra_predicted_rows_cannot_hide_behind_correct_gold_amounts(self) -> None:
        self.candidate["documents"][0]["predicted_row_count"] += 1
        refresh(self.candidate)
        result = self.compare()
        self.assertEqual(result["status"], "regression")
        self.assertIn("Regression: row_count_exact", result["reasons"])

    def test_threshold_boundary_is_inclusive(self) -> None:
        self.candidate["documents"][0]["header_exact"]["total"] = False
        refresh(self.candidate)
        self.assertEqual(self.compare(max_regression=1 / 60)["status"], "pass")
        self.assertEqual(self.compare(max_regression=.01)["status"], "regression")

    def test_incompatible_and_legacy_metadata_fail_closed(self) -> None:
        for key in ("manifest_sha256", "scoring_version", "schema_version", "dataset_id", "split", "report_version"):
            with self.subTest(key=key):
                self.candidate = copy.deepcopy(self.baseline)
                self.candidate[key] = "changed"
                result = self.compare()
                self.assertEqual(result["status"], "unusable_evidence")
                self.assertNotIn("metrics", result)
        self.candidate = copy.deepcopy(self.baseline)
        del self.candidate["report_version"]
        self.assertEqual(self.compare()["status"], "unusable_evidence")

    def test_replay_and_test_evidence_cannot_support_fresh_gate(self) -> None:
        for kind in ("replay", "test"):
            self.candidate["evidence_kind"] = kind
            self.assertEqual(self.compare()["status"], "unusable_evidence")

    def test_changes_require_explicit_declaration(self) -> None:
        self.candidate["extractor"]["version"] = "v-next"
        self.assertEqual(self.compare()["status"], "unusable_evidence")
        self.assertEqual(self.compare(allow_changes=("extractor",))["status"], "pass")
        self.candidate["runtime_versions"]["tesseract"] = "changed"
        self.assertEqual(self.compare(allow_changes=("extractor",))["status"], "unusable_evidence")
        self.assertEqual(self.compare(allow_changes=("extractor", "runtime_versions"))["status"], "pass")

    def test_missing_duplicate_documents_and_forged_summary_are_rejected(self) -> None:
        mutations = (
            lambda report: report["documents"].pop(),
            lambda report: report["documents"].append(copy.deepcopy(report["documents"][0])),
            lambda report: report["summary"].update(documents_processed=99),
            lambda report: report["documents"][0]["header_exact"].update(total=1),
            lambda report: report["documents"][0]["header_evidence_iou"].update(total=float("nan")),
            lambda report: report["documents"][0].update(ocr_seconds=-1),
            lambda report: report["documents"][0].update(gold_row_count=0),
            lambda report: report["documents"][0].update(family_group="another-family"),
            lambda report: report["documents"][0].update(processed=False),
            lambda report: report["documents"][0].update(predicted_row_count=0),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.candidate = copy.deepcopy(self.baseline)
                mutate(self.candidate)
                self.assertEqual(self.compare()["status"], "unusable_evidence")

    def test_bootstrap_preserves_family_pairs(self) -> None:
        # One family loses both correct totals; resampling documents independently
        # would create changes at half of the family-level step size.
        for doc in self.candidate["documents"][:2]:
            doc["header_exact"]["total"] = False
        refresh(self.candidate)
        result = compare_reports(self.baseline, self.candidate, MANIFEST, MANIFEST_HASH)
        self.assertAlmostEqual(result["metrics"]["required_header_exact"]["delta"], -2 / 60)
        low, high = result["metrics"]["required_header_exact"]["delta_ci95"]
        self.assertAlmostEqual(low, -.1)
        self.assertEqual(high, 0)
        self.assertEqual(result["by_family"][0]["candidate"]["required_header_exact"], .8)
        self.assertEqual(result["bootstrap"]["unit"], "layout_family")

    def test_invalid_policy_is_rejected(self) -> None:
        for tolerance in (float("nan"), -1, 2):
            with self.assertRaises(ValueError):
                self.compare(max_regression=tolerance)
        with self.assertRaises(ValueError):
            self.compare(allow_changes=("dataset",))

    def test_html_escapes_labels_and_contains_no_script(self) -> None:
        result = self.compare()
        result["reasons"] = ["<script>alert('x')</script>"]
        rendered = render_html(result)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("95% paired interval", rendered)
        self.assertIn("Failures and eligibility", rendered)

    def test_cli_writes_reports_and_exit_codes_without_overwriting_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline, candidate = root / "baseline.json", root / "candidate.json"
            output, html = root / "comparison.json", root / "comparison.html"
            baseline.write_text(json.dumps(self.baseline))
            args = ["eval-compare", str(baseline), str(candidate), "--output", str(output),
                    "--html", str(html), "--bootstrap-samples", "100"]
            for expected in (0, 1, 2):
                if expected == 1:
                    self.candidate["documents"][0] = score_document(MANIFEST["documents"][0], None)
                    refresh(self.candidate)
                if expected == 2:
                    self.candidate["split"] = "test"
                candidate.write_text(json.dumps(self.candidate))
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(args), expected)
                report = json.loads(output.read_text())
                self.assertEqual(report["inputs"]["baseline"]["sha256"], hashlib.sha256(baseline.read_bytes()).hexdigest())
                self.assertIn(report["status"], html.read_text())
            original = baseline.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(args + ["--output", str(baseline)])
            self.assertEqual(baseline.read_bytes(), original)

    def test_runner_accounts_for_every_exception_and_marks_test_evidence(self) -> None:
        calls = []

        def fail(path):
            calls.append(path)
            raise TimeoutError("unavailable")

        report = evaluate_development(ROOT, fail, evidence_kind="test")
        self.assertEqual(len(calls), 12)
        self.assertEqual(report["evidence_kind"], "test")
        self.assertEqual(report["summary"]["failures_by_type"], {"TimeoutError": 12})
        self.assertEqual(report["summary"]["header_exact"], {"correct": 0, "eligible": 120})

    def test_recorded_repeatability_report_is_reproducible_from_hashed_inputs(self) -> None:
        evidence = ROOT / "evals" / "development-repeatability-2026-10-02"
        result = compare_files(evidence / "baseline.json", evidence / "repeat.json", ROOT)
        self.assertEqual(result, json.loads((evidence / "comparison.json").read_text()))
        self.assertEqual(result["status"], "pass")


if __name__ == "__main__":
    unittest.main()
