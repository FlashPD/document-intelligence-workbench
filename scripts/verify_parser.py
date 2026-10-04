"""Run real-container checks and save an inspectable local evidence report."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from docwork.worker import PARSER_IMAGE


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.checks = []

    def startTest(self, test):
        self.started = time.perf_counter()
        self.outcome = "unfinished"
        self.detail = None
        super().startTest(test)

    def addSuccess(self, test):
        self.outcome = "passed"
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.outcome = "failed"
        self.detail = self._exc_info_to_string(err, test)
        super().addFailure(test, err)

    def addError(self, test, err):
        self.outcome = "error"
        self.detail = self._exc_info_to_string(err, test)
        super().addError(test, err)

    def addSkip(self, test, reason):
        self.outcome = "skipped"
        self.detail = reason
        super().addSkip(test, reason)

    def stopTest(self, test):
        self.checks.append({"id": test.id(), "status": self.outcome,
                            "seconds": round(time.perf_counter() - self.started, 3),
                            "evidence": getattr(test, "evidence", {}), "detail": self.detail})
        super().stopTest(test)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/parser-verification.json"))
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        parser.error("Output already exists; choose a fresh report path")
    root = Path(__file__).resolve().parents[1]
    paths = [*sorted((root / "src/docwork").glob("*.py")),
             *sorted((root / "tests").glob("container_*.py")), Path(__file__).resolve(),
             *sorted(path for path in (root / "sandbox").glob("*") if path.is_file()), root / ".dockerignore",
             root / "samples/clean.png", root / "samples/conflicting-total.png"]
    paths += sorted(path for path in (root / "tests/fixtures/security").glob("*") if path.is_file())
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    report = {"report_version": "parser-verification-v1", "status": "failed",
              "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "host": {"system": platform.system(), "machine": platform.machine(),
                       "python": platform.python_version()},
              "parser_image": PARSER_IMAGE, "source_sha256": hashes, "checks": [],
              "scope": "Self-authored fixtures and runtime probes; OCR/rules only. "
                       "Includes valid encrypted PDFs with empty/nonempty passwords, independently confirmed "
                       "by pinned pdfinfo before production refusal; legacy RC4 fixture, not encryption security. "
                       "Includes refused-loopback-model retry and abrupt host worker exit after a parser checkpoint, "
                       "with rules extraction on resume and a shortened real lease; "
                       "also portable backup/restore of a reviewed two-page PDF and an interrupted checkpoint. "
                       "Includes injected parser timeout and cgroup OOM probes with cleanup and real-parser retries; "
                       "deadlines are shortened and the OOM memory/swap limit is reduced to 64 MiB/0. "
                       "No held-out quality, real model inference, genuine-scan, natural invoice OOM, machine crash, "
                       "or arbitrary-stage interruption claim."}
    try:
        report["docker_version"] = json.loads(subprocess.check_output(
            ["docker", "version", "--format", "{{json .}}"], text=True, timeout=15))
        image = json.loads(subprocess.check_output(
            ["docker", "image", "inspect", PARSER_IMAGE], text=True, timeout=15))[0]
        report["image"] = {key: image[key] for key in ("Id", "Os", "Architecture", "RepoDigests")}
        suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern="container_*.py")
        result = unittest.TextTestRunner(verbosity=2, resultclass=EvidenceResult).run(suite)
        report["checks"] = result.checks
        # A skipped, empty, or interrupted suite cannot become passing evidence.
        passed = bool(result.checks) and all(check["status"] == "passed" for check in result.checks)
        unchanged = all(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
                        for name, digest in hashes.items())
        after = json.loads(subprocess.check_output(
            ["docker", "image", "inspect", PARSER_IMAGE], text=True, timeout=15))[0]
        report["inputs_unchanged"] = unchanged and after["Id"] == image["Id"]
        if passed and report["inputs_unchanged"]:
            report["status"] = "passed"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "report": str(output)}))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
