import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.performance import freeze, read_protocol, summarize
from docwork.queue_benchmark import DOCUMENTS

ROOT = Path(__file__).resolve().parents[1]


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / "frozen"
        self.protocol = freeze(ROOT, self.directory, parser_image="sha256:" + "a" * 64, host_workloads="test only")

    def tearDown(self):
        self.temporary.cleanup()

    def rows(self, variant="ocr_rules"):
        names = [f"cold-{n}" for n in range(3)] + ["warmup"] + [f"warm-{n}" for n in range(10)]
        if variant == "ocr_rules":
            names += [f"queue-{n}" for n in range(20)]
        return [{"id": name, "document_id": str(n), "status": "REVIEW_READY", "error_code": None,
                 "attempt_count": 1, "checkpoint_reused": False, "startup_seconds": 1 if name.startswith("cold-") else 0,
                 "upload_to_terminal_seconds": 2, "cold_workflow_seconds": 4, "stages_seconds": {"parsing": 1},
                 "processing_started_at_seconds": 2 * n, "processing_finished_at_seconds": 2 * n + 1,
                 "terminal_since_batch_start_seconds": 2 * n + 2, "page_count": 1, "max_observed_active": 1}
                for n, name in enumerate(names)]

    def test_freeze_is_source_bound_and_summary_retains_failures(self):
        self.assertEqual(read_protocol(ROOT, self.directory), self.protocol)
        self.assertEqual(len(self.protocol["queue"]), 20)
        self.assertEqual([row["id"] for row in self.protocol["queue"]], list(DOCUMENTS))
        rows = self.rows()
        view = summarize(self.protocol, "ocr_rules", rows)
        self.assertEqual(view["rules_latency_gate"], "pass")
        self.assertEqual(view["groups"]["cold"]["cold_workflow"]["p95_seconds"], 4)
        rows[6].update(status="FAILED", error_code="PARSER_TIMEOUT", upload_to_terminal_seconds=600)
        view = summarize(self.protocol, "ocr_rules", rows)
        self.assertEqual(view["failures"], 1)
        self.assertEqual(view["rules_latency_gate"], "fail")
        self.assertEqual(view["groups"]["warm"]["upload_to_terminal"]["p95_seconds"], 600)

    def test_missing_duplicate_retried_cached_invalid_and_overlapping_runs_cannot_pass(self):
        variants = []
        rows = self.rows()
        variants.append(rows[:-1])
        variants.append([rows[0], *rows])
        for change in ({"attempt_count": 2}, {"checkpoint_reused": True},
                       {"upload_to_terminal_seconds": float("nan")}, {"status": "PROCESSING"},
                       {"cold_workflow_seconds": 2}, {"stages_seconds": {"parsing": -1}}):
            changed = copy.deepcopy(rows)
            changed[0].update(change)
            variants.append(changed)
        changed = copy.deepcopy(rows)
        changed[-1]["processing_started_at_seconds"] = changed[-2]["processing_started_at_seconds"]
        variants.append(changed)
        for changed in variants:
            with self.assertRaises(ValueError):
                summarize(self.protocol, "ocr_rules", changed)

    def test_model_does_not_acquire_rules_latency_gate_or_hide_warmup_failure(self):
        rows = self.rows("span_llm")
        rows[3].update(status="FAILED", error_code="MODEL_UNAVAILABLE")
        summary = summarize(self.protocol, "span_llm", rows)
        self.assertEqual(summary["rules_latency_gate"], "not_applicable")
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(summary["scheduled"], 14)

    def test_changed_source_profile_and_schedule_cannot_be_run_after_freeze(self):
        import json
        from docwork.performance import source_hashes
        with patch("docwork.performance.source_hashes", return_value={**source_hashes(ROOT), "extra": "changed"}):
            with self.assertRaisesRegex(ValueError, "Implementation changed"):
                read_protocol(ROOT, self.directory)
        for name, value in (("model_profile_sha256", "0" * 64), ("warm_uploads", 9)):
            changed = {**self.protocol, name: value}
            (self.directory / "protocol.json").write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                read_protocol(ROOT, self.directory)


if __name__ == "__main__":
    unittest.main()
