"""Study failures, provenance and manual judgments cannot become silent passes."""
import copy
import hashlib
import json
import tempfile
import unittest
import importlib.util
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from docwork.intake import IntakeStore
from docwork.production_study import (
    VERSION, approved_results, audit_template, bounded_file, freeze, inventory,
    read_protocol, score_run, summarize_audit, validate_cases, write_json,
)
from docwork.review import _hash
from docwork.ocr import PNG_SIGNATURE
from test_web import candidate, SAMPLE

ROOT = Path(__file__).resolve().parents[1]
PARSER = "sha256:" + "a" * 64
SPEC = importlib.util.spec_from_file_location("production_study_runner", ROOT / "scripts/production_study.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class ProductionStudyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name).resolve()
        (self.directory / "input.png").write_bytes(SAMPLE)
        self.page, self.record = candidate()
        gold = {"id": "case-one", "asset": "input.png", "sha256": hashlib.sha256(SAMPLE).hexdigest(),
                "family_group": "injected-fixture", "capture_kind": "synthetic", "page_count": 1,
                "treatments": ["clean"], "permission": {"basis": "self_authored", "reference": "unit-test fixture", "redistribution": True},
                "annotation": {"author": "injected-test", "method": "generator", "before_predictions": True,
                               "source_sha256": hashlib.sha256(SAMPLE).hexdigest()},
                "fields": {name: field.value for name, field in self.record.fields.items()},
                "line_items": [{name: row[name]["value"] for name in ("description", "quantity", "unit_price", "line_total", "tax")}
                               for row in self.record.to_dict()["line_items"]]}
        self.spec = {"study_id": "injected-diagnostic", "documents": [gold]}
        self.spec_path = self.directory / "spec.json"
        write_json(self.spec_path, self.spec)
        self.protocol_dir = self.directory / "frozen"
        self.protocol = freeze(ROOT, self.spec_path, self.protocol_dir, parser_image=PARSER, mode="diagnostic")
        self.store = IntakeStore(self.directory / "workbench/review.sqlite", self.directory / "workbench/objects")
        self.doc = self.store.ingest(gold["sha256"], "fixture.png", self.page, self.record)
        detail = self.store.get(self.doc)
        self.run_dir = self.directory / "run"
        write_json(self.run_dir / "predictions/case-one.json", detail)
        self.png = PNG_SIGNATURE + b"\0" * 8 + (1000).to_bytes(4, "big") * 2
        (self.run_dir / "pages").mkdir()
        (self.run_dir / "pages/case-one-1.png").write_bytes(self.png)
        self.report = {"version": VERSION, "status": "complete", "variant": "ocr_rules", "parser_image": PARSER,
                       "protocol_sha256": hashlib.sha256((self.protocol_dir / "protocol.json").read_bytes()).hexdigest(),
                       "documents": [{"id": "case-one", "document_id": self.doc, "prediction": "predictions/case-one.json",
                                      "parser_image": PARSER, "failure_type": None,
                                      "page_sha256": {"1": hashlib.sha256(self.png).hexdigest()}}]}
        self.save_report()

    def save_report(self):
        self.report["artifacts"] = inventory(self.run_dir, exclude=("report.json",))
        (self.run_dir / "report.json").write_text(json.dumps(self.report))

    def assessment(self):
        data = audit_template(self.protocol_dir, self.run_dir)
        data["auditor"] = "injected-unit-test-not-human"
        for item in data["items"]:
            item.update(semantic_status="supported" if item["citations"] else "no_citation",
                        geometry_status="aligned_line_region" if item["citations"] else "unavailable",
                        inspected_pages=[1], rationale="Injected assessment for contract verification only")
        return data

    def test_diagnostic_cannot_satisfy_the_production_scan_gate(self):
        with self.assertRaisesRegex(ValueError, "eight cases"):
            validate_cases(self.spec["documents"], mode="production")
        self.assertEqual(score_run(self.protocol_dir, self.run_dir)["mode"], "diagnostic")
        self.assertEqual(read_protocol(self.protocol_dir, root=ROOT)["study_id"], "injected-diagnostic")

    def test_real_inputs_require_inspected_labels_and_capture_provenance(self):
        case = copy.deepcopy(self.spec["documents"][0])
        case["capture_kind"] = "scanner"
        with self.assertRaisesRegex(ValueError, "source inspection"):
            validate_cases([case], mode="diagnostic")
        case["annotation"]["method"] = "source_inspection"
        with self.assertRaisesRegex(ValueError, "acquisition provenance"):
            validate_cases([case], mode="diagnostic")
        case["capture_reference"] = "Injected metadata only, not a runtime scan claim"
        validate_cases([case], mode="diagnostic")

    def test_production_minimum_and_permission_annotation_requirements(self):
        cases = [copy.deepcopy(self.spec["documents"][0]) for _ in range(8)]
        for index, case in enumerate(cases):
            case["id"] = f"case-{index}"
            if index < 2:
                case.update(capture_kind="scanner", capture_reference="Injected fixture, not a real capture")
                case["annotation"]["method"] = "source_inspection"
        cases[2]["treatments"] = ["degraded"]
        cases[3]["treatments"] = ["rotated"]
        cases[4].update(page_count=2, treatments=["multi_page"])
        validate_cases(cases, mode="production")
        for change in (lambda case: case["permission"].update(reference=""),
                       lambda case: case["annotation"].update(before_predictions=False),
                       lambda case: case.update(page_count=True),
                       lambda case: case.update(sha256="b" * 64)):
            broken = copy.deepcopy(cases)
            change(broken[0])
            with self.assertRaises(ValueError):
                validate_cases(broken, mode="production")

    def test_runner_retains_all_upload_failures_without_labels_or_review_calls(self):
        calls = []
        class FakeClient:
            def __init__(self, server):
                pass
            def request(self, method, path, body=None, **kwargs):
                calls.append((method, path, body))
                if method == "GET":
                    return 303, b""
                self_body = body
                if self_body != SAMPLE:
                    raise AssertionError("Extraction input must contain original bytes, never labels")
                return 400, b'{}'
        server = SimpleNamespace(token="injected-test", serve_forever=lambda: None, shutdown=lambda: None)
        output = self.directory / "runner-output"
        with patch.object(RUNNER, "Client", FakeClient), patch.object(RUNNER, "_docker_image_id", return_value=PARSER), \
                patch.object(RUNNER, "ReviewServer") as constructor:
            constructor.return_value.__enter__.return_value = server
            report = RUNNER.run(ROOT, self.protocol_dir, output, "ocr_rules")
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["documents"][0]["failure_type"], "UPLOAD_HTTP_400")
        self.assertEqual(score_run(self.protocol_dir, output)["original_suggestions"]["documents_processed"], 0)
        self.assertEqual([path for method, path, body in calls if method == "POST"], ["/api/upload"])

    def test_complete_failures_are_scored_and_remain_in_audit_sample(self):
        self.report["documents"][0]["failure_type"] = "MODEL_UNAVAILABLE"
        self.save_report()
        score = score_run(self.protocol_dir, self.run_dir)["original_suggestions"]
        self.assertEqual((score["documents_scheduled"], score["documents_processed"]), (1, 0))
        self.assertEqual(score["failures_by_type"], {"MODEL_UNAVAILABLE": 1})
        sheet = audit_template(self.protocol_dir, self.run_dir)
        self.assertEqual(len(sheet["items"]), 7)
        sheet["auditor"] = "injected-test"
        for item in sheet["items"]:
            item.update(semantic_status="extraction_failed", geometry_status="not_applicable", rationale="Model failed")
        report = summarize_audit(self.protocol_dir, self.run_dir, sheet)
        self.assertEqual(report["semantic_counts"], {"extraction_failed": 7})
        sheet["items"][0]["semantic_status"] = "supported"
        with self.assertRaisesRegex(ValueError, "Failed extraction"):
            summarize_audit(self.protocol_dir, self.run_dir, sheet)

    def test_omitted_duplicate_wrong_protocol_and_interrupted_cases_cannot_score(self):
        original = copy.deepcopy(self.report)
        for key, value in (("status", "interrupted"), ("documents", []),
                           ("documents", original["documents"] * 2), ("protocol_sha256", "b" * 64)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.report = {**original, key: value}
                self.save_report()
                score_run(self.protocol_dir, self.run_dir)

    def test_protocol_assets_labels_and_sources_cannot_drift(self):
        for relative in ("inputs/case-one.png", "labels.json", "source_snapshot.json"):
            path = self.protocol_dir / relative
            original = path.read_bytes()
            path.write_bytes(b"changed")
            with self.subTest(path=relative), self.assertRaisesRegex(ValueError, "inventory"):
                read_protocol(self.protocol_dir)
            path.write_bytes(original)
        root = self.directory / "changed-root"
        root.mkdir()
        with self.assertRaises(OSError):
            read_protocol(self.protocol_dir, root=root)

    def test_changed_render_dimensions_and_approved_candidate_cannot_be_original(self):
        target = self.run_dir / "predictions/case-one.json"
        original = json.loads(target.read_text())
        for key, value in (("approval", {"actor": "someone"}), ("revision", 2),
                           ("source_sha256", "b" * 64), ("record_hash", "b" * 64)):
            target.write_text(json.dumps({**original, key: value}))
            self.save_report()
            with self.subTest(key=key), self.assertRaises(ValueError):
                score_run(self.protocol_dir, self.run_dir)
        target.write_text(json.dumps(original))
        path = self.run_dir / "pages/case-one-1.png"
        path.write_bytes(PNG_SIGNATURE + b"\0" * 8 + (999).to_bytes(4, "big") * 2)
        self.report["documents"][0]["page_sha256"]["1"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.save_report()
        with self.assertRaisesRegex(ValueError, "dimensions"):
            score_run(self.protocol_dir, self.run_dir)

    def test_audit_is_pending_until_every_target_has_a_bound_assessment(self):
        sheet = audit_template(self.protocol_dir, self.run_dir)
        with self.assertRaises(ValueError):
            summarize_audit(self.protocol_dir, self.run_dir, sheet)
        assessed = self.assessment()
        result = summarize_audit(self.protocol_dir, self.run_dir, assessed)
        self.assertEqual(result["targets_scheduled"], 7)
        for mutate in (lambda data: data["items"].pop(),
                       lambda data: data["items"][0].update(predicted_value="another value"),
                       lambda data: data["items"][0].update(inspected_pages=[]),
                       lambda data: data["items"][0].update(rationale="")):
            data = copy.deepcopy(assessed)
            mutate(data)
            with self.assertRaises(ValueError):
                summarize_audit(self.protocol_dir, self.run_dir, data)

    def test_absent_citation_cannot_be_declared_semantically_supported(self):
        path = self.run_dir / "predictions/case-one.json"
        data = json.loads(path.read_text())
        data["record"]["fields"]["total"]["evidence_ids"] = []
        data["record_hash"] = _hash(data["record"])
        path.write_text(json.dumps(data))
        self.save_report()
        assessed = self.assessment()
        item = next(item for item in assessed["items"] if item["path"] == "fields.total")
        item["semantic_status"] = "supported"
        with self.assertRaisesRegex(ValueError, "absent citation"):
            summarize_audit(self.protocol_dir, self.run_dir, assessed)

    def test_approved_results_are_separate_and_never_create_approval(self):
        original = inventory(self.run_dir)
        pending = approved_results(self.protocol_dir, self.run_dir, self.store)
        self.assertEqual(pending["approved_results"]["failures_by_type"], {"NotApproved": 1})
        self.assertIsNone(self.store.get(self.doc)["approval"])
        self.store.edit(self.doc, 1, "fields.total", "270.00", "injected-reviewer")
        self.store.approve(self.doc, 2, "injected-reviewer")
        report = approved_results(self.protocol_dir, self.run_dir, self.store)
        self.assertEqual(report["approved_results"]["documents_processed"], 1)
        self.assertEqual(report["documents"][0]["result"]["revision"], 2)
        self.assertEqual(inventory(self.run_dir), original)
        self.assertEqual(report["original_suggestions"], pending["original_suggestions"])

    def test_path_escape_symlink_and_unlisted_artifacts_cannot_pass(self):
        for name in ("../input.png", str(self.directory / "input.png")):
            with self.assertRaises(ValueError):
                bounded_file(self.directory, name)
        (self.run_dir / "extra.txt").write_text("unlisted")
        with self.assertRaisesRegex(ValueError, "inventory"):
            score_run(self.protocol_dir, self.run_dir)
        (self.run_dir / "extra.txt").unlink()
        (self.run_dir / "link").symlink_to(self.directory / "input.png")
        with self.assertRaisesRegex(ValueError, "symlink"):
            score_run(self.protocol_dir, self.run_dir)


if __name__ == "__main__":
    unittest.main()
