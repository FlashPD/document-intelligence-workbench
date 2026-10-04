"""Offline replay integrity, publication, and CLI contracts."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.cli import main
from docwork.demo_replay import CASES, prepare_replay
from docwork.intake import IntakeStore

ROOT = Path(__file__).resolve().parents[1]


class DemoReplayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name) / "replay"

    def test_offline_cases_retain_sources_issues_and_multiple_pages(self):
        with patch("docwork.ocr.tesseract_page", side_effect=AssertionError("Live OCR")), \
                patch("docwork.model_runtime.managed_server", side_effect=AssertionError("Model startup")):
            replay = prepare_replay(ROOT, self.output)
        self.assertEqual(json.loads((self.output / "replay.json").read_text()), replay)
        self.assertEqual([case["corpus_id"] for case in replay["cases"]], [case[0] for case in CASES])
        store = IntakeStore(self.output / "review.sqlite", self.output / "objects")
        details = [store.get(case["document_id"]) for case in replay["cases"]]
        self.assertEqual(len(store.list_documents()), 4)
        self.assertTrue(all(d["extraction"]["profile"] == "replay_ocr_rules" and d["approval"] is None for d in details))
        self.assertEqual(details[0]["issues"], [])
        self.assertIn("TOTAL_MISMATCH", [issue["code"] for issue in details[1]["issues"]])
        self.assertIn("INVALID_ROW_AMOUNT", [issue["code"] for issue in details[2]["issues"]])
        self.assertEqual(len(details[3]["pages"]), 2)
        for detail in details:
            for page in detail["pages"]:
                self.assertTrue(store.page_image_path(detail["document_id"], page["number"]).is_file())
        self.assertEqual(store.reconcile()["missing"], [])

    def test_existing_workbench_is_preserved(self):
        self.output.mkdir()
        sentinel = self.output / "review.sqlite"
        sentinel.write_bytes(b"existing review")
        with self.assertRaisesRegex(ValueError, "new output directory"):
            prepare_replay(ROOT, self.output)
        self.assertEqual(sentinel.read_bytes(), b"existing review")

    def test_changed_recorded_asset_refuses_atomic_publication(self):
        from docwork.demo_replay import file_hash

        def changed_hash(path):
            if path.name == "inv-f02-02.png":
                return "0" * 64
            return file_hash(path)

        with patch("docwork.demo_replay.file_hash", side_effect=changed_hash):
            with self.assertRaisesRegex(ValueError, "asset differs"):
                prepare_replay(ROOT, self.output)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.output.parent.iterdir()), [])

    def test_cli_prepare_only_and_server_share_separate_replay_store(self):
        for prepare_only in (True, False):
            output = self.output / str(prepare_only)
            arguments = ["demo-replay", "--output-dir", str(output), "--port", "9876"]
            if prepare_only:
                arguments.append("--prepare-only")
            with patch("docwork.web.serve") as serve, contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(arguments), 0)
            self.assertIn("no live extraction", printed.getvalue())
            if prepare_only:
                serve.assert_not_called()
            else:
                self.assertEqual(serve.call_args.args, (output / "review.sqlite", output / "objects", 9876))
                self.assertEqual(len(serve.call_args.kwargs["demo_replay"]["cases"]), 4)


if __name__ == "__main__":
    unittest.main()
