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
from docwork.invoice_run import run_invoice_baseline, verify_invoice_run
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


if __name__ == "__main__":
    unittest.main()
