"""Pinned local model assets and a single-use, authenticated evaluation server."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import secrets
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from .evaluation import evaluate_development
from .local_model import LocalModelConfig, PROMPT_SHA256, PROMPT_VERSION, _NoRedirect


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def load_profile(path: Path) -> dict:
    profile = json.loads(path.read_text())
    if profile.get("schema_version") != 1 or profile.get("platform") != "Darwin-arm64":
        raise ValueError("Expected a version 1 Darwin-arm64 model profile")
    for key in ("model", "runtime"):
        asset = profile[key]
        name = asset["filename"]
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("Asset filename must be a single path component")
        if len(asset["sha256"]) != 64 or any(char not in "0123456789abcdef" for char in asset["sha256"]):
            raise ValueError("Asset requires a SHA-256 digest")
        if type(asset["size_bytes"]) is not int or not 0 < asset["size_bytes"] <= 8 * 1024**3:
            raise ValueError("Asset size must be bounded")
        if not asset["url"].startswith("https://") or not asset["license_url"].startswith("https://"):
            raise ValueError("Asset and license downloads require HTTPS")
    directory = profile["runtime"]["directory"]
    if not directory or Path(directory).name != directory or directory in {".", ".."}:
        raise ValueError("Runtime directory must be a single path component")
    inference = profile["inference"]
    if (inference["context_tokens"] != 8192 or inference["parallel"] != 1
            or inference["gpu_layers"] != "all" or inference["temperature"] != 0
            or type(inference["seed"]) is not int):
        raise ValueError("This profile requires one 8192-token slot, greedy decoding, and Metal offload")
    LocalModelConfig("http://127.0.0.1:1", inference["model_id"],
                     inference["timeout_seconds"], inference["max_output_tokens"])
    return profile


def asset_paths(root: Path, profile: dict) -> dict[str, Path]:
    return {key: root / "artifacts" / folder / profile[key]["filename"]
            for key, folder in (("model", "models"), ("runtime", "runtime"))}


def verify_assets(root: Path, profile: dict) -> dict[str, Path]:
    paths = asset_paths(root, profile)
    for key, path in paths.items():
        expected = profile[key]
        if path.stat().st_size != expected["size_bytes"] or file_hash(path) != expected["sha256"]:
            raise ValueError(f"{key} asset differs from pinned size or SHA-256")
    return paths


def _download(url: str, destination: Path, maximum: int, digest: str | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as target:
            temporary = Path(target.name)
            with urllib.request.urlopen(url, timeout=60) as response:
                if not response.url.startswith("https://"):
                    raise ValueError("Download redirected away from HTTPS")
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > maximum:
                        raise ValueError("Download exceeds pinned size limit")
                    target.write(chunk)
        if digest is not None and (temporary.stat().st_size != maximum or file_hash(temporary) != digest):
            raise ValueError("Downloaded asset differs from pinned size or SHA-256")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def fetch_assets(root: Path, profile: dict) -> dict:
    """Explicit downloads only; existing mismatched files are never overwritten."""
    paths = asset_paths(root, profile)
    for key, path in paths.items():
        asset = profile[key]
        if path.exists():
            if path.stat().st_size != asset["size_bytes"] or file_hash(path) != asset["sha256"]:
                raise ValueError(f"Existing {key} asset is not the pinned file; move it aside first")
        else:
            _download(asset["url"], path, asset["size_bytes"], asset["sha256"])
        license_path = path.with_name(path.name + ".LICENSE")
        if not license_path.exists():
            _download(asset["license_url"], license_path, 128 * 1024)
    return {"profile": profile["profile"], "status": "verified", "assets": {
        key: {"sha256": profile[key]["sha256"], "size_bytes": path.stat().st_size}
        for key, path in paths.items()
    }}


def _extract_runtime(archive: Path, directory: Path, expected_root: str) -> Path:
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > 500 or sum(member.size for member in members) > 256 * 1024**2:
            raise ValueError("Runtime bundle exceeds extraction limits")
        for member in members:
            parts = Path(member.name).parts
            if not parts or parts[0] != expected_root or ".." in parts:
                raise ValueError("Runtime bundle contains an unexpected path")
        bundle.extractall(directory, filter="data")
    executable = directory / expected_root / "llama-server"
    if executable.is_symlink() or not executable.is_file():
        raise ValueError("Runtime bundle lacks a regular llama-server executable")
    return executable


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@contextmanager
def managed_server(root: Path, profile: dict, log_path: Path):
    """Start our own verified process, never attach to an arbitrary existing server."""
    if f"{platform.system()}-{platform.machine()}" != profile["platform"]:
        raise ValueError("This pinned runtime requires an Apple Silicon Mac")
    paths = verify_assets(root, profile)
    with tempfile.TemporaryDirectory(prefix="docwork-model-", dir=root / "artifacts" / "runtime") as scratch:
        executable = _extract_runtime(paths["runtime"], Path(scratch), profile["runtime"]["directory"])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        key = secrets.token_urlsafe(32)
        settings = profile["inference"]
        config = LocalModelConfig(f"http://127.0.0.1:{port}", settings["model_id"],
                                  settings["timeout_seconds"], settings["max_output_tokens"], api_key=key)
        command = [str(executable), "-m", str(paths["model"]), "--host", "127.0.0.1", "--port", str(port),
                   "--ctx-size", str(settings["context_tokens"]), "--parallel", "1", "--gpu-layers", "all",
                   "--alias", config.model_id, "--seed", str(settings["seed"]), "--api-key", key,
                   "--no-agent", "--no-webui", "--cors-origins", "http://127.0.0.1", "--offline",
                   "--log-verbosity", "4"]
        environment = {name: value for name, value in os.environ.items()
                       if name in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}}
        metadata = {"model_sha256": profile["model"]["sha256"],
                    "runtime_archive_sha256": profile["runtime"]["sha256"],
                    "runtime_executable_sha256": file_hash(executable),
                    "runtime_release": profile["runtime"]["release"],
                    "runtime_commit": profile["runtime"]["commit"],
                    "inference": settings, "peak_sampled_rss_bytes": None,
                    "rss_sample_count": 0, "rss_interval_seconds": 1,
                    "memory_note": "Sampled server process RSS only; not peak GPU allocation or total application memory.",
                    "shutdown_complete": False}
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stopped = threading.Event()
        start = time.perf_counter()
        with log_path.open("wb") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=environment)

            def sample_memory():
                while not stopped.is_set():
                    try:
                        sampled = subprocess.run(["ps", "-o", "rss=", "-p", str(process.pid)],
                                                 capture_output=True, text=True, timeout=2)
                        if sampled.returncode == 0 and sampled.stdout.strip():
                            rss = int(sampled.stdout.strip()) * 1024
                            metadata["peak_sampled_rss_bytes"] = max(metadata["peak_sampled_rss_bytes"] or 0, rss)
                            metadata["rss_sample_count"] += 1
                    except (OSError, ValueError, subprocess.TimeoutExpired):
                        pass
                    stopped.wait(1)

            monitor = threading.Thread(target=sample_memory, daemon=True)
            monitor.start()
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
                deadline = time.monotonic() + 120
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f"Local model server exited with status {process.returncode}; see server.log")
                    try:
                        request = urllib.request.Request(config.endpoint + "/v1/models",
                                                         headers={"Authorization": f"Bearer {key}"})
                        with opener.open(request, timeout=2) as response:
                            models = json.loads(response.read(65536))
                        if config.model_id in {model["id"] for model in models["data"]}:
                            break
                    except (OSError, ValueError, KeyError, urllib.error.URLError):
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Local model server did not become ready within 120 seconds")
                    stopped.wait(.25)
                metadata["startup_seconds"] = round(time.perf_counter() - start, 3)
                yield config, metadata
            finally:
                _stop_process(process)
                stopped.set()
                monitor.join(timeout=3)
                metadata["shutdown_complete"] = process.poll() is not None
                metadata["server_lifetime_seconds"] = round(time.perf_counter() - start, 3)
                # Runtime diagnostics must not retain the per-process credential.
                log_path.write_bytes(log_path.read_bytes().replace(key.encode(), b"<redacted>"))


def run_managed_evaluation(root: Path, profile_path: Path, output: Path,
                           fixture_runner: Callable[[Path, LocalModelConfig], dict]) -> dict:
    profile = load_profile(profile_path)
    # A fresh directory protects previous evidence against retries and partial runs.
    output.mkdir(parents=True, exist_ok=False)
    predictions = output / "predictions"
    predictions.mkdir()
    profile_bytes = profile_path.read_bytes()
    (output / "profile.json").write_bytes(profile_bytes)
    source_files = ("model_runtime.py", "local_model.py", "evaluation.py", "cli.py", "contracts.py",
                    "ocr.py", "validation.py", "geometry.py", "baseline.py")
    (output / "source_snapshot.json").write_text(json.dumps({
        name: Path(__file__).with_name(name).read_text() for name in source_files
    }, indent=2) + "\n")
    log_path = output / "server.log"
    started = time.perf_counter()
    try:
        with managed_server(root, profile, log_path) as (config, runtime):
            def run_fixture(path):
                print(f"Extracting {path.name}", flush=True)
                try:
                    result = fixture_runner(path, config)
                except Exception as exc:
                    (predictions / f"{path.stem}.json").write_text(json.dumps({
                        "source_sha256": file_hash(path), "failure_type": type(exc).__name__,
                    }, indent=2) + "\n")
                    raise
                result["source_sha256"] = file_hash(path)
                (predictions / f"{path.stem}.json").write_text(json.dumps(result, indent=2) + "\n")
                print(f"  {result.get('failure_type', 'processed')}: {result['runtime_seconds']}", flush=True)
                return result

            report = evaluate_development(root, run_fixture, extractor={
                "variant": "span_llm", "version": PROMPT_VERSION, "model_id": config.model_id,
                "prompt_sha256": PROMPT_SHA256, "timeout_seconds": config.timeout_seconds,
                "max_output_tokens": config.max_output_tokens,
                "weights_identity": "hash-verified assets loaded by an owned, authenticated child process",
                "model_sha256": profile["model"]["sha256"],
                "runtime_archive_sha256": profile["runtime"]["sha256"],
                "profile_sha256": hashlib.sha256(profile_bytes).hexdigest(),
            })
            report.pop("baseline_version", None)
        report["managed_runtime"] = runtime
        report["evaluation_wall_seconds"] = round(time.perf_counter() - started, 3)
        report["timing_protocol"] = "Serial documents after model readiness; no explicit warmup; first request is cold."
        report["artifacts"] = {str(path.relative_to(output)): file_hash(path)
                               for path in sorted(output.rglob("*")) if path.is_file()}
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
    except BaseException as exc:
        (output / "failure.json").write_text(json.dumps({"failure_type": type(exc).__name__,
                                                       "completed": False}, indent=2) + "\n")
        raise
