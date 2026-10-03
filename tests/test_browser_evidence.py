"""Offline browser evidence integrity and large CDP message contracts."""

import hashlib
import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from docwork.pilot_bundle import SOURCES
from docwork.release_readiness import REVIEW_BROWSER_CHECKS, verify_browser_report

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pilot_browser", ROOT / "scripts/verify_pilot_browser.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BrowserEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "root"
        self.directory = self.root / "evidence"
        self.directory.mkdir(parents=True)
        names = (*SOURCES, "scripts/verify_pilot_browser.py", "scripts/verify_review_browser.py", "src/docwork/geometry.py")
        snapshot = {name: "fixture source\n" for name in names}
        for name, text in snapshot.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        (self.directory / "source_snapshot.json").write_text(json.dumps(snapshot))
        (self.directory / "index.html").write_text("fixture demo")
        (self.directory / "frames").mkdir()
        self.frames = []
        for index in range(5):
            name = f"frames/{index}.png"
            (self.directory / name).write_bytes(b"fixture image")
            self.frames.append({"path": name, "caption": "Fixture"})
        self.report = {"report_version": "review-browser-workflow-v1", "status": "passed",
                       "human_timing_measurement": False, "checks": sorted(REVIEW_BROWSER_CHECKS),
                       "cases": ["inv-f02-02", "inv-f01-12", "inv-f05-30", "inv-f06-04"],
                       "frames": self.frames, "recording": None,
                       "source_sha256": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()}}
        self.save()

    def save(self):
        self.report["artifacts"] = {str(p.relative_to(self.directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in self.directory.rglob("*") if p.is_file() and p.name != "report.json"}
        (self.directory / "report.json").write_text(json.dumps(self.report))

    def test_complete_browser_fixture_is_verified_and_current_drift_stays_pending(self):
        self.assertEqual(verify_browser_report(self.root, self.directory)["status"], "passed")
        (self.root / "ui/app.js").write_text("updated")
        result = verify_browser_report(self.root, self.directory)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["source_drift"], ["ui/app.js"])

    def test_incomplete_coverage_or_human_relabel_cannot_pass(self):
        for key, value in (("human_timing_measurement", True), ("status", "failed"), ("checks", []),
                           ("cases", ["inv-f02-02"])):
            original = self.report[key]
            self.report[key] = value
            self.save()
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify_browser_report(self.root, self.directory)
            self.report[key] = original

    def test_changed_frame_or_unexpected_file_is_rejected(self):
        path = self.directory / "frames/0.png"
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "artifact inventory"):
            verify_browser_report(self.root, self.directory)
        self.save()
        (self.directory / "extra.txt").write_text("unlisted")
        with self.assertRaisesRegex(ValueError, "artifact inventory"):
            verify_browser_report(self.root, self.directory)

    def test_rehashed_snapshot_still_must_match_source_provenance(self):
        (self.directory / "source_snapshot.json").write_text("{}")
        self.save()
        with self.assertRaisesRegex(ValueError, "source snapshot"):
            verify_browser_report(self.root, self.directory)

    def test_frame_traversal_symlinks_and_missing_frames_are_rejected(self):
        self.report["frames"][0]["path"] = "../external.png"
        self.save()
        with self.assertRaisesRegex(ValueError, "frame"):
            verify_browser_report(self.root, self.directory)
        self.report["frames"][0]["path"] = "frames/0.png"
        self.report["frames"] = self.report["frames"][:1]
        self.save()
        with self.assertRaisesRegex(ValueError, "missing frames"):
            verify_browser_report(self.root, self.directory)
        (self.directory / "link").symlink_to(self.root / "ui", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            verify_browser_report(self.root, self.directory)

    def test_recorded_media_requires_container_and_observed_playback(self):
        (self.directory / "demo.webm").write_bytes(b"\x1aE\xdf\xa3fixture")
        self.report["recording"] = {"playback": {"width": 1440, "height": 1190, "current_time": .1}}
        self.save()
        self.assertEqual(verify_browser_report(self.root, self.directory)["status"], "passed")
        self.report["recording"]["playback"]["current_time"] = 0
        self.save()
        with self.assertRaisesRegex(ValueError, "successful playback"):
            verify_browser_report(self.root, self.directory)

    def test_websocket_masks_and_length_boundaries_support_large_screenshots(self):
        for length in (125, 126, 65535, 65536, 150000):
            with self.subTest(length=length):
                client = MODULE.DevTools.__new__(MODULE.DevTools)
                client.socket = Mock()
                payload = b"x" * length
                with patch.object(MODULE.os, "urandom", return_value=b"mask"):
                    client.send(payload)
                frame = client.socket.sendall.call_args.args[0]
                self.assertEqual(frame[0], 0x81)
                marker = frame[1] & 127
                if marker == 126:
                    self.assertEqual(struct.unpack("!H", frame[2:4])[0], length)
                    offset = 4
                elif marker == 127:
                    self.assertEqual(struct.unpack("!Q", frame[2:10])[0], length)
                    offset = 10
                else:
                    self.assertEqual(marker, length)
                    offset = 2
                self.assertEqual(frame[offset:offset+4], b"mask")
                data = frame[offset+4:]
                self.assertEqual(bytes(value ^ b"mask"[index % 4] for index, value in enumerate(data)), payload)


if __name__ == "__main__":
    unittest.main()
