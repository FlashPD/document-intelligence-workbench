"""Check locked inputs offline, or compare two explicit uncached Docker rebuilds.

Rebuild evidence compares dependency payloads and copied source, not OCI image
IDs: installation timestamps and image metadata need not be byte-identical.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("parser_installer", ROOT / "sandbox/install_parser.py")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_hashes(root):
    paths = [root / ".dockerignore", root / "Makefile", root / "scripts/verify_parser_build.py",
             *sorted((root / "sandbox").glob("*")), *sorted((root / "src/docwork").glob("*.py"))]
    return {str(path.relative_to(root)): digest(path) for path in paths if path.is_file()}


def check_inputs(root):
    lock = INSTALLER.read_lock(root / "sandbox")
    dockerfile = (root / "sandbox/Dockerfile").read_text()
    if [line for line in dockerfile.splitlines() if line.startswith("FROM ")] != [f"FROM {lock['base']}"]:
        raise ValueError("Docker base differs from parser lock")
    if ("COPY sandbox/parser-build-lock.json sandbox/install_parser.py /opt/docwork-build/" not in dockerfile or
            "RUN python /opt/docwork-build/install_parser.py" not in dockerfile):
        raise ValueError("Docker build bypasses the locked installer")
    return lock


PROBE = """
import hashlib, json, sys
from pathlib import Path
sys.path.insert(0, '/opt/docwork-build')
import install_parser
lock = install_parser.read_lock()
runtime = install_parser.runtime_inventory()
install_parser.verify_inventory(lock, runtime)
assert runtime == json.loads(Path('/opt/docwork-build/runtime.json').read_text()), 'Recorded runtime differs'
print(json.dumps({'runtime': runtime,
    'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app/docwork').glob('*.py')},
    'build_sha256': {name: hashlib.sha256(Path('/opt/docwork-build', name).read_bytes()).hexdigest()
                     for name in ('install_parser.py', 'parser-build-lock.json')}}))
"""


def check_probe(root, lock, probe):
    INSTALLER.verify_inventory(lock, probe["runtime"])
    expected_source = {path.name: digest(path) for path in (root / "src/docwork").glob("*.py")}
    expected_build = {name: digest(root / "sandbox" / name) for name in ("install_parser.py", "parser-build-lock.json")}
    if probe["source_sha256"] != expected_source or probe["build_sha256"] != expected_build:
        raise ValueError("Image source/build inputs differ from checkout")


def rebuild(root, output):
    lock = check_inputs(root)
    if output.exists() or output.is_symlink():
        raise ValueError("Choose a new rebuild report directory")
    hashes = input_hashes(root)
    output.mkdir(parents=True)
    report = {"version": "parser-rebuild-verification-v1", "status": "failed",
              "created_at_utc": datetime.now(timezone.utc).isoformat(), "input_sha256": hashes,
              "lock": lock, "builds": [], "scope": "Two uncached dependency installations on this architecture. "
              "Base layers may be cached; apt/Pillow installation layers are rerun. Compares complete installed Debian "
              "package versions, Python/Pillow, OCR asset bytes and copied source/build hashes. "
              "No bit-identical OCI-image, other-architecture, extraction-quality, timing or memory claim."}
    try:
        for number in (1, 2):
            tag = f"docwork-parser:rebuild-{uuid.uuid4().hex}"
            row = {"tag": tag, "status": "failed", "log": f"build-{number}.log"}
            report["builds"].append(row)
            with (output / row["log"]).open("x") as log:
                subprocess.run(["docker", "build", "--no-cache", "--progress", "plain", "-f", "sandbox/Dockerfile",
                                "-t", tag, "."], cwd=root, stdout=log, stderr=subprocess.STDOUT,
                               check=True, timeout=1800)
            image = json.loads(subprocess.check_output(["docker", "image", "inspect", tag], timeout=15))[0]
            row["image"] = {key: image[key] for key in ("Id", "Architecture", "Os", "RepoDigests")}
            row["probe"] = json.loads(subprocess.check_output([
                "docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
                "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--memory", "1g", "--cpus", "2", "--pids-limit", "64", "--entrypoint", "python",
                image["Id"], "-c", PROBE], timeout=30))
            check_probe(root, lock, row["probe"])
            row["status"] = "passed"
        report["payloads_equal"] = report["builds"][0]["probe"] == report["builds"][1]["probe"]
        report["inputs_unchanged"] = input_hashes(root) == hashes
        if report["payloads_equal"] and report["inputs_unchanged"]:
            report["status"] = "passed"
    except (OSError, ValueError, KeyError, AssertionError, subprocess.SubprocessError) as error:
        report["failure"] = f"{type(error).__name__}: {error}"
    report["artifacts"] = {p.name: digest(p) for p in output.iterdir() if p.is_file()}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, help="Explicitly rebuild twice; absent means offline lock checks only")
    args = parser.parse_args()
    if args.output_dir is None:
        lock = check_inputs(ROOT)
        print(json.dumps({"status": "passed", "scope": "offline lock consistency only", "base": lock["base"]}))
        return 0
    report = rebuild(ROOT, args.output_dir.resolve())
    print(json.dumps({"status": report["status"], "failure": report.get("failure"),
                      "images": [row.get("image", {}).get("Id") for row in report["builds"]],
                      "report": str(args.output_dir / "report.json")}))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
