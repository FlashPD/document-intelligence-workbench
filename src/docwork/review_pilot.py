"""Explicit author-pilot timing, separate from extraction and approval state."""

from __future__ import annotations

import hashlib
import fcntl
import json
import math
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .review import ReviewBlocked, ReviewConflict, _now

PILOT_VERSION = "assisted-author-pilot-v1"


def durations(events: list[dict], idle_seconds: int) -> dict:
    """Reading counts until the declared idle cutoff; pause intervals never count."""
    active = idle = paused = 0.0
    prior = 0.0
    running = True
    if type(idle_seconds) is not int or not 1 <= idle_seconds <= 3600:
        raise ValueError("Invalid pilot idle cutoff")
    for event in events:
        offset = event["offset_seconds"]
        if type(offset) not in (float, int) or not math.isfinite(offset) or offset < prior:
            raise ValueError("Pilot clock moved backwards")
        gap = offset - prior
        if running:
            active += min(gap, idle_seconds)
            idle += max(0, gap - idle_seconds)
        else:
            paused += gap
        if event["kind"] == "pause":
            running = False
        elif event["kind"] == "resume":
            running = True
        prior = offset
    return {"active_seconds": round(active, 3), "idle_seconds": round(idle, 3),
            "paused_seconds": round(paused, 3), "elapsed_seconds": round(prior, 3)}


