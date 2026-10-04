"""Bounded local intake and a leased, fenced processing queue.

Validation here is a cheap host-side gate. PDFs and image pixels still require
the isolated parser before their content can enter the review record.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import struct
import tempfile
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable

from .contracts import DocumentPage, InvoiceRecord, ValidationIssue
from .ocr import MAX_FILE_BYTES, MAX_PIXELS, PNG_SIGNATURE
from .parser_protocol import MAX_PAGE_BYTES, MAX_PAGES, MAX_RESULT_BYTES
from .review import ReviewConflict, ReviewStore, _atomic_write, _now
from .review_priority import score_review_priority

MAX_BATCH_FILES = 20
MAX_STORE_BYTES = 1024 * 1024 * 1024
MIME_BY_SUFFIX = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


@dataclass(frozen=True, slots=True)
class JobClaim:
    job_id: str
    document_id: str
    fence: int
    worker_id: str
    lease_until: float


def _display_name(name: str) -> str:
    clean = name.replace("\\", "/").rsplit("/", 1)[-1]
    if not clean or len(clean) > 255 or any(ord(char) < 32 or ord(char) == 127 for char in clean):
        raise ValueError("Invalid display filename")
    return clean


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
        raise ValueError("Malformed JPEG signature or end marker")
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            raise ValueError("Malformed JPEG marker")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker == 0xDA:
            break
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            continue
        if offset + 2 > len(data):
            break
        length = struct.unpack_from(">H", data, offset)[0]
        if length < 2 or offset + length > len(data):
            raise ValueError("Malformed JPEG segment length")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if length < 7:
                raise ValueError("Malformed JPEG frame")
            height, width = struct.unpack_from(">HH", data, offset + 3)
            return width, height
        offset += length
    raise ValueError("JPEG frame dimensions not found")


def _check_content(path: Path, name: str, declared_mime: str) -> tuple[str, int]:
    expected = MIME_BY_SUFFIX.get(Path(name).suffix.lower())
    if expected is None or declared_mime != expected:
        raise ValueError("Filename extension and MIME type must identify PDF, PNG, or JPEG")
    data = path.read_bytes()
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError("Document must be nonempty and at most 20 MB")
    if expected == "application/pdf":
        if not re.match(rb"^%PDF-\d\.\d(?:\r|\n|\s|%)?", data[:12]):
            raise ValueError("PDF signature is invalid")
    elif expected == "image/png":
        if len(data) < 33 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR" or data[8:12] != b"\x00\x00\x00\x0d":
            raise ValueError("PNG signature or IHDR is invalid")
        width, height = struct.unpack_from(">II", data, 16)
        if width == 0 or height == 0 or width * height > MAX_PIXELS:
            raise ValueError("Image exceeds 20 megapixels")
    else:
        width, height = _jpeg_dimensions(data)
        if width == 0 or height == 0 or width * height > MAX_PIXELS:
            raise ValueError("Image exceeds 20 megapixels")
    return expected, len(data)


class IntakeStore(ReviewStore):
    def __init__(self, database: Path, object_root: Path, *, max_store_bytes: int = MAX_STORE_BYTES):
        super().__init__(database)
        self.object_root = object_root.resolve()
        self.max_store_bytes = max_store_bytes
        if max_store_bytes < MAX_FILE_BYTES:
            raise ValueError("Object quota must allow at least one maximum-size document")
        (self.object_root / "quarantine").mkdir(parents=True, exist_ok=True)
        (self.object_root / "objects").mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS document_objects (
                    sha256 TEXT PRIMARY KEY, size_bytes INTEGER NOT NULL, relative_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL, worker_id TEXT, fence INTEGER NOT NULL DEFAULT 0,
                    lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT, created_at TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );
                CREATE TABLE IF NOT EXISTS document_pages (
                    document_id TEXT NOT NULL, page_number INTEGER NOT NULL,
                    image_sha256 TEXT NOT NULL, image_relpath TEXT NOT NULL,
                    PRIMARY KEY (document_id, page_number),
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );
                CREATE TABLE IF NOT EXISTS parser_checkpoints (
                    document_id TEXT PRIMARY KEY, cache_key TEXT NOT NULL,
                    parser_identity TEXT NOT NULL, result_json TEXT NOT NULL,
                    result_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );
                CREATE TABLE IF NOT EXISTS parser_checkpoint_pages (
                    document_id TEXT NOT NULL, page_number INTEGER NOT NULL,
                    image_sha256 TEXT NOT NULL, image_relpath TEXT NOT NULL,
                    PRIMARY KEY (document_id, page_number),
                    FOREIGN KEY (document_id) REFERENCES parser_checkpoints(document_id)
                );
            """)

    def submit(self, stream: BinaryIO, filename: str, declared_mime: str) -> str:
        name = _display_name(filename)
        fd, temp_name = tempfile.mkstemp(prefix="upload-", dir=self.object_root / "quarantine")
        temp_path = Path(temp_name)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as output:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise ValueError("Upload stream must yield bytes")
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise ValueError("Document exceeds 20 MB")
                    output.write(chunk)
                    digest.update(chunk)
                output.flush()
                os.fsync(output.fileno())
            media_type, checked_size = _check_content(temp_path, name, declared_mime)
            assert size == checked_size
            sha256 = digest.hexdigest()
            relative = Path("objects") / sha256[:2] / sha256
            object_path = self.object_root / relative
            document_id = uuid.uuid4().hex
            job_id = uuid.uuid4().hex
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT size_bytes, relative_path FROM document_objects WHERE sha256=?", (sha256,)).fetchone()
                if existing is None:
                    used = db.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM document_objects").fetchone()[0]
                    if used + size > self.max_store_bytes:
                        raise ValueError("Local document object quota exceeded")
                    object_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        os.link(temp_path, object_path)
                    except FileExistsError:
                        pass  # A prior interrupted transaction may have left an orphan.
                    db.execute("INSERT INTO document_objects VALUES (?,?,?,?)",
                               (sha256, size, str(relative), _now()))
                elif existing["size_bytes"] != size or existing["relative_path"] != str(relative):
                    raise ReviewConflict("Content-addressed object metadata conflict")
                if object_path.is_symlink() or not object_path.is_file() or hashlib.sha256(object_path.read_bytes()).hexdigest() != sha256:
                    raise ReviewConflict("Stored document object failed checksum verification")
                db.execute("INSERT INTO documents(id,source_sha256,source_name,page_json,current_revision,created_at,media_type,size_bytes,object_relpath,status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (document_id, sha256, name, "null", 0, _now(), media_type, size, str(relative), "RECEIVED"))
                db.execute("INSERT INTO jobs(id,document_id,status,created_at) VALUES (?,?,?,?)",
                           (job_id, document_id, "QUEUED", _now()))
                self._event(db, document_id, 0, "received", "intake", "")
            return document_id
        finally:
            temp_path.unlink(missing_ok=True)

    def submit_batch(self, files: Iterable[tuple[BinaryIO, str, str]]) -> list[str]:
        selected = list(itertools.islice(files, MAX_BATCH_FILES + 1))
        if not 1 <= len(selected) <= MAX_BATCH_FILES:
            raise ValueError("A batch must contain 1 to 20 documents")
        return [self.submit(stream, filename, mime) for stream, filename, mime in selected]

    def status(self, document_id: str) -> dict:
        with self._connect() as db:
            doc = self._current(db, document_id)
            job = db.execute("SELECT id,status,attempts,fence,error_code FROM jobs WHERE document_id=?", (document_id,)).fetchone()
            page_count = db.execute("SELECT COUNT(*) FROM document_pages WHERE document_id=?", (document_id,)).fetchone()[0]
            approved = db.execute("SELECT 1 FROM approvals WHERE document_id=? AND revision=?",
                                  (document_id, doc["current_revision"])).fetchone()
            checkpoint = db.execute("SELECT cache_key,parser_identity,created_at FROM parser_checkpoints WHERE document_id=?",
                                    (document_id,)).fetchone()
            return {
                "document_id": document_id, "source_name": doc["source_name"],
                "source_sha256": doc["source_sha256"], "media_type": doc["media_type"],
                "size_bytes": doc["size_bytes"], "status": "APPROVED" if approved else doc["status"],
                "page_image_sha256": doc["page_image_sha256"],
                "page_count": page_count or (1 if doc["current_revision"] else 0),
                "current_revision": doc["current_revision"], "job": dict(job) if job else None,
                "parser_checkpoint": dict(checkpoint) if checkpoint else None,
            }

    def list_documents(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("""
                SELECT d.id, d.source_name, d.source_sha256,
                       CASE WHEN a.document_id IS NOT NULL THEN 'APPROVED' ELSE d.status END AS status,
                       d.current_revision, d.created_at, j.status AS job_status,
                       j.error_code, r.issues_json
                FROM documents d LEFT JOIN jobs j ON j.document_id=d.id
                LEFT JOIN revisions r ON r.document_id=d.id AND r.revision=d.current_revision
                LEFT JOIN approvals a ON a.document_id=d.id AND a.revision=d.current_revision
                ORDER BY d.created_at DESC, d.id DESC
            """).fetchall()
            documents = []
            for row in rows:
                document = dict(row)
                issues_json = document.pop("issues_json")
                document["review_priority"] = (score_review_priority(json.loads(issues_json))
                                               if issues_json is not None else None)
                documents.append(document)
            status_order = {"REVIEW_READY": 3, "FAILED": 2, "APPROVED": 0}
            documents.sort(key=lambda item: (status_order.get(item["status"], 1),
                                             item["review_priority"]["points"] if item["review_priority"] else 0,
                                             item["created_at"], item["id"]), reverse=True)
            return documents

    def object_path(self, document_id: str) -> Path:
        with self._connect() as db:
            doc = self._current(db, document_id)
            if not doc["object_relpath"]:
                raise ValueError("Document has no stored original")
            path = self.object_root / doc["object_relpath"]
            if (not path.resolve().is_relative_to(self.object_root) or path.is_symlink() or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != doc["source_sha256"]):
                raise ReviewConflict("Stored original failed checksum verification")
            return path

    def page_image_path(self, document_id: str, page_number: int = 1) -> Path:
        if page_number < 1:
            raise ValueError("Page number must be positive")
        with self._connect() as db:
            doc = self._current(db, document_id)
            row = db.execute("SELECT image_sha256,image_relpath FROM document_pages WHERE document_id=? AND page_number=?",
                             (document_id, page_number)).fetchone()
            relative = row["image_relpath"] if row else doc["page_image_relpath"] if page_number == 1 else None
            digest = row["image_sha256"] if row else doc["page_image_sha256"] if page_number == 1 else None
            if not relative:
                raise ReviewConflict("Document has no rendered page")
            path = self.object_root / relative
            if (not path.resolve().is_relative_to(self.object_root) or path.is_symlink() or not path.is_file()
                    or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
                raise ReviewConflict("Rendered page failed checksum verification")
            return path

    def reconcile(self, *, prune: bool = False, min_age_seconds: int = 86400) -> dict:
        """Audit stored objects and renders; optionally remove aged unreferenced files.

        The database write lock prevents an intake or completion transaction from
        publishing a reference while its file is being classified or removed.
        Active upload scratch files live in quarantine and are never considered.
        """
        if min_age_seconds < 0:
            raise ValueError("Minimum orphan age must be nonnegative")
        references: dict[str, tuple[str, int | None]] = {}
        protected_paths: set[str] = set()
        metadata_errors: list[dict] = []

        def add(relative: str, digest: str, size: int | None = None) -> None:
            protected_paths.add(relative)
            path = Path(relative)
            if (path.is_absolute() or str(path) != relative or len(path.parts) != 3
                    or path.parts[0] not in ("objects", "renders")
                    or not re.fullmatch(r"[0-9a-f]{64}", digest or "")
                    or path.parts[1] != digest[:2]
                    or path.parts[2] != digest + (".png" if path.parts[0] == "renders" else "")):
                metadata_errors.append({"path": relative, "reason": "unsafe_reference"})
                return
            prior = references.get(relative)
            if prior is not None and (prior[0] != digest or
                                      (prior[1] is not None and size is not None and prior[1] != size)):
                metadata_errors.append({"path": relative, "reason": "conflicting_reference"})
                return
            references[relative] = (digest, size if size is not None else prior[1] if prior else None)

        missing: list[dict] = []
        corrupt: list[dict] = []
        orphans: list[dict] = []
        removed: list[str] = []
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT relative_path,sha256,size_bytes FROM document_objects"):
                add(row["relative_path"], row["sha256"], row["size_bytes"])
            for row in db.execute("SELECT object_relpath,source_sha256,size_bytes,page_image_relpath,page_image_sha256 FROM documents"):
                if row["object_relpath"]:
                    add(row["object_relpath"], row["source_sha256"], row["size_bytes"])
                if row["page_image_relpath"]:
                    add(row["page_image_relpath"], row["page_image_sha256"])
            for row in db.execute("SELECT image_relpath,image_sha256 FROM document_pages"):
                add(row["image_relpath"], row["image_sha256"])
            for row in db.execute("SELECT image_relpath,image_sha256 FROM parser_checkpoint_pages"):
                add(row["image_relpath"], row["image_sha256"])

            for relative, (digest, size) in sorted(references.items()):
                path = self.object_root / relative
                if not path.exists() and not path.is_symlink():
                    missing.append({"path": relative, "reason": "missing"})
                elif ((self.object_root / Path(relative).parts[0]).is_symlink() or
                      path.parent.is_symlink() or path.is_symlink() or not path.is_file() or
                      (size is not None and path.stat().st_size != size) or
                      hashlib.sha256(path.read_bytes()).hexdigest() != digest):
                    corrupt.append({"path": relative, "reason": "checksum_or_type"})

            for category in ("objects", "renders"):
                root = self.object_root / category
                if not root.exists() and not root.is_symlink():
                    continue
                if root.is_symlink() or not root.is_dir():
                    metadata_errors.append({"path": category, "reason": "unexpected_store_entry"})
                    continue
                for prefix in root.iterdir():
                    if prefix.is_symlink() or not prefix.is_dir():
                        metadata_errors.append({"path": str(prefix.relative_to(self.object_root)),
                                                "reason": "unexpected_store_entry"})
                        continue
                    for path in prefix.iterdir():
                        relative = str(path.relative_to(self.object_root))
                        if relative in protected_paths:
                            continue
                        if (path.is_symlink() or not path.is_file() or
                            not re.fullmatch(r"[0-9a-f]{2}", prefix.name) or
                            not re.fullmatch(r"[0-9a-f]{64}" + (r"\.png" if category == "renders" else ""), path.name) or
                            not path.name.startswith(prefix.name)):
                            metadata_errors.append({"path": relative, "reason": "unexpected_store_entry"})
                            continue
                        age = max(0, now - path.stat().st_mtime)
                        orphans.append({"path": relative, "age_seconds": int(age),
                                        "eligible": age >= min_age_seconds})
            if prune and not (missing or corrupt or metadata_errors):
                for item in orphans:
                    if item["eligible"]:
                        path = self.object_root / item["path"]
                        if path.is_symlink() or not path.is_file():
                            raise ReviewConflict(f"Orphan changed during reconciliation: {path}")
                        path.unlink()
                        removed.append(item["path"])
        return {"referenced": len(references), "missing": missing, "corrupt": corrupt,
                "metadata_errors": metadata_errors, "orphans": orphans, "removed": removed,
                "prune_blocked": prune and bool(missing or corrupt or metadata_errors)}

    def claim(self, worker_id: str, *, lease_seconds: int = 120) -> JobClaim | None:
        if not worker_id.strip() or not 1 <= lease_seconds <= 3600:
            raise ValueError("Worker ID and a 1-3600 second lease are required")
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT 1 FROM jobs WHERE status='PROCESSING' AND lease_until>=? LIMIT 1", (now,)).fetchone()
            if active:
                return None
            job = db.execute("SELECT id,document_id,fence FROM jobs WHERE status='QUEUED' OR (status='PROCESSING' AND lease_until<?) ORDER BY created_at,id LIMIT 1", (now,)).fetchone()
            if job is None:
                return None
            fence = job["fence"] + 1
            until = now + lease_seconds
            db.execute("UPDATE jobs SET status='PROCESSING',worker_id=?,fence=?,lease_until=?,attempts=attempts+1,error_code=NULL WHERE id=?",
                       (worker_id, fence, until, job["id"]))
            db.execute("UPDATE documents SET status='PROCESSING' WHERE id=?", (job["document_id"],))
            self._event(db, job["document_id"], 0, "claimed", worker_id, str(fence))
            return JobClaim(job["id"], job["document_id"], fence, worker_id, until)

    @staticmethod
    def _verify_claim(db, claim: JobClaim) -> None:
        job = db.execute("SELECT * FROM jobs WHERE id=?", (claim.job_id,)).fetchone()
        if (job is None or job["document_id"] != claim.document_id or job["status"] != "PROCESSING"
                or job["worker_id"] != claim.worker_id or job["fence"] != claim.fence
                or job["lease_until"] < time.time()):
            raise ReviewConflict("Processing lease is missing, expired, or fenced out")

    def renew(self, claim: JobClaim, *, lease_seconds: int = 120) -> JobClaim:
        """Extend only the live, fenced claim held by this worker."""
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("Lease must be 1-3600 seconds")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_claim(db, claim)
            until = time.time() + lease_seconds
            db.execute("UPDATE jobs SET lease_until=? WHERE id=?", (until, claim.job_id))
        return JobClaim(claim.job_id, claim.document_id, claim.fence, claim.worker_id, until)

    def save_parser_checkpoint(self, claim: JobClaim, cache_key: str, parser_identity: str,
                               result: bytes, images: Sequence[bytes]) -> None:
        """Commit supervisor-validated OCR and render references before extraction."""
        if not 0 < len(result) <= MAX_RESULT_BYTES or not 1 <= len(images) <= MAX_PAGES:
            raise ValueError("Invalid parser checkpoint size")
        if any(not 0 < len(image) <= MAX_PAGE_BYTES for image in images):
            raise ValueError("Invalid parser checkpoint render size")
        encoded = result.decode("utf-8")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_claim(db, claim)
            self._current(db, claim.document_id, 0)
            db.execute("DELETE FROM parser_checkpoint_pages WHERE document_id=?", (claim.document_id,))
            db.execute("INSERT OR REPLACE INTO parser_checkpoints VALUES (?,?,?,?,?,?)",
                       (claim.document_id, cache_key, parser_identity, encoded,
                        hashlib.sha256(result).hexdigest(), _now()))
            for number, image in enumerate(images, start=1):
                digest = hashlib.sha256(image).hexdigest()
                relative = Path("renders") / digest[:2] / f"{digest}.png"
                _atomic_write(self.object_root / relative, image)
                db.execute("INSERT INTO parser_checkpoint_pages VALUES (?,?,?,?)",
                           (claim.document_id, number, digest, str(relative)))
            self._verify_claim(db, claim)
            self._event(db, claim.document_id, 0, "parser_checkpoint_saved", claim.worker_id, cache_key)

    def load_parser_checkpoint(self, claim: JobClaim, cache_key: str) -> tuple[bytes, tuple[bytes, ...]] | None:
        """Read a matching checkpoint under the fence and check all artifact bytes."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_claim(db, claim)
            checkpoint = db.execute("SELECT * FROM parser_checkpoints WHERE document_id=? AND cache_key=?",
                                    (claim.document_id, cache_key)).fetchone()
            if checkpoint is None:
                return None
            result = checkpoint["result_json"].encode("utf-8")
            if (not 0 < len(result) <= MAX_RESULT_BYTES or
                    hashlib.sha256(result).hexdigest() != checkpoint["result_sha256"]):
                raise ValueError("Parser checkpoint manifest failed integrity verification")
            rows = db.execute("SELECT * FROM parser_checkpoint_pages WHERE document_id=? ORDER BY page_number",
                              (claim.document_id,)).fetchall()
            if not 1 <= len(rows) <= MAX_PAGES or [row["page_number"] for row in rows] != list(range(1, len(rows) + 1)):
                raise ValueError("Parser checkpoint page sequence is invalid")
            images = []
            for row in rows:
                digest = row["image_sha256"]
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError("Parser checkpoint render hash is invalid")
                relative = Path("renders") / digest[:2] / f"{digest}.png"
                path = self.object_root / relative
                if (row["image_relpath"] != str(relative) or path.is_symlink() or
                        path.parent.is_symlink() or (self.object_root / "renders").is_symlink() or
                        not path.is_file() or not 0 < path.stat().st_size <= MAX_PAGE_BYTES):
                    raise ValueError("Parser checkpoint render reference is invalid")
                image = path.read_bytes()
                if hashlib.sha256(image).hexdigest() != digest:
                    raise ValueError("Parser checkpoint render failed integrity verification")
                images.append(image)
            return result, tuple(images)

    def record_parser_reuse(self, claim: JobClaim, cache_key: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_claim(db, claim)
            self._event(db, claim.document_id, 0, "parser_checkpoint_reused", claim.worker_id, cache_key)

    def complete(self, claim: JobClaim, page: DocumentPage | Sequence[DocumentPage], record: InvoiceRecord,
                 page_image: bytes | Sequence[bytes] | None = None, *,
                 profile: str = "ocr_rules", model_id: str | None = None,
                 prompt_sha256: str | None = None,
                 extra_issues: Sequence[ValidationIssue] = ()) -> None:
        pages = (page,) if isinstance(page, DocumentPage) else tuple(page)
        images = () if page_image is None else (page_image,) if isinstance(page_image, bytes) else tuple(page_image)
        if images and len(images) != len(pages):
            raise ValueError("Each page needs one rendered image")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_claim(db, claim)
            for source_page, image in zip(pages, images):
                digest = hashlib.sha256(image).hexdigest()
                relative = Path("renders") / digest[:2] / f"{digest}.png"
                _atomic_write(self.object_root / relative, image)
                db.execute("INSERT INTO document_pages VALUES (?,?,?,?)",
                           (claim.document_id, source_page.number, digest, str(relative)))
                if source_page.number == 1:
                    db.execute("UPDATE documents SET page_image_sha256=?,page_image_relpath=? WHERE id=?",
                               (digest, str(relative), claim.document_id))
            self._attach_candidate(db, claim.document_id, pages, record, profile=profile,
                                   model_id=model_id, prompt_sha256=prompt_sha256,
                                   extra_issues=extra_issues)
            db.execute("UPDATE jobs SET status='COMPLETE',lease_until=NULL WHERE id=?", (claim.job_id,))

    def fail(self, claim: JobClaim, error_code: str) -> None:
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", error_code):
            raise ValueError("error_code must be an uppercase machine-readable token")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._verify_claim(db, claim)
            db.execute("UPDATE jobs SET status='FAILED',lease_until=NULL,error_code=? WHERE id=?", (error_code, claim.job_id))
            db.execute("UPDATE documents SET status='FAILED' WHERE id=?", (claim.document_id,))
            self._event(db, claim.document_id, 0, "failed", claim.worker_id, error_code)

    def retry(self, document_id: str) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._current(db, document_id, 0)
            if doc["status"] != "FAILED":
                raise ReviewConflict("Only failed, unreviewed documents can be retried")
            db.execute("UPDATE jobs SET status='QUEUED',worker_id=NULL,lease_until=NULL,error_code=NULL WHERE document_id=?", (document_id,))
            db.execute("UPDATE documents SET status='RECEIVED' WHERE id=?", (document_id,))
            self._event(db, document_id, 0, "retried", "operator", "")
