"""Exercise authenticated operational endpoints with injected attempt outcomes."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from docwork.intake import IntakeStore
from docwork.local_model import LocalModelConfig
from docwork.storage_budget import StorageLimitExceeded
from docwork.web import ReviewServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output_dir.absolute()
    if output.exists() or not output.is_relative_to(root / "artifacts") or any(p.is_symlink() for p in [output, *output.parents]):
        parser.error("Use a new runtime output directory under artifacts")
    paths = sorted([*root.glob("src/docwork/*.py"), *root.glob("ui/*"), Path(__file__).resolve()])
    sources = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    checks = []
    with tempfile.TemporaryDirectory(prefix="docwork-operations-") as temporary:
        store = IntakeStore(Path(temporary) / "review.sqlite", Path(temporary) / "objects")
        with ReviewServer(("127.0.0.1", 0), store,
                          model_config=LocalModelConfig("http://127.0.0.1:1", "unavailable-probe-model")) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cookie = ""

            def get(route, *, expected=200, processor=False):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                try:
                    headers = {"Cookie": cookie}
                    if processor:
                        headers["Authorization"] = f"Bearer {server.access.processing_token}"
                    connection.request("GET", route, headers=headers)
                    response = connection.getresponse()
                    raw = response.read()
                    assert response.status == expected, (route, response.status, expected)
                    return raw, response.getheader("Set-Cookie")
                finally:
                    connection.close()

            try:
                for route in ("/healthz", "/readyz", "/metrics"):
                    get(route, expected=401)
                checks.append("operational endpoints require authentication")
                _, session = get(f"/?token={server.token}", expected=303)
                cookie = session.split(";", 1)[0]
                for route in ("/healthz", "/readyz", "/metrics"):
                    get(route, expected=403, processor=True)
                checks.append("processing credentials cannot read operations with a reviewer cookie")
                assert json.loads(get("/healthz")[0]) == {"status": "alive"}
                checks.append("authenticated liveness responds")
                ready = json.loads(get("/readyz")[0])
                assert ready["ready"] and ready["model"] == "unavailable"
                checks.append("optional model outage does not block rules readiness")
                document = store.submit(io.BytesIO((root / "samples/clean.png").read_bytes()), "private-invoice-name.png", "image/png")
                view = json.loads(get("/metrics")[0])
                assert view["jobs"] == {"QUEUED": 1} and view["oldest_queue_age_seconds"] is not None
                checks.append("durable queued work exposes age without content")
                claim = store.claim("injected-worker")
                store.set_stage(claim, "PARSING")
                store.fail(claim, "PARSER_TIMEOUT")
                assert json.loads(get("/metrics")[0])["failure_codes"] == {"PARSER_TIMEOUT": 1}
                checks.append("injected failed attempt remains counted")
                store.retry(document)
                claim = store.claim("injected-worker")
                store.set_stage(claim, "PARSING")
                store.fail(claim, "MODEL_UNAVAILABLE")
                view = json.loads(get("/metrics")[0])
                assert view["retry_or_reprocess_count"] == 1 and view["timings"]["processing"]["count"] == 2
                assert view["timings"]["parsing"]["count"] == 2 and len(view["failure_codes"]) == 2
                checks.append("retry metrics preserve both original outcomes and stage coverage")
                raw = json.dumps(view)
                assert all(secret not in raw for secret in (document, "private-invoice", "injected-worker", "Aster", str(store.database), server.token))
                checks.append("metric payload omits document identity text paths actors and credentials")
                store.request_delete(document)
                store.run_deletions()
                assert json.loads(get("/metrics")[0])["timings"]["processing"]["count"] == 0
                checks.append("deletion removes per-document metric history")
                original = store.storage_budget.check
                def unavailable(*args, **kwargs):
                    raise StorageLimitExceeded("INSUFFICIENT_DISK_SPACE")
                store.storage_budget.check = unavailable
                server.readiness.cached = None
                assert json.loads(get("/readyz", expected=503)[0])["storage"] == "INSUFFICIENT_DISK_SPACE"
                store.storage_budget.check = original
                checks.append("storage guard refusal produces HTTP 503 without paths")
            finally:
                server.shutdown()
                thread.join(timeout=5)
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest for name, digest in sources.items())
    output.mkdir(parents=True)
    report = {"version": "operations-http-verification-v1", "status": "passed", "checks": checks,
              "recorded_at_utc": datetime.now(timezone.utc).isoformat(), "source_sha256": sources,
              "scope": "Real loopback HTTP and SQLite; fictional PNG intake with explicitly injected parser/model failure categories. "
                       "No live parsing, inference, human review, measured memory or performance claim."}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": "passed", "checks": len(checks), "report": str(output / "report.json")}, indent=2))


if __name__ == "__main__":
    main()
