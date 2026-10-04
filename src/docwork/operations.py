"""Content-free operational snapshots from durable attempt and stage history."""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .storage_budget import StorageLimitExceeded
from .worker import PARSER_IMAGE, _docker_image_id

STAGES = ("PARSING", "EXTRACTING", "CHECKING")
STATUSES = ("QUEUED", "PROCESSING", "COMPLETE", "FAILED", "CANCELLED")


def seconds(value):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.timestamp() if stamp.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def distribution(values):
    values = sorted(value for value in values if math.isfinite(value) and value >= 0)
    return {"count": len(values), "sum_seconds": round(sum(values), 6),
            "p50_seconds": round(values[math.ceil(.5 * len(values)) - 1], 6) if values else None,
            "p95_seconds": round(values[math.ceil(.95 * len(values)) - 1], 6) if values else None}


def snapshot(store, *, now=None):
    now = time.time() if now is None else now
    with store._connect() as db:
        # One read transaction avoids combining jobs and events from different states.
        db.execute("BEGIN")
        jobs = [dict(row) for row in db.execute("SELECT document_id,status,stage,attempts FROM jobs")]
        attempts = [dict(row) for row in db.execute(
            "SELECT document_id,fence,status,started_at,finished_at,error_code FROM processing_attempts")]
        events = [dict(row) for row in db.execute(
            "SELECT document_id,kind,detail,created_at FROM review_events WHERE kind IN "
            "('received','retried','reprocess_requested','processing_stage') ORDER BY id")]
    queued_at, queue_events, stage_events = {}, defaultdict(list), defaultdict(list)
    for event in events:
        stamp = seconds(event["created_at"])
        if stamp is None:
            continue
        doc = event["document_id"]
        if event["kind"] != "processing_stage":
            queued_at[doc] = stamp
            queue_events[doc].append(stamp)
        else:
            try:
                detail = json.loads(event["detail"])
                if type(detail["fence"]) is int and detail["stage"] in STAGES:
                    stage_events[(doc, detail["fence"])].append((stamp, detail["stage"]))
            except (KeyError, ValueError, TypeError):
                continue
    times = defaultdict(list)
    failures, outcomes = Counter(), Counter()
    incomplete = 0
    review_waits = []
    for attempt in attempts:
        status = attempt["status"]
        outcomes[status if status in (*STATUSES, "ABANDONED", "RESTORED", "CLEANUP_REQUIRED") else "OTHER"] += 1
        if status == "FAILED":
            # Only application failure categories are published, never stored free text.
            from .worker import KNOWN_REJECTIONS
            allowed = KNOWN_REJECTIONS | {"SOURCE_INTEGRITY_FAILED", "MODEL_UNAVAILABLE", "MODEL_API_REJECTED",
                "MODEL_CONTEXT_OVERFLOW", "MODEL_OUTPUT_INVALID", "PARSER_OUTPUT_INVALID", "PARSER_TIMEOUT",
                "PARSER_KILLED", "PARSER_FAILED", "PARSER_UNAVAILABLE", "PARSER_IMAGE_MISSING", "PARSER_IDENTITY_INVALID",
                "PARSER_CLEANUP_FAILED", "PARSER_CHECKPOINT_INVALID", "PROCESSING_FAILED",
                "STORAGE_QUOTA_EXCEEDED", "INSUFFICIENT_DISK_SPACE"}
            failures[attempt["error_code"] if attempt["error_code"] in allowed else "OTHER"] += 1
        start, end = seconds(attempt["started_at"]), seconds(attempt["finished_at"])
        if start is None or end is None or end < start:
            incomplete += 1
            continue
        times["processing"].append(end - start)
        submitted = [stamp for stamp in queue_events[attempt["document_id"]] if stamp <= start]
        if submitted:
            times["queue"].append(start - max(submitted))
        stages = stage_events[(attempt["document_id"], attempt["fence"])]
        for index, (stamp, stage) in enumerate(stages):
            stop = stages[index + 1][0] if index + 1 < len(stages) else end
            if start <= stamp <= stop <= end:
                times[stage.lower()].append(stop - stamp)
    ages = [max(0, now - queued_at[job["document_id"]]) for job in jobs
            if job["status"] == "QUEUED" and job["document_id"] in queued_at]
    # Review wait is elapsed time awaiting a person, never active review time.
    with store._connect() as db:
        for row in db.execute("SELECT MAX(p.finished_at) AS finished FROM processing_attempts p "
                              "JOIN documents d ON d.id=p.document_id WHERE p.status='COMPLETE' "
                              "AND d.status='REVIEW_READY' AND NOT EXISTS (SELECT 1 FROM approvals a "
                              "WHERE a.document_id=d.id AND a.revision=d.current_revision) GROUP BY d.id"):
            stamp = seconds(row["finished"])
            if stamp is not None:
                review_waits.append(max(0, now - stamp))
    return {"version": "operations-v1", "jobs": dict(Counter(
                job["status"] if job["status"] in STATUSES else "OTHER" for job in jobs)),
            "active_stages": dict(Counter(job["stage"] if job["stage"] in STAGES else "STOPPING_OR_CLEANUP"
                                          for job in jobs if job["status"] == "PROCESSING")),
            "oldest_queue_age_seconds": round(max(ages), 3) if ages else None,
            "queue_age_known_count": len(ages), "attempts": dict(outcomes),
            "retry_or_reprocess_count": sum(max(0, job["attempts"] - 1) for job in jobs),
            "failure_codes": dict(failures), "unfinished_or_invalid_timing_count": incomplete,
            "timings": {name: distribution(times[name]) for name in ("queue", "processing", "parsing", "extracting", "checking")},
            "review_wait": distribution(review_waits),
            "timing_method": "Persisted UTC wall-clock intervals; invalid/backward intervals omitted. "
                             "Processing includes failures. Stage coverage starts with operations-v1; "
                             "parsing includes checkpoint lookup/import, extracting includes model requests, "
                             "checking includes validation/persistence. Review wait is not active human effort. "
                             "Metrics cover retained documents; deletion removes their history."}


