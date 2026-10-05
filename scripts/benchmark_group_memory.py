"""Extend the frozen upload workload with separately bound native group evidence."""
from __future__ import annotations

import argparse
from contextlib import closing
import gzip
import json
import os
import platform
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import benchmark_performance as performance
from group_memory import METHOD, GroupSampler, require, summarize
from docwork.model_runtime import file_hash
from docwork.performance import write
from docwork.worker import PARSER_IMAGE, _docker_image_id

ROOT = Path(__file__).resolve().parents[1]
EXTRA_SOURCES = ("scripts/group_memory.py", "scripts/benchmark_group_memory.py", "tests/test_group_memory.py")


def extra_hashes():
    return {name: file_hash(ROOT / name) for name in EXTRA_SOURCES}


def freeze(output, host_workloads):
    require(platform.system() == "Darwin", "Native group measurements require macOS")
    protocol = performance.freeze(ROOT, output, parser_image=_docker_image_id(PARSER_IMAGE), host_workloads=host_workloads)
    group = {"version": METHOD, "performance_protocol_sha256": file_hash(output / "protocol.json"),
             "source_sha256": extra_hashes(), "utility_sha256": file_hash(Path("/usr/bin/footprint")),
             "sample_interval_seconds": .5, "max_complete_sample_gap_seconds": 5,
             "error_accounting": "classified-v1",
             "group_accounted_budget_bytes": 12 * 1024**3,
             "ownership": "All server descendants, same-user Docker backends/controllers, and exactly one VM service "
                          "whose open backing store matches Docker's vms/0/data/Docker.raw. No other active containers "
                          "beyond this run's observer and its database-bound job/fence names. No unrelated model inference.",
             "method": "Native footprint JSON in byte units with category/shared-mapping/charged-ledger data. "
                       "Report observed maxima of native de-duplicated dirty, clean and reclaimable categories. "
                       "Swapped and wired are subsets; guest/container memory is nested and never added. "
                       "GPU/IOKit/unmapped/nofootprint categories are retained where returned. System auxiliary "
                       "footprint is logical charged dirty memory, can exceed physical RAM, and stays separate.",
             "limits": "Includes all Docker guest/kernel/cache/observer overhead, not incremental app-only cost. "
                       "Global shared OS cache, unattributed system drivers, other-user services and browser are excluded. "
                       "Samples are inspection windows, not atomic machine snapshots or continuous peaks. "
                       "A sampled twelve-GiB budget is diagnostic, not an OS-enforced whole-application cap.",
             "host_workloads": host_workloads}
    write(output / "group-protocol.json", group)
    write(output / "group-source-snapshot.json", {name: (ROOT / name).read_text() for name in EXTRA_SOURCES})
    return {"status": "frozen", "performance_protocol": protocol["version"], "group_protocol": METHOD}


def read_group(protocol_dir, *, current):
    protocol = json.loads((protocol_dir / "group-protocol.json").read_text())
    require(protocol["version"] == METHOD and protocol["performance_protocol_sha256"] == file_hash(protocol_dir / "protocol.json"), "Group/performance protocol differs")
    require(protocol["sample_interval_seconds"] == .5 and protocol["max_complete_sample_gap_seconds"] == 5 and
            protocol["group_accounted_budget_bytes"] == 12 * 1024**3, "Group workload/budget changed")
    require(protocol.get("error_accounting") in (None, "classified-v1"), "Unknown group error-accounting method")
    snapshot = json.loads((protocol_dir / "group-source-snapshot.json").read_text())
    import hashlib
    require(set(snapshot) == set(EXTRA_SOURCES) and
            {name: hashlib.sha256(value.encode()).hexdigest() for name, value in snapshot.items()} == protocol["source_sha256"], "Group source snapshot differs")
    if current:
        require(protocol["source_sha256"] == extra_hashes() and
                protocol["utility_sha256"] == file_hash(Path("/usr/bin/footprint")), "Group implementation/utility changed after freeze")
    return protocol


def allowed_names(output, observer):
    names = {observer}
    for name in ("cold-0", "cold-1", "cold-2", "warm"):
        database = output / name / "review.sqlite"
        if database.is_file():
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
                try:
                    names |= {f"docwork-{job[:16]}-{fence}" for job, fence in db.execute("SELECT id,fence FROM jobs")}
                except sqlite3.Error as error:
                    raise ValueError("Job ownership database unavailable") from error
    return names


