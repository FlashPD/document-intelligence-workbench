"""Freeze and measure cold/warm production uploads through separate server processes."""
from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from urllib.parse import quote

from docwork.intake import IntakeStore, MIME_BY_SUFFIX
from docwork.model_runtime import file_hash, load_profile, managed_server
from docwork.operations import seconds, snapshot
from docwork.performance import VERSION, freeze, read_protocol, source_hashes, summarize, write
from docwork.web import ReviewServer
from docwork.worker import PARSER_IMAGE, _docker_image_id

ROOT = Path(__file__).resolve().parents[1]


def command(args, timeout=3):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=True).stdout


class MemorySampler:
    def __init__(self, application_pid):
        self.pid, self.stop = application_pid, threading.Event()
        self.host, self.containers = [], []
        self.errors = {"host": 0, "container": 0}
        self.started = time.monotonic()
        self.wall_started = time.time()
        self.threads = [threading.Thread(target=self.sample_host, daemon=True),
                        threading.Thread(target=self.sample_container, daemon=True)]

    def start(self):
        for thread in self.threads:
            thread.start()

    def sample_host(self):
        while not self.stop.is_set():
            try:
                rows = []
                for line in command(["ps", "-axo", "pid=,ppid=,rss=,comm="]).splitlines():
                    pid, parent, rss, name = line.strip().split(None, 3)
                    rows.append((int(pid), int(parent), int(rss) * 1024, name))
                descendants = {self.pid}
                for _ in rows:
                    added = {pid for pid, parent, _, _ in rows if parent in descendants}
                    if added <= descendants:
                        break
                    descendants |= added
                sample = {"elapsed_seconds": round(time.monotonic() - self.started, 3),
                    "wall_elapsed_seconds": round(time.time() - self.wall_started, 3),
                    "application_rss_bytes": sum(rss for pid, _, rss, name in rows if pid in descendants and "llama-server" not in name),
                    "model_rss_bytes": sum(rss for pid, _, rss, name in rows if pid in descendants and "llama-server" in name),
                    "docker_backend_rss_bytes": sum(rss for _, _, rss, name in rows if "com.docker.backend" in name),
                    "competing_model_processes": sum("llama-server" in name and pid not in descendants for pid, _, _, name in rows),
                    "load_average": list(os.getloadavg())}
                if platform.system() == "Darwin":
                    vm = command(["vm_stat"])
                    page_bytes = int(re.search(r"page size of (\d+) bytes", vm)[1])
                    free = int(re.search(r"Pages free:\s+(\d+)", vm)[1])
                    speculative = int(re.search(r"Pages speculative:\s+(\d+)", vm)[1])
                    sample["host_free_plus_speculative_bytes"] = (free + speculative) * page_bytes
                    # Query-only mode: never induce pressure or allocate memory.
                    pressure = command(["memory_pressure", "-Q"])
                    sample["host_free_percentage"] = int(re.search(r"free percentage:\s+(\d+)%", pressure)[1])
                self.host.append(sample)
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                self.errors["host"] += 1
            self.stop.wait(.25)

    def sample_container(self):
        while not self.stop.is_set():
            try:
                raw = command(["docker", "stats", "--no-stream", "--format", "{{json .}}"], timeout=5)
                values = []
                for line in raw.splitlines():
                    row = json.loads(line)
                    if row["Name"].startswith("docwork-"):
                        value = row["MemUsage"].split(" / ")[0]
                        match = re.fullmatch(r"([0-9.]+)(B|KiB|MiB|GiB|kB|MB|GB)", value)
                        multiplier = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3,
                                      "kB": 1000, "MB": 1000**2, "GB": 1000**3}[match[2]]
                        values.append(round(float(match[1]) * multiplier))
                self.containers.append({"elapsed_seconds": round(time.monotonic() - self.started, 3),
                                        "active_container_count": len(values), "working_set_bytes": sum(values)})
            except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
                self.errors["container"] += 1
            self.stop.wait(1)

    def close(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(timeout=7)
        result = {"host_samples": self.host, "container_samples": self.containers, "sampling_errors": self.errors}
        result["sampled_component_peaks_bytes"] = {name: max((row[name] for row in self.host), default=None)
            for name in ("application_rss_bytes", "model_rss_bytes", "docker_backend_rss_bytes")}
        result["sampled_component_peaks_bytes"]["parser_working_set_bytes"] = max(
            (row["working_set_bytes"] for row in self.containers if row["active_container_count"]), default=None)
        return result


class Client:
    def __init__(self, port, token):
        self.port, self.cookie = port, "docwork_session=" + token

    def call(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            connection.request(method, path, body, {"Cookie": self.cookie,
                "Origin": f"http://127.0.0.1:{self.port}", **(headers or {})})
            response = connection.getresponse()
            raw = response.read()
            if response.status not in (200, 201, 202):
                raise RuntimeError(f"HTTP_{response.status}")
            return json.loads(raw)
        finally:
            connection.close()


@contextmanager
def launch(protocol_dir, workbench, variant, memory_reports):
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "child", str(protocol_dir),
        "--workbench", str(workbench), "--variant", variant], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, cwd=ROOT)
    sampler = MemorySampler(process.pid)
    sampler.start()
    ready, handshake = threading.Event(), []

    def receive():
        # Session credentials travel only through the private pipe and are never retained.
        line = process.stdout.readline()
        try:
            handshake.append(json.loads(line))
        except ValueError:
            pass
        ready.set()

    reader = threading.Thread(target=receive, daemon=True)
    reader.start()
    try:
        if not ready.wait(180) or not handshake:
            raise RuntimeError("STARTUP_FAILED")
        client = Client(handshake[0]["port"], handshake[0]["token"])
        startup = round(time.monotonic() - started, 6)
        # Readiness probe is recorded separately and excluded from startup stopwatch.
        readiness = client.call("GET", "/readyz")
        if not readiness["ready"] or (variant == "span_llm" and readiness["model"] != "ready"):
            raise RuntimeError("NOT_READY")
        yield client, startup, readiness, started
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        memory = sampler.close()
        memory["clean_shutdown"] = process.returncode == 0
        if variant == "span_llm" and (workbench / "runtime.json").is_file():
            memory["model_runtime"] = json.loads((workbench / "runtime.json").read_text())
        memory_reports.append(memory)
        process.stdout.close()
        reader.join(timeout=1)


