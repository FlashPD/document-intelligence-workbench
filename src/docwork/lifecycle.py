"""Durable batch, stop, reprocessing and deletion operations for the local store."""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path

from .review import ReviewConflict, _now


class LifecycleMixin:
    def configure_claim(self, claim, profile: dict, *, reparse: bool = False) -> None:
        from .intake import validate_profile
        encoded = json.dumps(validate_profile(profile), sort_keys=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_claim(db, claim)
            db.execute("UPDATE jobs SET profile_json=?,reparse=? WHERE id=?", (encoded, int(reparse), claim.job_id))
            db.execute("UPDATE processing_attempts SET profile_json=? WHERE id=?", (encoded, f"{claim.job_id}:{claim.fence}"))

    def create_batch(self, count: int, *, profile: dict | None = None) -> str:
        from .intake import MAX_BATCH_FILES, validate_profile
        if type(count) is not int or not 1 <= count <= MAX_BATCH_FILES:
            raise ValueError("A batch must contain 1 to 20 documents")
        batch_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.storage_budget.check(64 * 1024)
            db.execute("INSERT INTO batches VALUES (?,?,?,?)",
                       (batch_id, count, json.dumps(validate_profile(profile), sort_keys=True), _now()))
            db.executemany("INSERT INTO batch_items VALUES (?,?,NULL,'WAITING_UPLOAD',NULL)",
                           [(batch_id, position) for position in range(count)])
        return batch_id

    def batch_status(self, batch_id: str) -> dict:
        with self._connect() as db:
            batch = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None:
                raise KeyError("Unknown batch")
            items = [dict(row) for row in db.execute(
                "SELECT b.position,b.document_id,CASE WHEN d.id IS NOT NULL THEN d.status ELSE b.status END AS status,"
                "COALESCE(j.error_code,b.error_code) AS error_code,j.stage "
                "FROM batch_items b LEFT JOIN documents d ON d.id=b.document_id "
                "LEFT JOIN jobs j ON j.document_id=b.document_id WHERE batch_id=? ORDER BY position", (batch_id,))]
            return {"batch_id": batch_id, "expected_count": batch["expected_count"],
                    "profile": json.loads(batch["profile_json"]), "items": items}

    def batches(self) -> list[dict]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT id AS batch_id,expected_count,created_at FROM batches "
                                                    "ORDER BY created_at DESC LIMIT 20")]

    def reject_batch_item(self, batch_id: str, position: int, code: str = "UPLOAD_REJECTED") -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", code):
            raise ValueError("Invalid batch error code")
        with self._connect() as db:
            db.execute("UPDATE batch_items SET status='REJECTED',error_code=? "
                       "WHERE batch_id=? AND position=? AND status='WAITING_UPLOAD'", (code, batch_id, position))

    def set_stage(self, claim, stage: str) -> None:
        if stage not in ("PARSING", "EXTRACTING", "CHECKING"):
            raise ValueError("Unknown processing stage")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_claim(db, claim)
            db.execute("UPDATE jobs SET stage=? WHERE id=?", (stage, claim.job_id))
            previous = db.execute("SELECT detail FROM review_events WHERE document_id=? AND kind='processing_stage' "
                                  "ORDER BY id DESC LIMIT 1", (claim.document_id,)).fetchone()
            detail = json.dumps({"fence": claim.fence, "stage": stage}, sort_keys=True)
            if previous is None or previous["detail"] != detail:
                self._event(db, claim.document_id, 0, "processing_stage", claim.worker_id, detail)

    def check_claim(self, claim) -> None:
        with self._connect() as db:
            self._verify_claim(db, claim)

    def cancel(self, document_id: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._current(db, document_id)
            job = db.execute("SELECT * FROM jobs WHERE document_id=?", (document_id,)).fetchone()
            if job is None or job["status"] not in ("QUEUED", "PROCESSING", "CANCELLED"):
                raise ReviewConflict("Only queued or processing work can be cancelled")
            active = job["status"] == "PROCESSING"
            db.execute("UPDATE jobs SET stop_requested=1,status=?,stage=? WHERE id=?",
                       ("PROCESSING" if active else "CANCELLED", "CANCELLING" if active else "CANCELLED", job["id"]))
            db.execute("UPDATE documents SET status=? WHERE id=?",
                       ("CANCELLING" if active else "CANCELLED", document_id))
            self._event(db, document_id, doc["current_revision"], "cancel_requested", "operator", "")

    def finish_stopped(self, claim, *, shutdown: bool = False) -> None:
        """Settle only our exact fence after its parser/request has unwound."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM jobs WHERE id=?", (claim.job_id,)).fetchone()
            if (job is None or job["fence"] != claim.fence or job["worker_id"] != claim.worker_id
                    or job["status"] != "PROCESSING"):
                return
            cancelled = bool(job["stop_requested"])
            status = "CANCELLED" if cancelled else "QUEUED" if shutdown else "FAILED"
            db.execute("UPDATE jobs SET status=?,stage=?,worker_id=NULL,lease_until=NULL WHERE id=?",
                       (status, status, claim.job_id))
            db.execute("UPDATE documents SET status=? WHERE id=? AND status!='DELETING'",
                       ("CANCELLED" if cancelled else "RECEIVED" if shutdown else "FAILED", claim.document_id))
            db.execute("UPDATE processing_attempts SET status=?,finished_at=? WHERE id=?",
                       ("CANCELLED" if cancelled else "ABANDONED", _now(), f"{claim.job_id}:{claim.fence}"))

    def reprocess(self, document_id: str, *, profile: dict | None = None, reparse: bool = False,
                  expected_revision: int | None = None) -> None:
        from .intake import validate_profile
        if type(reparse) is not bool:
            raise ValueError("reparse must be a boolean")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._current(db, document_id, expected_revision)
            if not doc["object_relpath"]:
                raise ReviewConflict("Reprocessing requires a stored original")
            job = db.execute("SELECT * FROM jobs WHERE document_id=?", (document_id,)).fetchone()
            if job is None or job["status"] in ("PROCESSING", "QUEUED") or job["stage"] == "CLEANUP_REQUIRED":
                raise ReviewConflict("Stop or finish existing processing before reprocessing")
            selected = validate_profile(profile if profile is not None else json.loads(job["profile_json"]))
            self.storage_budget.check(64 * 1024)
            self._archive_sources(db, document_id)
            db.execute("UPDATE jobs SET status='QUEUED',stage='QUEUED',worker_id=NULL,lease_until=NULL,"
                       "fence=fence+1,error_code=NULL,stop_requested=0,profile_json=?,reparse=? WHERE id=?",
                       (json.dumps(selected, sort_keys=True), int(reparse), job["id"]))
            db.execute("UPDATE documents SET status='RECEIVED' WHERE id=?", (document_id,))
            self._event(db, document_id, doc["current_revision"], "reprocess_requested", "operator",
                        json.dumps({"profile": selected, "reparse": reparse}, sort_keys=True))

    def attempts(self, document_id: str) -> list[dict]:
        with self._connect() as db:
            self._current(db, document_id)
            return [dict(row) for row in db.execute(
                "SELECT * FROM processing_attempts WHERE document_id=? ORDER BY started_at,fence", (document_id,))]

    def cleanup_failed(self, claim) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_claim(db, claim, allow_stopping=True)
            db.execute("UPDATE jobs SET status='FAILED',stage='CLEANUP_REQUIRED',error_code='PARSER_CLEANUP_FAILED',"
                       "stop_requested=1,lease_until=NULL WHERE id=?", (claim.job_id,))
            db.execute("UPDATE documents SET status='FAILED' WHERE id=? AND status NOT IN ('DELETING','CANCELLING')",
                       (claim.document_id,))
            db.execute("UPDATE processing_attempts SET status='CLEANUP_REQUIRED',error_code='PARSER_CLEANUP_FAILED' WHERE id=?",
                       (f"{claim.job_id}:{claim.fence}",))

    def recover_stops(self, *, cleanup=None) -> None:
        if cleanup is None:
            from .worker import remove_owned_container
            cleanup = remove_owned_container
        with self._connect() as db:
            jobs = [dict(row) for row in db.execute(
                "SELECT j.*,d.status AS document_status FROM jobs j JOIN documents d ON d.id=j.document_id "
                "WHERE j.stage='CLEANUP_REQUIRED' OR (j.status='PROCESSING' AND j.lease_until<?)", (time.time(),))]
        for job in jobs:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone()
                if current is None or current["fence"] != job["fence"] or current["status"] != job["status"]:
                    continue
                if current["status"] == "PROCESSING" and current["lease_until"] >= time.time():
                    continue
                # Recheck while holding the write lock, so a renewed lease or a
                # new fence cannot be terminated by stale recovery inspection.
                try:
                    cleanup(f"docwork-{job['id'][:16]}-{job['fence']}")
                    self._remove_scratch(job["id"], job["fence"])
                except (RuntimeError, OSError, ReviewConflict):
                    continue
                cancelling = job["document_status"] in ("CANCELLING", "DELETING")
                status = "CANCELLED" if cancelling else "FAILED" if current["stage"] == "CLEANUP_REQUIRED" else "QUEUED"
                db.execute("UPDATE jobs SET status=?,stage=?,stop_requested=0,worker_id=NULL,lease_until=NULL,fence=fence+1 WHERE id=?",
                           (status, status, job["id"]))
                db.execute("UPDATE documents SET status=? WHERE id=? AND status!='DELETING'",
                           ("CANCELLED" if cancelling else "FAILED" if status == "FAILED" else "RECEIVED", job["document_id"]))
                db.execute("UPDATE processing_attempts SET status=?,finished_at=? WHERE id=?",
                           ("CANCELLED" if cancelling else "ABANDONED", _now(), f"{job['id']}:{job['fence']}"))

    def _remove_scratch(self, job_id: str, fence: int) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id) or type(fence) is not int or fence < 1:
            raise ReviewConflict("Unsafe parser scratch identity")
        root = self.object_root / "quarantine"
        if self.object_root.is_symlink() or root.is_symlink():
            raise ReviewConflict("Unsafe parser scratch root")
        for path in root.glob(f"parse-{job_id[:16]}-{fence}-*"):
            if path.is_symlink() or not path.is_dir():
                raise ReviewConflict("Unsafe parser scratch entry")
            shutil.rmtree(path)

    def _document_artifacts(self, db, document_id: str) -> dict:
        doc = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        intake = set()
        if doc["object_relpath"]:
            intake.add(doc["object_relpath"])
        if doc["page_image_relpath"]:
            intake.add(doc["page_image_relpath"])
        for table in ("document_pages", "parser_checkpoint_pages"):
            intake.update(row[0] for row in db.execute(f"SELECT image_relpath FROM {table} WHERE document_id=?", (document_id,)))
        for row in db.execute("SELECT images_json FROM revision_sources WHERE document_id=?", (document_id,)):
            intake.update(image["image_relpath"] for image in json.loads(row[0]))
        exports = []
        directory = self.export_root / document_id
        if directory.is_symlink():
            raise ReviewConflict("Unsafe export directory")
        if directory.exists():
            for revision in directory.iterdir():
                if revision.is_symlink() or not revision.is_dir():
                    raise ReviewConflict("Unsafe export revision directory")
                exports.extend(str(path.relative_to(self.export_root)) for path in revision.iterdir())
        for row in db.execute("SELECT manifest_json FROM exports WHERE document_id=?", (document_id,)):
            for file in json.loads(row[0])["files"]:
                path = Path(file["path"])
                if not path.is_relative_to(self.export_root):
                    raise ReviewConflict("Unsafe export reference")
                exports.append(str(path.relative_to(self.export_root)))
        active = [f"docwork-{row['job_id'][:16]}-{row['fence']}" for row in db.execute(
            "SELECT job_id,fence FROM processing_attempts WHERE document_id=? AND status IN ('PROCESSING','ABANDONED')",
            (document_id,))]
        scratch = [[row["job_id"], row["fence"]] for row in db.execute(
            "SELECT job_id,fence FROM processing_attempts WHERE document_id=?", (document_id,))]
        return {"intake": sorted(intake), "exports": sorted(set(exports)), "containers": active, "scratch": scratch}

    def request_delete(self, document_id: str) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT status FROM deletion_jobs WHERE document_id=?", (document_id,)).fetchone()
            if existing:
                return {"document_id": document_id, "status": existing["status"]}
            doc = self._current(db, document_id)
            self._archive_sources(db, document_id)
            manifest = self._document_artifacts(db, document_id)
            db.execute("INSERT INTO deletion_jobs VALUES (?,'PENDING',?,?,NULL,NULL)",
                       (document_id, json.dumps(manifest, sort_keys=True), _now()))
            db.execute("UPDATE documents SET status='DELETING' WHERE id=?", (document_id,))
            db.execute("UPDATE jobs SET stop_requested=1,stage='DELETING',"
                       "status=CASE WHEN status='PROCESSING' THEN status ELSE 'CANCELLED' END WHERE document_id=?", (document_id,))
        return {"document_id": document_id, "status": "PENDING"}

    def deletion_status(self, document_id: str) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT document_id,status,created_at,finished_at,error_code FROM deletion_jobs WHERE document_id=?",
                             (document_id,)).fetchone()
            if row is None:
                raise KeyError("Unknown deletion")
            return dict(row)

    def deletions(self) -> list[dict]:
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT document_id,status,created_at,finished_at,error_code "
                                                    "FROM deletion_jobs ORDER BY created_at DESC LIMIT 20")]

    def _shared_intake(self, db, relative: str, document_id: str) -> bool:
        if db.execute("SELECT 1 FROM documents WHERE id!=? AND (object_relpath=? OR page_image_relpath=?)",
                      (document_id, relative, relative)).fetchone():
            return True
        for table in ("document_pages", "parser_checkpoint_pages"):
            if db.execute(f"SELECT 1 FROM {table} WHERE document_id!=? AND image_relpath=?", (document_id, relative)).fetchone():
                return True
        for row in db.execute("SELECT images_json FROM revision_sources WHERE document_id!=?", (document_id,)):
            if any(image["image_relpath"] == relative for image in json.loads(row[0])):
                return True
        return False

    @staticmethod
    def _delete_file(root: Path, relative: str, *, export_document: str | None = None) -> None:
        if export_document is None:
            valid = re.fullmatch(r"(objects|renders)/([0-9a-f]{2})/([0-9a-f]{64})(\.png)?", relative)
            if (not valid or valid[2] != valid[3][:2] or
                    bool(valid[4]) != (valid[1] == "renders")):
                raise ReviewConflict("Unsafe deletion artifact")
        elif not re.fullmatch(re.escape(export_document) + r"/revision-[1-9][0-9]*/(invoice\.json|header\.csv|line-items\.csv)", relative):
            raise ReviewConflict("Unsafe deletion export")
        path = root
        for part in Path(relative).parts:
            if path.is_symlink():
                raise ReviewConflict("Deletion cannot traverse a symlink")
            path = path / part
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ReviewConflict("Deletion target is not a regular file")
        path.unlink(missing_ok=True)

    def run_deletions(self, *, cleanup=None) -> int:
        """Resume durable manifests; no document data survives a completed tombstone."""
        if cleanup is None:
            from .worker import remove_owned_container
            cleanup = remove_owned_container
        with self._connect() as db:
            pending = [row[0] for row in db.execute("SELECT document_id FROM deletion_jobs WHERE status='PENDING'")]
        completed = 0
        for document_id in pending:
            try:
                with self._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    deletion = db.execute("SELECT * FROM deletion_jobs WHERE document_id=?", (document_id,)).fetchone()
                    if deletion["status"] != "PENDING":
                        continue
                    job = db.execute("SELECT * FROM jobs WHERE document_id=?", (document_id,)).fetchone()
                    if job and job["status"] == "PROCESSING" and job["lease_until"] >= time.time():
                        continue
                    manifest = json.loads(deletion["manifest_json"])
                    # Never unlink the mounted input while an owned parser may still run.
                    for name in manifest["containers"]:
                        cleanup(name)
                    for job_id, fence in manifest.get("scratch", []):
                        self._remove_scratch(job_id, fence)
                    for relative in manifest["intake"]:
                        if not self._shared_intake(db, relative, document_id):
                            self._delete_file(self.object_root, relative)
                    for relative in manifest["exports"]:
                        self._delete_file(self.export_root, relative, export_document=document_id)
                    for table in ("revision_sources", "exports", "approvals", "decisions", "revisions",
                                  "extraction_runs", "document_pages", "parser_checkpoint_pages", "parser_checkpoints",
                                  "processing_attempts", "jobs", "review_events"):
                        db.execute(f"DELETE FROM {table} WHERE document_id=?", (document_id,))
                    db.execute("UPDATE batch_items SET document_id=NULL,status='DELETED',error_code=NULL WHERE document_id=?", (document_id,))
                    db.execute("DELETE FROM documents WHERE id=?", (document_id,))
                    for relative in manifest["intake"]:
                        db.execute("DELETE FROM document_objects WHERE relative_path=? AND NOT EXISTS "
                                   "(SELECT 1 FROM documents WHERE object_relpath=?)", (relative, relative))
                    db.execute("UPDATE deletion_jobs SET status='DELETED',manifest_json='{}',finished_at=?,error_code=NULL WHERE document_id=?",
                               (_now(), document_id))
                    completed += 1
            except (OSError, ValueError, ReviewConflict, RuntimeError):
                with self._connect() as db:
                    db.execute("UPDATE deletion_jobs SET error_code='DELETION_RETRY_REQUIRED' WHERE document_id=? AND status='PENDING'",
                               (document_id,))
        return completed
