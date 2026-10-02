from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docwork.baseline import extract_invoice, extract_invoice_pages
from docwork.contracts import Box, DocumentPage, TextSpan
from docwork.intake import IntakeStore
from docwork.web import ReviewHandler

SAMPLE = (Path(__file__).resolve().parents[1] / "samples" / "clean.png").read_bytes()


def candidate():
    lines = (
        "Aster Studio LLC", "INVOICE", "Invoice Number: AST-1001", "Date: 2026-09-12",
        "Due Date: 2026-10-12", "Currency: USD", "Research workshop 2 125.00 250.00",
        "Subtotal: 250.00", "Tax: 20.00", "Discount: 0.00", "Shipping: 0.00", "Total: 275.00",
    )
    page = DocumentPage(1, 1000, 1000, tuple(
        TextSpan(f"s{index}", 1, line, Box(.1, .02 + index * .07, .9, .05 + index * .07), "fixture")
        for index, line in enumerate(lines)
    ))
    return page, extract_invoice(page)


class WebTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.store = IntakeStore(root / "review.sqlite", root / "objects")
        self.server = SimpleNamespace(
            store=self.store, token="test-token", origin="http://127.0.0.1:8765",
            repo_root=Path(__file__).resolve().parents[1],
        )
        self.cookie = ""

    def call(self, method, path, body=None, *, headers=None):
        outgoing = {"Cookie": self.cookie, **(headers or {})}
        if method == "POST":
            outgoing.setdefault("Origin", self.server.origin)
            outgoing.setdefault("Content-Type", "application/json")
            if isinstance(body, dict):
                body = json.dumps(body).encode()
            outgoing["Content-Length"] = str(len(body))
        handler = ReviewHandler.__new__(ReviewHandler)
        handler.server = self.server
        handler.command = method
        handler.path = path
        handler.requestline = f"{method} {path} HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.rfile = io.BytesIO(body or b"")
        handler.wfile = io.BytesIO()
        handler.headers = Message()
        for key, value in outgoing.items():
            handler.headers[key] = value
        if method == "GET":
            handler.do_GET()
        else:
            handler.do_POST()
        raw = handler.wfile.getvalue()
        head, _, content = raw.partition(b"\r\n\r\n")
        lines = head.decode("utf-8").split("\r\n")
        status = int(lines[0].split()[1])
        returned = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
        if returned.get("Content-Type", "").startswith("application/json"):
            content = json.loads(content)
        return status, content, returned

    def login(self):
        status, _, headers = self.call("GET", "/?token=test-token")
        self.assertEqual(status, 303)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def test_session_origin_and_upload_boundaries(self):
        self.assertEqual(self.call("GET", "/api/documents")[0], 401)
        self.assertEqual(self.call("GET", "/?token=wrong")[0], 401)
        self.login()
        status, html, headers = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Review Workbench", html)
        self.assertIn("nosniff", headers["X-Content-Type-Options"])
        self.assertEqual(self.call("POST", "/api/demo/seed", {"fixture": "clean"},
                                   headers={"Origin": "https://example.invalid"})[0], 403)
        self.assertEqual(self.call("POST", "/api/upload", SAMPLE,
                                   headers={"Content-Type": "application/pdf", "X-File-Name": "fake.pdf"})[0], 400)
        status, submitted, _ = self.call("POST", "/api/upload", SAMPLE,
                                         headers={"Content-Type": "image/png", "X-File-Name": "clean.png"})
        self.assertEqual(status, 201)
        self.assertEqual(submitted["status"], "RECEIVED")
        self.assertEqual(self.call("GET", "/api/documents")[1][0]["id"], submitted["document_id"])
        claim = self.store.claim("test-worker")
        self.store.fail(claim, "PARSER_UNAVAILABLE")
        status, retried, _ = self.call("POST", f"/api/documents/{submitted['document_id']}/retry", {})
        self.assertEqual(status, 200)
        self.assertEqual(retried["job"]["status"], "QUEUED")

    def test_review_decisions_approval_and_download(self):
        page, record = candidate()
        doc_id = self.store.ingest(hashlib.sha256(SAMPLE).hexdigest(), "clean.png", page, record)
        self.login()
        self.assertEqual(self.call("GET", f"/api/documents/{doc_id}/page")[1], SAMPLE)
        self.assertEqual(self.call("POST", f"/api/documents/{doc_id}/approve",
                                   {"revision": 1, "actor": "reviewer"})[0], 422)
        status, detail, _ = self.call("GET", f"/api/documents/{doc_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["issues"][0]["code"], "TOTAL_MISMATCH")
        status, _, _ = self.call("POST", f"/api/documents/{doc_id}/acknowledge", {
            "revision": 1, "code": "TOTAL_MISMATCH", "path": "fields.total",
            "reason": "Verified printed total", "actor": "reviewer",
        })
        self.assertEqual(status, 200)
        self.assertEqual(self.call("POST", f"/api/documents/{doc_id}/approve",
                                   {"revision": 1, "actor": "reviewer"})[0], 200)
        status, manifest, _ = self.call("POST", f"/api/documents/{doc_id}/export", {"format": "json"})
        self.assertEqual(status, 200)
        status, payload, headers = self.call("GET", manifest["files"][0]["url"])
        self.assertEqual(status, 200)
        self.assertEqual(payload["record"]["fields"]["total"]["value"], "275.00")
        self.assertIn("attachment", headers["Content-Disposition"])
        status, revised, _ = self.call("POST", f"/api/documents/{doc_id}/edit", {
            "revision": 1, "path": "fields.total", "value": "270.00", "actor": "reviewer",
        })
        self.assertEqual(status, 200)
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(self.call("POST", f"/api/documents/{doc_id}/edit", {
            "revision": 1, "path": "fields.total", "value": "999.00", "actor": "reviewer",
        })[0], 409)
        self.assertEqual(self.call("POST", f"/api/documents/{doc_id}/export", {"format": "json"})[0], 409)
        self.assertEqual(self.call("GET", f"/api/documents/{doc_id}/history")[0], 200)

    def test_trusted_fixture_route_creates_review_record(self):
        self.login()
        page, _ = candidate()
        with patch("docwork.web.tesseract_page", return_value=page) as ocr:
            status, record, _ = self.call("POST", "/api/demo/seed", {"fixture": "clean"})
        self.assertEqual(status, 201)
        self.assertEqual(record["source_sha256"], hashlib.sha256(SAMPLE).hexdigest())
        self.assertEqual(record["revision"], 1)
        self.assertEqual(ocr.call_count, 1)
        self.assertEqual(self.call("POST", "/api/demo/seed", {"fixture": "../../outside"})[0], 400)

    def test_numbered_page_route(self):
        doc_id = self.store.submit(io.BytesIO(SAMPLE), "clean.png", "image/png")
        first, _ = candidate()
        second = DocumentPage(2, first.width_px, first.height_px, (
            TextSpan("p2-l0001", 2, "Second page", Box(.1, .1, .9, .2), "fixture"),
        ))
        claim = self.store.claim("test-worker")
        self.store.complete(claim, (first, second), extract_invoice_pages((first, second)), (SAMPLE, SAMPLE))
        self.login()
        self.assertEqual(self.call("GET", f"/api/documents/{doc_id}/pages/2")[1], SAMPLE)
        self.assertEqual(self.call("GET", f"/api/documents/{doc_id}/pages/3")[0], 404)
        self.assertEqual(self.call("GET", f"/api/documents/{doc_id}")[1]["pages"][1]["number"], 2)


if __name__ == "__main__":
    unittest.main()