def submit(client, task, variant):
    started = time.monotonic()
    source = ROOT / task["path"]
    result = client.call("POST", "/api/upload", source.read_bytes(), {
        "Content-Type": MIME_BY_SUFFIX[source.suffix.lower()], "X-File-Name": quote(source.name), "X-Extractor": variant})
    return result["document_id"], started


def terminal(client, store, doc_id, started, protocol, *, batch_started=None):
    max_active = 0
    while True:
        state = client.call("GET", f"/api/documents/{doc_id}/status")
        with store._connect() as db:
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE status='PROCESSING'").fetchone()[0]
        max_active = max(max_active, active)
        if state["status"] in ("REVIEW_READY", "FAILED", "REJECTED", "CANCELLED"):
            break
        if time.monotonic() - started > protocol["deadline_seconds"]:
            client.call("POST", f"/api/documents/{doc_id}/cancel", b"{}", {"Content-Type": "application/json"})
            raise RuntimeError("PERFORMANCE_DEADLINE")
        time.sleep(.05)
    finished = time.monotonic()
    attempts = store.attempts(doc_id)
    last = attempts[-1]
    with store._connect() as db:
        reused = bool(db.execute("SELECT 1 FROM review_events WHERE document_id=? AND kind='parser_checkpoint_reused'",
                                 (doc_id,)).fetchone())
        events = db.execute("SELECT detail,created_at FROM review_events WHERE document_id=? AND kind='processing_stage' ORDER BY id",
                            (doc_id,)).fetchall()
    stage_times = {}
    for n, event in enumerate(events):
        stage = json.loads(event["detail"])["stage"]
        end = seconds(events[n + 1]["created_at"]) if n + 1 < len(events) else seconds(last["finished_at"])
        stage_times[stage.lower()] = end - seconds(event["created_at"])
    return {"document_id": doc_id, "status": state["status"],
            "error_code": state["job"]["error_code"] if state["status"] != "REVIEW_READY" else None,
            "page_count": state["page_count"], "attempt_count": len(attempts), "checkpoint_reused": reused,
            "upload_to_terminal_seconds": round(finished - started, 6), "stages_seconds": stage_times,
            "processing_started_at_seconds": seconds(last["started_at"]),
            "processing_finished_at_seconds": seconds(last["finished_at"]),
            "max_observed_active": max_active,
            "terminal_since_batch_start_seconds": round(finished - batch_started, 6) if batch_started else None}


