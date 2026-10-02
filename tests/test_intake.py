from __future__ import annotations

import io
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.baseline import extract_invoice
from docwork.contracts import Box, DocumentPage, TextSpan
from docwork.intake import IntakeStore, MAX_BATCH_FILES
from docwork.ocr import MAX_FILE_BYTES
from docwork.review import ReviewBlocked, ReviewConflict

SAMPLE = (Path(__file__).resolve().parents[1] / "samples" / "clean.png").read_bytes()


def candidate():
    lines = (
        "Aster Studio LLC", "INVOICE", "Invoice Number: AST-1001", "Date: 2026-09-12",
        "Currency: USD", "Research workshop 2 125.00 250.00", "Subtotal: 250.00",
        "Tax: 20.00", "Discount: 0.00", "Shipping: 0.00", "Total: 270.00",
    )
    page = DocumentPage(1, 1000, 1000, tuple(
        TextSpan(f"s{index}", 1, line, Box(.1, .02 + index * .07, .9, .05 + index * .07), "fixture")
        for index, line in enumerate(lines)
    ))
    return page, extract_invoice(page)


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.store = IntakeStore(root / "review.sqlite", root / "artifacts")

    def submit(self, data=SAMPLE, name="clean.png", mime="image/png"):
        return self.store.submit(io.BytesIO(data), name, mime)

    def test_submission_stores_one_object_and_separate_jobs_for_duplicates(self):
        first = self.submit(name="../clean.png")
        second = self.submit(name="C:\\tmp\\copy.png")
        self.assertNotEqual(first, second)
        first_status = self.store.status(first)
        second_status = self.store.status(second)
        self.assertEqual(first_status["source_name"], "clean.png")
        self.assertEqual(first_status["source_sha256"], second_status["source_sha256"])
        self.assertEqual(first_status["status"], "RECEIVED")
        self.assertNotEqual(first_status["job"]["id"], second_status["job"]["id"])
        self.assertEqual(self.store.object_path(first).read_bytes(), SAMPLE)
        self.assertEqual(len(list((self.store.object_root / "objects").rglob(first_status["source_sha256"]))), 1)
        with self.assertRaises(ReviewBlocked):
            self.store.get(first)

    def test_spoofed_and_oversized_inputs_leave_no_document(self):
        for data, name, mime in (
            (SAMPLE, "fake.pdf", "application/pdf"),
            (SAMPLE, "clean.png", "image/jpeg"),
            (b"", "empty.png", "image/png"),
            (SAMPLE, "bad\nname.png", "image/png"),
            (SAMPLE[:16] + struct.pack(">II", 5000, 5000) + SAMPLE[24:], "huge.png", "image/png"),
        ):
            with self.subTest(name=name, mime=mime):
                with self.assertRaises(ValueError):
                    self.submit(data, name, mime)
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
        self.assertEqual(list((self.store.object_root / "quarantine").iterdir()), [])

    def test_stream_size_limit_stops_before_object_commit(self):
        class OversizedStream:
            remaining = MAX_FILE_BYTES + 1

            def read(self, amount):
                size = min(amount, self.remaining)
                self.remaining -= size
                return b"x" * size

        with self.assertRaisesRegex(ValueError, "20 MB"):
            self.store.submit(OversizedStream(), "huge.png", "image/png")
        self.assertEqual(list((self.store.object_root / "quarantine").iterdir()), [])
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)

    def test_jpeg_header_and_batch_limit(self):
        jpeg = b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x64\x00\xc8\x03\x01\x11\x00\xff\xd9"
        document = self.submit(jpeg, "scan.jpeg", "image/jpeg")
        self.assertEqual(self.store.status(document)["media_type"], "image/jpeg")
        with self.assertRaises(ValueError):
            self.store.submit_batch([(io.BytesIO(SAMPLE), "x.png", "image/png")] * (MAX_BATCH_FILES + 1))

    def test_expired_lease_fences_stale_worker_and_completion_is_atomic(self):
        document = self.submit()
        first = self.store.claim("worker-one", lease_seconds=1)
        self.assertIsNotNone(first)
        assert first is not None
        with patch("docwork.intake.time.time", return_value=first.lease_until + 1):
            second = self.store.claim("worker-two", lease_seconds=60)
            assert second is not None
            self.assertGreater(second.fence, first.fence)
            page, record = candidate()
            with self.assertRaises(ReviewConflict):
                self.store.complete(first, page, record)
            self.store.complete(second, page, record)
        self.assertEqual(self.store.status(document)["status"], "REVIEW_READY")
        self.assertEqual(self.store.status(document)["job"]["status"], "COMPLETE")
        self.assertEqual(self.store.get(document)["record"]["fields"]["total"]["value"], "270.00")
        self.assertIsNone(self.store.claim("another-worker"))

    def test_only_one_live_processing_job_is_claimed(self):
        first_doc = self.submit()
        second_doc = self.submit()
        first = self.store.claim("worker-one")
        self.assertIsNotNone(first)
        self.assertIsNone(self.store.claim("worker-two"))
        page, record = candidate()
        self.store.complete(first, page, record)
        second = self.store.claim("worker-two")
        self.assertIsNotNone(second)
        self.assertEqual({first.document_id, second.document_id}, {first_doc, second_doc})

    def test_renewal_extends_only_the_current_fenced_claim(self):
        self.submit()
        first = self.store.claim("worker-one", lease_seconds=1)
        assert first is not None
        with patch("docwork.intake.time.time", return_value=first.lease_until - .1):
            renewed = self.store.renew(first, lease_seconds=60)
        self.assertGreater(renewed.lease_until, first.lease_until)
        with patch("docwork.intake.time.time", return_value=first.lease_until + 1):
            self.assertIsNone(self.store.claim("worker-two"))
        with patch("docwork.intake.time.time", return_value=renewed.lease_until + 1):
            second = self.store.claim("worker-two", lease_seconds=60)
            assert second is not None
            with self.assertRaises(ReviewConflict):
                self.store.renew(first)
        self.assertGreater(second.fence, first.fence)

    def test_failure_retry_and_object_integrity(self):
        document = self.submit()
        claim = self.store.claim("worker")
        assert claim is not None
        self.store.fail(claim, "PARSER_FAILED")
        self.assertEqual(self.store.status(document)["job"]["error_code"], "PARSER_FAILED")
        self.store.retry(document)
        self.assertEqual(self.store.status(document)["status"], "RECEIVED")
        self.assertEqual(self.store.claim("worker-two").fence, claim.fence + 1)
        path = self.store.object_path(document)
        path.write_bytes(b"changed")
        with self.assertRaises(ReviewConflict):
            self.store.object_path(document)

    def test_existing_review_database_gains_intake_columns(self):
        old_db = Path(self.temp.name) / "old-review.sqlite"
        with sqlite3.connect(old_db) as db:
            db.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, source_name TEXT NOT NULL, page_json TEXT NOT NULL, current_revision INTEGER NOT NULL, created_at TEXT NOT NULL)")
        upgraded = IntakeStore(old_db, Path(self.temp.name) / "upgraded-artifacts")
        document = upgraded.submit(io.BytesIO(SAMPLE), "clean.png", "image/png")
        self.assertEqual(upgraded.status(document)["status"], "RECEIVED")


if __name__ == "__main__":
    unittest.main()
