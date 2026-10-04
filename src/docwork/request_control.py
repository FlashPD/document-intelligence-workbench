"""Cancel an owned loopback request without terminating the model server."""

from __future__ import annotations

import http.client
import json
import socket
import threading
from urllib.parse import urlsplit

from .intake import JobStopped
from .local_model import (MAX_RESPONSE_BYTES, ModelUnavailable, ModelRequestRejected,
                          ModelOutputInvalid, _json_strict)


def cancellable_request(config, payload: dict, stop: threading.Event) -> str:
    if stop.is_set():
        raise JobStopped("Processing stopped")
    endpoint = urlsplit(config.endpoint)
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=config.timeout_seconds)
    finished = threading.Event()
    watcher = None
    try:
        connection.connect()
        owned_socket = connection.sock

        def interrupt():
            while not finished.wait(.05):
                if stop.is_set():
                    try:
                        owned_socket.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return

        watcher = threading.Thread(target=interrupt, name="docwork-request-stop", daemon=True)
        watcher.start()
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        connection.request("POST", "/v1/chat/completions", json.dumps(payload).encode(), headers)
        response = connection.getresponse()
        if 400 <= response.status < 500:
            raise ModelRequestRejected(f"Local model request rejected (HTTP {response.status})")
        if response.status != 200:
            raise ModelUnavailable("Local model endpoint unavailable")
        encoded = response.read(MAX_RESPONSE_BYTES + 1)
        if stop.is_set():
            raise JobStopped("Processing stopped")
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise ModelOutputInvalid("Model response exceeds 256 KiB")
        try:
            envelope = _json_strict(encoded)
            choice = envelope["choices"][0]
            content = choice["message"]["content"]
            if choice["finish_reason"] != "stop" or not isinstance(content, str):
                raise ValueError("Incomplete completion")
            return content
        except (KeyError, IndexError, TypeError, ValueError, UnicodeError) as exc:
            raise ModelOutputInvalid("Invalid model completion envelope") from exc
    except (OSError, http.client.HTTPException) as exc:
        if stop.is_set():
            raise JobStopped("Processing stopped") from exc
        raise ModelUnavailable("Local model endpoint unavailable") from exc
    finally:
        finished.set()
        connection.close()
        if watcher:
            watcher.join()
