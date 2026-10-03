"""Automated Chrome UI check on a disposable fixture; never human timing evidence."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import urlopen

from docwork.intake import IntakeStore
from docwork.pilot_bundle import prepare_pilot
from docwork.review_pilot import ReviewPilot
from docwork.web import ReviewServer


class DevTools:
    """Minimal local CDP transport; current methods come from Chrome itself."""
    def __init__(self, port: int, target: str):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.stream = self.socket.makefile("rb")
        self.counter = 0
        nonce = base64.b64encode(os.urandom(16)).decode()
        self.socket.sendall((f"GET /devtools/page/{target} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                             f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {nonce}\r\n"
                             "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        first = self.stream.readline()
        if b"101" not in first:
            raise RuntimeError("Local DevTools did not upgrade its connection")
        headers = {}
        while line := self.stream.readline().strip():
            key, value = line.decode().split(":", 1)
            headers[key.lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((nonce + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expected:
            raise RuntimeError("Unexpected local DevTools handshake")

    def send(self, payload: bytes, opcode=1):
        mask = os.urandom(4)
        length = len(payload)
        prefix = bytes((0x80 | opcode, 0x80 | length)) if length < 126 else bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        self.socket.sendall(prefix + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))

    def read(self):
        header = self.stream.read(2)
        if len(header) != 2:
            raise RuntimeError("Local DevTools connection ended")
        opcode = header[0] & 15
        length = header[1] & 127
        if length == 126:
            length = struct.unpack("!H", self.stream.read(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.stream.read(8))[0]
        if length > 20 * 1024 * 1024 or not header[0] & 0x80 or header[1] & 0x80:
            raise RuntimeError("Unsupported local DevTools frame")
        payload = self.stream.read(length)
        if opcode == 9:
            self.send(payload, opcode=10)
            return self.read()
        if opcode != 1:
            raise RuntimeError("Unexpected local DevTools frame type")
        return json.loads(payload)

    def call(self, method, params=None):
        self.counter += 1
        self.send(json.dumps({"id": self.counter, "method": method, "params": params or {}}).encode())
        while True:
            message = self.read()
            if message.get("id") == self.counter:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message["result"]

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in result:
            raise RuntimeError(result["exceptionDetails"]["text"])
        return result["result"].get("value")

    def wait(self, expression):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate(expression):
                return
            time.sleep(.1)
        raise AssertionError(f"Browser condition did not become true: {expression}")

    def close(self):
        self.stream.close()
        self.socket.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chrome", type=Path, default=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists() or not args.chrome.is_file():
        parser.error("Use a new output directory and an installed Chrome executable")
    root = Path(__file__).resolve().parents[1]
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="docwork-browser-") as temporary:
        directory = Path(temporary)
        protocol = prepare_pilot(root, root / "datasets/invoices-v1/manifest.json",
                                 root / "evals/invoice-freeze-2026-10-03/development", directory / "qa",
                                 document_ids=("inv-f02-02",))
        store = IntakeStore(directory / "qa/review.sqlite", directory / "qa/objects")
        pilot = ReviewPilot(directory / "qa", store)
        process = client = None
        with ReviewServer(("127.0.0.1", 0), store, review_pilot=pilot) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                profile = directory / "chrome-profile"
                process = subprocess.Popen([str(args.chrome), "--headless", "--disable-gpu", "--disable-background-networking",
                                            "--disable-sync", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0",
                                            f"--user-data-dir={profile}", "--window-size=1440,1100", "about:blank"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                deadline = time.monotonic() + 15
                while not (profile / "DevToolsActivePort").exists() and time.monotonic() < deadline:
                    time.sleep(.1)
                port = int((profile / "DevToolsActivePort").read_text().splitlines()[0])
                with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as response:
                    target = next(item["id"] for item in json.load(response) if item["type"] == "page")
                client = DevTools(port, target)
                client.call("Page.enable")
                client.call("Page.navigate", {"url": f"{server.origin}/?token={server.token}"})
                client.wait("typeof pilotData !== 'undefined' && pilotData !== null && !$('pilot-panel').hidden")
                client.evaluate("$('actor').value = 'automated-browser-check'")
                assert client.evaluate("$('queue').closest('.panel').hidden && $('seed-clean').closest('.actions').hidden")
                assert client.evaluate("fetch('/api/documents/' + pilotData.next_document.document_id).then(response => response.status)") == 422
                client.evaluate("$('pilot-start').click()")
                client.wait("state.detail !== null && !$('page-canvas').hidden")
                assert client.evaluate("getComputedStyle($('empty')).display === 'none'")
                assert client.evaluate("$('doc-kind').textContent.includes('RECORDED OCR RULES')")
                client.evaluate("document.querySelector('[data-path=\"fields.invoice_number\"]').click()")
                client.wait("$('highlights').children.length > 0 && !$('page-canvas').hidden")
                screenshot = client.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
                (output / "review.png").write_bytes(base64.b64decode(screenshot["data"]))
                client.evaluate("$('pilot-pause').click()")
                client.wait("openPilotTrial()?.status === 'PAUSED' && $('review-grid').hidden")
                assert client.evaluate("$('actor').disabled && getComputedStyle($('review-grid')).display === 'none'")
                client.evaluate("$('pilot-resume').click()")
                client.wait("openPilotTrial()?.status === 'RUNNING' && !$('review-grid').hidden")
                client.evaluate("$('approve-button').click()")
                client.wait("state.detail.approval !== null")
                client.evaluate("$('export-json').click()")
                client.wait("$('downloads').querySelector('a') !== null")
                client.evaluate("$('pilot-finish').click()")
                client.wait("pilotData.trials[0].status === 'COMPLETE'")
                browser = client.call("Browser.getVersion")
                report = {"report_version": "review-pilot-browser-check-v1", "status": "passed",
                          "human_timing_measurement": False, "browser": browser["product"],
                          "checks": ["declared pilot mode", "no source before start", "source canvas and field highlight",
                                     "hidden empty workspace", "pause hides review", "reviewer label locked",
                                     "resume", "approval", "JSON export", "completion"],
                          "source_sha256": protocol["source_sha256"],
                          "verification_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                          "screenshot_sha256": hashlib.sha256((output / "review.png").read_bytes()).hexdigest(),
                          "scope": "Automated Chrome UI check on one disposable development fixture. "
                                   "No human review time, productivity comparison, or author-pilot outcome. "
                                   "Disposable trial/database/browser profile removed after verification."}
                (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps({"status": "passed", "human_timing_measurement": False, "output": str(output)}))
            finally:
                if client:
                    client.close()
                if process:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                server.shutdown()
                thread.join(timeout=5)
                pilot.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
