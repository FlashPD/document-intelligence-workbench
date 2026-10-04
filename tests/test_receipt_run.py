from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from docwork.cord import map_labels
from docwork.receipt import extract_receipt_rules
from docwork.receipt_run import freeze_receipts, run_receipts, verify_receipt_run
from test_receipt import page

ROOT = Path(__file__).resolve().parents[1]


class ReceiptRunTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.profile = ROOT / "config/model-mac-instruct.json"
        self.manifest = self.root / "corpus" / "manifest.json"
        self.manifest.parent.mkdir()
        docs = []
        for split in ("validation", "test"):
            for i in range(2):
                id = f"cord-{split}-{i}"
                asset = self.manifest.parent / f"{id}.png"
                asset.write_bytes(b"fixture-" + id.encode())
                gold = map_labels({"gt_parse": {"menu": {"nm": "REAL GANACHE", "cnt": "1", "price": "16,500"},
                                                "total": {"total_price": "32.450"}}}, id)
                docs.append({**gold, "split": split, "source_sha256": hashlib.sha256(asset.read_bytes()).hexdigest(),
                             "asset": {"path": asset.name, "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()}})
        self.manifest.write_text(json.dumps({"documents": docs}))
        mocking = patch("docwork.receipt_run.verify_cord", return_value={})
        mocking.start()
        self.addCleanup(mocking.stop)
        mocking = patch("docwork.receipt_run.subprocess.check_output", return_value="fixture-tesseract\n")
        mocking.start()
        self.addCleanup(mocking.stop)
        mocking = patch("docwork.receipt_run.tesseract_page", return_value=page())
        self.ocr = mocking.start()
        self.addCleanup(mocking.stop)

        @contextmanager
        def server(root, profile, log):
            log.write_text("fixture model log")
            yield None, {"shutdown_complete": True, "model_sha256": profile["model"]["sha256"],
                         "runtime_archive_sha256": profile["runtime"]["sha256"], "inference": profile["inference"]}

        mocking = patch("docwork.receipt_run.managed_server", side_effect=server)
        mocking.start()
        self.addCleanup(mocking.stop)
        mocking = patch("docwork.receipt_run.extract_receipt_model", side_effect=lambda page, config: extract_receipt_rules(page))
        self.model = mocking.start()
        self.addCleanup(mocking.stop)

    def run_variant(self, path, **kwargs):
        return run_receipts(ROOT, self.manifest, self.profile, self.root / path, **kwargs)

    def development(self):
        self.run_variant("rules-dev")
        self.run_variant("model-dev", variant="span_llm", ocr_run=self.root / "rules-dev", limit=1)
        freeze = self.root / "freeze.json"
        freeze_receipts(self.manifest, self.root / "rules-dev", self.root / "model-dev", self.profile, freeze)
        return freeze

    def test_test_split_is_sealed_without_current_development_freeze(self):
        with self.assertRaisesRegex(ValueError, "requires a freeze"):
            self.run_variant("sealed", split="test")
        self.assertFalse((self.root / "sealed").exists())
        freeze = self.development()
        report = self.run_variant("test", split="test", freeze_path=freeze)
        self.assertEqual(report["summary"]["documents_scheduled"], 2)
        self.assertEqual(verify_receipt_run(self.manifest, self.root / "test")["status"], "verified")
        with self.assertRaises(ValueError):
            self.run_variant("subset-test", split="test", freeze_path=freeze, limit=1)

    def test_model_uses_shared_ocr_and_resume_does_not_repeat_inference(self):
        self.run_variant("rules")
        report = self.run_variant("model", variant="span_llm", ocr_run=self.root / "rules")
        self.assertEqual(self.ocr.call_count, 2)
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(self.run_variant("model", variant="span_llm", ocr_run=self.root / "rules", resume=True), report)
        self.assertEqual(self.model.call_count, 2)

    def test_every_ocr_failure_stays_in_row_and_header_denominators(self):
        with patch("docwork.receipt_run.tesseract_page", side_effect=RuntimeError("unreadable")):
            report = self.run_variant("failed")
        self.assertEqual(report["summary"]["documents_processed"], 0)
        self.assertEqual(report["summary"]["row_detection"]["fn"], 2)
        self.assertEqual(report["summary"]["header_fields"]["total"]["fn"], 2)
        self.assertEqual(verify_receipt_run(self.manifest, self.root / "failed")["status"], "verified")

    def test_tampering_predictions_and_freeze_is_rejected(self):
        freeze = self.development()
        frozen = json.loads(freeze.read_text())
        frozen["prompt_sha256"] = "changed"
        freeze.write_text(json.dumps(frozen))
        with self.assertRaisesRegex(ValueError, "pipeline"):
            self.run_variant("bad-test", split="test", freeze_path=freeze)
        path = self.root / "rules-dev/predictions/cord-validation-0.json"
        path.write_text("{}")
        with self.assertRaisesRegex(ValueError, "artifact differs"):
            verify_receipt_run(self.manifest, self.root / "rules-dev")


if __name__ == "__main__":
    unittest.main()
