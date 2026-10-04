from __future__ import annotations

import json
import threading
import unittest
from unittest.mock import Mock, patch

from docwork.intake import JobStopped
from docwork.local_model import LocalModelConfig, ModelOutputInvalid, ModelRequestRejected
from docwork.request_control import cancellable_request


class RequestControlTests(unittest.TestCase):
    def setUp(self):
        self.config = LocalModelConfig("http://127.0.0.1:8080", "model", api_key="ephemeral")
        self.stop = threading.Event()
        self.connection = Mock()

    def request(self):
        with patch("docwork.request_control.http.client.HTTPConnection", return_value=self.connection):
            return cancellable_request(self.config, {"model": "model"}, self.stop)

    def test_cancel_interrupts_only_the_owned_connection_and_closes_it(self):
        interrupted = threading.Event()
        self.connection.sock.shutdown.side_effect = lambda mode: interrupted.set()
        def blocked():
            self.stop.set()
            self.assertTrue(interrupted.wait(2))
            raise OSError("owned socket closed")
        self.connection.getresponse.side_effect = blocked
        with self.assertRaises(JobStopped):
            self.request()
        self.connection.sock.shutdown.assert_called_once()
        self.connection.close.assert_called_once()

    def test_complete_response_keeps_bounded_envelope_and_ephemeral_auth(self):
        response = self.connection.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}).encode()
        self.assertEqual(self.request(), "{}")
        self.assertEqual(self.connection.request.call_args.args[3]["Authorization"], "Bearer ephemeral")
        response.read.assert_called_once_with(256 * 1024 + 1)
        self.connection.sock.shutdown.assert_not_called()

    def test_rejection_and_partial_completion_do_not_become_success(self):
        response = self.connection.getresponse.return_value
        response.status = 401
        with self.assertRaises(ModelRequestRejected):
            self.request()
        response.status = 200
        response.read.return_value = b'{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}'
        with self.assertRaises(ModelOutputInvalid):
            self.request()
