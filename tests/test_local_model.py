from __future__ import annotations

import json
import unittest
import urllib.error
from unittest.mock import patch

from docwork.contracts import Box, DocumentPage, TextSpan, HEADER_FIELDS
from docwork.local_model import (
    LocalModelConfig, ModelContextOverflow, ModelOutputInvalid, extract_page,
    extract_pages, ModelRequestRejected, _request,
)
from docwork.validation import validate_invoice


def page(number: int, line: str = "Total: 270.00") -> DocumentPage:
    return DocumentPage(number, 1000, 1000, (
        TextSpan(f"p{number}-l0001", number, line, Box(.1, .1, .9, .2), "tesseract-eng"),
    ))


def output(number: int, *, total: str = "270.00", evidence: str | None = None) -> str:
    fields = {name: {"value": None, "evidence_ids": []} for name in HEADER_FIELDS}
    fields["total"] = {"value": total, "evidence_ids": [evidence or f"p{number}-l0001"]}
    return json.dumps({"fields": fields, "line_items": []})


class LocalModelTests(unittest.TestCase):
    def setUp(self):
        self.config = LocalModelConfig("http://127.0.0.1:8080", "local-test-model")

    def test_model_endpoint_is_loopback_only(self):
        for endpoint in ("https://127.0.0.1:8080", "http://example.com:8080",
                         "http://127.0.0.1:8080/other", "http://127.0.0.1:8080/?x=1",
                         "http://user:pass@127.0.0.1:8080", "http://127.0.0.1"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                LocalModelConfig(endpoint, "model")

    def test_strict_output_and_one_schema_repair(self):
        requests = []
        responses = iter(["not JSON", output(1)])
        def fake(config, payload):
            requests.append(payload)
            return next(responses)
        record = extract_page(page(1), self.config, fake)
        self.assertEqual(record.fields["total"].value, "270.00")
        self.assertEqual(record.fields["total"].raw, "Total: 270.00")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["response_format"]["type"], "json_object")
        self.assertIn("p1-l0001", requests[0]["messages"][1]["content"])
        self.assertEqual(len(requests[1]["messages"]), 3)

    def test_duplicate_keys_and_extra_fields_are_rejected(self):
        with self.assertRaises(ModelOutputInvalid):
            extract_page(page(1), self.config, lambda config, payload: '{"fields":{},"fields":{},"line_items":[]}')
        invalid = json.loads(output(1))
        invalid["fields"]["total"]["confidence"] = .99
        with self.assertRaises(ModelOutputInvalid):
            extract_page(page(1), self.config, lambda config, payload: json.dumps(invalid))

    def test_hallucinated_reference_never_becomes_trusted_geometry(self):
        record = extract_page(page(1), self.config,
                              lambda config, payload: output(1, evidence="invented"))
        self.assertIsNone(record.fields["total"].raw)
        issues = validate_invoice(record, page(1))
        self.assertTrue(any(issue.code == "EVIDENCE_UNKNOWN" for issue in issues))

    def test_page_budget_is_explicit(self):
        huge = page(1, "x" * 13_000)
        with self.assertRaises(ModelContextOverflow):
            extract_page(huge, self.config, lambda config, payload: self.fail("Model should not be called"))

    def test_cross_page_conflict_is_preserved(self):
        pages = (page(1), page(2, "Total: 275.00"))
        result = extract_pages(pages, self.config,
                               lambda config, payload: output(json.loads(payload["messages"][1]["content"])["page"],
                                                             total="270.00" if "p1-l0001" in payload["messages"][1]["content"] else "275.00"))
        self.assertEqual(result.record.fields["total"].value, "270.00")
        self.assertEqual([(issue.code, issue.path) for issue in result.issues],
                         [("MODEL_HEADER_CONFLICT", "fields.total")])

    def test_transport_checks_completion_envelope_and_http_rejection(self):
        class Response:
            status = 200
            def __init__(self, content):
                self.content = content
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return self.content
        class Opener:
            def __init__(self, content):
                self.content = content
            def open(self, request, timeout):
                self_test.assertEqual(request.full_url, "http://127.0.0.1:8080/v1/chat/completions")
                self_test.assertEqual(timeout, 90)
                return Response(self.content)
        self_test = self
        envelope = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": output(1)}}]}).encode()
        with patch("docwork.local_model.urllib.request.build_opener", return_value=Opener(envelope)):
            self.assertEqual(_request(self.config, {"model": "local-test-model"}), output(1))
        truncated = json.dumps({"choices": [{"finish_reason": "length", "message": {"content": output(1)}}]}).encode()
        with patch("docwork.local_model.urllib.request.build_opener", return_value=Opener(truncated)):
            with self.assertRaises(ModelOutputInvalid):
                _request(self.config, {})
        class Rejected:
            def open(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 400, "unsupported schema", {}, None)
        with patch("docwork.local_model.urllib.request.build_opener", return_value=Rejected()):
            with self.assertRaises(ModelRequestRejected):
                _request(self.config, {})


if __name__ == "__main__":
    unittest.main()
