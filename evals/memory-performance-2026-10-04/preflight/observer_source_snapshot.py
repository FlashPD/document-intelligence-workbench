"""Read-only workload memory counters; guest and host views are never summed.

Darwin uses libproc RUSAGE_INFO_V4 from the public SDK, not task memory dumps.
The bounded observer reads Linux VM counters through the fixed parser image.
"""
from __future__ import annotations

import ctypes
import json
import math
import platform
import re
import subprocess
import threading
import time
import uuid


METHOD = "component-memory-v2"
RUSAGE_FIELDS = (
    "user_time system_time pkg_idle_wkups interrupt_wkups pageins wired_size resident_size phys_footprint "
    "proc_start_abstime proc_exit_abstime child_user_time child_system_time child_pkg_idle_wkups "
    "child_interrupt_wkups child_pageins child_elapsed_abstime diskio_bytesread diskio_byteswritten "
    "cpu_time_qos_default cpu_time_qos_maintenance cpu_time_qos_background cpu_time_qos_utility "
    "cpu_time_qos_legacy cpu_time_qos_user_initiated cpu_time_qos_user_interactive billed_system_time "
    "serviced_system_time logical_writes lifetime_max_phys_footprint instructions cycles billed_energy "
    "serviced_energy interval_max_phys_footprint runnable_time"
).split()


class UsageV4(ctypes.Structure):
    _fields_ = [("uuid", ctypes.c_uint8 * 16), *((name, ctypes.c_uint64) for name in RUSAGE_FIELDS)]


class DarwinFootprint:
    def __init__(self):
        if platform.system() != "Darwin":
            raise OSError("Darwin footprint counters unavailable on this host")
        self.library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.library.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        self.library.proc_pid_rusage.restype = ctypes.c_int

    def read(self, pid):
        value = UsageV4()
        if self.library.proc_pid_rusage(pid, 4, ctypes.byref(value)) != 0:
            code = ctypes.get_errno()
            raise OSError(code, "Process footprint unavailable")
        return {"pid": pid, "start_abstime": value.proc_start_abstime,
                "physical_footprint_bytes": value.phys_footprint,
                "lifetime_max_physical_footprint_bytes": value.lifetime_max_phys_footprint,
                "resident_bytes": value.resident_size, "wired_bytes": value.wired_size}


MEMINFO_KEYS = ("MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SReclaimable",
                "Shmem", "Slab", "KernelStack", "PageTables", "SwapTotal", "SwapFree")
OBSERVER_CODE = """
import json, re, time
from pathlib import Path
keys = set(%r)
while True:
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        match = re.fullmatch(r'([A-Za-z_()]+):\\s+(\\d+) kB', line)
        if match and match[1] in keys:
            values[match[1]] = int(match[2]) * 1024
    print(json.dumps({'guest_uptime_seconds': float(Path('/proc/uptime').read_text().split()[0]),
        'meminfo_bytes': values,
        'observer_memory_current_bytes': int(Path('/sys/fs/cgroup/memory.current').read_text()),
        'observer_memory_peak_bytes': int(Path('/sys/fs/cgroup/memory.peak').read_text())}), flush=True)
    time.sleep(.25)
""" % (MEMINFO_KEYS,)


def validate_guest(row):
    if set(row["meminfo_bytes"]) != set(MEMINFO_KEYS):
        raise ValueError("Guest VM memory counters incomplete")
    for name, value in row["meminfo_bytes"].items():
        if type(value) is not int or value < 0:
            raise ValueError(f"Invalid guest counter: {name}")
    for field in ("observer_memory_current_bytes", "observer_memory_peak_bytes"):
        if type(row[field]) is not int or row[field] < 0:
            raise ValueError("Invalid observer memory counter")
    values = row["meminfo_bytes"]
    if (values["MemTotal"] <= 0 or values["MemAvailable"] > values["MemTotal"] or
            values["MemFree"] > values["MemTotal"] or values["SwapFree"] > values["SwapTotal"] or
            row["observer_memory_peak_bytes"] < row["observer_memory_current_bytes"] or
            type(row["guest_uptime_seconds"]) not in (int, float) or
            not math.isfinite(row["guest_uptime_seconds"]) or row["guest_uptime_seconds"] < 0):
        raise ValueError("Inconsistent guest VM memory counters")
    return row


