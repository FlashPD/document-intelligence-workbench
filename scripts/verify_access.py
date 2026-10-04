"""Exercise reviewer/processing capabilities through real loopback HTTP.

Recorded fictional candidates; no live OCR, model, or human study.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from docwork.demo_replay import prepare_replay
from docwork.intake import IntakeStore
from docwork.web import ReviewServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output_dir.absolute()
    if output.exists() or output.is_symlink() or not output.is_relative_to(root / "artifacts"):
        parser.error("Use a new output directory under artifacts")
    sources = sorted([*root.glob("src/docwork/*.py"), *root.glob("ui/*"), Path(__file__).resolve()])
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    checks = []
    with tempfile.TemporaryDirectory(prefix="docwork-access-") as temporary:
        directory = Path(temporary)
        replay = prepare_replay(root, directory / "replay")
        store = IntakeStore(directory / "replay/review.sqlite", directory / "replay/objects")
        # A live intake boundary around recorded candidates; no processor runs.
        with ReviewServer(("127.0.0.1", 0), store) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cookie = ""

            def call(method, route, data=None, *, credential=None, origin=None, expected=200):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                headers = {"Cookie": cookie, "Origin": origin or server.origin}
                if credential:
                    headers["Authorization"] = f"Bearer {credential}"
                body = json.dumps(data).encode() if data is not None else None
                if body is not None:
                    headers["Content-Type"] = "application/json"
                try:
                    connection.request(method, route, body, headers)
                    response = connection.getresponse()
                    raw = response.read()
                    assert response.status == expected, (method, route, response.status, expected)
                    return raw, dict(response.getheaders())
                finally:
                    connection.close()

            try:
                doc = replay["cases"][0]["document_id"]
                route = f"/api/documents/{doc}"
                call("GET", route + "/pages/1", expected=401)
                _, headers = call("GET", f"/?token={server.token}", expected=303)
                cookie = headers["Set-Cookie"].split(";", 1)[0]
                runtime = json.loads(call("GET", "/api/runtime")[0])
                assert runtime["principal"] == server.access.reviewer.as_dict()
                checks.append("session establishes the server OS reviewer")
                call("GET", route + "/pages/1")
                checks.append("page bytes require reviewer authentication")
                before = store.history(doc)
                for action, data in (("edit", {"revision": 1, "path": "fields.total", "value": "0"}),
                                     ("acknowledge", {"revision": 1, "code": "REQUIRED_MISSING", "path": "fields.total", "reason": "checked"}),
                                     ("approve", {"revision": 1})):
                    call("POST", route + "/" + action, {**data, "actor": "forged-reviewer"}, expected=403)
                assert store.history(doc) == before
                checks.append("forged actors cannot edit acknowledge or approve")
                call("POST", route + "/approve", {"revision": 1}, credential="model-runtime-key", expected=401)
                checks.append("model credentials cannot authorize review")
                call("POST", route + "/approve", {"revision": 1}, origin="https://example.invalid", expected=403)
                checks.append("same-origin mutations remain mandatory")
                for action in ("edit", "acknowledge", "approve", "export", "delete", "reprocess", "cancel"):
                    call("POST", route + "/" + action, {"actor": runtime["principal"]["actor"]},
                         credential=server.access.processing_token, expected=403)
                assert store.history(doc) == before
                checks.append("processing capability cannot gain reviewer authority with a reviewer cookie")
                call("POST", "/api/batches", {"count": 1}, credential=server.access.processing_token, expected=201)
                checks.append("processing capability can create an intake batch")
                original = store.get(doc)["record"]["fields"]["invoice_number"]["value"]
                call("POST", route + "/edit", {"revision": 1, "path": "fields.invoice_number", "value": original})
                call("POST", route + "/approve", {"revision": 1}, expected=409)
                approval = json.loads(call("POST", route + "/approve", {"revision": 2})[0])
                assert approval["actor"] == runtime["principal"]["actor"]
                checks.append("edits and current-revision approvals bind the established actor")
                for format in ("json", "csv"):
                    manifest = json.loads(call("POST", route + "/export", {"format": format})[0])
                    for entry in manifest["files"]:
                        raw = call("GET", entry["url"])[0]
                        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
                        call("GET", entry["url"], credential=server.access.processing_token, expected=403)
                        saved = cookie
                        cookie = ""
                        call("GET", entry["url"], expected=401)
                        cookie = saved
                checks.append("JSON and both CSV files preserve checksums and require review permission")
                call("GET", route + "/pages/1", credential=server.access.processing_token, expected=403)
                checks.append("processing capability cannot retrieve page images")
            finally:
                server.shutdown()
                thread.join(timeout=5)
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())
    report = {"report_version": "local-access-verification-v1", "status": "passed",
              "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "checks": checks,
              "source_sha256": hashes, "inputs_unchanged": True,
              "scope": "Real loopback HTTP with fictional recorded candidates. No OCR, inference, human identity provider or multi-user isolation."}
    output.mkdir(parents=True)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": "passed", "checks": len(checks), "report": str(output / "report.json")}, indent=2))


if __name__ == "__main__":
    main()
