"""Real container smoke check; run only when Docker daemon and image are ready."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from docwork.intake import IntakeStore
from docwork.worker import process_one


class ParserSmoke(unittest.TestCase):
    def test_container_processes_committed_png(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "clean.png"
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            store = IntakeStore(root / "review.sqlite", root / "objects")
            document_id = store.submit(io.BytesIO(sample.read_bytes()), "clean.png", "image/png")
            process_one(store, "smoke")
            self.assertEqual(store.status(document_id)["status"], "REVIEW_READY", store.status(document_id))
            self.assertTrue(store.get(document_id)["page"]["spans"])


if __name__ == "__main__":
    unittest.main()