def group_summary(protocol, report, output):
    require(report["status"] == "complete" and len(report["memory"]) == 4, "Base workload did not complete")
    launches = []
    artifacts = {}
    for memory in report["memory"]:
        value = memory["native_group"]
        require(value["method"] == METHOD and value["clean_shutdown"], "Native sampler did not close")
        require(not any(error["category"] == "AttributionViolation" for error in value["errors"]), "Competing workload invalidates group ownership")
        relative = Path(value["artifact"])
        require(len(relative.parts) == 1 and not relative.is_absolute() and relative.suffix == ".gz", "Unsafe group artifact path")
        path = output / relative
        require(value["artifact"] not in artifacts, "Duplicate native launch artifact")
        require(path.is_file() and not path.is_symlink(), "Missing/unsafe group artifact")
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            samples = json.load(stream)
        require(len(samples) == value["samples"], "Native sample count differs")
        require(type(value["application_pid"]) is int and value["application_pid"] > 0 and all(
            any(item["pid"] == value["application_pid"] and item["component"] == "application" for item in sample["selection"])
            for sample in samples), "Native samples omit owning application")
        launch = summarize(samples)
        launch["query_errors"] = len(value["errors"])
        if protocol.get("error_accounting") == "classified-v1":
            launch["query_error_categories"] = {key: sum(error["category"] == key for error in value["errors"])
                                                for key in sorted({error["category"] for error in value["errors"]})}
        launch["observed_budget_result"] = "pass" if launch["max_observed_group_accounted_bytes"] <= protocol["group_accounted_budget_bytes"] else "fail"
        launch["sampling_gap_result"] = "pass" if launch["max_sample_gap_seconds"] <= protocol["max_complete_sample_gap_seconds"] else "fail"
        if report["variant"] == "span_llm":
            require(launch["model_samples"] > 0, "Missing native model observations")
        launches.append(launch)
        artifacts[value["artifact"]] = file_hash(path)
    return {"method": METHOD, "launches": launches, "artifacts": artifacts,
            "group_accounted_budget_bytes": protocol["group_accounted_budget_bytes"],
            "max_observed_group_accounted_bytes": max(row["max_observed_group_accounted_bytes"] for row in launches),
            "max_observed_group_dirty_bytes": max(row["max_observed_group_dirty_bytes"] for row in launches),
            "max_observed_accounted_resident_bytes": max(row["max_observed_accounted_resident_bytes"] for row in launches),
            "all_sampled_budgets_pass": all(row["observed_budget_result"] == "pass" for row in launches),
            "all_sampling_gaps_pass": all(row["sampling_gap_result"] == "pass" for row in launches),
            "whole_memory_acceptance": "pending: inspect ownership, query errors, stage coverage and excluded "
                                       "kernel/shared-cache/driver boundaries before closing G12. Sampled native group "
                                       "accounting is not an exact continuous application-exclusive physical-memory peak."}


def run(protocol_dir, output, variant):
    require(not output.exists(), "Choose a new output directory")
    protocol = read_group(protocol_dir, current=True)
    base_class = performance.MemorySampler

    class Sampler(base_class):
        def __init__(self, pid, image):
            super().__init__(pid, image)
            self.group = GroupSampler(pid, output / f"native-group-{pid}.json.gz",
                                      lambda: allowed_names(output, self.guest.name), interval=protocol["sample_interval_seconds"])

        def start(self):
            super().start()
            self.group.start()

        def close(self):
            try:
                native = self.group.close()
            except Exception as error:
                native = {"method": METHOD, "clean_shutdown": False, "error": type(error).__name__}
            finally:
                result = super().close()
            result["native_group"] = native
            return result

    result = {"version": METHOD, "status": "failed", "variant": variant,
              "protocol_sha256": file_hash(protocol_dir / "group-protocol.json")}
    try:
        with patch.object(performance, "MemorySampler", Sampler):
            base = performance.run(protocol_dir, output, variant)
        read_group(protocol_dir, current=True)
        result["summary"] = group_summary(protocol, base, output)
        result["base_report_sha256"] = file_hash(output / "report.json")
        result["source_unchanged"] = True
        result["status"] = "complete"
    except Exception as error:
        result["failure"] = {"type": type(error).__name__, "detail": str(error)}
    finally:
        output.mkdir(parents=True, exist_ok=True)
        write(output / "group-report.json", result)
    return result


def verify(protocol_dir, output):
    protocol = read_group(protocol_dir, current=False)
    base_verification = performance.verify(protocol_dir, output)
    result = json.loads((output / "group-report.json").read_text())
    require(result["version"] == METHOD and result["status"] == "complete" and result["source_unchanged"], "Incomplete group schedule")
    require(result["protocol_sha256"] == file_hash(protocol_dir / "group-protocol.json") and
            result["base_report_sha256"] == file_hash(output / "report.json"), "Group report binding differs")
    base = json.loads((output / "report.json").read_text())
    require(result["variant"] == base["variant"] and result["summary"] == group_summary(protocol, base, output), "Group summary differs")
    return {"status": "verified", "variant": result["variant"], "summary": result["summary"],
            "base_source_current": base_verification["source_current"],
            "group_source_current": protocol["source_sha256"] == extra_hashes()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    frozen = actions.add_parser("freeze")
    frozen.add_argument("--output-dir", type=Path, required=True)
    frozen.add_argument("--host-workloads", required=True)
    for action in ("run", "verify"):
        command = actions.add_parser(action)
        command.add_argument("protocol", type=Path)
        if action == "run":
            command.add_argument("--output-dir", type=Path, required=True)
            command.add_argument("--variant", choices=("ocr_rules", "span_llm"), required=True)
        else:
            command.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.action == "freeze":
        result = freeze(args.output_dir.resolve(), args.host_workloads)
    elif args.action == "verify":
        result = verify(args.protocol.resolve(), args.output.resolve())
    else:
        # Preserve the base runner's explicit idle-sleep policy, without inducing pressure.
        inhibitor = subprocess.Popen(["caffeinate", "-i"]) if platform.system() == "Darwin" else None
        try:
            result = run(args.protocol.resolve(), args.output_dir.resolve(), args.variant)
        finally:
            if inhibitor is not None:
                inhibitor.terminate()
                inhibitor.wait(timeout=10)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in ("complete", "verified", "frozen") else 2


if __name__ == "__main__":
    sys.exit(main())
