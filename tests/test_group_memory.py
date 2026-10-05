"""Native group counter/ownership tests; synthetic inputs are not runtime evidence."""
from __future__ import annotations

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import group_memory as group
import benchmark_group_memory as benchmark
sys.path.pop(0)


def values(dirty=100, swapped=20, clean=30, reclaimable=10, wired=5):
    return {"dirty": dirty, "swapped": swapped, "clean": clean, "reclaimable": reclaimable,
            "wired": wired, "regions": 1}


def sample():
    selection = [{"pid": 1, "start_abstime": 10, "component": "application"},
                 {"pid": 2, "start_abstime": 20, "component": "docker_vm"}]
    return {"elapsed_seconds": 1.0, "selection": selection, "containers": [], "native": {
        "unit": "byte", "bytes per unit": 1, "page size": 16384, "errors": [], "warnings": [],
        "processes": [{"pid": item["pid"], "footprint": 100, "auxiliary": {"phys_footprint": 100, "phys_footprint_peak": 200},
                       "categories": {"IOKit (graphics)": values()}} for item in selection],
        "shared": [{"pids": [1, 2], "specific_to_pid": 1, "categories": {"mapped file": values()}}],
        "summary": {"IOKit (graphics)": values(), "total": values()}, "total footprint": 100,
        "start_time": {"mach_absolute_time_ns": 1, "mach_continuous_time_ns": 10, "wall_time_s": 20},
        "end_time": {"mach_absolute_time_ns": 2, "mach_continuous_time_ns": 11, "wall_time_s": 21}}}


