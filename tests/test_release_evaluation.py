from __future__ import annotations

import hashlib
import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from docwork.release_evaluation import score_saved_invoice_run, verify_invoice_manifest
from docwork.cli import main
from tests.test_release_scoring import candidate, gold

ROOT = Path(__file__).resolve().parents[1]
IMAGES = (ROOT / "samples" / "clean.png", ROOT / "samples" / "conflicting-total.png",
          ROOT / "samples" / "development" / "dev-02-01.png")


class ReleaseEvaluationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "assets").mkdir()
        self.predictions = self.root / "predictions"
        self.predictions.mkdir()
        self.documents = []
        for index, (split, image) in enumerate(zip(("development", "calibration", "test"), IMAGES), start=1):
            source = gold()
            source["id"] = f"invoice-{index}"
            source["split"] = split
            source["family_group"] = f"family-{index}"
            source["fields"]["supplier_name"] = f"Fictional Vendor {index}"
            path = self.root / "assets" / f"invoice-{index}.png"
            path.write_bytes(image.read_bytes())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            source["source_sha256"] = digest
            source["assets"] = [{"path": str(path.relative_to(self.root)), "sha256": digest}]
            self.documents.append(source)
        self.manifest = {"manifest_version": "invoice-corpus-v1", "dataset_id": "fixture-invoices",
                         "documents": self.documents}
        self.manifest_path = self.root / "manifest.json"
        self.save_manifest()

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    def test_scored_split_has_hashes_and_counts_all_documents(self):
        source = self.documents[2]
        prediction = {"source_sha256": source["source_sha256"], "record": candidate(source)}
        (self.predictions / f"{source['id']}.json").write_text(json.dumps(prediction))
        report = score_saved_invoice_run(self.manifest_path, self.predictions, "test")
        self.assertEqual(report["status"], "scored")
        self.assertEqual(report["summary"]["documents_scheduled"], 1)
        self.assertEqual(report["summary"]["row_exact"]["f1"], 1)
        self.assertEqual(len(report["implementation_sha256"]), 64)
        self.assertEqual(report["documents"][0]["prediction_sha256"],
                         hashlib.sha256((self.predictions / "invoice-3.json").read_bytes()).hexdigest())

    def test_missing_prediction_is_incomplete_evidence_and_counts_as_failure(self):
        report = score_saved_invoice_run(self.manifest_path, self.predictions, "test")
        self.assertEqual(report["status"], "incomplete_evidence")
        self.assertEqual(report["missing_prediction_ids"], ["invoice-3"])
        self.assertEqual(report["summary"]["documents_processed"], 0)
        self.assertEqual(report["summary"]["row_detection"]["fn"], 2)

    def test_explicit_model_failure_is_scored_with_complete_evidence(self):
        source = self.documents[2]
        (self.predictions / "invoice-3.json").write_text(json.dumps({
            "source_sha256": source["source_sha256"], "record": None,
            "failure_type": "ModelOutputInvalid",
        }))
        report = score_saved_invoice_run(self.manifest_path, self.predictions, "test")
        self.assertEqual(report["status"], "scored")
        self.assertEqual(report["summary"]["documents_processed"], 0)
        self.assertEqual(report["summary"]["failures_by_type"], {"ModelOutputInvalid": 1})

    def test_cross_split_vendor_and_content_leakage_are_rejected(self):
        self.documents[2]["family_group"] = self.documents[0]["family_group"]
        with self.assertRaisesRegex(ValueError, "family appears"):
            verify_invoice_manifest(self.manifest, self.manifest_path)
        self.documents[2]["family_group"] = "family-3"
        self.documents[2]["fields"]["supplier_name"] = self.documents[0]["fields"]["supplier_name"]
        with self.assertRaisesRegex(ValueError, "vendor appears"):
            verify_invoice_manifest(self.manifest, self.manifest_path)
        self.documents[2]["fields"]["supplier_name"] = "Fictional Vendor 3"
        self.documents[2]["assets"][0]["path"] = self.documents[0]["assets"][0]["path"]
        self.documents[2]["assets"][0]["sha256"] = self.documents[0]["assets"][0]["sha256"]
        self.documents[2]["source_sha256"] = self.documents[0]["source_sha256"]
        with self.assertRaisesRegex(ValueError, "asset appears"):
            verify_invoice_manifest(self.manifest, self.manifest_path)

    def test_tampered_asset_and_wrong_prediction_source_are_rejected(self):
        path = self.root / self.documents[2]["assets"][0]["path"]
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Asset hash differs"):
            verify_invoice_manifest(self.manifest, self.manifest_path)
        path.write_bytes(IMAGES[2].read_bytes())
        source = self.documents[2]
        (self.predictions / "invoice-3.json").write_text(json.dumps({
            "source_sha256": "0" * 64, "record": candidate(source),
        }))
        with self.assertRaisesRegex(ValueError, "Prediction source hash differs"):
            score_saved_invoice_run(self.manifest_path, self.predictions, "test")

    def test_extra_prediction_file_is_rejected(self):
        (self.predictions / "invoice-other.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Unexpected prediction files"):
            score_saved_invoice_run(self.manifest_path, self.predictions, "test")

    def test_cli_writes_a_report_and_signals_missing_predictions(self):
        report_path = self.root / "report.json"
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["eval-score-invoices", str(self.manifest_path), str(self.predictions),
                         "--split", "test", "--output", str(report_path)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(report_path.read_text())["status"], "incomplete_evidence")


if __name__ == "__main__":
    unittest.main()
