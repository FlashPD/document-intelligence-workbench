from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from docwork.contracts import Box, DocumentPage, TextSpan
from docwork.intake import IntakeStore
from docwork.ocr import png_dimensions
from docwork.parser_protocol import PARSER_VERSION
from docwork.review import ReviewBlocked
from docwork.worker import ParserFailure, process_one, validate_output

SAMPLE = (Path(__file__).resolve().parents[1] / "samples" / "clean.png").read_bytes()
LINES = (
    "Aster Studio LLC", "INVOICE", "Invoice Number: AST-1001", "Date: 2026-09-12",
    "Due Date: 2026-10-12", "Currency: USD", "Research workshop 2 125.00 250.00",
    "Subtotal: 250.00", "Tax: 20.00", "Discount: 0.00", "Shipping: 0.00", "Total: 270.00",
)


def parser_result(output: Path, source: Path, *, source_hash: str | None = None,
                  lines_by_page: tuple[tuple[str, ...], ...] = (LINES,)) -> None:
    pages = []
    for number, lines in enumerate(lines_by_page, start=1):
        raster = output / f"page-{number:04d}.png"
        raster.write_bytes(SAMPLE)
        width, height = png_dimensions(raster)
        page = DocumentPage(number, width, height, tuple(
            TextSpan(f"p{number}-l{index:04d}", number, line,
                     Box(.1, .02 + index * .07, .9, .05 + index * .07), "tesseract-eng")
            for index, line in enumerate(lines, start=1)
        ))
        pages.append({"page_sha256": hashlib.sha256(SAMPLE).hexdigest(), "page": asdict(page)})
    (output / "result.json").write_text(json.dumps({
        "parser_version": PARSER_VERSION,
        "source_sha256": source_hash or hashlib.sha256(source.read_bytes()).hexdigest(),
        "pages": pages,
    }))


class WorkerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.store = IntakeStore(root / "review.sqlite", root / "objects")

    def submit(self):
        return self.store.submit(io.BytesIO(SAMPLE), "clean.png", "image/png")

    def test_queued_upload_becomes_reviewable_and_page_is_preserved(self):
        document_id = self.submit()
        with self.assertRaises(ReviewBlocked):
            self.store.get(document_id)

        def runner(source, mime, output, claim, *, image):
            self.assertEqual(mime, "image/png")
            self.assertEqual(image, "test-image")
            parser_result(output, source)

        self.assertEqual(process_one(self.store, "worker", image="test-image", runner=runner), document_id)
        self.assertEqual(self.store.status(document_id)["status"], "REVIEW_READY")
        self.assertEqual(self.store.status(document_id)["job"]["status"], "COMPLETE")
        self.assertEqual(self.store.page_image_path(document_id).read_bytes(), SAMPLE)
        self.assertEqual(self.store.get(document_id)["record"]["fields"]["total"]["value"], "270.00")
        self.store.approve(document_id, 1, "reviewer")
        self.assertEqual(self.store.export(document_id, "json")["format"], "json")
        self.assertIsNone(process_one(self.store, "worker", runner=runner))

    def test_bad_parser_result_is_failed_and_retryable(self):
        document_id = self.submit()

        def bad_runner(source, mime, output, claim, *, image):
            parser_result(output, source, source_hash="0" * 64)

        process_one(self.store, "worker", runner=bad_runner)
        self.assertEqual(self.store.status(document_id)["job"]["error_code"], "PARSER_OUTPUT_INVALID")
        self.store.retry(document_id)
        process_one(self.store, "worker", runner=lambda source, mime, output, claim, image: parser_result(output, source))
        self.assertEqual(self.store.status(document_id)["status"], "REVIEW_READY")
        self.assertEqual(self.store.status(document_id)["job"]["attempts"], 2)

    def test_two_pages_merge_rows_and_keep_second_page_evidence(self):
        document_id = self.submit()
        first = tuple(line for line in LINES if not line.startswith("Research workshop"))
        second = ("INVOICE", "Research workshop 2 125.00 250.00")
        process_one(self.store, "worker", runner=lambda source, mime, output, claim, image:
                    parser_result(output, source, lines_by_page=(first, second)))
        detail = self.store.get(document_id)
        self.assertEqual(self.store.status(document_id)["page_count"], 2)
        self.assertEqual([page["number"] for page in detail["pages"]], [1, 2])
        row = detail["record"]["line_items"][0]
        self.assertEqual(row["line_total"]["evidence_ids"], ["p2-l0002"])
        self.assertEqual(self.store.page_image_path(document_id, 2).read_bytes(), SAMPLE)
        self.store.edit(document_id, 1, "line_items.row-001.description", "Research workshop", "reviewer",
                        ("p2-l0002",))
        self.assertFalse(any(issue["code"] == "EVIDENCE_UNKNOWN" for issue in self.store.get(document_id)["issues"]))
        self.store.approve(document_id, 2, "reviewer")
        manifest = self.store.export(document_id, "json")
        exported = json.loads(Path(manifest["files"][0]["path"]).read_text())
        self.assertEqual(len(exported["pages"]), 2)

    def test_rejects_page_number_and_output_mismatch(self):
        with tempfile.TemporaryDirectory() as scratch:
            output = Path(scratch)
            source = output.parent / "source.png"
            source.write_bytes(SAMPLE)
            parser_result(output, source, lines_by_page=(LINES, ("INVOICE",)))
            source.unlink()
            (output / "page-0002.png").unlink()
            with self.assertRaises(ParserFailure):
                validate_output(output, hashlib.sha256(SAMPLE).hexdigest())

    def test_repeated_header_conflict_requires_review(self):
        document_id = self.submit()
        second = ("INVOICE", "Total: 275.00")
        process_one(self.store, "worker", runner=lambda source, mime, output, claim, image:
                    parser_result(output, source, lines_by_page=(LINES, second)))
        detail = self.store.get(document_id)
        self.assertEqual(detail["record"]["fields"]["total"]["value"], "270.00")
        self.assertIn("HEADER_CONFLICT", [issue["code"] for issue in detail["issues"]])
        with self.assertRaises(ReviewBlocked):
            self.store.approve(document_id, 1, "reviewer")
        self.store.acknowledge(document_id, 1, "HEADER_CONFLICT", "fields.total",
                               "Confirmed page one total", "reviewer")
        self.store.approve(document_id, 1, "reviewer")

    def test_rejects_symlinks_and_unexpected_files(self):
        with tempfile.TemporaryDirectory() as scratch:
            output = Path(scratch)
            source = output / "source.png"
            source.write_bytes(SAMPLE)
            parser_result(output, source)
            source.unlink()
            (output / "page-0001.png").unlink()
            (output / "page-0001.png").symlink_to(Path(__file__))
            with self.assertRaises(ParserFailure):
                validate_output(output, hashlib.sha256(SAMPLE).hexdigest())
            (output / "page-0001.png").unlink()
            (output / "page-0001.png").write_bytes(SAMPLE)
            (output / "extra").write_text("unexpected")
            with self.assertRaises(ParserFailure):
                validate_output(output, hashlib.sha256(SAMPLE).hexdigest())

    def test_parser_rejection_does_not_create_candidate(self):
        document_id = self.submit()

        def reject(source, mime, output, claim, *, image):
            raise ParserFailure("IMAGE_DECODE_FAILED")

        process_one(self.store, "worker", runner=reject)
        self.assertEqual(self.store.status(document_id)["job"]["error_code"], "IMAGE_DECODE_FAILED")
        with self.assertRaises(ReviewBlocked):
            self.store.get(document_id)


if __name__ == "__main__":
    unittest.main()
