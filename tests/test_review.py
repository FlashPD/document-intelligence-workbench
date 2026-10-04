from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from docwork.baseline import extract_invoice
from docwork.contracts import Box, DocumentPage, TextSpan
from docwork.review import ReviewBlocked, ReviewConflict, ReviewStore


def candidate(total: str = "270.00") -> tuple[DocumentPage, object]:
    lines = (
        "Aster Studio LLC", "INVOICE", "Invoice Number: AST-1001", "Date: 2026-09-12",
        "Due Date: 2026-10-12", "Currency: USD", "Research workshop 2 125.00 250.00",
        "Subtotal: 250.00", "Tax: 20.00", "Discount: 0.00", "Shipping: 0.00",
        f"Total: {total}",
    )
    page = DocumentPage(1, 1000, 1000, tuple(
        TextSpan(f"s{index}", 1, line, Box(.1, .02 + index * .07, .9, .05 + index * .07), "fixture")
        for index, line in enumerate(lines)
    ))
    return page, extract_invoice(page)


class ReviewStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "review.sqlite"
        self.store = ReviewStore(self.db)
        page, record = candidate()
        self.document = self.store.ingest(hashlib.sha256(b"first").hexdigest(), "first.png", page, record)

    def test_restart_revision_and_approval_bound_export(self) -> None:
        with self.assertRaises(ReviewConflict):
            self.store.export(self.document, "json")
        first = self.store.approve(self.document, 1, "reviewer")
        exported = self.store.export(self.document, "json")
        self.assertEqual(exported, self.store.export(self.document, "json"))
        payload = json.loads(Path(exported["files"][0]["path"]).read_text())
        self.assertEqual(payload["approval_hash"], first["approval_hash"])
        self.assertEqual(payload["decision_hash"], first["decision_hash"])
        self.assertEqual(payload["record"]["fields"]["total"]["value"], "270.00")

        restarted = ReviewStore(self.db)
        new_revision = restarted.edit(self.document, 1, "fields.total", "275.00", "reviewer")
        self.assertEqual(new_revision, 2)
        with self.assertRaises(ReviewConflict):
            restarted.edit(self.document, 1, "fields.total", "999.00", "reviewer")
        with self.assertRaises(ReviewConflict):
            restarted.export(self.document, "json")
        with self.assertRaises(ReviewBlocked):
            restarted.approve(self.document, 2, "reviewer")
        restarted.acknowledge(self.document, 2, "TOTAL_MISMATCH", "fields.total", "Verified original invoice", "reviewer")
        second = restarted.approve(self.document, 2, "reviewer")
        self.assertNotEqual(first["approval_hash"], second["approval_hash"])
        with self.assertRaises(ReviewConflict):
            restarted.acknowledge(self.document, 2, "TOTAL_MISMATCH", "fields.total", "Changed mind", "reviewer")
        second_export = restarted.export(self.document, "json")
        self.assertEqual(json.loads(Path(second_export["files"][0]["path"]).read_text())["record"]["fields"]["total"]["value"], "275.00")
        self.assertEqual(json.loads(Path(exported["files"][0]["path"]).read_text())["record"]["fields"]["total"]["value"], "270.00")
        self.assertEqual(self.store.get(self.document, 1)["approval"]["approval_hash"], first["approval_hash"])

    def test_retry_detects_changed_export(self) -> None:
        self.store.approve(self.document, 1, "reviewer")
        manifest = self.store.export(self.document, "json")
        Path(manifest["files"][0]["path"]).write_text("changed")
        with self.assertRaises(ReviewConflict):
            self.store.export(self.document, "json")

    def test_required_missing_cannot_be_approved_even_if_acknowledged(self) -> None:
        self.store.edit(self.document, 1, "fields.currency", None, "reviewer")
        self.store.acknowledge(self.document, 2, "REQUIRED_MISSING", "fields.currency", "No currency visible", "reviewer")
        with self.assertRaises(ReviewBlocked):
            self.store.approve(self.document, 2, "reviewer")

    def test_duplicate_content_is_separate_and_csv_text_is_escaped(self) -> None:
        page, record = candidate()
        duplicate = self.store.ingest(hashlib.sha256(b"first").hexdigest(), "second.png", page, record)
        self.assertNotEqual(duplicate, self.document)
        self.store.approve(self.document, 1, "reviewer")
        with self.assertRaises(ReviewConflict):
            self.store.export(duplicate, "json")
        self.store.edit(self.document, 1, "line_items.row-001.description", " =HYPERLINK(\"bad\")", "reviewer")
        self.store.approve(self.document, 2, "reviewer")
        manifest = self.store.export(self.document, "csv")
        item_path = next(Path(entry["path"]) for entry in manifest["files"] if entry["path"].endswith("line-items.csv"))
        with item_path.open(newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["description"], "' =HYPERLINK(\"bad\")")
        self.assertEqual(rows[0]["line_total"], "250.00")

    def test_conflict_needs_explicit_decision_and_history_survives_restart(self) -> None:
        page, record = candidate("275.00")
        document = self.store.ingest(hashlib.sha256(b"conflict").hexdigest(), "conflict.png", page, record)
        with self.assertRaises(ReviewBlocked):
            self.store.approve(document, 1, "reviewer")
        self.store.acknowledge(document, 1, "TOTAL_MISMATCH", "fields.total", "Checked source", "reviewer")
        self.store.approve(document, 1, "reviewer")
        self.assertEqual([event["kind"] for event in ReviewStore(self.db).history(document)],
                         ["candidate_created", "issue_acknowledged", "approved"])


if __name__ == "__main__":
    unittest.main()