def run(protocol_dir, output, variant):
    protocol = read_protocol(ROOT, protocol_dir)
    if _docker_image_id(PARSER_IMAGE) != protocol["parser_image"]:
        raise ValueError("Frozen parser image changed")
    if "llama-server" in command(["ps", "-axo", "comm="]):
        raise ValueError("Stop unrelated inference before controlled measurements")
    output.mkdir(parents=True)
    measurements, memories, readiness_reports = [], [], []
    report = {"version": VERSION, "variant": variant, "protocol_sha256": file_hash(protocol_dir / "protocol.json"),
              "status": "interrupted", "measurements": measurements, "readiness": readiness_reports,
              "memory": memories, "docker_vm_memory_limit_bytes": int(command(["docker", "info", "--format", "{{.MemTotal}}"], timeout=15)),
              "host_physical_memory_bytes": int(command(["sysctl", "-n", "hw.memsize"])) if platform.system() == "Darwin" else None,
              "idle_sleep_inhibited": platform.system() == "Darwin"}
    try:
        for n in range(3):
            workbench = output / f"cold-{n}"
            print(f"{variant}: cold launch {n + 1}/3", flush=True)
            with launch(protocol_dir, workbench, variant, memories) as (client, startup, readiness, launch_started):
                readiness_reports.append(readiness)
                doc_id, started = submit(client, protocol["clean"], variant)
                store = IntakeStore(workbench / "review.sqlite", workbench / "objects")
                row = terminal(client, store, doc_id, started, protocol)
                measurements.append({"id": f"cold-{n}", "startup_seconds": startup,
                                     "cold_workflow_seconds": round(time.monotonic() - launch_started, 6), **row})
                write(output / f"cold-{n}-metrics.json", snapshot(store))
        workbench = output / "warm"
        with launch(protocol_dir, workbench, variant, memories) as (client, startup, readiness, launch_started):
            readiness_reports.append(readiness)
            store = IntakeStore(workbench / "review.sqlite", workbench / "objects")
            for name in ["warmup", *[f"warm-{n}" for n in range(10)]]:
                print(f"{variant}: {name}", flush=True)
                doc_id, started = submit(client, protocol["clean"], variant)
                row = terminal(client, store, doc_id, started, protocol)
                measurements.append({"id": name, "startup_seconds": 0, **row})
            if variant == "ocr_rules":
                batch_started = time.monotonic()
                batch = client.call("POST", "/api/batches", json.dumps({"count": 20}).encode(), {"Content-Type": "application/json"})
                submitted = []
                for n, task in enumerate(protocol["queue"]):
                    started = time.monotonic()
                    source = ROOT / task["path"]
                    state = client.call("POST", "/api/upload", source.read_bytes(), {
                        "Content-Type": MIME_BY_SUFFIX[source.suffix], "X-File-Name": quote(source.name),
                        "X-Extractor": variant, "X-Batch-Id": batch["batch_id"], "X-Batch-Position": str(n)})
                    submitted.append((state["document_id"], started))
                for n, (doc_id, started) in enumerate(submitted):
                    print(f"ocr_rules: queue {n + 1}/20", flush=True)
                    row = terminal(client, store, doc_id, started, protocol, batch_started=batch_started)
                    measurements.append({"id": f"queue-{n}", "startup_seconds": 0, **row})
            write(output / "warm-metrics.json", snapshot(store))
        read_protocol(ROOT, protocol_dir)
        if _docker_image_id(PARSER_IMAGE) != protocol["parser_image"]:
            raise ValueError("Parser changed during measurement")
        report["summary"] = summarize(protocol, variant, measurements)
        if any(not memory["clean_shutdown"] or (variant == "span_llm" and not memory.get("model_runtime", {}).get("shutdown_complete")) or
               any(sample["competing_model_processes"] or abs(sample["wall_elapsed_seconds"] - sample["elapsed_seconds"]) > 5
                   for sample in memory["host_samples"])
               for memory in memories):
            raise ValueError("Unclean shutdown or competing inference invalidates controlled measurement")
        report["status"] = "complete"
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
    finally:
        write(output / "measurements.json", measurements)
        write(output / "memory.json", memories)
        report["artifacts_sha256"] = {str(path.relative_to(output)): file_hash(path)
            for path in sorted([*output.glob("*-metrics.json"), output / "measurements.json", output / "memory.json"])}
        write(output / "report.json", report)
    return report


