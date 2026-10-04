"""Freeze, run, score and manually audit a bounded production invoice study."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import platform
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import quote

from docwork.intake import IntakeStore, MIME_BY_SUFFIX
from docwork.model_runtime import file_hash, load_profile, managed_server
from docwork.production_study import (
    VERSION, approved_results, audit_template, bounded_file, freeze, inventory,
    prepare_diagnostic, read_protocol, score_run, summarize_audit, write_json,
)
from docwork.web import ReviewServer
from docwork.worker import PARSER_IMAGE, _docker_image_id


class Client:
    def __init__(self, server):
        self.server, self.cookie = server, ""

    def request(self, method, path, body=None, *, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)
        try:
            connection.request(method, path, body=body, headers={"Cookie": self.cookie,
                "Origin": self.server.origin, **(headers or {})})
            response = connection.getresponse()
            content = response.read()
            if response.getheader("Set-Cookie"):
                self.cookie = response.getheader("Set-Cookie").split(";", 1)[0]
            return response.status, content
        finally:
            connection.close()


def run(root: Path, protocol_dir: Path, output: Path, variant: str) -> dict:
    protocol = read_protocol(protocol_dir, root=root)
    parser_id = _docker_image_id(PARSER_IMAGE)
    if parser_id != protocol["parser_image"]:
        raise ValueError("Parser image changed after freeze")
    workbench = output.with_name(output.name + "-workbench")
    if output.exists() or workbench.exists():
        raise ValueError("Use new run and workbench directories")
    output.mkdir(parents=True)
    report = {"version": VERSION, "protocol_sha256": file_hash(protocol_dir / "protocol.json"),
              "variant": variant, "status": "interrupted", "documents": [],
              "parser_image": parser_id, "model_runtime": None,
              "host": {"platform": platform.platform(), "python": platform.python_version()},
              "scope": "Fresh uploads to a supervised serial workbench. Suggestions remain immutable and unapproved. Timing is diagnostic, not controlled performance acceptance; no human review or automatic semantic assessment."}
    runtime = None
    try:
        profile = load_profile(protocol_dir / "profile.json")
        context = managed_server(root, profile, output / "model-server.log") if variant == "span_llm" else nullcontext((None, None))
        with context as (config, runtime):
            store = IntakeStore(workbench / "review.sqlite", workbench / "objects")
            with ReviewServer(("127.0.0.1", 0), store, model_config=config, background_processing=True) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                client = Client(server)
                try:
                    assert client.request("GET", f"/?token={server.token}")[0] == 303
                    for case in protocol["documents"]:
                        result = {"id": case["id"], "failure_type": None}
                        report["documents"].append(result)
                        source = bounded_file(protocol_dir, case["asset"])
                        started = time.monotonic()
                        print(f"Production study {variant}: {case['id']}", flush=True)
                        try:
                            status, raw = client.request("POST", "/api/upload", source.read_bytes(), headers={
                                "Content-Type": MIME_BY_SUFFIX[source.suffix], "X-File-Name": quote(source.name),
                                "X-Extractor": variant})
                            if status != 201:
                                result["failure_type"] = f"UPLOAD_HTTP_{status}"
                                continue
                            submitted = json.loads(raw)
                            doc_id = result["document_id"] = submitted["document_id"]
                            deadline = started + protocol["document_deadline_seconds"]
                            while True:
                                status, raw = client.request("GET", f"/api/documents/{doc_id}/status")
                                if status != 200:
                                    raise RuntimeError("STATUS_UNAVAILABLE")
                                state = json.loads(raw)
                                if state["status"] in ("REVIEW_READY", "FAILED", "REJECTED", "CANCELLED"):
                                    break
                                if time.monotonic() >= deadline:
                                    client.request("POST", f"/api/documents/{doc_id}/cancel", b"{}",
                                                   headers={"Content-Type": "application/json"})
                                    raise TimeoutError("STUDY_DEADLINE")
                                time.sleep(.1)
                            if state["status"] != "REVIEW_READY":
                                result["failure_type"] = (state.get("job") or {}).get("error_code") or state["status"]
                                continue
                            status, raw = client.request("GET", f"/api/documents/{doc_id}")
                            if status != 200:
                                raise RuntimeError("CANDIDATE_UNAVAILABLE")
                            detail = json.loads(raw)
                            relative = f"predictions/{case['id']}.json"
                            write_json(output / relative, detail)
                            result["prediction"] = relative
                            result["parser_image"] = state["parser_checkpoint"]["parser_identity"]
                            result["page_sha256"] = {}
                            for page in detail["pages"]:
                                status, raster = client.request("GET", f"/api/documents/{doc_id}/pages/{page['number']}")
                                if status != 200:
                                    raise RuntimeError("RENDER_UNAVAILABLE")
                                path = output / f"pages/{case['id']}-{page['number']}.png"
                                path.parent.mkdir(exist_ok=True)
                                with path.open("xb") as target:
                                    target.write(raster)
                                result["page_sha256"][str(page["number"])] = hashlib.sha256(raster).hexdigest()
                            if len(detail["pages"]) != case["page_count"]:
                                result["failure_type"] = "DECLARED_PAGE_COUNT_MISMATCH"
                        except Exception as exc:
                            # Study evidence retains a stable failure category, not raw document errors.
                            result["failure_type"] = type(exc).__name__
                        finally:
                            result["upload_to_terminal_seconds"] = round(time.monotonic() - started, 3)
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
        read_protocol(protocol_dir, root=root)
        if _docker_image_id(PARSER_IMAGE) != parser_id:
            raise ValueError("Parser image changed during the run")
        report["status"] = "complete"
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
    finally:
        report["model_runtime"] = runtime
        report["artifacts"] = inventory(output)
        write_json(output / "report.json", report)
    if report["status"] == "complete":
        score_run(protocol_dir, output)  # Check source/candidate bindings before presenting completion.
    return report


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    diagnostic = commands.add_parser("prepare-diagnostic")
    diagnostic.add_argument("--output-dir", type=Path, required=True)
    create = commands.add_parser("freeze")
    create.add_argument("spec", type=Path)
    create.add_argument("--mode", choices=("production", "diagnostic"), required=True)
    create.add_argument("--output-dir", type=Path, required=True)
    execute = commands.add_parser("run")
    execute.add_argument("protocol", type=Path)
    execute.add_argument("--variant", choices=("ocr_rules", "span_llm"), required=True)
    execute.add_argument("--output-dir", type=Path, required=True)
    for action in ("score", "audit-template", "audit-report", "approved"):
        command = commands.add_parser(action)
        command.add_argument("protocol", type=Path)
        command.add_argument("run", type=Path)
        command.add_argument("--output", type=Path, required=True)
        if action == "audit-report":
            command.add_argument("assessment", type=Path)
        if action == "approved":
            command.add_argument("--workbench", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = getattr(args, "output_dir", None) or getattr(args, "output", None)
        if output.exists() or output.is_symlink() or any(parent.is_symlink() for parent in output.absolute().parents):
            raise ValueError("Use a new output path without symlinks")
        if not output.absolute().is_relative_to(root / "artifacts"):
            raise ValueError("Runtime study output belongs under artifacts")
        if args.action == "prepare-diagnostic":
            result = prepare_diagnostic(root, args.output_dir)
        elif args.action == "freeze":
            result = freeze(root, args.spec, args.output_dir, parser_image=_docker_image_id(PARSER_IMAGE), mode=args.mode)
        elif args.action == "run":
            result = run(root, args.protocol, args.output_dir, args.variant)
        else:
            if args.action == "score":
                result = score_run(args.protocol, args.run)
            elif args.action == "audit-template":
                result = audit_template(args.protocol, args.run)
            elif args.action == "audit-report":
                result = summarize_audit(args.protocol, args.run, json.loads(args.assessment.read_text()))
            else:
                database = args.workbench / "review.sqlite"
                if not database.is_file():
                    raise ValueError("Existing study workbench is required")
                result = approved_results(args.protocol, args.run, IntakeStore(database, args.workbench / "objects"))
            write_json(args.output, result)
        print(json.dumps({"action": args.action, "output": str(output), "status": result.get("status", "written")}, indent=2))
        return 2 if args.action == "run" and result["status"] != "complete" else 0
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Production study failed: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
