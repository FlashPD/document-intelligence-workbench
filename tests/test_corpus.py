from __future__ import annotations

import unittest
from pathlib import Path

from docwork.corpus import verify_synthetic_corpus


class SyntheticCorpusTests(unittest.TestCase):
    def test_frozen_assets_labels_and_split_contract(self):
        manifest = Path(__file__).resolve().parents[1] / "datasets" / "invoices-v1" / "manifest.json"
        report = verify_synthetic_corpus(manifest)
        self.assertEqual(report["documents"], 540)
        self.assertEqual(report["split_counts"], {"development": 180,
                                                  "calibration": 180, "test": 180})
        self.assertEqual(report["families"], 18)
        self.assertEqual(report["multi_page_documents"], 90)
        self.assertEqual(report["degraded_documents"], 90)
        self.assertEqual(report["intentional_total_conflicts"], 72)
        self.assertEqual(report["ambiguous_date_cases"], 42)


if __name__ == "__main__":
    unittest.main()
