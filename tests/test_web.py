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
from docwork.access import LocalAccess, Principal
from docwork.local_model import LocalModelConfig

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
    def test_operations_routes_require_reviewer_and_surface_not_ready_status(self):
        from docwork.operations import Readiness
        self.server.supervisor = None
        self.server.readiness = Readiness(self.server)
        for route in ("/healthz", "/readyz", "/metrics"):
            self.assertEqual(self.call("GET", route)[0], 401)
        self.login()
        self.assertEqual(self.call("GET", "/healthz")[1], {"status": "alive"})
        self.assertTrue(self.call("GET", "/readyz")[1]["ready"])
        self.assertEqual(self.call("GET", "/metrics")[1]["jobs"], {})
        for route in ("/healthz", "/readyz", "/metrics"):
            self.assertEqual(self.call("GET", route, headers={"Authorization":
                f"Bearer {self.server.access.processing_token}"})[0], 403)
        self.server.readiness.cached = {"ready": False, "storage": "INSUFFICIENT_DISK_SPACE"}
        self.assertEqual(self.call("GET", "/readyz")[0], 503)

    def test_batch_lifecycle_api_and_background_wakeup_are_nonblocking(self):
        from unittest.mock import Mock
        self.login()
        self.server.supervisor = Mock()
        status, batch, _ = self.call("POST", "/api/batches", {"count": 2})
        self.assertEqual(status, 201)
        status, uploaded, _ = self.call("POST", "/api/upload", SAMPLE, headers={
            "Content-Type": "image/png", "X-File-Name": "batch.png",
            "X-Batch-Id": batch["batch_id"], "X-Batch-Position": "0"})
        self.assertEqual(status, 201)
        doc = uploaded["document_id"]
        self.assertEqual(uploaded["job"]["status"], "QUEUED")
        self.server.supervisor.notify.assert_called_once()
        status, _, _ = self.call("POST", "/api/upload", b"invalid", headers={
            "Content-Type": "image/png", "X-File-Name": "bad.png",
            "X-Batch-Id": batch["batch_id"], "X-Batch-Position": "1"})
        self.assertEqual(status, 400)
        status, outcome, _ = self.call("GET", f"/api/batches/{batch['batch_id']}")
        self.assertEqual(outcome["items"][1]["status"], "REJECTED")
        status, stopped, _ = self.call("POST", f"/api/documents/{doc}/cancel", {})
        self.assertEqual((status, stopped["status"]), (202, "CANCELLED"))
        status, _, _ = self.call("POST", f"/api/documents/{doc}/reprocess", {"revision": 0, "reparse": True})
        self.assertEqual(status, 202)
        status, _, _ = self.call("POST", f"/api/documents/{doc}/delete", {})
        self.assertEqual(status, 202)
        self.assertEqual(self.call("GET", f"/api/documents/{doc}")[0], 404)
        self.store.run_deletions()
        status, deleted, _ = self.call("GET", f"/api/deletions/{doc}")
        self.assertEqual((status, deleted["status"]), (200, "DELETED"))

    def test_batch_limits_managed_profiles_and_typed_reprocessing(self):
        self.login()
        for count in (0, 21, True, "2"):
            self.assertEqual(self.call("POST", "/api/batches", {"count": count})[0], 400)
        self.assertEqual(self.call("POST", "/api/batches", {"count": 1, "extractor": "span_llm"})[0], 400)
        _, uploaded, _ = self.call("POST", "/api/upload", SAMPLE, headers={"Content-Type": "image/png", "X-File-Name": "one.png"})
        doc = uploaded["document_id"]
        self.call("POST", f"/api/documents/{doc}/cancel", {})
        self.assertEqual(self.call("POST", f"/api/documents/{doc}/reprocess", {"revision": 0, "reparse": "yes"})[0], 400)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.store = IntakeStore(root / "review.sqlite", root / "objects")
        self.server = SimpleNamespace(
            store=self.store, token="test-token", origin="http://127.0.0.1:8765",
            repo_root=Path(__file__).resolve().parents[1],
            model_config=None, model_profile=None,
        )
        self.server.access = LocalAccess("test-token")
        self.server.access.reviewer = Principal("reviewer", "reviewer")
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

    def test_forged_review_actor_never_mutates_a_revision(self):
        page, record = candidate()
        doc = self.store.ingest(hashlib.sha256(SAMPLE).hexdigest(), "clean.png", page, record)
        self.login()
        history = self.store.history(doc)
        for actor in ("extractor", "service:processor", "someone-else", None, {"role": "reviewer"}):
            for action, data in (
                ("edit", {"revision": 1, "path": "fields.total", "value": "270.00"}),
                ("acknowledge", {"revision": 1, "code": "TOTAL_MISMATCH", "path": "fields.total", "reason": "checked"}),
                ("approve", {"revision": 1}),
            ):
                with self.subTest(actor=actor, action=action):
                    self.assertEqual(self.call("POST", f"/api/documents/{doc}/{action}", {**data, "actor": actor})[0], 403)
        self.assertEqual(self.store.history(doc), history)
        self.assertEqual(self.store.get(doc)["revision"], 1)
        self.assertEqual(self.call("POST", f"/api/documents/{doc}/edit", {
            "revision": 1, "path": "fields.total", "value": "270.00"})[0], 200)
        approval = self.call("POST", f"/api/documents/{doc}/approve", {"revision": 2})[1]
        self.assertEqual(approval["actor"], "reviewer")
        self.assertTrue(all(event["actor"] == "reviewer" for event in self.store.history(doc)[1:]))

    def test_processing_capability_is_limited_to_intake_and_execution(self):
        page, record = candidate()
        doc = self.store.ingest(hashlib.sha256(SAMPLE).hexdigest(), "clean.png", page, record)
        self.store.edit(doc, 1, "fields.total", "270.00", "reviewer")
        self.store.approve(doc, 2, "reviewer")
        self.login()
        manifest = self.call("POST", f"/api/documents/{doc}/export", {"format": "json"})[1]
        history = self.store.history(doc)
        headers = {"Authorization": f"Bearer {self.server.access.processing_token}"}
        for route in ("/", "/api/runtime", "/api/documents", f"/api/documents/{doc}",
                      f"/api/documents/{doc}/page", f"/api/documents/{doc}/pages/1",
                      f"/api/documents/{doc}/history", manifest["files"][0]["url"]):
            with self.subTest(route=route):
                self.assertEqual(self.call("GET", route, headers=headers)[0], 403)
        for action in ("edit", "acknowledge", "approve", "export", "delete", "cancel", "reprocess", "retry"):
            self.assertEqual(self.call("POST", f"/api/documents/{doc}/{action}",
                                       {"actor": "reviewer"}, headers=headers)[0], 403)
        self.assertEqual(self.call("POST", "/api/pilot/start", {}, headers=headers)[0], 403)
        self.assertEqual(self.call("POST", "/api/batches", {"count": 1}, headers=headers)[0], 201)
        self.assertEqual(self.call("POST", "/api/upload", SAMPLE, headers={**headers,
            "Content-Type": "image/png", "X-File-Name": "worker.png"})[0], 201)
        with patch("docwork.web.process_one", return_value=None):
            self.assertEqual(self.call("POST", "/api/process-one", {}, headers=headers)[0], 200)
        self.assertEqual(self.call("POST", "/api/batches", {"count": 1}, headers={**headers,
            "Origin": "https://example.invalid"})[0], 403)
        self.assertEqual(self.store.history(doc), history)

    def test_untrusted_credentials_cannot_read_artifacts_or_approve(self):
        page, record = candidate()
        doc = self.store.ingest(hashlib.sha256(SAMPLE).hexdigest(), "clean.png", page, record)
        self.store.edit(doc, 1, "fields.total", "270.00", "reviewer")
        self.store.approve(doc, 2, "reviewer")
        self.login()
        url = self.call("POST", f"/api/documents/{doc}/export", {"format": "json"})[1]["files"][0]["url"]
        self.cookie = ""
        for route in (f"/api/documents/{doc}/pages/1", url):
            self.assertEqual(self.call("GET", route)[0], 401)
        for credential in ("Bearer model-runtime-key", "Bearer extractor", "Bearer é"):
            self.assertEqual(self.call("POST", f"/api/documents/{doc}/approve", {"revision": 2},
                                       headers={"Authorization": credential})[0], 401)
        self.assertEqual(self.call("GET", "/?token=é")[0], 401)
        self.login()
        self.assertEqual(self.call("GET", url, headers={"Authorization": "Bearer model-runtime-key"})[0], 401)

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

    def test_browser_model_profile_rejects_remote_endpoints(self):
        self.login()
        status, error, _ = self.call("POST", "/api/process-one", {
            "extractor": "span_llm", "model_endpoint": "http://example.com:8080",
            "model_id": "remote-model",
        })
        self.assertEqual(status, 400)
        self.assertIn("loopback", error["error"])
        self.assertEqual(self.call("GET", "/api/documents")[1], [])

    def test_managed_model_credentials_stay_server_side(self):
        config = LocalModelConfig("http://127.0.0.1:8080", "pinned-model", api_key="private-key")
        self.server.model_config = config
        self.server.model_profile = "pinned-profile"
        self.assertEqual(self.call("GET", "/api/runtime")[0], 401)
        self.login()
        status, runtime, _ = self.call("GET", "/api/runtime")
        self.assertEqual(status, 200)
        self.assertEqual(runtime, {"principal": {"actor": "reviewer", "role": "reviewer"}, "managed_model": True, "model_id": "pinned-model", "profile": "pinned-profile", "demo_replay": None, "review_pilot": False, "background_processing": False, "worker": None})
        self.assertNotIn("private-key", json.dumps(runtime))
        with patch("docwork.web.process_one", return_value=None) as process:
            self.assertEqual(self.call("POST", "/api/process-one", {"extractor": "span_llm"})[0], 200)
        self.assertIs(process.call_args.kwargs["model_config"], config)

    def test_managed_model_rejects_browser_endpoint_overrides(self):
        self.server.model_config = LocalModelConfig("http://127.0.0.1:8080", "pinned-model", api_key="private-key")
        self.login()
        with patch("docwork.web.process_one") as process:
            status, error, _ = self.call("POST", "/api/process-one", {
                "extractor": "span_llm", "model_endpoint": "http://127.0.0.1:9090", "model_id": "other-model"})
        self.assertEqual(status, 400)
        self.assertIn("omit endpoint overrides", error["error"])
        process.assert_not_called()

    def test_replay_runtime_and_server_enforced_processing_boundary(self):
        self.server.demo_replay = {"version": "portfolio-demo-replay-v1", "cases": []}
        self.assertEqual(self.call("GET", "/api/runtime")[0], 401)
        self.login()
        self.assertEqual(self.call("GET", "/api/runtime")[1]["demo_replay"], self.server.demo_replay)
        with patch("docwork.web.tesseract_page") as ocr, patch("docwork.web.process_one") as process:
            for path, data in (("/api/demo/seed", {"fixture": "clean"}),
                               ("/api/process-one", {"extractor": "ocr_rules"}),
                               ("/api/upload", SAMPLE)):
                self.assertEqual(self.call("POST", path, data)[0], 422)
            ocr.assert_not_called()
            process.assert_not_called()
        self.assertEqual(self.store.list_documents(), [])

    def test_replay_retains_review_approval_and_export_provenance(self):
        from docwork.demo_replay import prepare_replay
        root = self.server.repo_root
        output = self.store.database.parent / "replay"
        self.server.demo_replay = prepare_replay(root, output)
        self.store = self.server.store = IntakeStore(output / "review.sqlite", output / "objects")
        self.login()
        doc_id = self.server.demo_replay["cases"][0]["document_id"]
        route = f"/api/documents/{doc_id}"
        self.assertEqual(self.call("GET", route + "/pages/1")[0], 200)
        self.assertEqual(self.call("POST", route + "/export", {"format": "json"})[0], 409)
        original = self.call("GET", route)[1]["record"]["fields"]["invoice_number"]["value"]
        self.assertEqual(self.call("POST", route + "/edit", {
            "revision": 1, "path": "fields.invoice_number", "value": original,
            "actor": "reviewer"})[1]["revision"], 2)
        self.assertEqual(self.call("POST", route + "/approve", {"revision": 1, "actor": "reviewer"})[0], 409)
        self.assertEqual(self.call("POST", route + "/approve", {"revision": 2, "actor": "reviewer"})[0], 200)
        manifest = self.call("POST", route + "/export", {"format": "json"})[1]
        exported = self.call("GET", manifest["files"][0]["url"])[1]
        self.assertEqual(exported["extraction"]["profile"], "replay_ocr_rules")
        self.assertEqual(exported["revision"], 2)


if __name__ == "__main__":
    unittest.main()
