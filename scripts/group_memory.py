"""Native macOS group accounting; never sum nested guest/container observations."""
from __future__ import annotations

import gzip
import json
import math
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from memory_accounting import DarwinFootprint

METHOD = "native-group-footprint-v1"
COUNTERS = ("dirty", "swapped", "clean", "reclaimable", "wired", "regions")
COMPONENTS = {"application", "model", "docker_support", "docker_vm"}
ERROR_REASONS = {
    "Application process unavailable": "application_unavailable",
    "Exactly one associated Docker VM is required": "vm_unavailable",
    "VM backing-store inspection unavailable": "backing_inspection_unavailable",
    "Native group query failed": "native_query_failed",
    "Native inspection is incomplete": "native_inspection_incomplete",
    "Process exited/reused during native query": "process_exited_or_reused",
    "Job ownership database unavailable": "job_registry_unavailable",
}


class AttributionViolation(ValueError):
    """A competing workload invalidates the accounting boundary for the run."""


def require(value, message):
    if not value:
        raise ValueError(message)


def counter(value):
    require(type(value) is int and value >= 0, "Invalid native memory counter")
    return value


def totals(data):
    require(set(data) == set(COUNTERS), "Incomplete native category counters")
    values = {name: counter(data[name]) for name in COUNTERS}
    require(values["swapped"] <= values["dirty"] and
            values["wired"] <= values["dirty"] + values["clean"] + values["reclaimable"], "Inconsistent native subsets")
    return values


def validate_native(data, selection):
    require(data["unit"] == "byte" and data["bytes per unit"] == 1, "Native accounting needs byte units")
    require(data["errors"] == [] and data["warnings"] == [], "Native inspection is incomplete")
    require(type(data["page size"]) is int and data["page size"] > 0, "Invalid native page size")
    identities = {item["pid"]: item for item in selection}
    require(len(identities) == len(selection) and bool(identities), "Duplicate/empty process selection")
    require(all(item["component"] in COMPONENTS and type(item["pid"]) is int and item["pid"] > 0 and
                type(item["start_abstime"]) is int and item["start_abstime"] > 0 for item in selection), "Invalid process identity")
    pids = [item["pid"] for item in data["processes"]]
    require(len(pids) == len(set(pids)) and set(pids) == set(identities), "Native process selection differs")
    for process in data["processes"]:
        counter(process["footprint"])
        current = counter(process["auxiliary"]["phys_footprint"])
        require(counter(process["auxiliary"]["phys_footprint_peak"]) >= current, "Invalid native high-water counter")
        for values in process["categories"].values():
            totals(values)
    for entry in data["shared"]:
        require(bool(entry["pids"]) and set(entry["pids"]) <= set(pids) and
                ("specific_to_pid" not in entry or entry["specific_to_pid"] in entry["pids"]), "Shared mapping has unknown process")
        for values in entry["categories"].values():
            totals(values)
    for values in data["summary"].values():
        totals(values)
    total = totals(data["summary"]["total"])
    require(counter(data["total footprint"]) == total["dirty"], "Native summary footprint differs")
    start, end = data["start_time"], data["end_time"]
    for name in ("mach_absolute_time_ns", "mach_continuous_time_ns", "wall_time_s"):
        require(type(start[name]) in (int, float) and type(end[name]) in (int, float) and
                math.isfinite(start[name]) and math.isfinite(end[name]) and 0 <= start[name] <= end[name], "Invalid native observation window")
    return total


def process_rows():
    raw = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss=,comm="], text=True, timeout=5)
    return [(int(pid), int(parent), int(rss) * 1024, name)
            for pid, parent, rss, name in (line.strip().split(None, 3) for line in raw.splitlines())]


def descendant_pids(rows, root):
    values = {root}
    for _ in rows:
        added = {pid for pid, parent, _, _ in rows if parent in values}
        if added <= values:
            break
        values |= added
    return values


def docker_vm_association(pid):
    expected = str(Path.home() / "Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw")
    result = subprocess.run(["/usr/sbin/lsof", "-n", "-Fn", "-p", str(pid)],
                            capture_output=True, text=True, timeout=10, check=False)
    require(result.returncode == 0, "VM backing-store inspection unavailable")
    return any(line == "n" + expected for line in result.stdout.splitlines())


