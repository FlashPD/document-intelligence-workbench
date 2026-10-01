"""Phase 0 CLI: local preflight and a trusted-fixture OCR baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from .baseline import BASELINE_VERSION, extract_invoice
from .evaluation import evaluate_development
from .ocr import tesseract_page
from .validation import validate_invoice


def _run(args: list[str], timeout: int = 5) -> str | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def doctor() -> dict:
    hardware = _run(["system_profiler", "SPHardwareDataType"]) if sys.platform == "darwin" else None
    facts = {}
    if hardware:
        for line in hardware.splitlines():
            key, _, value = line.strip().partition(": ")
            if key in {"Model Name", "Model Identifier", "Chip", "Memory"}:
                facts[key.lower().replace(" ", "_")] = value
    tesseract = shutil.which("tesseract")
    langs = _run([tesseract, "--list-langs"]) if tesseract else None
    tesseract_version = _run([tesseract, "--version"]) if tesseract else None
    docker = shutil.which("docker")
    docker_version = _run([docker, "info", "--format", "{{.ServerVersion}}"], timeout=3) if docker else None
    usage = shutil.disk_usage(Path.cwd())
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "python_3_12_or_newer": sys.version_info >= (3, 12),
        "hardware": facts,
        "free_disk_gib": round(usage.free / 1024**3, 1),
        "tesseract_available": bool(tesseract),
        "tesseract_version": tesseract_version.splitlines()[0] if tesseract_version else None,
        "tesseract_languages": [line for line in (langs or "").splitlines()[1:] if line],
        "docker_daemon_available": docker_version is not None,
        "docker_server_version": docker_version,
        "local_model_status": "not_checked_in_phase_0",
        "phase_0_ready_for_fixture_baseline": bool(tesseract and langs and "eng" in langs),
    }


def baseline_fixture(path: Path) -> dict:
    repo_samples = Path(__file__).resolve().parents[2] / "samples"
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(repo_samples.resolve()) or resolved.suffix.lower() != ".png":
        raise ValueError("Phase 0 baseline accepts only PNGs in this repository's samples directory")
    start = time.perf_counter()
    page = tesseract_page(resolved)
    ocr_seconds = time.perf_counter() - start
    record = extract_invoice(page)
    issues = validate_invoice(record, page)
    return {
        "mode": "fresh_fixture_ocr_rules",
        "baseline_version": BASELINE_VERSION,
        "python_version": platform.python_version(),
        "tesseract_version": (_run(["tesseract", "--version"]) or "unknown").splitlines()[0],
        "source_sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "source_name": resolved.name,
        "runtime_seconds": {"ocr": round(ocr_seconds, 3), "total": round(time.perf_counter() - start, 3)},
        "page": asdict(page),
        "record": record.to_dict(),
        "issues": [asdict(issue) for issue in issues],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docwork")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Report local Phase 0 runtime facts")
    baseline = commands.add_parser("baseline", help="Run OCR/rules on a self-authored sample PNG")
    baseline.add_argument("fixture", type=Path)
    baseline.add_argument("--output", type=Path)
    development = commands.add_parser("eval-development", help="Score all frozen self-authored development PNGs")
    development.add_argument("--output", type=Path, default=Path("artifacts/development-baseline.json"))
    args = parser.parse_args(argv)
    try:
        if args.command == "doctor":
            data = doctor()
        elif args.command == "baseline":
            data = baseline_fixture(args.fixture)
        else:
            data = evaluate_development(Path(__file__).resolve().parents[2], baseline_fixture)
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        parser.exit(2, f"docwork: {exc}\n")
    rendered = json.dumps(data, indent=2) + "\n"
    if args.command in ("baseline", "eval-development") and args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
        print(args.output)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
