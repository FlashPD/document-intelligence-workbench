"""Bounded production-parser queue measurements, separate from quality scores."""

from __future__ import annotations

import hashlib
import html
import io
import json
import math
import platform
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .intake import IntakeStore
from .invoice_run import _write_new
from .model_runtime import file_hash
from .release_evaluation import verify_invoice_manifest
from .review import _atomic_write
from .worker import PARSER_IMAGE, _docker_image_id, process_one

VERSION = "bounded-parser-queue-v1"
DOCUMENTS = tuple(f"inv-f{family:02d}-{number:02d}" for family in range(1, 7)
                  for number in range(1, 5 if family in (1, 6) else 4))


def write(path: Path, value: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, (json.dumps(value, indent=2, allow_nan=False) + "\n").encode())


def summarize(measurements: list[dict]) -> dict:
    if len(measurements) != 20 or {m["corpus_id"] for m in measurements} != set(DOCUMENTS):
        raise ValueError("Queue report requires all 20 declared documents")
    if len({m["document_id"] for m in measurements}) != 20:
        raise ValueError("Queue report contains duplicate jobs")
    for row in measurements:
        for name in ("started_seconds", "finished_seconds"):
            value = row[name]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("Queue timings must be finite nonnegative seconds")
        if row["finished_seconds"] < row["started_seconds"]:
            raise ValueError("Queue completion precedes processing")
        if row["status"] not in ("REVIEW_READY", "FAILED"):
            raise ValueError("Queue report contains unfinished jobs")
        if (row["status"] == "FAILED") != bool(row["error_code"]):
            raise ValueError("Queue failure status and error code differ")
        if type(row["page_count"]) is not int or row["page_count"] < 0:
            raise ValueError("Queue page counts must be nonnegative integers")
    for left, right in zip(measurements, measurements[1:]):
        if right["started_seconds"] < left["finished_seconds"]:
            raise ValueError("Queue timings overlap despite a serial worker")
    durations = sorted(row["finished_seconds"] - row["started_seconds"] for row in measurements)
    failures = Counter(row["error_code"] for row in measurements if row["status"] == "FAILED")
    return {"scheduled": 20, "review_ready": 20 - sum(failures.values()),
            "failed": sum(failures.values()), "failure_codes": dict(sorted(failures.items())),
            "pages_ready": sum(row["page_count"] for row in measurements),
            "processing_sum_seconds": round(sum(durations), 3),
            "processing_p50_seconds": round(durations[math.ceil(.5 * len(durations)) - 1], 3),
            "processing_p95_seconds": round(durations[math.ceil(.95 * len(durations)) - 1], 3),
            "queue_drain_seconds": measurements[-1]["finished_seconds"],
            "last_start_wait_seconds": measurements[-1]["started_seconds"]}