class ReviewPilot:
    def __init__(self, directory: Path, store, *, clock=time.monotonic):
        self.directory = directory.resolve(strict=True)
        self.store, self.clock = store, clock
        self.protocol = json.loads((self.directory / "protocol.json").read_text())
        if self.protocol["pilot_version"] != PILOT_VERSION:
            raise ValueError("Unknown review pilot protocol")
        if self.store.database != self.directory / "review.sqlite":
            raise ValueError("Pilot must use its dedicated review database")
        self.documents = {doc["document_id"]: doc for doc in self.protocol["documents"]}
        self.protocol_hash = hashlib.sha256((self.directory / "protocol.json").read_bytes()).hexdigest()
        self.path = self.directory / "timing.json"
        self.lock = threading.RLock()
        self.starts = {}
        self._lock_stream = (self.directory / ".pilot.lock").open("a")
        try:
            fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_stream.close()
            raise ReviewConflict("Another pilot server owns this session") from exc
        self.data = (json.loads(self.path.read_text()) if self.path.exists()
                     else {"pilot_version": PILOT_VERSION, "protocol_sha256": self.protocol_hash, "trials": []})
        if self.data["protocol_sha256"] != self.protocol_hash:
            raise ValueError("Timing evidence belongs to a different protocol")
        # Server-monotonic timing cannot bridge a restart or machine reboot.
        # Keep interrupted participants in the denominator rather than retiming.
        for trial in self.data["trials"]:
            if trial["status"] in ("RUNNING", "PAUSED"):
                trial["status"] = "ABANDONED"
                trial["abandon_reason"] = "server_restarted; unobserved interval excluded"
        self._save()

    def _save(self):
        temporary = None
        try:
            fd, name = tempfile.mkstemp(prefix=".timing-", dir=self.directory)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write((json.dumps(self.data, indent=2) + "\n").encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, ValueError):
            if self.path.exists():
                self.data = json.loads(self.path.read_text())
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def close(self):
        if hasattr(self, "_lock_stream") and not self._lock_stream.closed:
            self._lock_stream.close()

    def __del__(self):
        self.close()

    def view(self) -> dict:
        with self.lock:
            trials = [{**trial, **durations(trial["events"], self.protocol["idle_cutoff_seconds"])}
                      for trial in self.data["trials"]]
            consumed = {trial["document_id"] for trial in trials}
            next_doc = next((doc for doc in self.protocol["documents"] if doc["document_id"] not in consumed), None)
            return {"pilot_version": PILOT_VERSION, "scheduled": len(self.documents),
                    "idle_cutoff_seconds": self.protocol["idle_cutoff_seconds"],
                    "next_document": next_doc, "trials": trials,
                    "scope": "Author assisted review of precomputed OCR/rules suggestions; no manual-entry comparison."}

    def start(self, document_id: str, actor: str) -> dict:
        if not actor.strip() or len(actor) > 100:
            raise ValueError("A reviewer audit label of at most 100 characters is required")
        with self.lock:
            if any(t["status"] in ("RUNNING", "PAUSED") for t in self.data["trials"]):
                raise ReviewConflict("A pilot trial is already open")
            if not self.view()["next_document"] or self.view()["next_document"]["document_id"] != document_id:
                raise ReviewConflict("Use the next declared pilot document; trials cannot be repeated")
            detail = self.store.get(document_id)
            if (detail["revision"] != 1 or detail["approval"] is not None or
                    detail["record_hash"] != self.documents[document_id]["initial_record_hash"]):
                raise ReviewConflict("Pilot candidate changed before timing began")
            trial = {"id": uuid.uuid4().hex, "document_id": document_id, "actor": actor,
                     "status": "RUNNING", "started_at": _now(), "initial_record_hash": detail["record_hash"],
                     "events": [{"id": uuid.uuid4().hex, "kind": "start", "offset_seconds": 0.0, "recorded_at": _now()}]}
            self.starts[trial["id"]] = self.clock()
            self.data["trials"].append(trial)
            self._save()
            return self.view()

    def guard(self, document_id: str, actor: str | None = None):
        with self.lock:
            trial = next((t for t in self.data["trials"] if t["document_id"] == document_id), None)
            if trial is None or trial["status"] != "RUNNING":
                raise ReviewBlocked("Start or resume this pilot trial before changing the record")
            if actor is not None and trial["actor"] != actor:
                raise ReviewConflict("Keep the same reviewer audit label during the trial")

    def guard_view(self, document_id: str):
        with self.lock:
            if not any(t["document_id"] == document_id for t in self.data["trials"]):
                raise ReviewBlocked("Start the declared pilot trial before opening its source or suggestions")

    def event(self, trial_id: str, kind: str, event_id: str) -> dict:
        if kind not in ("interaction", "pause", "resume", "finish", "abandon"):
            raise ValueError("Unknown pilot event")
        if not isinstance(event_id, str) or len(event_id) != 32 or any(c not in "0123456789abcdef" for c in event_id):
            raise ValueError("A hexadecimal event ID is required")
        with self.lock:
            trial = next((t for t in self.data["trials"] if t["id"] == trial_id), None)
            if trial is None:
                raise KeyError("Unknown pilot trial")
            previous = next((e for e in trial["events"] if e["id"] == event_id), None)
            if previous:
                if previous["kind"] != kind:
                    raise ReviewConflict("Pilot event ID reused with a different action")
                return self.view()
            if trial["status"] not in ("RUNNING", "PAUSED"):
                raise ReviewConflict("Pilot trial is already closed")
            if kind == "resume" and trial["status"] != "PAUSED":
                raise ReviewConflict("Trial is not paused")
            if kind in ("interaction", "pause", "finish") and trial["status"] != "RUNNING":
                raise ReviewConflict("Resume the trial first")
            final = None
            if kind == "finish":
                detail = self.store.get(trial["document_id"])
                if not detail["approval"] or detail["approval"]["actor"] != trial["actor"]:
                    raise ReviewBlocked("Approve the current revision with the trial reviewer label first")
                try:
                    exported, _ = self.store.exported_file(trial["document_id"], detail["revision"], "json", "invoice.json")
                except KeyError as exc:
                    raise ReviewBlocked("Create the approved JSON export before finishing") from exc
                payload = json.loads(exported)
                if payload["record_hash"] != detail["record_hash"]:
                    raise ReviewConflict("Export differs from the current approved record")
                history = self.store.history(trial["document_id"])
                final = {"revision": detail["revision"], "record_hash": detail["record_hash"],
                         "approval_hash": detail["approval"]["approval_hash"],
                         "export_sha256": hashlib.sha256(exported).hexdigest(),
                         "corrections": sum(e["kind"] == "field_edited" for e in history),
                         "acknowledgments": sum(e["kind"] == "issue_acknowledged" for e in history)}
            trial["events"].append({"id": event_id, "kind": kind,
                                    "offset_seconds": self.clock() - self.starts[trial_id], "recorded_at": _now()})
            if kind in ("pause", "resume", "finish", "abandon"):
                trial["status"] = {"pause": "PAUSED", "resume": "RUNNING", "finish": "COMPLETE", "abandon": "ABANDONED"}[kind]
            if final:
                trial["final"] = final
            self._save()
            return self.view()
