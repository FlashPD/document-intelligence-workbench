"""All-document accounting and tamper checks for serial queue measurements."""

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.queue_benchmark import DOCUMENTS, render, run_queue, summarize, verify_queue
from docwork.worker import ParserFailure
from test_worker import parser_result

ROOT = Path(__file__).resolve().parents[1]


class QueueBenchmarkTests(unittest.TestCase):
    def measurements(self):
        return [{"corpus_id": id, "document_id": f"job-{i}", "status": "REVIEW_READY",
                 "media_type": "image/png", "page_count": 1, "error_code": None,
                 "started_seconds": i * 10, "finished_seconds": (i + 1) * 10}
                for i, id in enumerate(DOCUMENTS)]

    def test_failed_call_remains_in_percentiles_and_denominators(self):
        rows = self.measurements()
        rows[-1].update(status="FAILED", error_code="PARSER_TIMEOUT", page_count=0, finished_seconds=1190)
        summary = summarize(rows)
        self.assertEqual((summary["scheduled"], summary["failed"], summary["review_ready"]), (20, 1, 19))
        self.assertEqual(summary["processing_sum_seconds"], 1190)
        self.assertEqual(summary["processing_p95_seconds"], 10)
        self.assertEqual(summary["queue_drain_seconds"], 1190)
        self.assertEqual(summary["last_start_wait_seconds"], 190)
        self.assertEqual(summary["failure_codes"], {"PARSER_TIMEOUT": 1})

    def test_partial_duplicate_nonfinite_and_overlapping_calls_refuse_report(self):
        original = self.measurements()
        variants = [original[:-1]]
        for field, value in (("corpus_id", original[0]["corpus_id"]), ("document_id", original[0]["document_id"]),
                             ("started_seconds", float("nan")), ("finished_seconds", -1),
                             ("started_seconds", 0), ("status", "PROCESSING")):
            rows = copy.deepcopy(original)
            rows[-1][field] = value
            variants.append(rows)
        for rows in variants:
            with self.subTest(last=rows[-1]), self.assertRaises(ValueError):
                summarize(rows)

    def test_production_worker_report_retains_failure_and_verifies_offline(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "queue"
            calls = []

            def runner(source, mime, directory, claim, *, image, stop_event=None):
                calls.append(image)
                if len(calls) == 2:
                    raise ParserFailure("PARSER_TIMEOUT")
                parser_result(directory, source)

            with patch("docwork.queue_benchmark._docker_image_id", return_value="sha256:" + "a" * 64), \
                    patch("docwork.worker._docker_image_id", return_value="sha256:" + "a" * 64), \
                    patch("docwork.worker._docker_run", side_effect=runner), contextlib.redirect_stdout(io.StringIO()):
                report = run_queue(ROOT, output, co_running_workload="Deterministic injected parser test")
            self.assertEqual(len(calls), 20)
            self.assertEqual(set(calls), {"sha256:" + "a" * 64})
            self.assertEqual((report["status"], report["summary"]["failed"]), ("completed_with_failures", 1))
            self.assertEqual(verify_queue(ROOT, output)["status"], "verified")
            self.assertEqual(verify_queue(ROOT, output)["source_drift"], [])
            changed = copy.deepcopy(report)
            changed["summary"]["failed"] = 0
            (output / "report.json").write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "summary"):
                verify_queue(ROOT, output)
            (output / "report.json").write_text(json.dumps(report))
            (output / "index.html").write_text("Invented speedup")
            with self.assertRaisesRegex(ValueError, "presentation"):
                verify_queue(ROOT, output)
            (output / "index.html").write_text(render(report))
            with self.assertRaisesRegex(ValueError, "new output"):
                run_queue(ROOT, output, co_running_workload="test")
            predictions = output / "predictions" / f"{DOCUMENTS[1]}.json"
            predictions.write_text("{}")
            with self.assertRaisesRegex(ValueError, "artifacts"):
                verify_queue(ROOT, output)

    def test_summary_and_presentation_cannot_relabel_failures_or_inject_markup(self):
        report = {"summary": summarize(self.measurements()), "measurements": self.measurements(),
                  "scope": "<script>untrusted</script>", "run": {"co_running_workload": "<img onerror='bad'>"}}
        rendered = render(report)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;img", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("No controlled warm/cold latency", rendered)


if __name__ == "__main__":
    unittest.main()