def child(protocol_dir, workbench, variant):
    protocol = read_protocol(ROOT, protocol_dir)
    context = managed_server(ROOT, load_profile(ROOT / protocol["model_profile"]), workbench / "model.log") if variant == "span_llm" else nullcontext((None, None))
    runtime = None
    try:
        with context as (config, runtime):
            store = IntakeStore(workbench / "review.sqlite", workbench / "objects")
            with ReviewServer(("127.0.0.1", 0), store, model_config=config, background_processing=True) as server:
                print(json.dumps({"port": server.server_port, "token": server.token}), flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
    finally:
        if runtime is not None:
            write(workbench / "runtime.json", runtime)


def verify(protocol_dir, directory):
    protocol = json.loads((protocol_dir / "protocol.json").read_text())
    report = json.loads((directory / "report.json").read_text())
    if report["version"] != VERSION or report["status"] != "complete" or report["protocol_sha256"] != file_hash(protocol_dir / "protocol.json"):
        raise ValueError("Incomplete or mismatched performance report")
    if report["summary"] != summarize(protocol, report["variant"], report["measurements"]):
        raise ValueError("Performance summary changed")
    if (report["measurements"] != json.loads((directory / "measurements.json").read_text()) or
            report["memory"] != json.loads((directory / "memory.json").read_text())):
        raise ValueError("Raw performance measurements changed")
    if len(report["memory"]) != 4 or len(report["readiness"]) != 4:
        raise ValueError("Missing launch evidence")
    for memory in report["memory"]:
        if not memory["clean_shutdown"] or (report["variant"] == "span_llm" and
                not memory.get("model_runtime", {}).get("shutdown_complete")):
            raise ValueError("Unclean launch shutdown")
        for sample in memory["host_samples"]:
            if sample["competing_model_processes"] or abs(sample["wall_elapsed_seconds"] - sample["elapsed_seconds"]) > 5:
                raise ValueError("Clock/suspension or competing inference invalidates controlled timing")
    for name, digest in report["artifacts_sha256"].items():
        path = directory / name
        if not path.resolve().is_relative_to(directory.resolve()) or path.is_symlink() or file_hash(path) != digest:
            raise ValueError("Metric artifact changed")
    return {"status": "verified", "source_current": protocol["source_sha256"] == source_hashes(ROOT),
            "summary": report["summary"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    frozen = actions.add_parser("freeze")
    frozen.add_argument("--host-workloads", required=True)
    frozen.add_argument("--output-dir", type=Path, required=True)
    for name in ("run", "child", "verify"):
        action = actions.add_parser(name)
        action.add_argument("protocol", type=Path)
        if name == "verify":
            action.add_argument("directory", type=Path)
        else:
            action.add_argument("--variant", choices=("ocr_rules", "span_llm"), required=True)
            action.add_argument("--workbench" if name == "child" else "--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "verify":
            result = verify(args.protocol, args.directory)
        else:
            output = (args.workbench if args.action == "child" else args.output_dir).absolute()
            if output.exists() or not output.is_relative_to(ROOT / "artifacts") or any(path.is_symlink() for path in [output, *output.parents]):
                raise ValueError("Use a new runtime directory under artifacts without symlinks")
            if args.action == "freeze":
                result = freeze(ROOT, output, parser_image=_docker_image_id(PARSER_IMAGE), host_workloads=args.host_workloads)
            elif args.action == "child":
                child(args.protocol, output, args.variant)
                return 0
            else:
                sleep_guard = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())]) if platform.system() == "Darwin" else None
                try:
                    if sleep_guard is not None and sleep_guard.poll() is not None:
                        raise RuntimeError("IDLE_SLEEP_GUARD_UNAVAILABLE")
                    result = run(args.protocol, output, args.variant)
                finally:
                    if sleep_guard is not None:
                        sleep_guard.terminate()
                        sleep_guard.wait(timeout=5)
        print(json.dumps({"status": result.get("status", "frozen"), "summary": result.get("summary")}, indent=2))
        return 0 if result.get("status", "frozen") in ("complete", "verified", "frozen") else 2
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"Performance command failed: {type(exc).__name__}\n")


if __name__ == "__main__":
    raise SystemExit(main())
