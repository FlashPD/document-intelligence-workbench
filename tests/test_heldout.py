from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docwork.heldout import create_freeze, run_heldout, verify_heldout, quality_breakdown, finalize_saved_heldout
from docwork.invoice_run import run_invoice_baseline
from tests.test_release_scoring import gold
import test_invoice_run

ROOT = Path(__file__).resolve().parents[1]
OCR_IDENTITY = {"version": "fixture-tesseract", "executable_sha256": "1" * 64,
                "language_assets": {"eng": "2" * 64, "osd": "3" * 64}}


class HeldoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.corpus = self.root / "corpus"
        self.corpus.mkdir()
        sample = (ROOT / "samples/clean.png").read_bytes()
        docs = []
        for split in ("development", "calibration", "test"):
            asset = self.corpus / f"{split}.png"
            asset.write_bytes(sample + split.encode())
            doc = gold()
            doc.update(id=f"invoice-{split}", split=split, family_group=f"family-{split}",
                       source_sha256=hashlib.sha256(asset.read_bytes()).hexdigest(),
                       assets=[{"path": asset.name, "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()}],
                       pages=[{"number": 1}])
            doc["fields"]["supplier_name"] = f"Vendor {split}"
            docs.append(doc)
        self.manifest = self.corpus / "manifest.json"
        self.manifest.write_text(json.dumps({"manifest_version": "invoice-corpus-v1", "dataset_id": "freeze-fixture", "documents": docs}))
        self.evidence = [self.root / "development", self.root / "calibration"]
        with patch("docwork.invoice_run.tesseract_page", side_effect=test_invoice_run.InvoiceRunTests.page), \
                patch("docwork.invoice_run.subprocess.run", return_value=SimpleNamespace(stdout="fixture-tesseract\n")):
            for directory in self.evidence:
                run_invoice_baseline(self.manifest, directory, split=directory.name)
        identity = patch("docwork.heldout.ocr_identity", return_value=copy.deepcopy(OCR_IDENTITY))
        identity.start()
        self.addCleanup(identity.stop)
        self.freeze = self.root / "freeze.json"
        self.run_dir = self.root / "heldout"

    def freeze_run(self):
        return create_freeze(self.root, self.manifest, self.freeze, self.evidence)

    def evaluate(self, **kwargs):
        with patch("docwork.invoice_run.tesseract_page", side_effect=test_invoice_run.InvoiceRunTests.page) as ocr:
            report = run_heldout(self.manifest, self.freeze, self.run_dir, **kwargs)
        return report, ocr.call_count

    def test_freeze_and_run_score_every_test_document_and_verify_offline(self):
        freeze = self.freeze_run()
        self.assertEqual(freeze["document_ids"], ["invoice-test"])
        report, calls = self.evaluate()
        self.assertEqual(calls, 1)
        self.assertEqual(report["split"], "test")
        self.assertEqual(report["summary"]["documents_scheduled"], 1)
        self.assertEqual(verify_heldout(self.manifest, self.run_dir)["status"], "verified")
        resumed, calls = self.evaluate(resume=True)
        self.assertEqual(calls, 0)
        self.assertEqual(resumed, report)

    def test_freeze_requires_both_measured_splits_and_cannot_overwrite(self):
        with self.assertRaisesRegex(ValueError, "Both development and calibration"):
            create_freeze(self.root, self.manifest, self.freeze, self.evidence[:1])
        self.freeze_run()
        with self.assertRaisesRegex(ValueError, "Freeze already exists"):
            self.freeze_run()

    def test_freeze_rejects_changed_extraction_source_in_selection_evidence(self):
        path = self.evidence[0] / "source_snapshot.json"
        snapshot = json.loads(path.read_text())
        snapshot["baseline.py"] += "\n# altered\n"
        path.write_text(json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, "source snapshot"):
            self.freeze_run()
        self.assertFalse(self.freeze.exists())

    def test_changed_source_or_ocr_assets_cannot_enter_test(self):
        self.freeze_run()
        for target in ("source", "ocr"):
            with self.subTest(target=target):
                where = "docwork.heldout.source_hashes" if target == "source" else "docwork.heldout.ocr_identity"
                with patch(where, return_value={}):
                    with self.assertRaisesRegex(ValueError, "Frozen implementation or OCR runtime changed"):
                        self.evaluate()
                self.assertFalse(self.run_dir.exists())

    def test_freeze_cannot_drop_test_documents_or_change_variant(self):
        original = self.freeze_run()
        for field, value in (("document_ids", []), ("extractor", "spatial_rules"), ("ocr_psm", 3)):
            changed = {**original, field: value}
            self.freeze.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                self.evaluate()
            self.assertFalse(self.run_dir.exists())

    def test_gold_labels_are_not_passed_to_extractor(self):
        self.freeze_run()
        from docwork.invoice_run import _extract
        received = []

        def extract(metadata, *args):
            received.append(metadata)
            return _extract(metadata, *args)

        with patch("docwork.heldout._extract", side_effect=extract):
            self.evaluate()
        self.assertEqual(set(received[0]), {"id", "source_sha256", "assets", "pages"})
        self.assertEqual(received[0]["pages"], [{"number": 1}])

    def test_failures_stay_in_denominators_and_have_complete_evidence(self):
        self.freeze_run()
        with patch("docwork.invoice_run.tesseract_page", side_effect=RuntimeError("OCR failed")):
            report = run_heldout(self.manifest, self.freeze, self.run_dir)
        self.assertEqual(report["summary"]["documents_scheduled"], 1)
        self.assertEqual(report["summary"]["documents_processed"], 0)
        self.assertEqual(report["summary"]["failures_by_type"], {"RuntimeError": 1})
        self.assertEqual(verify_heldout(self.manifest, self.run_dir)["status"], "verified")

    def test_prediction_tampering_is_rejected_on_resume_and_verification(self):
        self.freeze_run()
        self.evaluate()
        path = self.run_dir / "predictions/invoice-test.json"
        prediction = json.loads(path.read_text())
        prediction["record"]["fields"]["total"]["value"] = "999.00"
        path.write_text(json.dumps(prediction))
        with self.assertRaisesRegex(ValueError, "checksum differs"):
            verify_heldout(self.manifest, self.run_dir)
        with self.assertRaises(ValueError):
            self.evaluate(resume=True)

    def test_report_and_source_snapshot_tampering_are_rejected(self):
        self.freeze_run()
        self.evaluate()
        path = self.run_dir / "report.json"
        original = path.read_text()
        report = json.loads(original)
        report["summary"]["header_macro_f1"] = .123
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "report differs"):
            verify_heldout(self.manifest, self.run_dir)
        path.write_text(original)
        snapshot_path = self.run_dir / "source_snapshot.json"
        snapshot = json.loads(snapshot_path.read_text())
        snapshot["baseline.py"] += "\n# changed\n"
        snapshot_path.write_text(json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, "snapshot differs"):
            verify_heldout(self.manifest, self.run_dir)

    def test_resume_recomputes_uncommitted_prediction_after_interrupted_ledger_write(self):
        self.freeze_run()
        from docwork.heldout import _write_new

        def write(path, value):
            if path.name == "completed.json":
                raise OSError("interruption before ledger commit")
            _write_new(path, value)

        with patch("docwork.heldout._write_new", side_effect=write):
            with self.assertRaises(OSError):
                self.evaluate()
        self.assertTrue((self.run_dir / "predictions/invoice-test.json").exists())
        self.assertFalse((self.run_dir / "report.json").exists())
        report, calls = self.evaluate(resume=True)
        self.assertEqual(calls, 1)
        self.assertEqual(report["status"], "scored")
        self.assertEqual(verify_heldout(self.manifest, self.run_dir)["status"], "verified")

    def test_uncertainty_keeps_degraded_derivatives_with_their_parent(self):
        from docwork.release_scoring import score_invoice
        base = gold()
        base.update(id="base", split="test", parent_id=None)
        degraded = copy.deepcopy(base)
        degraded.update(id="degraded", parent_id="base")
        scores = [score_invoice(base, None), score_invoice(degraded, None)]
        manifest = {"documents": [base, degraded]}
        breakdown = quality_breakdown(manifest, scores, draws=20)
        self.assertEqual(breakdown["uncertainty"]["parent_groups"], 1)
        self.assertEqual(breakdown["uncertainty"]["percentile_95_intervals"]["header_macro_f1"], [0.0, 0.0])
        self.assertEqual(next(iter(breakdown["families"].values()))["documents_scheduled"], 2)
        self.assertEqual(breakdown, quality_breakdown(manifest, scores, draws=20))

    def test_bootstrap_can_resample_a_document_more_than_once(self):
        from docwork.release_scoring import score_invoice
        first = gold()
        first.update(id="first", split="test")
        second = copy.deepcopy(first)
        second["id"] = "second"
        breakdown = quality_breakdown({"documents": [first, second]},
                                      [score_invoice(first, None), score_invoice(second, None)], draws=100)
        self.assertEqual(breakdown["uncertainty"]["parent_groups"], 2)
        self.assertEqual(breakdown["uncertainty"]["percentile_95_intervals"]["exact_row_f1"], [0.0, 0.0])

    def test_reporting_finalization_preserves_freeze_and_prediction_bytes(self):
        self.freeze_run()
        self.evaluate()
        freeze = (self.run_dir / "freeze.json").read_bytes()
        prediction = (self.run_dir / "predictions/invoice-test.json").read_bytes()
        (self.run_dir / "report.json").unlink()  # Simulate failure before a report was published.
        with patch("docwork.invoice_run.tesseract_page") as ocr:
            report = finalize_saved_heldout(self.manifest, self.run_dir)
        ocr.assert_not_called()
        self.assertEqual((self.run_dir / "freeze.json").read_bytes(), freeze)
        self.assertEqual((self.run_dir / "predictions/invoice-test.json").read_bytes(), prediction)
        self.assertEqual(report["reporting_correction"]["version"], "bootstrap-replicate-ids-v1")
        self.assertEqual(verify_heldout(self.manifest, self.run_dir)["status"], "verified")
        with self.assertRaisesRegex(ValueError, "report already exists"):
            finalize_saved_heldout(self.manifest, self.run_dir)


if __name__ == "__main__":
    unittest.main()
