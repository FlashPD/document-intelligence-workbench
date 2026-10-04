"""Verify a clean source snapshot or an exact Git commit without runtime assets.

By default includes tracked and nonignored current-change files. With --ref,
reads only committed Git objects, independently of the index and working tree.
No inference or downloads run. This does not verify remote clone availability.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_WORKFLOW = "evals/operations-2026-10-04/model-workflow"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command_passed(name: str, result: subprocess.CompletedProcess) -> bool:
    if result.returncode != 0:
        return False
    if name != "contracts":
        return True
    log = result.stdout + result.stderr
    count = re.search(r"Ran (\d+) tests? in", log)
    return bool(count and int(count[1]) > 0 and re.search(r"\nOK\s*$", log))


def snapshot_files(root: Path, output: Path) -> list[Path]:
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root
    ).decode().split("\0")
    files = []
    for name in sorted(set(names) - {""}):
        relative = Path(name)
        path = root / relative
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Source snapshot path escapes the repository")
        if path.is_relative_to(output):
            continue
        if relative.parts[0] in {"artifacts", ".venv", ".git"}:
            raise ValueError("Local runtime data must not enter the clean snapshot")
        if any((root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)):
            raise ValueError("Source snapshot cannot contain symlinks")
        if not path.is_file():
            raise ValueError(f"Source snapshot file is missing: {name}")
        files.append(path)
    if not files:
        raise ValueError("Source snapshot is empty")
    return files


def committed_files(root: Path, ref: str) -> tuple[dict, list[dict]]:
    # Resolve once: a branch/tag moving later cannot change the checked tree.
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        cwd=root, stderr=subprocess.PIPE, text=True,
    ).strip()
    tree = subprocess.check_output(["git", "rev-parse", f"{commit}^{{tree}}"], cwd=root, text=True).strip()
    entries = subprocess.check_output(["git", "ls-tree", "-r", "-z", "--full-tree", commit], cwd=root)
    files = []
    for entry in entries.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, blob = metadata.decode("ascii").split()
        relative = Path(os.fsdecode(name))
        if (relative.is_absolute() or ".." in relative.parts or not relative.parts or
                relative.parts[0] in {"artifacts", ".venv", ".git"}):
            raise ValueError("Committed snapshot contains a forbidden source path")
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Committed snapshot cannot contain symlinks or submodules")
        files.append({"path": str(relative), "mode": mode, "blob": blob})
    if not files:
        raise ValueError("Committed snapshot is empty")
    return {"requested_ref": ref, "commit": commit, "tree": tree}, files


def copy_committed_files(root: Path, files: list[dict], checkout: Path) -> dict[str, str]:
    # Use blobs directly: git archive may omit or transform files through
    # export-ignore/export-subst attributes. Spool the batch outside the tree
    # so the complete corpus need not be held in memory.
    hashes = {}
    with tempfile.TemporaryFile() as stream:
        subprocess.run(["git", "cat-file", "--batch"], cwd=root, check=True,
                       input="".join(f"{entry['blob']}\n" for entry in files).encode("ascii"),
                       stdout=stream, stderr=subprocess.PIPE, timeout=120)
        stream.seek(0)
        for entry in files:
            blob, kind, size = stream.readline().decode("ascii").strip().split()
            if blob != entry["blob"] or kind != "blob":
                raise ValueError("Git returned an unexpected source object")
            remaining = int(size)
            destination = checkout / entry["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as target:
                while remaining:
                    chunk = stream.read(min(remaining, 1024 * 1024))
                    if not chunk:
                        raise ValueError("Git source object was truncated")
                    target.write(chunk)
                    remaining -= len(chunk)
            if stream.read(1) != b"\n":
                raise ValueError("Git source object delimiter is missing")
            destination.chmod(0o755 if entry["mode"] == "100755" else 0o644)
            hashes[entry["path"]] = digest(destination)
        if stream.read(1):
            raise ValueError("Git returned additional source objects")
    return hashes


def verify(root: Path, output: Path, workflow: str = DEFAULT_WORKFLOW, *, ref: str | None = None) -> dict:
    root, output = root.resolve(strict=True), output.absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Checkout verification requires a new output directory")
    if ".." in output.parts or any(parent.is_symlink() for parent in output.parents):
        raise ValueError("Checkout output cannot traverse parents or symlinks")
    if not output.is_relative_to(root / "evals") and not output.is_relative_to(root / "artifacts"):
        raise ValueError("Checkout reports must be under evals or artifacts")
    relative_workflow = Path(workflow)
    if relative_workflow.is_absolute() or ".." in relative_workflow.parts:
        raise ValueError("Workflow must be repository-relative")
    git_source = None
    if ref is None:
        files = snapshot_files(root, output)
        hashes = {str(path.relative_to(root)): digest(path) for path in files}
    else:
        git_source, files = committed_files(root, ref)
        hashes = {}
    output.mkdir(parents=True)
    report = {"report_version": "portfolio-clean-checkout-v2", "status": "failed",
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "mode": "committed-git-tree" if git_source else "clean-source-snapshot",
              "git_source": git_source, "files_copied": len(files), "input_sha256": hashes,
              "verification_script_sha256": digest(Path(__file__)), "python_version": platform.python_version(),
              "checks": [], "scope": (
              "Exact committed Git blobs copied into an isolated temporary tree; working-tree/index changes are excluded. "
              if git_source else "Tracked and nonignored current-change files copied into an isolated temporary tree. ") +
              "No existing artifacts, model weights, runtime assets, virtual environment or CORD source images copied. "
              "Saved invoice/model workflow evidence is audited offline, and the replay is prepared through the public CLI. "
              "The verifier is the invoking script identified by its hash. Remote clone availability, fresh inference, "
              "browser behavior and human review are not checked. Full CORD rescoring requires explicit dataset setup."}
    commands = [
        ("contracts", ["-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"]),
        ("corpus", ["-m", "docwork.cli", "eval-verify-corpus"]),
        ("invoice_baseline", ["-m", "docwork.cli", "eval-verify-heldout", "evals/invoice-heldout-2026-10-03/ocr-rules-v0.3-psm1"]),
        ("invoice_model", ["-m", "docwork.cli", "eval-verify-invoice-model", "evals/invoice-model-heldout-2026-10-03"]),
        ("model_workflow", ["-m", "docwork.cli", "eval-verify-model-workflow", workflow]),
        ("author_pilot", ["scripts/archive_review_pilot.py", "verify", "evals/author-review-pilot-2026-10-03", "--require-complete"]),
        ("post_pilot_corrections", ["scripts/correct_pilot_records.py", "verify", "evals/post-pilot-corrections-2026-10-04-operations.json"]),
        ("offline_replay", ["-m", "docwork.cli", "demo-replay", "--prepare-only", "--output-dir", "artifacts/checkout-replay"]),
    ]
    try:
        with tempfile.TemporaryDirectory(prefix="docwork-release-checkout-") as temporary:
            checkout = Path(temporary)
            if git_source:
                hashes.update(copy_committed_files(root, files, checkout))
            else:
                for path in files:
                    destination = checkout / path.relative_to(root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
                    if digest(destination) != hashes[str(path.relative_to(root))]:
                        raise ValueError("Input changed while copying the source snapshot")
            report["started_without_artifacts"] = not (checkout / "artifacts").exists()
            env = {**os.environ, "PYTHONPATH": str(checkout / "src"), "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONNOUSERSITE": "1"}
            for name, arguments in commands:
                print(f"Clean checkout: {name}", flush=True)
                result = subprocess.run([sys.executable, *arguments], cwd=checkout, env=env,
                                        capture_output=True, text=True, timeout=300)
                log = output / f"{name}.log"
                log.write_text(result.stdout + result.stderr)
                report["checks"].append({"id": name, "status": "passed" if command_passed(name, result) else "failed",
                                         "returncode": result.returncode, "command": [sys.executable, *arguments],
                                         "log_sha256": digest(log)})
                if report["checks"][-1]["status"] == "failed":
                    print((result.stdout + result.stderr)[-4000:], file=sys.stderr, flush=True)
            report["inputs_unchanged"] = (git_source is not None or
                all(digest(root / name) == checksum for name, checksum in hashes.items()))
            report["checkout_inputs_unchanged"] = all(digest(checkout / name) == checksum for name, checksum in hashes.items())
            if (report["inputs_unchanged"] and report["checkout_inputs_unchanged"] and report["started_without_artifacts"] and
                    all(c["status"] == "passed" for c in report["checks"])):
                report["status"] = "passed"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model-workflow", default=DEFAULT_WORKFLOW)
    parser.add_argument("--ref", help="Verify only the exact commit/tag/branch resolved from this Git reference")
    args = parser.parse_args()
    try:
        report = verify(Path(__file__).resolve().parents[1], args.output_dir, args.model_workflow, ref=args.ref)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Checkout verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "files_copied": report["files_copied"],
                      "mode": report["mode"], "git_source": report["git_source"], "failure": report.get("failure"),
                      "checks": report["checks"], "report": str(args.output_dir / "report.json")}, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