class GroupMemoryTests(unittest.TestCase):
    def test_shared_mapping_can_be_group_owned_without_a_specific_process(self):
        row = sample()
        del row["native"]["shared"][0]["specific_to_pid"]
        self.assertEqual(group.validate_native(row["native"], row["selection"])["dirty"], 100)

    def test_native_summary_not_sum_of_nested_or_per_process_observations(self):
        row = sample()
        row["guest_memory_bytes"] = 10**12
        result = group.summarize([row])
        self.assertEqual(result["max_observed_group_dirty_bytes"], 100)
        self.assertEqual(result["max_observed_accounted_resident_bytes"], 120)
        self.assertEqual(result["max_observed_group_accounted_bytes"], 140)
        self.assertEqual(result["process_lifetime_dirty_upper_bound_bytes"], 400)
        self.assertEqual(result["driver_category_names"], ["IOKit (graphics)"])

    def test_unknown_duplicate_reused_and_missing_processes_cannot_validate(self):
        for change in (lambda r: r["selection"].pop(),
                       lambda r: r["selection"].append(r["selection"][0]),
                       lambda r: r["selection"][0].update(start_abstime=0),
                       lambda r: r["selection"][0].update(pid=True),
                       lambda r: r["native"]["processes"][0].update(pid=99),
                       lambda r: r["native"]["shared"][0].update(pids=[99]),
                       lambda r: r["selection"][0].update(component="unknown")):
            row = sample()
            change(row)
            with self.assertRaises(ValueError):
                group.validate_native(row["native"], row["selection"])

    def test_native_errors_warnings_invalid_units_subsets_and_times_cannot_pass(self):
        for change in (lambda r: r.update(errors=["unreadable"]), lambda r: r.update(warnings=["partial"]),
                       lambda r: r.update(unit="page"), lambda r: r.update(**{"total footprint": 101}),
                       lambda r: r["summary"]["total"].update(swapped=101),
                       lambda r: r["summary"]["total"].update(clean=-1),
                       lambda r: r["summary"]["total"].update(dirty=True),
                       lambda r: r["processes"][0]["auxiliary"].update(phys_footprint_peak=99),
                       lambda r: r["end_time"].update(wall_time_s=19)):
            row = sample()
            change(row["native"])
            with self.assertRaises(ValueError):
                group.validate_native(row["native"], row["selection"])

    def test_pid_reuse_separates_lifetime_upper_bound_from_observed_group_peak(self):
        left, right = sample(), sample()
        right["elapsed_seconds"] = 2
        right["selection"][0]["start_abstime"] = 30
        result = group.summarize([left, right])
        self.assertEqual(result["max_observed_group_dirty_bytes"], 100)
        self.assertEqual(result["process_lifetime_dirty_upper_bound_bytes"], 600)
        self.assertEqual(result["max_sample_gap_seconds"], 1)

    def test_virtual_machine_name_alone_never_establishes_docker_ownership(self):
        sampler = object.__new__(group.GroupSampler)
        sampler.pid, sampler.associations, sampler.reader = 1, {}, Mock()
        sampler.reader.read.side_effect = lambda pid: {"pid": pid, "start_abstime": pid * 10}
        rows = [(1, 0, 0, "/usr/bin/python"), (2, 1, 0, "/runtime/llama-server"),
                (3, 0, 0, "/Applications/Docker.app/com.docker.backend"),
                (4, 1, 0, "/System/com.apple.Virtualization.VirtualMachine"),
                (5, 0, 0, "/System/com.apple.Virtualization.VirtualMachine")]
        # A VM descendant belongs to the app tree; the actual Docker VM must still have backing proof.
        with patch.object(group, "docker_vm_association", return_value=False):
            with self.assertRaisesRegex(ValueError, "associated Docker VM"):
                sampler.select(rows)
        sampler.associations.clear()
        with patch.object(group, "docker_vm_association", return_value=True):
            selected = sampler.select(rows)
        self.assertEqual([item["pid"] for item in selected if item["component"] == "docker_vm"], [5])
        self.assertEqual([item["pid"] for item in selected if item["component"] == "model"], [2])

    def test_unrelated_container_rejects_the_whole_boundary_before_native_query(self):
        sampler = object.__new__(group.GroupSampler)
        sampler.select = Mock(return_value=sample()["selection"])
        sampler.allowed_containers = lambda: {"owned"}
        with patch.object(group, "process_rows", return_value=[]), \
                patch.object(group.subprocess, "check_output", return_value="foreign\n"), \
                patch.object(group.subprocess, "run") as native:
            with self.assertRaises(group.AttributionViolation):
                sampler.sample()
        native.assert_not_called()

    def test_no_complete_samples_missing_vm_and_reverse_timeline_fail_summary(self):
        with self.assertRaises(ValueError):
            group.summarize([])
        row = sample()
        row["selection"][1]["component"] = "docker_support"
        with self.assertRaisesRegex(ValueError, "Missing workload/VM"):
            group.summarize([row])
        left, right = sample(), sample()
        right["elapsed_seconds"] = 0
        with self.assertRaisesRegex(ValueError, "timeline"):
            group.summarize([left, right])

    def test_compressed_artifact_count_path_shutdown_and_model_coverage_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            memories = []
            for number in range(4):
                path = output / f"native-{number}.json.gz"
                with gzip.open(path, "wt") as stream:
                    json.dump([sample()], stream)
                memories.append({"native_group": {"method": group.METHOD, "clean_shutdown": True,
                                                  "artifact": path.name, "application_pid": 1,
                                                  "samples": 1, "errors": []}})
            memory = memories[0]
            report = {"status": "complete", "variant": "ocr_rules", "memory": memories}
            protocol = {"group_accounted_budget_bytes": 12 * 1024**3, "max_complete_sample_gap_seconds": 5}
            result = benchmark.group_summary(protocol, report, output)
            self.assertTrue(result["all_sampled_budgets_pass"])
            self.assertNotIn("query_error_categories", result["launches"][0])
            protocol["error_accounting"] = "classified-v1"
            classified = benchmark.group_summary(protocol, report, output)
            self.assertEqual(classified["launches"][0]["query_error_categories"], {})
            report["variant"] = "span_llm"
            with self.assertRaisesRegex(ValueError, "model observations"):
                benchmark.group_summary(protocol, report, output)
            report["variant"] = "ocr_rules"
            for change in ({"artifact": "../native.json.gz"}, {"samples": 2}, {"clean_shutdown": False},
                           {"application_pid": 99}, {"artifact": "native-1.json.gz"},
                           {"errors": [{"category": "AttributionViolation"}]}):
                original = dict(memory["native_group"])
                memory["native_group"].update(change)
                with self.assertRaises(ValueError):
                    benchmark.group_summary(protocol, report, output)
                memory["native_group"] = original


if __name__ == "__main__":
    unittest.main()
