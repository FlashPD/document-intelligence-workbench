from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docwork.baseline import extract_invoice
from docwork.contracts import Box, DocumentPage, TextSpan
from docwork.invoice_run import _pipeline_snapshot, run_invoice_baseline, verify_invoice_run
from tests.test_release_scoring import gold


class InvoiceRunTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        self.source = self.corpus / "source.png"
        self.source.write_bytes((Path(__file__).resolve().parents[1] / "samples" / "clean.png").read_bytes())
        source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.document = gold()
        self.document.update({"split": "development", "source_sha256": source_hash,
                              "assets": [{"path": "source.png", "sha256": source_hash}],
                              "pages": [{"number": 1}]})
        self.manifest = self.corpus / "manifest.json"
        self.manifest.write_text(json.dumps({"manifest_version": "invoice-corpus-v1",
                                             "dataset_id": "test-run",
                                             "documents": [self.document]}))
        self.run_dir = self.root / "run"
        runtime = patch("docwork.invoice_run.subprocess.run",
                        return_value=SimpleNamespace(stdout="tesseract 5.4.1\n"))
        runtime.start()
        self.addCleanup(runtime.stop)

    @staticmethod
    def page(path, *, page_number, page_segmentation_mode=1):
        lines = ["Aster Studio LLC", "Invoice Number: AST-1", "Date: 2026-09-12",
                 "Currency: USD", "Subtotal: 30.00", "Total: 30.00",
                 "Design review 1 10.00 10.00", "Design review 2 10.00 20.00"]
        spans = tuple(TextSpan(f"p{page_number}-l{index}", page_number, line,
                               Box(.1, index * .05, .6, index * .05 + .03), "fixture")
                      for index, line in enumerate(lines, start=1))
        return DocumentPage(page_number, 1200, 1600, spans)

    def test_full_run_and_resume_are_scored_without_rerunning_ocr(self):
        with patch("docwork.invoice_run.tesseract_page", side_effect=self.page) as ocr:
            report = run_invoice_baseline(self.manifest, self.run_dir)
            self.assertEqual(ocr.call_count, 1)
            resumed = run_invoice_baseline(self.manifest, self.run_dir, resume=True)
            self.assertEqual(ocr.call_count, 1)
        self.assertEqual(report["status"], "scored")
        self.assertEqual(resumed["summary"]["row_exact"]["f1"], 1)
        prediction = json.loads((self.run_dir / "predictions" / "invoice-1.json").read_text())
        self.assertEqual(prediction["source_sha256"], self.document["source_sha256"])
        self.assertEqual(prediction["input_mode"], "verified_corpus_png_previews")
        self.assertEqual(prediction["ocr_psm"], 1)
        self.assertEqual(len(prediction["pages"]), 1)
        self.assertTrue((self.run_dir / "source_snapshot.json").is_file())
        self.assertEqual(verify_invoice_run(self.manifest, self.run_dir)["status"], "verified")

    def test_ocr_failure_is_explicit_and_stays_in_denominator(self):
        with patch("docwork.invoice_run.tesseract_page", side_effect=RuntimeError("OCR failed")):
            report = run_invoice_baseline(self.manifest, self.run_dir)
        self.assertEqual(report["status"], "scored")
        self.assertEqual(report["summary"]["documents_processed"], 0)
        self.assertEqual(report["summary"]["failures_by_type"], {"RuntimeError": 1})

    def test_calibration_uses_its_own_split_and_test_stays_sealed(self):
        self.document["split"] = "calibration"
        self.manifest.write_text(json.dumps({"manifest_version": "invoice-corpus-v1",
                                             "dataset_id": "test-run", "documents": [self.document]}))
        with patch("docwork.invoice_run.tesseract_page", side_effect=self.page):
            report = run_invoice_baseline(self.manifest, self.run_dir, split="calibration")
        self.assertEqual(report["split"], "calibration")
        self.assertEqual(verify_invoice_run(self.manifest, self.run_dir)["status"], "verified")
        with self.assertRaisesRegex(ValueError, "development or calibration only"):
            run_invoice_baseline(self.manifest, self.root / "sealed", split="test")

    def test_resume_rejects_changed_prediction(self):
        with patch("docwork.invoice_run.tesseract_page", side_effect=self.page):
            run_invoice_baseline(self.manifest, self.run_dir)
        path = self.run_dir / "predictions" / "invoice-1.json"
        prediction = json.loads(path.read_text())
        prediction["source_sha256"] = "0" * 64
        path.write_text(json.dumps(prediction))
        with self.assertRaisesRegex(ValueError, "Prediction source hash"):
            run_invoice_baseline(self.manifest, self.run_dir, resume=True)
        with self.assertRaisesRegex(ValueError, "Prediction run identity|Prediction source hash"):
            verify_invoice_run(self.manifest, self.run_dir)

    def test_corner_ocr_artifact_does_not_replace_supplier(self):
        page = self.page(self.source, page_number=1)
        artifact = TextSpan("corner", 1, "agama", Box(0, 0, .04, .02), "fixture")
        noisy_page = DocumentPage(1, 1200, 1600, (artifact, *page.spans))
        record = extract_invoice(noisy_page)
        self.assertEqual(record.fields["supplier_name"].value, "Aster Studio LLC")

    def test_verifier_detects_changed_candidate_with_same_source_hash(self):
        with patch("docwork.invoice_run.tesseract_page", side_effect=self.page):
            run_invoice_baseline(self.manifest, self.run_dir)
        path = self.run_dir / "predictions" / "invoice-1.json"
        prediction = json.loads(path.read_text())
        prediction["record"]["fields"]["total"]["value"] = "99.00"
        path.write_text(json.dumps(prediction))
        with self.assertRaisesRegex(ValueError, "Saved invoice report differs"):
            verify_invoice_run(self.manifest, self.run_dir)

    def test_resume_recovers_interrupted_prediction_write(self):
        with patch("docwork.invoice_run.tesseract_page", side_effect=self.page) as ocr:
            run_invoice_baseline(self.manifest, self.run_dir)
            (self.run_dir / "report.json").unlink()
            (self.run_dir / "predictions" / "invoice-1.json").unlink()
            (self.run_dir / "predictions" / ".invoice-1.json.tmp").write_text("partial")
            report = run_invoice_baseline(self.manifest, self.run_dir, resume=True)
            self.assertEqual(ocr.call_count, 2)
        self.assertEqual(report["status"], "scored")
        self.assertFalse((self.run_dir / "predictions" / ".invoice-1.json.tmp").exists())

    def test_source_snapshot_tampering_is_rejected(self):
        with patch("docwork.invoice_run.tesseract_page", side_effect=self.page):
            run_invoice_baseline(self.manifest, self.run_dir)
        path = self.run_dir / "source_snapshot.json"
        snapshot = json.loads(path.read_text())
        snapshot["baseline.py"] += "\n# altered\n"
        path.write_text(json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, "source snapshot"):
            verify_invoice_run(self.manifest, self.run_dir)
        with self.assertRaisesRegex(ValueError, "Pipeline source changed"):
            run_invoice_baseline(self.manifest, self.run_dir, resume=True)

    def test_unknown_ocr_reference_in_saved_prediction_is_rejected(self):
        with patch("docwork.invoice_run.tesseract_page", side_effect=self.page):
            run_invoice_baseline(self.manifest, self.run_dir)
        path = self.run_dir / "predictions/invoice-1.json"
        saved = json.loads(path.read_text())
        saved["record"]["line_items"][0]["description"]["evidence_ids"] = ["invented"]
        path.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, "unknown OCR evidence"):
            verify_invoice_run(self.manifest, self.run_dir)

    def test_source_change_during_run_cannot_publish_scored_report(self):
        snapshot = _pipeline_snapshot()
        changed = {**snapshot, "baseline.py": snapshot["baseline.py"] + "\n# changed\n"}
        with patch("docwork.invoice_run.tesseract_page", side_effect=self.page), \
                patch("docwork.invoice_run._pipeline_snapshot", side_effect=[snapshot, changed]):
            with self.assertRaisesRegex(ValueError, "Pipeline source changed during run"):
                run_invoice_baseline(self.manifest, self.run_dir)
        self.assertFalse((self.run_dir / "report.json").exists())

    def test_spatial_profile_is_explicit_and_resume_cannot_change_extractor(self):
        from dataclasses import replace
        original = self.page(self.source, page_number=1)
        split = replace(original, spans=tuple(
            replace(span, text="Design review", box=Box(.1, .35, .3, .38))
            if span.id == "p1-l7" else span for span in original.spans
        ) + (TextSpan("quantity", 1, "1", Box(.45, .35, .46, .38), "fixture"),
             TextSpan("prices", 1, "10.00 10.00", Box(.65, .35, .85, .38), "fixture")))
        candidate_dir = self.root / "spatial"
        with patch("docwork.invoice_run.tesseract_page", return_value=split):
            default = run_invoice_baseline(self.manifest, self.run_dir)
            candidate = run_invoice_baseline(self.manifest, candidate_dir, extractor="spatial_rules")
        self.assertEqual(default["run_identity"]["baseline_version"], "ocr-rules-v0.3")
        self.assertEqual(default["summary"]["row_detection"]["tp"], 1)
        self.assertEqual(candidate["run_identity"]["extractor"], "spatial_rules")
        self.assertEqual(candidate["run_identity"]["baseline_version"], "ocr-rules-v0.4")
        self.assertEqual(candidate["summary"]["row_detection"]["tp"], 2)
        self.assertEqual(verify_invoice_run(self.manifest, candidate_dir)["status"], "verified")
        with self.assertRaisesRegex(ValueError, "Run identity changed"):
            run_invoice_baseline(self.manifest, candidate_dir, resume=True)


if __name__ == "__main__":
    unittest.main()
