"""Verify real pinned inference through the authenticated upload/review HTTP API.

Only the two named, hash-pinned fictional fixtures can be reviewed by this probe.
It never connects to an existing workbench database or model server.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import platform
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from docwork.intake import IntakeStore
from docwork.model_runtime import file_hash, load_profile, managed_server
from docwork.web import ReviewServer
from docwork.worker import PARSER_IMAGE, _docker_image_id

FIXTURES = {
    "clean.png": "925a3b1858f6b322330ba16148cbd9e498117e89979182fe897af60a90e1a106",
    "conflicting-total.png": "13f2eef374c009a698a31e17639417d0b970df0feca6329e1053a59e8be331d3",
}
SOURCES = ("worker.py", "intake.py", "review.py", "local_model.py", "model_runtime.py", "web.py",
           "contracts.py", "validation.py", "ocr.py", "geometry.py", "baseline.py",
           "review_priority.py", "parser_protocol.py", "parser_entry.py")


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


class Client:
    def __init__(self, server):
        self.server = server
        self.cookie = ""

    def call(self, method, path, body=None, media="application/json", filename=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=600)
        headers = {"Cookie": self.cookie, "Origin": self.server.origin}
        if body is not None:
            body = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers["Content-Type"] = media
        if filename:
            headers["X-File-Name"] = filename
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read()
            if response.getheader("Set-Cookie"):
                self.cookie = response.getheader("Set-Cookie").split(";", 1)[0]
            return response.status, content
        finally:
            connection.close()

    def json(self, method, path, body=None, expected=200):
        status, content = self.call(method, path, body)
        check(status == expected, f"{method} {path}: expected {expected}, got {status}: {content[:256]!r}")
        return json.loads(content)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def verify_document(client, store, root, output, name):
    source = root / "samples" / name
    check(file_hash(source) == FIXTURES[name], "Fixture differs from the pinned fictional sample")
    status, content = client.call("POST", "/api/upload", source.read_bytes(), "image/png", name)
    check(status == 201, "HTTP upload failed")
    submitted = json.loads(content)
    doc_id = submitted["document_id"]
    check(submitted["status"] == "RECEIVED", "Upload was not queued")
    started = time.perf_counter()
    processed = client.json("POST", "/api/process-one", {"extractor": "span_llm"})
    seconds = round(time.perf_counter() - started, 3)
    check(processed["document_id"] == doc_id and processed["status"] == "REVIEW_READY",
          f"Model worker failed: {processed.get('job')}")
    detail = client.json("GET", f"/api/documents/{doc_id}")
    check(detail["extraction"]["profile"] == "span_llm", "Unexpected extractor")
    check(detail["approval"] is None, "Extraction inherited an approval")
    known = {span["id"] for page in detail["pages"] for span in page["spans"]}
    fields = list(detail["record"]["fields"].values()) + [row[field] for row in detail["record"]["line_items"]
              for field in ("description", "quantity", "unit_price", "line_total", "tax")]
    check(all(set(field["evidence_ids"]).issubset(known) for field in fields), "Unknown model evidence")
    check(not any(issue["code"].startswith("EVIDENCE_") for issue in detail["issues"]), "Unsupported model evidence")
    check(detail["record"]["fields"]["invoice_number"]["value"] == "AST-1001", "Wrong fixture invoice number")
    check(len(detail["record"]["line_items"]) == 1, "Wrong fixture row count")
    check(detail["record"]["line_items"][0]["line_total"]["value"] == "250.00", "Wrong fixture line total")
    write_json(output / "predictions" / f"{source.stem}.json", detail)
    for page in detail["pages"]:
        status, raster = client.call("GET", f"/api/documents/{doc_id}/pages/{page['number']}")
        check(status == 200 and hashlib.sha256(raster).hexdigest() == file_hash(store.page_image_path(doc_id, page["number"])),
              "HTTP page bytes differ from the checked render")
        path = output / "pages" / f"{source.stem}-{page['number']}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raster)
    client.json("POST", f"/api/documents/{doc_id}/export", {"format": "json"}, expected=409)
    revision = 1
    actor = "fictional-fixture-verification"
    if name == "conflicting-total.png":
        check(detail["record"]["fields"]["total"]["value"] == "275.00", "Observed conflict was overwritten")
        check("TOTAL_MISMATCH" in {issue["code"] for issue in detail["issues"]}, "Total conflict was not flagged")
        client.json("POST", f"/api/documents/{doc_id}/approve", {"revision": 1, "actor": actor}, expected=422)
        revised = client.json("POST", f"/api/documents/{doc_id}/edit", {
            "revision": 1, "path": "fields.total", "value": "270.00", "actor": actor})
        revision = revised["revision"]
        check(revision == 2 and revised["approval"] is None, "Correction did not create an unapproved revision")
        original = store.get(doc_id, 1)
        check(original["record"]["fields"]["total"]["value"] == "275.00", "Correction erased the original suggestion")
        write_json(output / "reviews" / f"{source.stem}.json", revised)
    approval = client.json("POST", f"/api/documents/{doc_id}/approve", {"revision": revision, "actor": actor})
    export_hashes = {}
    for format in ("json", "csv"):
        manifest = client.json("POST", f"/api/documents/{doc_id}/export", {"format": format})
        check(manifest["revision"] == revision, "Export revision differs from approval")
        for entry in manifest["files"]:
            status, content = client.call("GET", entry["url"])
            digest = hashlib.sha256(content).hexdigest()
            check(status == 200 and digest == entry["sha256"], "Export HTTP checksum mismatch")
            target = output / "exports" / source.stem / format / Path(entry["path"]).name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            export_hashes[str(target.relative_to(output))] = digest
    client.json("POST", f"/api/documents/{doc_id}/edit", {
        "revision": revision - 1, "path": "fields.total", "value": "999.00", "actor": actor}, expected=409)
    reopened = IntakeStore(store.database, store.object_root)
    check(reopened.get(doc_id)["approval"]["approval_hash"] == approval["approval_hash"], "Reopen lost approval")
    for format in ("json", "csv"):
        check(reopened.export(doc_id, format)["revision"] == revision, "Reopen lost idempotent export")
    write_json(output / "history" / f"{source.stem}.json", client.json("GET", f"/api/documents/{doc_id}/history"))
    return {"fixture": name, "source_sha256": FIXTURES[name], "status": "passed", "process_seconds": seconds,
            "parser_checkpoint": processed["parser_checkpoint"], "issues_before_review": detail["issues"],
            "approved_revision": revision, "approval_hash": approval["approval_hash"], "export_sha256": export_hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=Path("config/model-mac-instruct.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    paths = [*(root / "src/docwork" / name for name in SOURCES), Path(__file__).resolve(),
             root / "ui/app.js", root / "ui/index.html", root / "sandbox/Dockerfile"]
    hashes = {str(path.relative_to(root)): file_hash(path) for path in paths}
    profile = load_profile(args.profile)
    write_json(output / "profile.json", profile)
    write_json(output / "source_snapshot.json", {str(path.relative_to(root)): path.read_text() for path in paths})
    report = {"report_version": "real-model-upload-workflow-v1", "status": "failed",
              "started_at_utc": datetime.now(timezone.utc).isoformat(), "source_sha256": hashes,
              "host": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
              "checks": [], "scope": "Two hash-pinned fictional PNG uploads via real HTTP, Docker parsing, and pinned real inference. "
              "Automated fixture reviewer only; no held-out quality, visual browser, or multi-page model claim."}
    runtime = None
    try:
        report["parser_image_id"] = _docker_image_id(PARSER_IMAGE)
        with tempfile.TemporaryDirectory(prefix="model-workflow-") as temporary:
            store = IntakeStore(Path(temporary) / "review.sqlite", Path(temporary) / "intake")
            with managed_server(root, profile, output / "server.log") as (config, runtime):
                with ReviewServer(("127.0.0.1", 0), store, model_config=config, model_profile=profile["profile"]) as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    try:
                        client = Client(server)
                        check(client.call("GET", "/api/runtime")[0] == 401, "Runtime metadata lacked session protection")
                        check(client.call("GET", f"/?token={server.token}")[0] == 303, "HTTP session setup failed")
                        metadata = client.json("GET", "/api/runtime")
                        check(metadata["managed_model"] and config.api_key not in json.dumps(metadata), "Model credentials leaked")
                        for name in FIXTURES:
                            print(f"Verifying real model upload: {name}", flush=True)
                            report["checks"].append(verify_document(client, store, root, output, name))
                    finally:
                        server.shutdown()
                        thread.join(timeout=5)
        report["inputs_unchanged"] = all(file_hash(root / name) == digest for name, digest in hashes.items())
        check(report["inputs_unchanged"] and _docker_image_id(PARSER_IMAGE) == report["parser_image_id"], "Inputs changed during verification")
        check(runtime["shutdown_complete"], "Managed model server did not stop")
        report["status"] = "passed"
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["managed_runtime"] = runtime
        report["artifacts"] = {str(path.relative_to(output)): file_hash(path)
                               for path in sorted(output.rglob("*")) if path.is_file()}
        write_json(output / "report.json", report)
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