class GuestObserver:
    def __init__(self, image, origin):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError("Observer needs the immutable frozen parser image")
        self.name = "docwork-memory-" + uuid.uuid4().hex
        self.image, self.origin = image, origin
        self.samples, self.errors = [], 0
        self.process = self.thread = None
        self.clean_shutdown = False
        self.cleanup_failure = None
        self.creation_attempted = False

    def start(self):
        self.creation_attempted = True
        subprocess.run([
            "docker", "create", "--pull", "never", "--name", self.name, "--network", "none",
            "--read-only", "--user", "65534:65534", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--memory", "32m", "--memory-swap", "32m",
            "--cpus", "0.1", "--pids-limit", "16", "--entrypoint", "python",
            self.image, "-u", "-c", OBSERVER_CODE], capture_output=True, text=True, check=True, timeout=60)
        self.process = subprocess.Popen(["docker", "start", "--attach", self.name],
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.thread = threading.Thread(target=self.receive, daemon=True)
        self.thread.start()

    def receive(self):
        for line in self.process.stdout:
            try:
                row = validate_guest(json.loads(line))
                self.samples.append({"received_elapsed_seconds": round(time.monotonic() - self.origin, 6), **row})
            except (ValueError, KeyError, TypeError):
                self.errors += 1

    def close(self):
        if self.creation_attempted:
            try:
                # Signal only the named observer we created. No shell or daemon mounts.
                subprocess.run(["docker", "rm", "--force", self.name], capture_output=True, timeout=15, check=True)
                if self.process is not None:
                    self.process.wait(timeout=15)
                inspected = subprocess.run(["docker", "inspect", self.name], capture_output=True, text=True, timeout=15)
                self.clean_shutdown = (inspected.returncode == 1 and
                                       "no such" in inspected.stderr.lower() and bool(self.samples))
            except (OSError, subprocess.SubprocessError) as error:
                self.errors += 1
                self.cleanup_failure = type(error).__name__
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=5)
                if self.thread is not None:
                    self.thread.join(timeout=5)
                    if self.thread.is_alive():
                        self.errors += 1
                self.process.stdout.close()
        return {"samples": self.samples, "sampling_errors": self.errors, "clean_shutdown": self.clean_shutdown,
                "cleanup_failure": self.cleanup_failure,
                "scope": "Whole Linux VM meminfo, including daemon/kernel/cache and a bounded measurement observer; "
                         "parser working sets are nested. Guest occupancy is not host VM residency or app-exclusive use."}


def validate_process(row):
    if row["component"] not in ("application", "model", "docker_backend"):
        raise ValueError("Unknown footprint component")
    for name in ("pid", "start_abstime"):
        if type(row[name]) is not int or row[name] <= 0:
            raise ValueError("Unusable process identity")
    for name in ("physical_footprint_bytes", "lifetime_max_physical_footprint_bytes", "resident_bytes", "wired_bytes"):
        if type(row[name]) is not int or row[name] < 0:
            raise ValueError("Invalid physical-footprint counter")
    if row["lifetime_max_physical_footprint_bytes"] < row["physical_footprint_bytes"]:
        raise ValueError("Process high-water counter below current footprint")
    return row


def physical_sample(rows, descendants, reader):
    selected = {pid: "model" if "llama-server" in name else "application"
                for pid, _, _, name in rows if pid in descendants}
    selected.update({pid: "docker_backend" for pid, _, _, name in rows if "com.docker.backend" in name})
    values, errors = [], []
    for pid, component in sorted(selected.items()):
        try:
            value = reader.read(pid)
            if value["pid"] != pid:
                raise ValueError("Unusable process identity")
            values.append(validate_process({"component": component, **value}))
        except (OSError, ValueError, KeyError, TypeError):
            errors.append({"pid": pid, "component": component, "category": "unavailable_or_exited"})
    return {"processes": values, "errors": errors}


def summarize_memory(memories):
    components = {}
    for component in ("application", "model", "docker_backend"):
        samples, identities = [], {}
        for memory in memories:
            for sample in memory["host_samples"]:
                for row in sample.get("physical_footprint", {}).get("processes", []):
                    validate_process(row)
                    if row["component"] != component:
                        continue
                    samples.append(row["physical_footprint_bytes"])
                    key = (row["pid"], row["start_abstime"])
                    identities[key] = max(identities.get(key, 0), row["lifetime_max_physical_footprint_bytes"])
        components[component] = {"process_observations": len(samples), "process_identities": len(identities),
            "max_observed_single_process_footprint_bytes": max(samples, default=None),
            "max_observed_single_process_lifetime_high_water_bytes": max(identities.values(), default=None)}
    guests = [row for memory in memories for row in memory.get("guest_vm", {}).get("samples", [])]
    for row in guests:
        validate_guest(row)
    return {"method": METHOD, "components": components, "guest_observations": len(guests),
            "guest_vm_max_used_excluding_free_bytes": max((row["meminfo_bytes"]["MemTotal"] -
                row["meminfo_bytes"]["MemFree"] for row in guests), default=None),
            "guest_vm_max_used_excluding_available_bytes": max((row["meminfo_bytes"]["MemTotal"] -
                row["meminfo_bytes"]["MemAvailable"] for row in guests), default=None),
            "observer_max_cgroup_peak_bytes": max((row["observer_memory_peak_bytes"] for row in guests), default=None),
            "process_read_errors": sum(len(row.get("physical_footprint", {}).get("errors", []))
                for memory in memories for row in memory["host_samples"]),
            "guest_sampling_errors": sum(memory.get("guest_vm", {}).get("sampling_errors", 0) for memory in memories),
            "total_memory_acceptance": "pending: host VM residency/driver ownership and application-exclusive union "
                                       "are not established; guest and host observations are not summed",
            "limitations": "Single-process high-water counters include lifetime before observation, especially Docker. "
                           "They are not simultaneous totals or whole-workload peaks. Clean file-backed residency is "
                           "reported separately by RSS; footprint includes charged compressed/IOKit memory. Sampling "
                           "may miss short-lived processes and peaks. Guest occupancy includes shared VM overhead/cache."}
