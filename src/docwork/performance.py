"""Frozen fresh-upload performance protocol; all scheduled outcomes are retained."""
from __future__ import annotations

import json
import math
import platform
import re
from pathlib import Path

from .model_runtime import file_hash, load_profile
from .operations import distribution
from .queue_benchmark import DOCUMENTS

VERSION = "controlled-upload-performance-v1"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as handle:
        handle.write(json.dumps(value, indent=2, allow_nan=False) + "\n")


def source_hashes(root):
    paths = sorted([*root.glob("src/docwork/*.py"), *root.glob("ui/*"),
                    root / "scripts/benchmark_performance.py", root / "sandbox/Dockerfile"])
    return {str(path.relative_to(root)): file_hash(path) for path in paths if path.is_file()}


def freeze(root, output, *, parser_image, host_workloads):
    if output.exists() or not host_workloads.strip() or not re.fullmatch(r"sha256:[0-9a-f]{64}", parser_image):
        raise ValueError("Use a new protocol directory and declare host workloads")
    corpus = json.loads((root / "datasets/invoices-v1/manifest.json").read_text())
    selected = {doc["id"]: doc for doc in corpus["documents"] if doc["id"] in DOCUMENTS}
    if set(selected) != set(DOCUMENTS) or any(doc["split"] != "development" for doc in selected.values()):
        raise ValueError("Performance queue must use the frozen development selection")
    tasks = []
    for name in DOCUMENTS:
        asset = selected[name]["assets"][0]
        path = root / "datasets/invoices-v1" / asset["path"]
        if file_hash(path) != asset["sha256"]:
            raise ValueError("Queue input hash mismatch")
        tasks.append({"id": name, "path": str(path.relative_to(root)), "sha256": asset["sha256"]})
    clean = {"id": "clean", "path": "samples/clean.png", "sha256": file_hash(root / "samples/clean.png")}
    profile = root / "config/model-mac-instruct.json"
    load_profile(profile)
    protocol = {"version": VERSION, "source_sha256": source_hashes(root), "parser_image": parser_image,
        "model_profile": str(profile.relative_to(root)), "model_profile_sha256": file_hash(profile),
        "host": {"platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version()},
        "host_workloads": host_workloads, "cold_launches": 3, "warm_uploads": 10,
        "host_sleep_policy": "On macOS the runner owns caffeinate -i for its lifetime. A greater-than-five-second "
                             "wall/monotonic divergence invalidates controlled timing; lid closure/forced sleep is not prevented.",
        "clean": clean, "queue": tasks, "warmup_uploads": 1,
        "rules_warm_p95_budget_seconds": 60, "deadline_seconds": 1800,
        "cache_policy": "New Python/model processes for cold launches; OS file caches and Docker VM/image remain warm. "
                        "One untimed clean warmup before warm uploads. Every upload creates a new document; "
                        "no parser checkpoint reuse and no retries. No OS cache purge or Docker VM reboot.",
        "resources": {"parser_memory_bytes": 1024**3, "parser_cpus": 2, "parser_pids": 64,
                      "parser_deadline_seconds": 600, "artifact_growth_bytes": 20 * 1024**3,
                      "disk_reserve_bytes": 256 * 1024**2},
        "memory_method": {"host_sample_interval_seconds": .25, "container_sample_interval_seconds": 1,
            "components": "Application process-tree RSS excluding llama; llama RSS separately; Docker backend RSS "
                          "includes VM/support overhead. Container working set nested inside Docker, never summed with VM RSS.",
            "limits": "Sampled lower bounds, not exact peaks. Process RSS may share pages and does not measure Metal "
                      "allocations or unified-memory ownership. Docker backend RSS is not an exclusive VM allocation. "
                      "No aggregate whole-application peak or GPU-memory claim. Missing samples remain explicit.",
            "host_headroom": "vm_stat free plus speculative pages only; not total reclaimable/available RAM. "
                             "Host load and memory_pressure free percentage sampled; not dedicated idle hardware."}}
    output.mkdir(parents=True)
    write(output / "protocol.json", protocol)
    return protocol


def read_protocol(root, directory):
    protocol = json.loads((directory / "protocol.json").read_text())
    if protocol["version"] != VERSION or protocol["source_sha256"] != source_hashes(root):
        raise ValueError("Implementation changed after performance freeze")
    for task in [protocol["clean"], *protocol["queue"]]:
        path = root / task["path"]
        if not path.resolve().is_relative_to(root) or path.is_symlink() or file_hash(path) != task["sha256"]:
            raise ValueError("Frozen input changed")
    if file_hash(root / protocol["model_profile"]) != protocol["model_profile_sha256"]:
        raise ValueError("Frozen model profile changed")
    if (protocol["cold_launches"], protocol["warm_uploads"], protocol["warmup_uploads"],
        [task["id"] for task in protocol["queue"]]) != (3, 10, 1, list(DOCUMENTS)):
        raise ValueError("Performance schedule changed")
    return protocol


def summarize(protocol, variant, measurements):
    if variant not in ("ocr_rules", "span_llm"):
        raise ValueError("Unknown performance variant")
    expected = [f"cold-{n}" for n in range(3)] + ["warmup"] + [f"warm-{n}" for n in range(10)]
    if variant == "ocr_rules":
        expected += [f"queue-{n}" for n in range(20)]
    if [row["id"] for row in measurements] != expected:
        raise ValueError("Missing, duplicate or reordered scheduled performance outcomes")
    for row in measurements:
        if row["status"] not in ("REVIEW_READY", "FAILED", "REJECTED", "CANCELLED"):
            raise ValueError("Unfinished performance outcome")
        if (row["status"] != "REVIEW_READY") != bool(row["error_code"]):
            raise ValueError("Performance failure category mismatch")
        for name in ("upload_to_terminal_seconds", "startup_seconds"):
            value = row[name]
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid performance duration")
        if row.get("attempt_count") != 1 or row.get("checkpoint_reused") is not False:
            raise ValueError("Performance outcome needs one fresh attempt without checkpoint reuse")
        if row["id"].startswith("cold-") and (type(row.get("cold_workflow_seconds")) not in (int, float) or
                not math.isfinite(row["cold_workflow_seconds"]) or
                row["cold_workflow_seconds"] < row["startup_seconds"] + row["upload_to_terminal_seconds"]):
            raise ValueError("Cold workflow excludes startup or upload time")
        for name, value in row["stages_seconds"].items():
            if name not in ("parsing", "extracting", "checking") or type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError("Invalid stage timing")
    groups = {}
    for group in ("cold", "warm", "queue"):
        rows = [row for row in measurements if row["id"].startswith(group + "-")]
        if not rows:
            continue
        groups[group] = {"scheduled": len(rows), "ready": sum(row["status"] == "REVIEW_READY" for row in rows),
            "failed": sum(row["status"] != "REVIEW_READY" for row in rows),
            "upload_to_terminal": distribution([row["upload_to_terminal_seconds"] for row in rows]),
            "startup": distribution([row["startup_seconds"] for row in rows]),
            "cold_workflow": distribution([row["cold_workflow_seconds"] for row in rows]) if group == "cold" else None,
            "stages": {stage: distribution([row["stages_seconds"][stage] for row in rows if stage in row["stages_seconds"]])
                       for stage in ("parsing", "extracting", "checking")}}
    if "queue" in groups:
        rows = [row for row in measurements if row["id"].startswith("queue-")]
        starts = [row["processing_started_at_seconds"] for row in rows]
        ends = [row["processing_finished_at_seconds"] for row in rows]
        ordered = sorted(zip(starts, ends))
        if any(start is None or end is None or end < start for start, end in ordered) or any(
                right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
            raise ValueError("Queue attempt intervals are missing or overlap")
        drain = max(row["terminal_since_batch_start_seconds"] for row in rows)
        pages = sum(row["page_count"] for row in rows if row["status"] == "REVIEW_READY")
        groups["queue"].update({"batch_start_to_drain_seconds": drain, "pages_ready": pages,
                               "pages_per_minute": round(pages * 60 / drain, 3) if drain else None,
                               "max_observed_active": max(row["max_observed_active"] for row in rows)})
    warm = groups["warm"]
    return {"groups": groups, "rules_latency_gate": ("pass" if warm["failed"] == 0 and
            warm["upload_to_terminal"]["p95_seconds"] <= protocol["rules_warm_p95_budget_seconds"] else "fail")
            if variant == "ocr_rules" else "not_applicable",
            "scheduled": len(measurements), "failures": sum(bool(row["error_code"]) for row in measurements),
            "memory_acceptance": "pending: sampled component RSS cannot establish whole-application unified-memory peak"}
