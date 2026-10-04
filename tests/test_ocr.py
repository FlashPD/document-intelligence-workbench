from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.ocr import tesseract_page


class OcrModeTests(unittest.TestCase):
    def test_sparse_page_falls_back_when_orientation_detection_fails(self):
        source = Path(__file__).resolve().parents[1] / "samples" / "clean.png"
        tsv = ("level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\tconf\ttext\n"
               "5\t1\t1\t1\t10\t20\t50\t15\t95\tAster\n")
        responses = [subprocess.CompletedProcess([], 1, "", "Too few characters"),
                     subprocess.CompletedProcess([], 0, tsv, "")]
        with patch("docwork.ocr.subprocess.run", side_effect=responses) as run:
            page = tesseract_page(source)
        self.assertEqual(page.spans[0].text, "Aster")
        self.assertEqual([call.args[0][-3:] for call in run.call_args_list],
                         [["--psm", "1", "tsv"], ["--psm", "3", "tsv"]])

    def test_unsupported_mode_is_rejected_before_ocr(self):
        with self.assertRaisesRegex(ValueError, "page segmentation modes"):
            tesseract_page(Path("unused.png"), page_segmentation_mode=6)


if __name__ == "__main__":
    unittest.main()
