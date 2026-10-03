from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from docwork.baseline import extract_invoice
from docwork.contracts import Box, DocumentPage, TextSpan, HEADER_FIELDS
from docwork.intake import IntakeStore, JobClaim
from docwork.local_model import LocalModelConfig, ModelUnavailable
from docwork.ocr import png_dimensions
from docwork.parser_protocol import PARSER_VERSION
from docwork.review import ReviewBlocked, ReviewConflict
from docwork.worker import ParserFailure, _docker_run, _keep_lease, process_one, validate_output

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


def model_output() -> str:
    values = {
        "supplier_name": ("Aster Studio LLC", 1), "invoice_number": ("AST-1001", 3),
        "issue_date": ("2026-09-12", 4), "due_date": ("2026-10-12", 5),
        "currency": ("USD", 6), "subtotal": ("250.00", 8), "tax": ("20.00", 9),
        "discount": ("0.00", 10), "shipping": ("0.00", 11), "total": ("270.00", 12),
    }
    fields = {name: {"value": values[name][0], "evidence_ids": [f"p1-l{values[name][1]:04d}"]}
              for name in HEADER_FIELDS}
    row = {name: {"value": value, "evidence_ids": ["p1-l0007"]} for name, value in (
        ("description", "Research workshop"), ("quantity", "2"),
        ("unit_price", "125.00"), ("line_total", "250.00"),
    )}
    row["tax"] = {"value": None, "evidence_ids": []}
    return json.dumps({"fields": fields, "line_items": [row]})


class WorkerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.store = IntakeStore(root / "review.sqlite", root / "objects")

    def submit(self):
        return self.store.submit(io.BytesIO(SAMPLE), "clean.png", "image/png")

    def test_missing_parser_image_does_not_attempt_network_pull(self):
        claim = JobClaim("job-1", "document-1", 1, "worker", 0)
        result = subprocess.CompletedProcess([], 125, b"", b"No such image: absent")
        with patch("docwork.worker.shutil.which", return_value="docker"), \
                patch("docwork.worker.subprocess.run", return_value=result) as run:
            with self.assertRaises(ParserFailure) as failure:
                _docker_run(Path("source"), "image/png", Path("output"), claim, image="absent")
        self.assertEqual(failure.exception.code, "PARSER_IMAGE_MISSING")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--pull") + 1], "never")
        self.assertEqual(run.call_count, 1)

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

    def test_heartbeat_keeps_long_running_job_owned(self):
        self.submit()
        claim = self.store.claim("worker-one", lease_seconds=1)
        assert claim is not None
        with _keep_lease(self.store, claim, lease_seconds=1, interval_seconds=.1):
            time.sleep(1.2)
            self.assertIsNone(self.store.claim("worker-two"))
        self.assertEqual(self.store.status(claim.document_id)["job"]["fence"], claim.fence)

    def test_heartbeat_failure_is_reported_to_worker(self):
        self.submit()
        claim = self.store.claim("worker-one", lease_seconds=1)
        assert claim is not None
        with patch.object(self.store, "renew", side_effect=ReviewConflict("fenced out")):
            with self.assertRaisesRegex(ReviewConflict, "renewal failed"):
                with _keep_lease(self.store, claim, lease_seconds=1, interval_seconds=.01):
                    time.sleep(.05)

    def test_stale_worker_cannot_fail_reclaimed_job(self):
        document_id = self.submit()
        replacement = None

        def stolen_runner(source, mime, output, claim, *, image):
            nonlocal replacement
            with patch("docwork.intake.time.time", return_value=claim.lease_until + 1):
                replacement = self.store.claim("worker-two", lease_seconds=120)
            parser_result(output, source)

        with self.assertRaises(ReviewConflict):
            process_one(self.store, "worker-one", runner=stolen_runner)
        assert replacement is not None
        status = self.store.status(document_id)
        self.assertEqual(status["job"]["status"], "PROCESSING")
        self.assertEqual(status["job"]["fence"], replacement.fence)
        self.assertIsNone(status["job"]["error_code"])
        page = DocumentPage(1, 1000, 1000, ())
        # The replacement can still publish its own candidate.
        self.store.complete(replacement, page, extract_invoice(page))
        self.assertEqual(self.store.status(document_id)["status"], "REVIEW_READY")

    def test_corrupted_original_fails_with_source_integrity_code(self):
        document_id = self.submit()
        self.store.object_path(document_id).write_bytes(b"changed")
        self.assertEqual(process_one(self.store, "worker-one"), document_id)
        self.assertEqual(self.store.status(document_id)["job"]["error_code"], "SOURCE_INTEGRITY_FAILED")

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

    def test_local_model_profile_is_reviewable_and_has_provenance(self):
        document_id = self.submit()
        config = LocalModelConfig("http://127.0.0.1:8080", "test-model")
        process_one(self.store, "worker", extractor="span_llm", model_config=config,
                    runner=lambda source, mime, output, claim, image: parser_result(output, source),
                    model_request=lambda config, payload: model_output())
        detail = self.store.get(document_id)
        self.assertEqual(detail["extraction"]["profile"], "span_llm")
        self.assertEqual(detail["extraction"]["model_id"], "test-model")
        self.assertEqual(detail["record"]["fields"]["total"]["value"], "270.00")
        self.assertEqual(detail["issues"], [])
        self.store.approve(document_id, 1, "reviewer")
        manifest = self.store.export(document_id, "json")
        self.assertEqual(json.loads(Path(manifest["files"][0]["path"]).read_text())["extraction"]["profile"],
                         "span_llm")

    def test_local_model_failure_is_explicit_and_retryable(self):
        document_id = self.submit()
        config = LocalModelConfig("http://127.0.0.1:8080", "test-model")
        runner = lambda source, mime, output, claim, image: parser_result(output, source)
        def unavailable(config, payload):
            raise ModelUnavailable("offline")
        process_one(self.store, "worker", extractor="span_llm", model_config=config,
                    runner=runner, model_request=unavailable)
        self.assertEqual(self.store.status(document_id)["job"]["error_code"], "MODEL_UNAVAILABLE")
        with self.assertRaises(ReviewBlocked):
            self.store.get(document_id)
        self.store.retry(document_id)
        process_one(self.store, "worker", extractor="span_llm", model_config=config,
                    runner=runner, model_request=lambda config, payload: model_output())
        self.assertEqual(self.store.status(document_id)["status"], "REVIEW_READY")

    def test_model_page_conflict_survives_review_edit(self):
        document_id = self.submit()
        config = LocalModelConfig("http://127.0.0.1:8080", "test-model")
        def model_request(config, payload):
            page = json.loads(payload["messages"][1]["content"])["page"]
            if page == 1:
                return model_output()
            fields = {name: {"value": None, "evidence_ids": []} for name in HEADER_FIELDS}
            fields["total"] = {"value": "275.00", "evidence_ids": ["p2-l0002"]}
            return json.dumps({"fields": fields, "line_items": []})
        process_one(self.store, "worker", extractor="span_llm", model_config=config,
                    runner=lambda source, mime, output, claim, image: parser_result(
                        output, source, lines_by_page=(LINES, ("INVOICE", "Amount due 275.00"))),
                    model_request=model_request)
        self.assertIn("MODEL_HEADER_CONFLICT", [issue["code"] for issue in self.store.get(document_id)["issues"]])
        self.store.edit(document_id, 1, "fields.total", "275.00", "reviewer", ("p2-l0002",))
        self.assertIn("MODEL_HEADER_CONFLICT", [issue["code"] for issue in self.store.get(document_id)["issues"]])

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
