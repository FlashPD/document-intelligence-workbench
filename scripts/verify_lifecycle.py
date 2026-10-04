"""Verify live serial rules processing and browser lifecycle on disposable fixtures.

Requires Docker and Chrome. No inference or human productivity measurement.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import patch

from docwork.intake import IntakeStore, JobStopped
from docwork.web import ReviewServer
from docwork.worker import _docker_run, parser_command
from verify_pilot_browser import DevTools


def wait_for(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError("Lifecycle state did not settle")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chrome", type=Path, default=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists() or not args.chrome.is_file():
        parser.error("Use a new output directory and installed Chrome")
    root = Path(__file__).resolve().parents[1]
    output.mkdir(parents=True)
    checks = []
    with tempfile.TemporaryDirectory(prefix="docwork-lifecycle-") as temporary:
        directory = Path(temporary)
        store = IntakeStore(directory / "review.sqlite", directory / "intake")
        server = ReviewServer(("127.0.0.1", 0), store, background_processing=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        process = client = None

        def api(method, route, body=None, *, raw=False):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            headers = {"Cookie": f"docwork_session={server.token}", "Origin": server.origin}
            if body is not None:
                headers["Content-Type"] = "application/json"
                body = json.dumps(body).encode()
            try:
                connection.request(method, route, body, headers)
                response = connection.getresponse()
                data = response.read()
                assert response.status < 300, (response.status, data)
                return data if raw else json.loads(data)
            finally:
                connection.close()

        try:
            profile = directory / "chrome"
            process = subprocess.Popen([str(args.chrome), "--headless", "--disable-gpu", "--disable-background-networking",
                "--disable-sync", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0",
                f"--user-data-dir={profile}", "--window-size=1440,1100", "about:blank"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for(lambda: (profile / "DevToolsActivePort").exists(), 15)
            port = int((profile / "DevToolsActivePort").read_text().splitlines()[0])
            with urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as response:
                target = next(item["id"] for item in json.load(response) if item["type"] == "page")
            client = DevTools(port, target)
            client.call("Page.enable")
            client.call("Page.navigate", {"url": f"{server.origin}/?token={server.token}"})
            client.wait("typeof state !== 'undefined' && state.background")
            assert client.evaluate("$('process-button').hidden")
            checks.append("live browser owns an automatic worker")
            invalid = directory / "invalid.png"
            invalid.write_bytes(b"not a PNG")
            document = client.call("DOM.getDocument")["root"]["nodeId"]
            node = client.call("DOM.querySelector", {"nodeId": document, "selector": "#upload"})["nodeId"]
            client.call("DOM.setFileInputFiles", {"nodeId": node, "files": [str(root / "samples/clean.png"), str(invalid)]})
            client.evaluate("$('upload-button').click()")
            wait_for(lambda: client.evaluate("state.detail !== null && !$('page-canvas').hidden"))
            doc = client.evaluate("state.selectedId")
            batch = api("GET", "/api/batches/" + client.evaluate("state.batchId"))
            assert [item["status"] for item in batch["items"]] == ["REVIEW_READY", "REJECTED"]
            checks.append("native multiple upload records success and rejection separately")
            client.wait("!$('operations-status').hidden && $('operations-status').textContent.includes('Processing ready')")
            metrics = api("GET", "/metrics")
            assert api("GET", "/readyz")["ready"]
            assert metrics["jobs"] == {"COMPLETE": 1} and metrics["timings"]["parsing"]["count"] == 1
            checks.append("native live browser exposes readiness and content-free queue metrics")
            (output / "review-ready.png").write_bytes(base64.b64decode(client.call("Page.captureScreenshot")["data"]))
            detail = api("GET", f"/api/documents/{doc}")
            assert detail["revision"] == 1 and detail["extraction"]["profile"] == "ocr_rules"
            api("POST", f"/api/documents/{doc}/approve", {"revision": 1})
            manifest = api("POST", f"/api/documents/{doc}/export", {"format": "json"})
            exported = api("GET", manifest["files"][0]["url"], raw=True)
            # The real browser queues a fresh parse and polls until it can review.
            client.evaluate("$('reparse').checked=true; $('reprocess-button').click()")
            wait_for(lambda: client.evaluate("state.detail !== null && state.detail.revision === 2"))
            current = api("GET", f"/api/documents/{doc}")
            assert current["approval"] is None
            assert api("GET", f"/api/documents/{doc}?revision=1")["pages"] == detail["pages"]
            assert api("GET", manifest["files"][0]["url"], raw=True) == exported
            checks.append("native reprocessing preserves historical sources and export bytes and requires new approval")
            (output / "reprocessed.png").write_bytes(base64.b64decode(client.call("Page.captureScreenshot")["data"]))
            # Cancel a newly queued fresh parse before publication.
            client.evaluate("$('reprocess-button').click()")
            wait_for(lambda: client.evaluate("!$('cancel-button').hidden && state.detail === null"), 10)
            client.evaluate("$('cancel-button').click()")
            wait_for(lambda: store.status(doc)["status"] == "CANCELLED")
            assert store.status(doc)["current_revision"] == 2
            checks.append("native cancellation prevents candidate publication")
            # A duplicate owns the same original/render independently.
            with (root / "samples/clean.png").open("rb") as handle:
                duplicate = store.submit(handle, "duplicate.png", "image/png")
            server.supervisor.notify()
            wait_for(lambda: store.status(duplicate)["status"] == "REVIEW_READY")
            original, render = store.object_path(duplicate), store.page_image_path(duplicate)
            # Schedule browser confirm acceptance before the synchronous click.
            client.call("Runtime.evaluate", {"expression": "setTimeout(() => $('delete-button').click(), 50)"})
            time.sleep(.2)
            client.call("Page.handleJavaScriptDialog", {"accept": True})
            wait_for(lambda: any(item["document_id"] == doc and item["status"] == "DELETED" for item in store.deletions()))
            assert original.exists() and render.exists()
            checks.append("native tracked deletion retains shared artifacts")
            api("POST", f"/api/documents/{duplicate}/delete", {})
            wait_for(lambda: store.deletion_status(duplicate)["status"] == "DELETED")
            assert not original.exists() and not render.exists()
            assert not list(store.export_root.rglob("invoice.json"))
            checks.append("last-reference deletion removes local objects renders history and exports")
            assert not list((store.object_root / "quarantine").iterdir())
            assert server.supervisor.status()["last_error"] is None
            # Force a live owned container to remain in flight. This controlled
            # sleep probe uses the production resource policy/stop transport;
            # it is distinct from the real OCR workflow above.
            server.supervisor.close()
            with (root / "samples/clean.png").open("rb") as handle:
                probe = store.submit(handle, "owned-stop-probe.png", "image/png")
            claim = store.claim("owned-stop-probe")
            owned_name = f"docwork-{claim.job_id[:16]}-{claim.fence}"
            stopped, errors = threading.Event(), []
            identity = subprocess.check_output(["docker", "image", "inspect", "docwork-parser:v3", "--format", "{{.Id}}"], text=True).strip()
            with tempfile.TemporaryDirectory(dir=store.object_root / "quarantine") as scratch:
                def controlled_command(source, mime, target, claim, *, image):
                    command = parser_command(source, mime, target, claim, image=image)
                    position = command.index(image)
                    return command[:position] + ["--entrypoint", "python", image, "-c", "import time; time.sleep(60)"]
                def run_probe():
                    try:
                        _docker_run(store.object_path(probe), "image/png", Path(scratch), claim, image=identity, stop_event=stopped)
                    except Exception as exc:
                        errors.append(exc)
                with patch("docwork.worker.parser_command", side_effect=controlled_command):
                    worker = threading.Thread(target=run_probe, daemon=True)
                    worker.start()
                    try:
                        wait_for(lambda: subprocess.run(["docker", "inspect", owned_name], capture_output=True).returncode == 0, 15)
                        store.cancel(probe)
                    finally:
                        stopped.set()
                        worker.join(timeout=15)
                    assert not worker.is_alive() and len(errors) == 1 and isinstance(errors[0], JobStopped)
                    assert subprocess.run(["docker", "inspect", owned_name], capture_output=True).returncode != 0
            store.finish_stopped(claim)
            assert store.status(probe)["status"] == "CANCELLED"
            store.request_delete(probe)
            assert store.run_deletions() == 1
            checks.append("controlled active Docker parser stop confirms owned removal before settling cancellation")
        finally:
            if client:
                client.close()
            if process:
                process.terminate()
                process.wait(timeout=10)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    source_paths = sorted([*root.glob("src/docwork/*.py"), *root.glob("ui/*"), Path(__file__).resolve()])
    report = {"status": "passed", "checks": checks,
              "method": "Scripted native Chrome controls, production background HTTP server and real Docker/Tesseract on fictional PNG; separate controlled sleep probe of production owned-container cancellation. No human timing, real scans, performance gate or model inference claim.",
              "input_sha256": hashlib.sha256((root / "samples/clean.png").read_bytes()).hexdigest(),
              "parser_image_id": subprocess.check_output(["docker", "image", "inspect", "docwork-parser:v3", "--format", "{{.Id}}"], text=True).strip(),
              "sources": {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths},
              "artifacts": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in output.iterdir()}}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "checks": len(checks), "report": str(output / "report.json")}))


if __name__ == "__main__":
    main()
