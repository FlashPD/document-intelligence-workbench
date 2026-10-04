"""Fresh-source explicit model setup followed by network-restricted live workflow.

Apple Silicon only. Docker/Python are host prerequisites; parser builds have their
own verifier. --download obtains pinned assets; --asset-source transfers already
verified bytes instead and does not claim fresh network acquisition.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from verify_release_checkout import digest, snapshot_files

POLICY = '''(version 1)
(allow default)
(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))
(allow network-outbound (remote unix-socket))
'''
DENIAL_PROBE = '''import errno, json, socket
s = socket.socket()
s.settimeout(2)
try:
    s.connect(("192.0.2.1", 443))
except OSError as error:
    assert error.errno == errno.EPERM, repr(error)
    print(json.dumps({"external_connect_errno": error.errno}))
else:
    raise RuntimeError("External connection was permitted")
finally:
    s.close()
'''
NETWORK_PROBE = '''import json, socket, subprocess, sys
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    with socket.create_connection(listener.getsockname(), timeout=2):
        peer, _ = listener.accept()
        peer.close()
exec(%r)
child = subprocess.run([sys.executable, "-c", %r], capture_output=True, text=True, check=True)
assert json.loads(child.stdout)["external_connect_errno"] == 1
print(json.dumps({"loopback": "passed", "parent_external_denial": "passed", "child_external_denial": "passed"}))
''' % (DENIAL_PROBE, DENIAL_PROBE)
MISSING_PROBE = '''from pathlib import Path
from docwork.model_runtime import load_profile, managed_server
root = Path.cwd()
assert not (root / "artifacts").exists()
try:
    with managed_server(root, load_profile(root / "config/model-mac-instruct.json"), root / "artifacts/server.log"):
        raise RuntimeError("Missing assets unexpectedly started a model")
except FileNotFoundError:
    assert not (root / "artifacts").exists()
    print("Missing assets refused; no runtime/download artifacts created")
else:
    raise RuntimeError("Missing assets did not fail")
'''
CHECKS = ("network_policy", "missing_assets", "explicit_setup", "offline_assets",
          "offline_fetch_reuse", "offline_workflow", "saved_workflow")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def run_process(command, *, cwd, env, stream, timeout):
    """Give owned Python children time to unwind before stopping their session."""
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stream,
                               stderr=subprocess.STDOUT, start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    except BaseException:
        # SIGINT raises KeyboardInterrupt in Python, allowing managed_server and
        # parser cleanup to unwind. A subprocess.run timeout would kill only the
        # wrapper immediately, potentially leaving its native descendants alive.
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        raise
    finally:
        # Catch descendants left by an abnormal wrapper exit. The new session
        # contains only this owned command's children, never existing servers.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def transfer_assets(source: Path, checkout: Path):
    from docwork.model_runtime import asset_paths, load_profile, verify_assets
    profile = load_profile(checkout / "config/model-mac-instruct.json")
    paths = verify_assets(source, profile)
    destinations = asset_paths(checkout, profile)
    for key, path in paths.items():
        destination = destinations[key]
        destination.parent.mkdir(parents=True, exist_ok=True)
        license_path = path.with_name(path.name + ".LICENSE")
        if path.is_symlink() or license_path.is_symlink() or not license_path.is_file():
            raise ValueError("Explicit asset transfer requires regular assets and license files")
        # Copies are independent: no symlinks/hard links into the existing cache.
        shutil.copyfile(path, destination)
        shutil.copyfile(license_path, destination.with_name(destination.name + ".LICENSE"))
    verify_assets(checkout, profile)


def verify(root: Path, output: Path, *, download=False, asset_source: Path | None = None):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("Live setup verification requires the supported Apple Silicon Mac")
    if download == (asset_source is not None):
        raise ValueError("Choose explicit downloads or verified local asset transfer")
    root, output = root.resolve(strict=True), output.absolute()
    if (output.exists() or output.is_symlink() or ".." in output.parts or
            any(parent.is_symlink() for parent in output.parents) or
            not output.is_relative_to(root / "artifacts")):
        raise ValueError("Use a new nonsymlink output directory under artifacts")
    files = snapshot_files(root, output)
    hashes = {str(path.relative_to(root)): digest(path) for path in files}
    output.mkdir(parents=True)
    policy_path = output / "offline.sb"
    policy_path.write_text(POLICY)
    # Retain implementation, not runtime caches or the complete historical corpus.
    snapshot = {name: (root / name).read_text() for name in hashes
                if Path(name).parts[0] in {"src", "scripts", "config", "ui", "sandbox"}
                or name in {"Makefile", ".dockerignore", "pyproject.toml"}}
    write_json(output / "source_snapshot.json", snapshot)
    report = {"report_version": "fresh-model-setup-v1", "status": "failed",
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "host": {"system": platform.system(), "machine": platform.machine(),
                       "python": platform.python_version()},
              "setup_mode": "download" if download else "verified-local-transfer",
              "input_sha256": hashes, "files_copied": len(files), "checks": [],
              "scope": "Fresh current working-source snapshot; explicit pinned model/runtime setup, then "
                       "live fictional upload/review/export under inherited macOS outbound-network policy. "
                       "Host Docker/Python and installed parser image are prerequisites; this is not a new "
                       "machine install, browser/human study, memory gate, or published-tag verification. "
                       "Local sockets remain available; Docker daemon/services are outside the process policy, "
                       "while parser containers use their own network-none boundary."}
    try:
        with tempfile.TemporaryDirectory(prefix="docwork-setup-") as temporary:
            checkout = Path(temporary)
            for path in files:
                destination = checkout / path.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
                if digest(destination) != hashes[str(path.relative_to(root))]:
                    raise ValueError("Source changed while copying")
            report["started_without_artifacts"] = not (checkout / "artifacts").exists()
            environment = {name: value for name, value in os.environ.items()
                           if name in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}}
            environment.update(PYTHONPATH=str(checkout / "src"), PYTHONNOUSERSITE="1",
                               PYTHONDONTWRITEBYTECODE="1")

            def run(name, arguments, *, offline=True, timeout=900):
                print(f"Fresh setup: {name}", flush=True)
                command = [sys.executable, *arguments]
                if offline:
                    command = ["/usr/bin/sandbox-exec", "-f", str(policy_path), *command]
                start = time.monotonic()
                log = output / f"{name}.log"
                # Stream to disk: failures/timeouts retain partial diagnostics.
                with log.open("xb") as stream:
                    returncode = run_process(command, cwd=checkout, env=environment, stream=stream, timeout=timeout)
                report["checks"].append({"id": name, "status": "passed" if returncode == 0 else "failed",
                                         "returncode": returncode, "offline_policy": offline,
                                         "command": command, "seconds": round(time.monotonic() - start, 3)})
                if returncode:
                    raise RuntimeError(f"{name} failed; see {log.name}")

            run("network_policy", ["-c", NETWORK_PROBE], timeout=15)
            run("missing_assets", ["-c", MISSING_PROBE], timeout=15)
            if asset_source is not None:
                transfer_assets(asset_source.resolve(strict=True), checkout)
            run("explicit_setup", ["-m", "docwork.cli", "models", "fetch"], offline=not download, timeout=1800)
            run("offline_assets", ["-m", "docwork.cli", "models", "verify"], timeout=60)
            run("offline_fetch_reuse", ["-m", "docwork.cli", "models", "fetch"], timeout=60)
            workflow = output / "workflow"
            run("offline_workflow", ["scripts/verify_model_workflow.py", "--output-dir", str(workflow)])
            run("saved_workflow", ["-m", "docwork.cli", "eval-verify-model-workflow", str(workflow)], timeout=60)
            from docwork.workflow_evidence import verify_workflow_evidence
            verify_workflow_evidence(workflow)
            from docwork.model_runtime import asset_paths, file_hash, load_profile
            profile = load_profile(checkout / "config/model-mac-instruct.json")
            write_json(output / "profile.json", profile)
            report["setup_assets"] = {
                key: {"sha256": file_hash(path), "size_bytes": path.stat().st_size,
                      "license_sha256": file_hash(path.with_name(path.name + ".LICENSE"))}
                for key, path in asset_paths(checkout, profile).items()}
            report["inputs_unchanged"] = all(digest(root / name) == checksum for name, checksum in hashes.items())
            report["checkout_inputs_unchanged"] = all(digest(checkout / name) == checksum for name, checksum in hashes.items())
            report["runtime_scratch_removed"] = not list((checkout / "artifacts/runtime").glob("docwork-model-*"))
            if not all(report[key] for key in ("started_without_artifacts", "inputs_unchanged",
                                               "checkout_inputs_unchanged", "runtime_scratch_removed")):
                raise ValueError("Source changed, assets preexisted, or owned runtime scratch remains")
            report["status"] = "passed"
    except (OSError, ValueError, RuntimeError, KeyboardInterrupt, subprocess.SubprocessError) as error:
        report["failure"] = f"{type(error).__name__}: {error}"
    report["artifacts"] = {str(path.relative_to(output)): digest(path)
                           for path in sorted(output.rglob("*")) if path.is_file()}
    write_json(output / "report.json", report)
    return report


def verify_saved(directory: Path):
    from docwork.model_runtime import load_profile
    from docwork.workflow_evidence import verify_workflow_evidence
    directory = directory.resolve(strict=True)
    report = json.loads((directory / "report.json").read_text())
    if report.get("report_version") != "fresh-model-setup-v1" or report.get("status") != "passed":
        raise ValueError("Setup evidence is not a completed passing run")
    for key in ("started_without_artifacts", "inputs_unchanged", "checkout_inputs_unchanged", "runtime_scratch_removed"):
        if report.get(key) is not True:
            raise ValueError(f"Setup invariant missing: {key}")
    if report.get("setup_mode") not in {"download", "verified-local-transfer"}:
        raise ValueError("Unknown setup mode")
    if [check["id"] for check in report["checks"]] != list(CHECKS):
        raise ValueError("Setup schedule is incomplete or reordered")
    for check in report["checks"]:
        expected_offline = check["id"] != "explicit_setup" or report["setup_mode"] != "download"
        if check["status"] != "passed" or check["returncode"] != 0 or check["offline_policy"] is not expected_offline:
            raise ValueError("Setup check failed or its network boundary differs")
        if (check["command"][:2] == ["/usr/bin/sandbox-exec", "-f"]) is not expected_offline:
            raise ValueError("Setup command did not invoke the declared offline boundary")
    actual = {str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file()}
    if actual != set(report["artifacts"]) | {"report.json"}:
        raise ValueError("Setup artifact inventory differs")
    for name, checksum in report["artifacts"].items():
        relative = Path(name)
        if (relative.is_absolute() or ".." in relative.parts or str(relative) != name or
                any((directory / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)) or
                digest(directory / name) != checksum):
            raise ValueError("Setup artifact path or checksum differs")
    if (directory / "offline.sb").read_text() != POLICY:
        raise ValueError("Offline policy differs")
    probe = json.loads((directory / "network_policy.log").read_text().splitlines()[-1])
    if probe != {"loopback": "passed", "parent_external_denial": "passed", "child_external_denial": "passed"}:
        raise ValueError("Network policy probe did not pass")
    if "Missing assets refused; no runtime/download artifacts created" not in (directory / "missing_assets.log").read_text():
        raise ValueError("Missing assets refusal was not recorded")
    source = json.loads((directory / "source_snapshot.json").read_text())
    import hashlib
    if not source or any(hashlib.sha256(value.encode()).hexdigest() != report["input_sha256"].get(name)
                         for name, value in source.items()):
        raise ValueError("Setup source snapshot differs")
    profile = load_profile(directory / "profile.json")
    for key in ("model", "runtime"):
        if any(report["setup_assets"][key][field] != profile[key][field] for field in ("sha256", "size_bytes")):
            raise ValueError("Setup assets differ from profile")
    verify_workflow_evidence(directory / "workflow")
    workflow = json.loads((directory / "workflow/report.json").read_text())
    # The nested verifier checks its complete workflow snapshot (including the
    # Dockerfile). Cross-bind those hashes to the original checkout inventory;
    # they need not also appear in the enclosing verifier's source-text copy.
    if "scripts/verify_model_setup.py" not in source:
        raise ValueError("Setup source snapshot omits the setup verifier")
    if any(checksum != report["input_sha256"].get(name) for name, checksum in workflow["source_sha256"].items()):
        raise ValueError("Live workflow did not use the fresh source snapshot")
    if json.loads((directory / "workflow/profile.json").read_text()) != profile:
        raise ValueError("Live workflow profile differs from setup")
    return {"status": "verified", "setup_mode": report["setup_mode"], "checks": len(CHECKS),
            "note": "Saved evidence integrity; no fresh setup/inference or independent attestation."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify", type=Path, help="Audit a saved bundle offline")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--download", action="store_true")
    mode.add_argument("--asset-source", type=Path)
    args = parser.parse_args()
    if bool(args.verify) == bool(args.output_dir) or (args.verify and (args.download or args.asset_source)):
        parser.error("Choose saved verification or a new output with an explicit setup mode")
    try:
        report = (verify_saved(args.verify) if args.verify else
                  verify(Path(__file__).resolve().parents[1], args.output_dir,
                         download=args.download, asset_source=args.asset_source))
        print(json.dumps({key: report[key] for key in ("status", "setup_mode")}, indent=2))
        return 0 if report["status"] in {"passed", "verified"} else 2
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print(f"Setup verification failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