def render(report: dict) -> str:
    summary = report["summary"]
    rows = "".join(f'<tr><td>{html.escape(row["corpus_id"])}</td><td>{html.escape(row["media_type"])}</td>'
                   f'<td>{row["page_count"]}</td><td>{html.escape(row["status"])}</td>'
                   f'<td>{row["started_seconds"]:.3f}</td><td>{row["finished_seconds"]-row["started_seconds"]:.3f}</td>'
                   f'<td>{row["finished_seconds"]:.3f}</td><td>{html.escape(row["error_code"] or "")}</td></tr>'
                   for row in report["measurements"])
    return f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bounded parser queue</title><style>body{{max-width:1100px;margin:auto;padding:24px;font:16px/1.6 system-ui;color:#19362b}}table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;text-align:left;border-bottom:1px solid #ccd9df}}.scroll{{overflow:auto}}</style>
<h1>20-document serial parser queue</h1><p>{html.escape(report['scope'])}</p>
<p>{summary['review_ready']}/20 review-ready; {summary['failed']} failed. Queue drained in {summary['queue_drain_seconds']:.3f} seconds.
Processing P50 {summary['processing_p50_seconds']:.3f}s / P95 {summary['processing_p95_seconds']:.3f}s, including failures.</p>
<p>Other workload: {html.escape(report['run']['co_running_workload'])}. No controlled warm/cold latency, model inference, human review, quality, or concurrent capacity claim.</p>
<p>Wait is measured from the fully submitted batch; processing includes Docker startup, parsing, OCR, import, rules, validation, and persistence.
Percentiles use nearest rank. Container limit: two CPUs / 1 GiB; actual peak memory was not measured.</p>
<div class="scroll"><table><thead><tr><th>Document</th><th>Input</th><th>Pages</th><th>Outcome</th><th>Wait s</th><th>Process s</th><th>Completed s</th><th>Failure</th></tr></thead><tbody>{rows}</tbody></table></div>
<p><a href="report.json">Report and hashes</a> · <a href="measurements.json">All measurements</a></p></html>'''


def run_queue(root: Path, output: Path, *, co_running_workload: str) -> dict:
    root = root.resolve(strict=True)
    if output.exists() or output.is_symlink():
        raise ValueError("Queue benchmark needs a new output directory")
    output = output.absolute()
    relative = output.resolve().relative_to(root) if output.resolve().is_relative_to(root) else None
    if relative is not None and (not relative.parts or relative.parts[0] != "artifacts"):
        raise ValueError("Run queue measurements under artifacts, outside source and recorded evidence")
    if not co_running_workload.strip():
        raise ValueError("Declare other workloads, including 'none observed' when applicable")
    manifest = root / "datasets/invoices-v1/manifest.json"
    corpus = json.loads(manifest.read_text())
    verify_invoice_manifest(corpus, manifest)
    selected = {doc["id"]: doc for doc in corpus["documents"] if doc["id"] in DOCUMENTS}
    if set(selected) != set(DOCUMENTS) or any(doc["split"] != "development" or doc["assets"][0]["sha256"] != doc["source_sha256"] for doc in selected.values()):
        raise ValueError("Queue benchmark needs the 20 declared development documents")
    image_id = _docker_image_id(PARSER_IMAGE)
    source_names = [str(path.relative_to(root)) for path in sorted((root / "src/docwork").glob("*.py"))]
    source_names += ["scripts/benchmark_queue.py", "sandbox/Dockerfile"]
    snapshot = {name: (root / name).read_text() for name in source_names}
    output.mkdir(parents=True)
    write(output / "source_snapshot.json", snapshot)
    tasks = [{"corpus_id": id, "path": selected[id]["assets"][0]["path"],
              "source_sha256": selected[id]["source_sha256"],
              "media_type": "application/pdf" if selected[id]["assets"][0]["path"].endswith(".pdf") else "image/png"}
             for id in DOCUMENTS]
    run = {"version": VERSION, "started_at_utc": datetime.now(timezone.utc).isoformat(),
           "manifest_sha256": file_hash(manifest), "documents": tasks,
           "parser_image_id": image_id, "co_running_workload": co_running_workload.strip(),
           "worker_count": 1, "extractor": "ocr_rules", "reparse": True,
           "host": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
           "source_sha256": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()}}
    write(output / "run.json", run)
    store = IntakeStore(output / "workbench/review.sqlite", output / "workbench/objects")
    before_intake = time.perf_counter()
    ids = store.submit_batch([(io.BytesIO((manifest.parent / task["path"]).read_bytes()), Path(task["path"]).name, task["media_type"])
                              for task in tasks])
    epoch = time.perf_counter()
    intake_seconds = round(epoch - before_intake, 6)
    pending = dict(zip(ids, tasks))
    measurements = []
    while pending:
        started = time.perf_counter() - epoch
        document_id = process_one(store, "queue-benchmark", image=image_id, reparse=True)
        finished = time.perf_counter() - epoch
        if document_id not in pending:
            raise RuntimeError("Queue did not process a unique scheduled document")
        task = pending.pop(document_id)
        status = store.status(document_id)
        if status["source_sha256"] != task["source_sha256"]:
            raise RuntimeError("Queue source changed after intake")
        if status["status"] == "REVIEW_READY":
            detail = store.get(document_id)
            if detail["approval"] is not None or status["job"]["attempts"] != 1:
                raise RuntimeError("Benchmark inherited approval or retried a completed job")
            if status["parser_checkpoint"]["parser_identity"] != image_id:
                raise RuntimeError("Queue parser differs from the frozen image")
            prediction = {"source_sha256": task["source_sha256"], "pages": detail["pages"],
                          "record": detail["record"], "issues": detail["issues"]}
        else:
            prediction = {"source_sha256": task["source_sha256"], "record": None,
                          "failure_type": status["job"]["error_code"]}
        write(output / "predictions" / f'{task["corpus_id"]}.json', prediction)
        measurements.append({"corpus_id": task["corpus_id"], "document_id": document_id,
                             "media_type": task["media_type"], "status": status["status"],
                             "page_count": status["page_count"], "error_code": status["job"]["error_code"],
                             "started_seconds": round(started, 6), "finished_seconds": round(finished, 6)})
        _write_new(output / "measurements.json", measurements)
        print(f'Queue {len(measurements)}/20: {task["corpus_id"]} {status["status"]}', flush=True)
    if any(file_hash(root / name) != digest for name, digest in run["source_sha256"].items()) or file_hash(manifest) != run["manifest_sha256"]:
        raise RuntimeError("Benchmark inputs changed during execution")
    summary = summarize(measurements)
    report = {"report_version": VERSION, "status": "completed_with_failures" if summary["failed"] else "completed",
              "run": run, "intake_seconds": intake_seconds, "measurements": measurements, "summary": summary,
              "scope": "Fixed 20 fictional development originals, fresh production Docker parsing and OCR/rules, one serial worker. "
                       "PNG and original two-page PDF inputs; all failures retained. No tuning or held-out quality scores.",
              "artifacts": {str(path.relative_to(output)): file_hash(path) for path in sorted(output.rglob("*"))
                            if path.is_file() and not path.is_relative_to(output / "workbench")}}
    write(output / "report.json", report)
    (output / "index.html").write_text(render(report))
    return report


def verify_queue(root: Path, directory: Path) -> dict:
    report = json.loads((directory / "report.json").read_text())
    run = json.loads((directory / "run.json").read_text())
    measurements = json.loads((directory / "measurements.json").read_text())
    snapshot = json.loads((directory / "source_snapshot.json").read_text())
    if report["report_version"] != VERSION or report["run"] != run or report["measurements"] != measurements:
        raise ValueError("Queue report differs from its recorded run")
    if report["summary"] != summarize(measurements):
        raise ValueError("Queue summary differs from all-document measurements")
    if type(report["intake_seconds"]) not in (int, float) or not math.isfinite(report["intake_seconds"]) or report["intake_seconds"] < 0:
        raise ValueError("Queue intake timing is invalid")
    expected_status = "completed_with_failures" if report["summary"]["failed"] else "completed"
    if report["status"] != expected_status or run["worker_count"] != 1 or run["reparse"] is not True or run["extractor"] != "ocr_rules":
        raise ValueError("Queue report scope or outcomes differ")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", run["parser_image_id"]):
        raise ValueError("Queue parser image identity is invalid")
    if {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()} != run["source_sha256"]:
        raise ValueError("Queue source snapshot differs")
    for name in snapshot:
        if Path(name).is_absolute() or ".." in Path(name).parts or not (root / name).resolve().is_relative_to(root.resolve()):
            raise ValueError("Queue source path escapes the repository")
    if any(path.is_symlink() for path in directory.rglob("*")):
        raise ValueError("Queue evidence cannot contain symlinks")
    inventory = {str(path.relative_to(directory)): file_hash(path) for path in directory.rglob("*")
                 if path.is_file() and not path.is_relative_to(directory / "workbench") and path.name not in ("report.json", "index.html")}
    if report["artifacts"] != inventory:
        raise ValueError("Queue artifacts differ from recorded hashes")
    if (directory / "index.html").read_text() != render(report):
        raise ValueError("Queue presentation differs from the report")
    manifest = root / "datasets/invoices-v1/manifest.json"
    if file_hash(manifest) != run["manifest_sha256"]:
        raise ValueError("Queue corpus changed")
    corpus = {doc["id"]: doc for doc in json.loads(manifest.read_text())["documents"]}
    if [doc["corpus_id"] for doc in run["documents"]] != list(DOCUMENTS):
        raise ValueError("Queue selection differs from the declared 20 development cases")
    for task in run["documents"]:
        doc = corpus[task["corpus_id"]]
        prediction = json.loads((directory / "predictions" / f'{task["corpus_id"]}.json').read_text())
        measured = next(row for row in measurements if row["corpus_id"] == task["corpus_id"])
        if (doc["split"] != "development" or task["source_sha256"] != doc["source_sha256"]
                or prediction["source_sha256"] != task["source_sha256"] or task["path"] != doc["assets"][0]["path"]
                or file_hash(manifest.parent / task["path"]) != task["source_sha256"]
                or measured["media_type"] != task["media_type"]):
            raise ValueError("Queue prediction source differs from its development original")
        if (prediction["record"] is None) != (measured["status"] == "FAILED"):
            raise ValueError("Queue prediction and measured outcome differ")
        if prediction["record"] is None and prediction["failure_type"] != measured["error_code"]:
            raise ValueError("Queue failure code differs")
        if prediction["record"] is not None and len(prediction["pages"]) != measured["page_count"]:
            raise ValueError("Queue page count differs from recorded pages")
    drift = sorted(name for name, digest in run["source_sha256"].items() if not (root / name).is_file() or file_hash(root / name) != digest)
    return {"status": "verified", "summary": report["summary"], "source_drift": drift}