class Readiness:
    def __init__(self, server):
        self.server, self.lock = server, threading.Lock()
        self.checked_at, self.cached = 0, None

    def view(self):
        with self.lock:
            if self.cached is not None and time.monotonic() - self.checked_at < 5:
                return self.cached
            server = self.server
            try:
                with server.store._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("UPDATE jobs SET fence=fence WHERE 0")
                    db.rollback()
                database = "ready"
            except Exception:
                database = "unavailable"
            try:
                inventory = server.store.storage_budget.inventory()
                server.store.storage_budget.check(0)
                storage = "unsafe_entries" if inventory["unsafe_entries"] else "ready"
            except StorageLimitExceeded as exc:
                storage = exc.code
            except Exception:
                storage = "unavailable"
            supervisor = server.supervisor
            worker = "ready" if supervisor and supervisor.status()["running"] and not supervisor.last_error else (
                "unavailable" if supervisor else "not_enabled")
            parser = "not_required"
            if supervisor:
                try:
                    _docker_image_id(PARSER_IMAGE)
                    parser = "ready"
                except Exception:
                    parser = "unavailable"
            model = "not_configured"
            config = server.model_config
            if config:
                model = "unavailable"
                try:
                    # No public network, redirects, proxy fallback or document payload.
                    from .model_runtime import _NoRedirect
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
                    request = urllib.request.Request(config.endpoint.rstrip("/") + "/v1/models",
                        headers={"Authorization": f"Bearer {config.api_key}"} if config.api_key else {})
                    with opener.open(request, timeout=2) as response:
                        data = json.loads(response.read(65537))
                    if config.model_id in {row["id"] for row in data["data"]}:
                        model = "ready"
                except Exception:
                    pass
            self.cached = {"ready": database == storage == "ready" and worker != "unavailable" and parser != "unavailable",
                           "service": "ready", "database": database, "storage": storage,
                           "worker": worker, "parser": parser, "model": model,
                           "model_required_for_rules": False, "cache_seconds": 5}
            self.checked_at = time.monotonic()
            return self.cached