class GroupSampler:
    def __init__(self, application_pid, output, allowed_containers, *, interval=.5):
        self.pid, self.output, self.allowed_containers = application_pid, output, allowed_containers
        self.interval, self.reader = interval, DarwinFootprint()
        self.origin = time.monotonic()
        self.samples, self.errors, self.associations = [], [], {}
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, name="native-group-memory", daemon=True)
        self.closed = False

    def select(self, rows):
        descendants = descendant_pids(rows, self.pid)
        selected = []
        for pid, _, _, name in rows:
            component = None
            if pid in descendants:
                component = "model" if Path(name).name == "llama-server" else "application"
            elif Path(name).name in ("com.docker.backend", "com.docker.virtualization"):
                component = "docker_support"
            elif Path(name).name == "com.apple.Virtualization.VirtualMachine":
                identity = self.reader.read(pid)
                key = (pid, identity["start_abstime"])
                if key not in self.associations:
                    self.associations[key] = docker_vm_association(pid)
                if self.associations[key]:
                    component = "docker_vm"
            if component:
                identity = self.reader.read(pid)
                selected.append({"pid": pid, "start_abstime": identity["start_abstime"], "component": component})
        require(any(item["pid"] == self.pid for item in selected), "Application process unavailable")
        vm_count = sum(item["component"] == "docker_vm" for item in selected)
        if vm_count > 1:
            raise AttributionViolation("Multiple associated Docker VMs")
        require(vm_count == 1, "Exactly one associated Docker VM is required")
        return sorted(selected, key=lambda item: item["pid"])

    def sample(self):
        selected = self.select(process_rows())
        containers = subprocess.check_output(["docker", "ps", "--format", "{{.Names}}"], text=True, timeout=5).splitlines()
        if not set(containers) <= self.allowed_containers():
            raise AttributionViolation("Unrelated container invalidates VM attribution")
        with tempfile.TemporaryDirectory(prefix="docwork-native-footprint-") as temporary:
            path = Path(temporary) / "native.json"
            command = ["/usr/bin/footprint", "--wired", "--swapped", "--sysFootprint", "-f", "bytes", "-j", str(path),
                       *[str(item["pid"]) for item in selected]]
            result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
            require(result.returncode == 0 and path.is_file(), "Native group query failed")
            data = json.loads(path.read_text())
        validate_native(data, selected)
        for item in selected:
            require(self.reader.read(item["pid"])["start_abstime"] == item["start_abstime"], "Process exited/reused during native query")
        return {"elapsed_seconds": time.monotonic() - self.origin, "selection": selected,
                "containers": containers, "native": data}

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                self.samples.append(self.sample())
            except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                # Error labels never contain process command arguments/document data.
                self.errors.append({"elapsed_seconds": time.monotonic() - self.origin,
                                    "category": type(error).__name__,
                                    "errno": error.errno if isinstance(error, OSError) else None,
                                    "reason": ERROR_REASONS.get(str(error), "query_or_counter_error")})
            self.stop.wait(self.interval)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=25)
        self.closed = not self.thread.is_alive()
        require(self.closed, "Native group observer did not stop")
        with gzip.open(self.output, "wt", encoding="utf-8") as stream:
            json.dump(self.samples, stream, separators=(",", ":"), allow_nan=False)
        return {"method": METHOD, "artifact": self.output.name, "samples": len(self.samples),
                "application_pid": self.pid,
                "errors": self.errors, "clean_shutdown": self.closed,
                "observation_seconds": time.monotonic() - self.origin}


def summarize(samples):
    require(bool(samples), "No complete native group observations")
    totals_list = []
    peaks = {}
    driver_categories = set()
    models = 0
    for row in samples:
        values = validate_native(row["native"], row["selection"])
        require(sum(item["component"] == "docker_vm" for item in row["selection"]) == 1 and
                any(item["component"] == "application" for item in row["selection"]), "Missing workload/VM boundary")
        identities = {item["pid"]: item for item in row["selection"]}
        models += any(item["component"] == "model" for item in row["selection"])
        for process in row["native"]["processes"]:
            identity = identities[process["pid"]]
            key = (identity["component"], identity["pid"], identity["start_abstime"])
            peaks[key] = max(peaks.get(key, 0), process["auxiliary"]["phys_footprint_peak"])
        driver_categories |= {name for name in row["native"]["summary"]
                              if any(term in name.lower() for term in ("iokit", "graphics", "unmapped", "nofootprint"))}
        totals_list.append(values)
    times = [row["elapsed_seconds"] for row in samples]
    require(all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in times) and
            all(left <= right for left, right in zip(times, times[1:])), "Invalid group observation timeline")
    return {"method": METHOD, "complete_samples": len(samples), "model_samples": models,
            "first_sample_seconds": times[0], "last_sample_seconds": times[-1],
            "max_sample_gap_seconds": max((right - left for left, right in zip(times, times[1:])), default=0),
            "max_observed_group_dirty_bytes": max(row["dirty"] for row in totals_list),
            "max_observed_group_swapped_bytes": max(row["swapped"] for row in totals_list),
            "max_observed_accounted_resident_bytes": max(row["dirty"] - row["swapped"] + row["clean"] + row["reclaimable"] for row in totals_list),
            "max_observed_group_accounted_bytes": max(row["dirty"] + row["clean"] + row["reclaimable"] for row in totals_list),
            "max_observed_group_wired_bytes": max(row["wired"] for row in totals_list),
            "process_lifetime_dirty_upper_bound_bytes": sum(peaks.values()),
            "driver_category_names": sorted(driver_categories),
            "limits": "Native dirty/clean/reclaimable mapping categories are de-duplicated across selected user tasks. "
                      "Swapped is a dirty subset charged at original size; wired is not added. Clean excludes globally shared OS cache. "
                      "Docker VM includes guest/kernel/cache/observer; guest/container counters are nested and not added. "
                      "Lifetime dirty upper bound sums maxima across process identities, can include pre-workload Docker use, "
                      "and is not a simultaneous peak or resident-memory bound. Unattributed kernel/driver/other-user services "
                      "and the browser are outside the selected group. Sampled maxima are lower bounds, not continuous peaks."}
