"""Live fault injection through the real Docker supervisor and durable queue."""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.intake import IntakeStore
from docwork.review import ReviewBlocked
from docwork.worker import PARSER_IMAGE, ParserFailure, _docker_run, parser_command, process_one


class ParserResourceChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = IntakeStore(self.root / "review.sqlite", self.root / "objects")
        self.sample = Path(__file__).resolve().parents[1] / "samples/clean.png"
        self.evidence = {}

    def fault(self, code: str, expected: str, *, timeout: int, reduced_memory: bool = False):
        document_id = self.store.submit(io.BytesIO(self.sample.read_bytes()), "clean.png", "image/png")
        started = int(time.time())
        container_name = None

        def command(source, media_type, output, claim, *, image):
            nonlocal container_name
            original = parser_command(source, media_type, output, claim, image=image)
            container_name = original[original.index("--name") + 1]
            if reduced_memory:
                original[original.index("--memory") + 1] = "64m"
                original[-4:-4] = ["--memory-swap", "64m"]
            self.evidence["command"] = original[:-4] + ["--entrypoint", "python", image, "-c", code]
            return self.evidence["command"]

        def runner(source, media_type, output, claim, *, image):
            with patch("docwork.worker.parser_command", side_effect=command), \
                    patch("docwork.worker.PARSER_TIMEOUT", timeout):
                try:
                    _docker_run(source, media_type, output, claim, image=image)
                except ParserFailure:
                    marker = output / "probe-started.json"
                    self.assertTrue(marker.is_file(), "Deadline must occur after the real container starts")
                    self.evidence["probe"] = json.loads(marker.read_text())
                    raise

        self.assertEqual(process_one(self.store, "resource-probe", runner=runner), document_id)
        status = self.store.status(document_id)
        self.assertEqual(status["job"]["error_code"], expected)
        self.assertEqual(status["status"], "FAILED")
        self.assertEqual(status["current_revision"], 0)
        self.assertEqual(status["page_count"], 0)
        self.assertIsNone(status["parser_checkpoint"])
        with self.assertRaises(ReviewBlocked):
            self.store.get(document_id)
        with self.assertRaisesRegex(KeyError, "Unknown revision: 0"):
            self.store.export(document_id, "json")
        self.assertEqual(list((self.store.object_root / "quarantine").iterdir()), [])
        self.assertEqual(hashlib.sha256(self.store.object_path(document_id).read_bytes()).hexdigest(), status["source_sha256"])

        self.assertIsNotNone(container_name)
        remaining = subprocess.run(["docker", "container", "inspect", container_name],
                                   capture_output=True, text=True, timeout=15)
        self.assertNotEqual(remaining.returncode, 0)
        self.assertIn("No such container", remaining.stderr)
        # Docker's event history corroborates actual OOM/kill and removal even
        # though --rm has already deleted the inspectable container state.
        history = subprocess.run(["docker", "events", "--since", str(started),
                                  "--until", str(int(time.time()) + 1), "--filter", f"container={container_name}",
                                  "--format", "{{json .}}"],
                                 capture_output=True, text=True, timeout=15, check=True)
        events = [json.loads(line) for line in history.stdout.splitlines()]
        actions = [event["Action"] for event in events]
        self.assertIn("start", actions)
        self.assertIn("destroy", actions)
        if reduced_memory:
            self.assertIn("oom", actions, "Exit 137 alone cannot substantiate an OOM claim")
            deaths = [event for event in events if event["Action"] == "die"]
            self.assertEqual(deaths[-1]["Actor"]["Attributes"]["exitCode"], "137")
            self.assertEqual(self.evidence["probe"]["memory_max"], "67108864")
            self.assertEqual(self.evidence["probe"]["swap_max"], "0")
        else:
            self.assertIn("kill", actions)
        self.evidence.update(error_code=expected, timeout_seconds=timeout, events=events,
                             container_removed=True, scratch_removed=True,
                             candidate_published=False, source_sha256=status["source_sha256"],
                             scope="Injected Python probe in the pinned parser image; production isolation policy. "
                                   "Memory/swap reduced to 64 MiB/0 for OOM; deadline shortened for timeout. "
                                   "This is supervisor fault handling, not a claim about natural invoice resource usage.")

        self.store.retry(document_id)
        self.assertEqual(process_one(self.store, "normal-parser"), document_id)
        after = self.store.status(document_id)
        self.assertEqual(after["status"], "REVIEW_READY")
        self.assertEqual(after["job"]["attempts"], 2)
        self.assertGreater(after["job"]["fence"], status["job"]["fence"])
        self.assertIsNone(after["job"]["error_code"])
        detail = self.store.get(document_id)
        self.assertEqual(detail["record"]["fields"]["invoice_number"]["value"], "AST-1001")
        self.assertIsNone(detail["approval"])
        self.assertEqual(list((self.store.object_root / "quarantine").iterdir()), [])
        self.evidence.update(retry_status=after["status"], attempts=after["job"]["attempts"],
                             fence_before=status["job"]["fence"], fence_after=after["job"]["fence"],
                             retry_parser_checkpoint=after["parser_checkpoint"])

    def test_timeout_kills_container_then_real_parser_retry_succeeds(self):
        self.fault("""
import json, time
from pathlib import Path
Path('/output/probe-started.json').write_text(json.dumps({'started': True}))
time.sleep(120)
""", "PARSER_TIMEOUT", timeout=8)

    def test_cgroup_oom_is_recorded_then_real_parser_retry_succeeds(self):
        self.fault("""
import json
from pathlib import Path
Path('/output/probe-started.json').write_text(json.dumps({
    'started': True,
    'memory_max': Path('/sys/fs/cgroup/memory.max').read_text().strip(),
    'swap_max': Path('/sys/fs/cgroup/memory.swap.max').read_text().strip(),
}))
chunks = []
while True:
    chunk = bytearray(8 * 1024 * 1024)
    for offset in range(0, len(chunk), 4096):
        chunk[offset] = 1
    chunks.append(chunk)
""", "PARSER_KILLED", timeout=30, reduced_memory=True)
