from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from docwork.contracts import Box
from docwork.evaluation import box_iou, score_document, summarize_document_scores, verify_development_manifest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "datasets" / "development-v0.json").read_text())


class DevelopmentEvaluationTests(unittest.TestCase):
    def test_frozen_manifest_assets_and_transforms(self) -> None:
        verify_development_manifest(MANIFEST, ROOT)
        rotated = [item for item in MANIFEST["documents"] if item["treatment"] == "crop_rotate_270_clockwise"]
        self.assertEqual(len(rotated), 1)
        self.assertEqual(rotated[0]["geometry"]["rotation_clockwise"], 270)

    def test_asset_hash_change_is_rejected(self) -> None:
        tampered = copy.deepcopy(MANIFEST)
        tampered["documents"][0]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            verify_development_manifest(tampered, ROOT)

    def test_failed_document_stays_in_metric_denominators(self) -> None:
        gold = MANIFEST["documents"][0]
        score = score_document(gold, None)
        report = summarize_document_scores([score])
        self.assertEqual(report["documents_scheduled"], 1)
        self.assertEqual(report["documents_processed"], 0)
        self.assertEqual(report["header_exact"], {"correct": 0, "eligible": 10})
        self.assertEqual(report["line_total_exact_by_order"], {"correct": 0, "eligible": len(gold["line_items"])})

    def test_box_iou(self) -> None:
        self.assertEqual(box_iou(Box(0, 0, .5, .5), Box(.5, .5, 1, 1)), 0)
        self.assertEqual(box_iou(Box(.1, .1, .2, .2), Box(.1, .1, .2, .2)), 1)


if __name__ == "__main__":
    unittest.main()
