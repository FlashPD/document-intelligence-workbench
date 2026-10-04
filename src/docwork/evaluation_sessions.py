"""Account for every model session, including abrupt loss of runtime metadata."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .model_runtime import file_hash


def latest_session_stopped(directory: Path) -> bool:
    """Older missing metadata must not hide a later stopped attempt."""
    sessions = directory / "sessions"
    entries = sorted(sessions.iterdir()) if sessions.is_dir() else []
    return bool(entries and (entries[-1] / "runtime.json").is_file())


def audit_sessions(directory: Path) -> dict:
    """Validate recorded lifecycle evidence without inventing missing measurements.

    A missing runtime.json is never a running-process signal. Interrupted logs
    require a separate, hash-bound observation; normal runtime checks remain in
    the domain verifier. Local observations are not signed attestation.
    """
    sessions = directory / "sessions"
    if sessions.is_symlink() or not sessions.is_dir():
        raise ValueError("Model run lacks a regular sessions directory")
    entries = sorted(sessions.iterdir())
    if not entries:
        raise ValueError("Model run has no owned sessions")
    recorded, interrupted = [], []
    for session in entries:
        if session.is_symlink() or not session.is_dir() or not re.fullmatch(r"\d{4}", session.name):
            raise ValueError("Unexpected model session entry")
        if any(path.is_symlink() for path in session.iterdir()):
            raise ValueError("Model session evidence cannot contain symlinks")
        log = session / "server.log"
        if not log.is_file():
            raise ValueError("Model session lacks its server log")
        runtime, observation = session / "runtime.json", session / "interruption.json"
        if runtime.is_file():
            metadata = json.loads(runtime.read_text())
            if metadata.get("shutdown_complete") is not True or observation.exists():
                raise ValueError("Model lifecycle has unverified or contradictory shutdown evidence")
            recorded.append(session.name)
        else:
            if not observation.is_file():
                raise ValueError(f"Session {session.name} has no runtime metadata or interruption observation")
            data = json.loads(observation.read_text())
            when = datetime.fromisoformat(data["observed_at_utc"])
            if (data.get("version") != "model-session-interruption-v1" or when.utcoffset() is None
                    or data.get("session") != session.name or data.get("process_observation") != "no_project_model_or_evaluation_process_observed"
                    or data.get("shutdown_complete") is not None or data.get("peak_sampled_rss_bytes") is not None
                    or data.get("server_log_sha256") != file_hash(log)
                    or data.get("freeze_sha256") != file_hash(directory / "freeze.json")
                    or not isinstance(data.get("completed_predictions"), dict) or not data["completed_predictions"]
                    or not isinstance(data.get("limitations"), str) or not data["limitations"].strip()):
                raise ValueError("Interrupted model session observation is invalid")
            for name, digest in data["completed_predictions"].items():
                if Path(name).name != name or not name.endswith(".json"):
                    raise ValueError("Interrupted-session prediction path is unsafe")
                path = directory / "predictions" / name
                if path.is_symlink() or not path.is_file() or file_hash(path) != digest:
                    raise ValueError("Interrupted-session completed prediction changed")
            interrupted.append({"session": session.name, "completed_predictions": len(data["completed_predictions"]),
                                "observation_sha256": file_hash(observation), "limitations": data["limitations"]})
    return {"status": "recorded_with_interruptions" if interrupted else "recorded",
            "recorded_runtime_sessions": recorded, "interrupted_sessions": interrupted,
            "memory_coverage": "Recorded runtime sessions only; interrupted-session RSS and shutdown metadata are unavailable."
                               if interrupted else "Recorded runtime sessions only; process RSS is sampled, not a GPU or workbench peak."}
