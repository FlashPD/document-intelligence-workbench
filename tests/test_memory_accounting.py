import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("memory_accounting", Path(__file__).resolve().parents[1] / "scripts/memory_accounting.py")
MEMORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MEMORY)


def process(pid=1, start=10, current=100, peak=200):
    return {"pid": pid, "start_abstime": start, "physical_footprint_bytes": current,
            "lifetime_max_physical_footprint_bytes": peak, "resident_bytes": 1000, "wired_bytes": 10}


def guest():
    return {"guest_uptime_seconds": 10.5, "meminfo_bytes": {**dict.fromkeys(MEMORY.MEMINFO_KEYS, 0),
        "MemTotal": 1000, "MemFree": 300, "MemAvailable": 500},
        "observer_memory_current_bytes": 10, "observer_memory_peak_bytes": 20}


class MemoryAccountingTests(unittest.TestCase):
    def test_component_ownership_excludes_unrelated_models_and_deduplicates_pids(self):
        rows = [(1, 0, 1000, "python"), (2, 1, 1000, "llama-server"),
                (3, 0, 1000, "com.docker.backend"), (4, 0, 1000, "other/llama-server")]
        reader = Mock()
        reader.read.side_effect = lambda pid: process(pid=pid)
        report = MEMORY.physical_sample(rows, {1, 2, 3}, reader)
        self.assertEqual([(v["pid"], v["component"]) for v in report["processes"]],
                         [(1, "application"), (2, "model"), (3, "docker_backend")])
        self.assertEqual(reader.read.call_count, 3)

    def test_failed_process_reads_remain_explicit_instead_of_becoming_zero(self):
        reader = Mock()
        reader.read.side_effect = OSError("process exited")
        report = MEMORY.physical_sample([(1, 0, 1000, "python")], {1}, reader)
        self.assertEqual(report["processes"], [])
        self.assertEqual(report["errors"], [{"pid": 1, "component": "application", "category": "unavailable_or_exited"}])

    def test_pid_reuse_and_lifetime_peaks_do_not_become_an_aggregate_peak(self):
        samples = [{"physical_footprint": {"processes": [{"component": "model", **process(start=stamp, peak=peak)}], "errors": []}}
                   for stamp, peak in ((10, 200), (10, 300), (20, 1000))]
        memory = {"host_samples": samples, "guest_vm": {"samples": [guest()], "sampling_errors": 0}}
        summary = MEMORY.summarize_memory([memory])
        self.assertEqual(summary["components"]["model"]["process_identities"], 2)
        self.assertEqual(summary["components"]["model"]["max_observed_single_process_lifetime_high_water_bytes"], 1000)
        self.assertEqual(summary["guest_vm_max_used_excluding_free_bytes"], 700)
        self.assertEqual(summary["guest_vm_max_used_excluding_available_bytes"], 500)
        self.assertNotIn("total_memory_bytes", summary)
        self.assertTrue(summary["total_memory_acceptance"].startswith("pending:"))

    def test_incomplete_or_inconsistent_guest_counters_are_rejected(self):
        variants = []
        row = guest(); del row["meminfo_bytes"]["MemAvailable"]; variants.append(row)
        for key, value in (("MemAvailable", 1001), ("MemFree", -1), ("MemTotal", True), ("SwapFree", 1)):
            row = guest(); row["meminfo_bytes"][key] = value; variants.append(row)
        row = guest(); row["observer_memory_peak_bytes"] = 9; variants.append(row)
        row = guest(); row["guest_uptime_seconds"] = float("nan"); variants.append(row)
        for row in variants:
            with self.subTest(row=row), self.assertRaises(ValueError):
                MEMORY.validate_guest(row)

    def test_corrupt_footprint_identity_or_high_water_is_rejected_offline(self):
        for change in ({"start_abstime": 0}, {"physical_footprint_bytes": -1},
                       {"lifetime_max_physical_footprint_bytes": 99}, {"component": "unknown"}):
            row = {"component": "model", **process(), **change}
            with self.subTest(change=change), self.assertRaises(ValueError):
                MEMORY.summarize_memory([{"host_samples": [{"physical_footprint": {"processes": [row]}}]}])

    def test_observer_has_no_pull_network_privileges_mounts_or_unbounded_resources(self):
        observer = MEMORY.GuestObserver("sha256:" + "a" * 64, 0)
        with patch.object(MEMORY.subprocess, "run") as run, patch.object(MEMORY.subprocess, "Popen"), \
                patch.object(MEMORY.threading, "Thread"):
            observer.start()
        command = run.call_args.args[0]
        for option, value in (("--pull", "never"), ("--network", "none"), ("--user", "65534:65534"),
                              ("--memory", "32m"), ("--memory-swap", "32m"), ("--cpus", "0.1"), ("--pids-limit", "16")):
            self.assertEqual(command[command.index(option) + 1], value)
        self.assertIn("--read-only", command)
        self.assertNotIn("--privileged", command)
        self.assertNotIn("--volume", command)
        self.assertNotIn("--mount", command)
        with self.assertRaises(ValueError):
            MEMORY.GuestObserver("mutable:tag", 0)

    def test_observer_cleanup_failure_or_missing_samples_cannot_pass(self):
        observer = MEMORY.GuestObserver("sha256:" + "a" * 64, 0)
        observer.creation_attempted = True
        with patch.object(MEMORY.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 15)):
            report = observer.close()
        self.assertFalse(report["clean_shutdown"])
        self.assertEqual(report["sampling_errors"], 1)
        self.assertEqual(report["cleanup_failure"], "TimeoutExpired")
        observer = MEMORY.GuestObserver("sha256:" + "a" * 64, 0)
        observer.creation_attempted = True
        with patch.object(MEMORY.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 1, "", "No such object")]):
            self.assertFalse(observer.close()["clean_shutdown"])
        observer.samples = [guest()]
        with patch.object(MEMORY.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 1, "[]", "error: no such object")]):
            self.assertTrue(observer.close()["clean_shutdown"])

    def test_observer_that_exits_before_cleanup_cannot_claim_complete_sampling(self):
        observer = MEMORY.GuestObserver("sha256:" + "a" * 64, 0)
        observer.creation_attempted = True
        observer.process = Mock()
        observer.process.poll.return_value = 137
        observer.samples = [guest()]
        with patch.object(MEMORY.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0),
                subprocess.CompletedProcess([], 1, "[]", "error: no such object")]):
            report = observer.close()
        self.assertTrue(report["early_exit"])
        self.assertFalse(report["clean_shutdown"])


if __name__ == "__main__":
    unittest.main()
