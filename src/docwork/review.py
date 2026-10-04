"""Durable, local review of trusted candidate records.

This layer accepts already parsed pages. Intake and parser isolation are separate
work; callers must not feed arbitrary uploaded documents to the Phase 0 OCR path.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import uuid
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from .contracts import HEADER_FIELDS, REQUIRED_FIELDS, Box, DocumentPage, FieldValue, InvoiceRecord, LineItem, TextSpan, ValidationIssue
from .parser_protocol import PARSER_VERSION
from .review_priority import score_review_priority
from .validation import validate_invoice
from .storage_budget import StorageBudget

POLICY_VERSION = "review-v1"
EXPORT_SCHEMA_VERSION = "export-v1"


class ReviewConflict(Exception):
    """The caller used a stale revision or an approval no longer applies."""


class ReviewBlocked(Exception):
    """A record still needs a human decision before approval or export."""


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _field(data: dict) -> FieldValue:
    return FieldValue(
        value=data["value"], raw=data["raw"], evidence_ids=tuple(data.get("evidence_ids", ())),
        origin=data.get("origin", "observed"), missing_reason=data.get("missing_reason"),
    )


def record_from_dict(data: dict) -> InvoiceRecord:
    return InvoiceRecord(
        schema_version=data["schema_version"],
        fields={name: _field(value) for name, value in data["fields"].items()},
        line_items=tuple(LineItem(row_id=row["row_id"], **{
            name: _field(row[name]) for name in ("description", "quantity", "unit_price", "line_total", "tax")
        }) for row in data["line_items"]),
    )


def page_from_dict(data: dict) -> DocumentPage:
    return DocumentPage(
        number=data["number"], width_px=data["width_px"], height_px=data["height_px"],
        spans=tuple(TextSpan(
            id=span["id"], page=span["page"], text=span["text"],
            box=Box(**span["box"]) if span["box"] else None,
            method=span["method"], confidence=span.get("confidence"),
        ) for span in data["spans"]),
    )


def pages_from_json(encoded: str) -> tuple[DocumentPage, ...]:
    data = json.loads(encoded)
    return tuple(page_from_dict(page) for page in (data if isinstance(data, list) else [data]))


def pages_to_json(pages: Sequence[DocumentPage]) -> str:
    return _json([asdict(page) for page in pages])


def _issue_key(issue: dict) -> str:
    return f"{issue['code']}|{issue['path']}"


def _csv_text(value: str | None) -> str:
    if value is None:
        return ""
    # Spreadsheet programs may ignore leading whitespace before a formula.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else value


def _csv_number(value: str | None) -> str:
    if value is None:
        return ""
    return value if re.fullmatch(r"-?\d+(?:\.\d+)?", value) else _csv_text(value)


def _atomic_write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ReviewConflict(f"Export path already holds different content: {path}")
        return digest
    fd, temp_name = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temp_name, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ReviewConflict(f"Export path already holds different content: {path}") from None
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return digest


class ReviewStore:
    def __init__(self, database: Path, export_root: Path | None = None):
        self.database = database.resolve()
        self.export_root = (export_root or self.database.parent / "exports").resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, source_name TEXT NOT NULL,
                    page_json TEXT NOT NULL, current_revision INTEGER NOT NULL, created_at TEXT NOT NULL,
                    media_type TEXT, size_bytes INTEGER, object_relpath TEXT,
                    page_image_sha256 TEXT, page_image_relpath TEXT,
                    status TEXT NOT NULL DEFAULT 'REVIEW_READY'
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    document_id TEXT NOT NULL, revision INTEGER NOT NULL, parent_revision INTEGER,
                    record_json TEXT NOT NULL, record_hash TEXT NOT NULL, issues_json TEXT NOT NULL,
                    actor TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (document_id, revision),
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    document_id TEXT NOT NULL, revision INTEGER NOT NULL, issue_key TEXT NOT NULL,
                    reason TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (document_id, revision, issue_key),
                    FOREIGN KEY (document_id, revision) REFERENCES revisions(document_id, revision)
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    document_id TEXT NOT NULL, revision INTEGER NOT NULL, record_hash TEXT NOT NULL,
                    decision_hash TEXT NOT NULL, approval_hash TEXT NOT NULL,
                    actor TEXT NOT NULL, policy_version TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY (document_id, revision),
                    FOREIGN KEY (document_id, revision) REFERENCES revisions(document_id, revision)
                );
                CREATE TABLE IF NOT EXISTS exports (
                    document_id TEXT NOT NULL, revision INTEGER NOT NULL, format TEXT NOT NULL,
                    schema_version TEXT NOT NULL, manifest_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (document_id, revision, format, schema_version),
                    FOREIGN KEY (document_id, revision) REFERENCES approvals(document_id, revision)
                );
                CREATE TABLE IF NOT EXISTS review_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT NOT NULL,
                    revision INTEGER NOT NULL, kind TEXT NOT NULL, actor TEXT NOT NULL,
                    detail TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS extraction_runs (
                    document_id TEXT PRIMARY KEY, profile TEXT NOT NULL, model_id TEXT,
                    prompt_sha256 TEXT, parser_version TEXT NOT NULL, input_sha256 TEXT NOT NULL,
                    extra_issues_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES documents(id)
                );
                CREATE TABLE IF NOT EXISTS revision_sources (
                    document_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    page_json TEXT NOT NULL, extraction_json TEXT NOT NULL, images_json TEXT NOT NULL,
                    PRIMARY KEY (document_id, revision),
                    FOREIGN KEY (document_id, revision) REFERENCES revisions(document_id, revision)
                );
            """)
            # Preserve databases created by the first review prototype.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(documents)")}
            for name, definition in (
                ("media_type", "TEXT"), ("size_bytes", "INTEGER"),
                ("object_relpath", "TEXT"), ("status", "TEXT NOT NULL DEFAULT 'REVIEW_READY'"),
                ("page_image_sha256", "TEXT"), ("page_image_relpath", "TEXT"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE documents ADD COLUMN {name} {definition}")
            policy = (db.execute("SELECT * FROM storage_policy WHERE id=1").fetchone()
                      if db.execute("SELECT 1 FROM sqlite_master WHERE name='storage_policy'").fetchone() else None)
            if policy:
                self.storage_budget = StorageBudget(self.database, self.database.parent / policy["object_root"],
                                                   self.export_root, maximum=policy["max_artifact_bytes"],
                                                   reserve=policy["disk_reserve_bytes"])

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _event(db: sqlite3.Connection, document_id: str, revision: int, kind: str, actor: str, detail: str) -> None:
        db.execute("INSERT INTO review_events(document_id, revision, kind, actor, detail, created_at) VALUES (?,?,?,?,?,?)",
                   (document_id, revision, kind, actor, detail, _now()))

    @staticmethod
    def _current(db: sqlite3.Connection, document_id: str, expected_revision: int | None = None) -> sqlite3.Row:
        row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None or row["status"] == "DELETING":
            raise KeyError(f"Unknown document: {document_id}")
        if expected_revision is not None and row["current_revision"] != expected_revision:
            raise ReviewConflict(f"Current revision is {row['current_revision']}, expected {expected_revision}")
        return row

    @staticmethod
    def _reviewable(doc) -> None:
        if doc["status"] not in ("REVIEW_READY", "APPROVED"):
            raise ReviewConflict("Review and new exports require a review-ready candidate")

    def _write_artifact(self, path: Path, content: bytes) -> str:
        budget = getattr(self, "storage_budget", None)
        if budget:
            budget.roots = (budget.roots[0], self.export_root.absolute())
        return budget.write(path, content, _atomic_write) if budget else _atomic_write(path, content)

    def _guard_metadata(self, content_bytes: int = 0) -> None:
        budget = getattr(self, "storage_budget", None)
        if budget:
            # Reserve for copied JSON, SQLite pages/indexes and the WAL before
            # changing references. Physical DB/WAL size is also inventoried.
            budget.check(content_bytes * 4 + 64 * 1024)

    def _write_export_files(self, outputs: list[tuple[Path, bytes]]) -> list[dict]:
        self._guard_metadata(sum(len(content) for _, content in outputs))
        created, files = [], []
        try:
            for path, content in outputs:
                existed = path.exists() or path.is_symlink()
                digest = self._write_artifact(path, content)
                if not existed:
                    created.append((path, digest))
                files.append({"path": str(path), "sha256": digest})
        except Exception:
            # Export holds the database write lock. Clean only files created by
            # this attempt, preserving retries of already immutable exports.
            for path, digest in created:
                if not path.is_symlink() and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                    path.unlink()
            raise
        return files

    def _archive_sources(self, db, document_id: str, *, attempt_id: str | None = None) -> None:
        """Snapshot sources before replacing an extraction, including old databases."""
        doc = self._current(db, document_id)
        extraction = db.execute("SELECT profile,model_id,prompt_sha256,parser_version,input_sha256,created_at "
                                "FROM extraction_runs WHERE document_id=?", (document_id,)).fetchone()
        data = dict(extraction) if extraction else None
        if data is not None and attempt_id is not None:
            data["attempt_id"] = attempt_id
        images = []
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='document_pages'").fetchone():
            images = [dict(row) for row in db.execute(
                "SELECT page_number,image_sha256,image_relpath FROM document_pages WHERE document_id=? ORDER BY page_number",
                (document_id,))]
        if not images and doc["page_image_relpath"]:
            images = [{"page_number": 1, "image_sha256": doc["page_image_sha256"],
                       "image_relpath": doc["page_image_relpath"]}]
        db.execute("INSERT OR IGNORE INTO revision_sources "
                   "SELECT document_id,revision,?,?,? FROM revisions WHERE document_id=?",
                   (doc["page_json"], _json(data), _json(images), document_id))

    @staticmethod
    def _revision(db: sqlite3.Connection, document_id: str, revision: int) -> sqlite3.Row:
        row = db.execute("SELECT * FROM revisions WHERE document_id=? AND revision=?", (document_id, revision)).fetchone()
        if row is None:
            raise KeyError(f"Unknown revision: {revision}")
        return row

    def ingest(self, source_sha256: str, source_name: str, page: DocumentPage, record: InvoiceRecord) -> str:
        if len(source_sha256) != 64 or any(c not in "0123456789abcdef" for c in source_sha256):
            raise ValueError("source_sha256 must be a lowercase SHA-256 hex digest")
        document_id = uuid.uuid4().hex
        record_data = record.to_dict()
        issues = [asdict(issue) for issue in validate_invoice(record, page)]
        with self._connect() as db:
            self._guard_metadata(len(_json(record_data).encode()) + len(_json(asdict(page)).encode()))
            db.execute("INSERT INTO documents(id,source_sha256,source_name,page_json,current_revision,created_at,status) VALUES (?,?,?,?,?,?,?)",
                       (document_id, source_sha256, source_name, _json(asdict(page)), 1, _now(), "REVIEW_READY"))
            db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?)",
                       (document_id, 1, None, _json(record_data), _hash(record_data), _json(issues), "extractor", _now()))
            db.execute("INSERT INTO extraction_runs VALUES (?,?,?,?,?,?,?,?)",
                       (document_id, "ocr_rules", None, None, "trusted-fixture-tesseract",
                        _hash([asdict(page)]), "[]", _now()))
            self._event(db, document_id, 1, "candidate_created", "extractor", "")
        return document_id

    def _attach_candidate(self, db: sqlite3.Connection, document_id: str,
                          pages: Sequence[DocumentPage], record: InvoiceRecord, *,
                          profile: str = "ocr_rules", model_id: str | None = None,
                          prompt_sha256: str | None = None,
                          extra_issues: Sequence[ValidationIssue] = ()) -> None:
        if not pages or [page.number for page in pages] != list(range(1, len(pages) + 1)):
            raise ValueError("Candidate pages must be consecutive from page 1")
        span_ids = [span.id for page in pages for span in page.spans]
        if len(span_ids) != len(set(span_ids)):
            raise ValueError("Span IDs must be unique across the document")
        data = record.to_dict()
        issues = [asdict(issue) for issue in (*validate_invoice(record, pages), *extra_issues)]
        doc = self._current(db, document_id)
        if doc["status"] not in ("RECEIVED", "PROCESSING"):
            raise ReviewConflict(f"Cannot attach candidate in status {doc['status']}")
        self._archive_sources(db, document_id)
        revision = doc["current_revision"] + 1
        db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?)",
                   (document_id, revision, doc["current_revision"] or None,
                    _json(data), _hash(data), _json(issues), "extractor", _now()))
        db.execute("INSERT OR REPLACE INTO extraction_runs VALUES (?,?,?,?,?,?,?,?)",
                   (document_id, profile, model_id, prompt_sha256, PARSER_VERSION,
                    _hash([asdict(page) for page in pages]), _json([asdict(issue) for issue in extra_issues]), _now()))
        db.execute("UPDATE documents SET page_json=?, current_revision=?, status='REVIEW_READY' WHERE id=?",
                   (pages_to_json(pages), revision, document_id))
        self._event(db, document_id, revision, "candidate_created", "extractor", "")

    def get(self, document_id: str, revision: int | None = None) -> dict:
        with self._connect() as db:
            doc = self._current(db, document_id)
            if doc["current_revision"] == 0:
                raise ReviewBlocked(f"Document is {doc['status']} and has no candidate record")
            selected = revision or doc["current_revision"]
            rev = self._revision(db, document_id, selected)
            pages = pages_from_json(doc["page_json"])
            extraction = db.execute("SELECT profile,model_id,prompt_sha256,parser_version,input_sha256,created_at FROM extraction_runs WHERE document_id=?",
                                    (document_id,)).fetchone()
            source = db.execute("SELECT * FROM revision_sources WHERE document_id=? AND revision=?",
                                (document_id, selected)).fetchone()
            if source:
                pages = pages_from_json(source["page_json"])
            decisions = db.execute("SELECT issue_key, reason, actor, created_at FROM decisions WHERE document_id=? AND revision=? ORDER BY issue_key",
                                   (document_id, selected)).fetchall()
            approval = db.execute("SELECT * FROM approvals WHERE document_id=? AND revision=?", (document_id, selected)).fetchone()
            issues = json.loads(rev["issues_json"])
            return {
                "document_id": document_id, "source_sha256": doc["source_sha256"],
                "source_name": doc["source_name"], "page": asdict(pages[0]),
                "pages": [asdict(page) for page in pages],
                "extraction": json.loads(source["extraction_json"]) if source else dict(extraction) if extraction else None,
                "revision": selected, "current_revision": doc["current_revision"],
                "record": json.loads(rev["record_json"]), "record_hash": rev["record_hash"],
                "issues": issues, "review_priority": score_review_priority(issues),
                "decisions": [dict(row) for row in decisions],
                "approval": dict(approval) if approval else None,
            }

    def edit(self, document_id: str, expected_revision: int, path: str, value: str | None,
             actor: str, evidence_ids: tuple[str, ...] | None = None) -> int:
        if not actor.strip():
            raise ValueError("actor is required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._current(db, document_id, expected_revision)
            self._reviewable(doc)
            self._archive_sources(db, document_id)
            current = self._revision(db, document_id, expected_revision)
            record = record_from_dict(json.loads(current["record_json"]))
            pages = pages_from_json(doc["page_json"])
            parts = path.split(".")
            if len(parts) == 2 and parts[0] == "fields" and parts[1] in record.fields:
                old = record.fields[parts[1]]
                target = "header"
            elif len(parts) == 3 and parts[0] == "line_items" and parts[2] in ("description", "quantity", "unit_price", "line_total", "tax"):
                row = next((item for item in record.line_items if item.row_id == parts[1]), None)
                if row is None:
                    raise ValueError(f"Unknown row ID: {parts[1]}")
                old = getattr(row, parts[2])
                target = "row"
            else:
                raise ValueError(f"Unsupported field path: {path}")
            refs = old.evidence_ids if evidence_ids is None else evidence_ids
            unknown = set(refs) - {span.id for page in pages for span in page.spans}
            if unknown:
                raise ValueError(f"Unknown evidence IDs: {', '.join(sorted(unknown))}")
            revised = FieldValue(
                value=value, raw=old.raw, evidence_ids=refs,
                origin="reviewer", missing_reason="reviewer_marked_missing" if value is None else None,
            )
            if target == "header":
                fields = dict(record.fields)
                fields[parts[1]] = revised
                record = replace(record, fields=fields)
            else:
                rows = tuple(replace(item, **{parts[2]: revised}) if item.row_id == parts[1] else item
                             for item in record.line_items)
                record = replace(record, line_items=rows)
            new_revision = expected_revision + 1
            data = record.to_dict()
            self._guard_metadata(len(_json(data).encode()) + len(doc["page_json"].encode()))
            run = db.execute("SELECT extra_issues_json FROM extraction_runs WHERE document_id=?", (document_id,)).fetchone()
            extra_issues = json.loads(run["extra_issues_json"]) if run else []
            issues = [asdict(issue) for issue in validate_invoice(record, pages)] + extra_issues
            db.execute("INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?)",
                       (document_id, new_revision, expected_revision, _json(data), _hash(data), _json(issues), actor, _now()))
            db.execute("UPDATE documents SET current_revision=?, status='REVIEW_READY' WHERE id=?",
                       (new_revision, document_id))
            self._event(db, document_id, new_revision, "field_edited", actor, path)
            db.execute("INSERT INTO revision_sources SELECT document_id,?,page_json,extraction_json,images_json "
                       "FROM revision_sources WHERE document_id=? AND revision=?",
                       (new_revision, document_id, expected_revision))
            return new_revision

    def acknowledge(self, document_id: str, expected_revision: int, code: str, path: str,
                    reason: str, actor: str) -> None:
        if not reason.strip() or not actor.strip():
            raise ValueError("reason and actor are required")
        key = f"{code}|{path}"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._reviewable(self._current(db, document_id, expected_revision))
            rev = self._revision(db, document_id, expected_revision)
            if db.execute("SELECT 1 FROM approvals WHERE document_id=? AND revision=?",
                          (document_id, expected_revision)).fetchone():
                raise ReviewConflict("Approved decisions are immutable; edit the record to create a new revision")
            issues = json.loads(rev["issues_json"])
            if not any(_issue_key(issue) == key and issue["blocking"] for issue in issues):
                raise ValueError(f"No blocking issue at {key}")
            self._guard_metadata(len(reason.encode()) + len(actor.encode()))
            db.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?) ON CONFLICT(document_id,revision,issue_key) DO UPDATE SET reason=excluded.reason, actor=excluded.actor, created_at=excluded.created_at",
                       (document_id, expected_revision, key, reason, actor, _now()))
            self._event(db, document_id, expected_revision, "issue_acknowledged", actor, key)

    def approve(self, document_id: str, expected_revision: int, actor: str) -> dict:
        if not actor.strip() or actor == "extractor":
            raise ValueError("a human reviewer identity is required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._current(db, document_id, expected_revision)
            self._reviewable(doc)
            rev = self._revision(db, document_id, expected_revision)
            record = json.loads(rev["record_json"])
            absent = [name for name in REQUIRED_FIELDS if not (record["fields"][name]["value"] or "").strip()]
            if absent:
                raise ReviewBlocked(f"Required fields unresolved: {', '.join(absent)}")
            issues = json.loads(rev["issues_json"])
            decisions = [dict(row) for row in db.execute(
                "SELECT issue_key, reason, actor, created_at FROM decisions WHERE document_id=? AND revision=? ORDER BY issue_key",
                (document_id, expected_revision))]
            decided = {decision["issue_key"] for decision in decisions}
            pending = [_issue_key(issue) for issue in issues if issue["blocking"] and _issue_key(issue) not in decided]
            if pending:
                raise ReviewBlocked(f"Blocking issues unresolved: {', '.join(pending)}")
            existing = db.execute("SELECT * FROM approvals WHERE document_id=? AND revision=?",
                                  (document_id, expected_revision)).fetchone()
            if existing:
                if existing["actor"] != actor:
                    raise ReviewConflict(f"Revision already approved by {existing['actor']}")
                return {**dict(existing), "source_sha256": doc["source_sha256"]}
            payload = {
                "document_id": document_id, "revision": expected_revision,
                "record_hash": rev["record_hash"], "source_sha256": doc["source_sha256"],
                "decision_hash": _hash(decisions), "policy_version": POLICY_VERSION, "actor": actor,
            }
            approval_hash = _hash(payload)
            created_at = _now()
            self._guard_metadata(len(actor.encode()))
            db.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?)",
                       (document_id, expected_revision, rev["record_hash"], payload["decision_hash"],
                        approval_hash, actor, POLICY_VERSION, created_at))
            db.execute("UPDATE documents SET status='APPROVED' WHERE id=?", (document_id,))
            self._event(db, document_id, expected_revision, "approved", actor, approval_hash)
            return {**payload, "approval_hash": approval_hash, "created_at": created_at}

    def export(self, document_id: str, format: str) -> dict:
        if format not in ("json", "csv"):
            raise ValueError("format must be json or csv")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            doc = self._current(db, document_id)
            self._reviewable(doc)
            revision = doc["current_revision"]
            rev = self._revision(db, document_id, revision)
            approved = db.execute("SELECT * FROM approvals WHERE document_id=? AND revision=?",
                                  (document_id, revision)).fetchone()
            if approved is None or approved["record_hash"] != rev["record_hash"]:
                raise ReviewConflict("Current revision has no matching approval")
            existing = db.execute("SELECT manifest_json FROM exports WHERE document_id=? AND revision=? AND format=? AND schema_version=?",
                                  (document_id, revision, format, EXPORT_SCHEMA_VERSION)).fetchone()
            if existing:
                manifest = json.loads(existing["manifest_json"])
                for file in manifest["files"]:
                    path = Path(file["path"])
                    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != file["sha256"]:
                        raise ReviewConflict(f"Previously exported file is missing or changed: {path}")
                return manifest
            record = json.loads(rev["record_json"])
            issues = json.loads(rev["issues_json"])
            decisions = [dict(row) for row in db.execute("SELECT issue_key, reason, actor, created_at FROM decisions WHERE document_id=? AND revision=? ORDER BY issue_key",
                                                  (document_id, revision))]
            base = self.export_root / document_id / f"revision-{revision}"
            files: list[dict] = []
            if format == "json":
                extraction = db.execute("SELECT profile,model_id,prompt_sha256,parser_version,input_sha256,created_at FROM extraction_runs WHERE document_id=?",
                                        (document_id,)).fetchone()
                data = {
                    "schema_version": EXPORT_SCHEMA_VERSION, "document_id": document_id,
                    "revision": revision, "source_sha256": doc["source_sha256"],
                    "record_hash": rev["record_hash"], "approval_hash": approved["approval_hash"],
                    "decision_hash": approved["decision_hash"],
                    "record": record, "page": asdict(pages_from_json(doc["page_json"])[0]),
                    "pages": [asdict(page) for page in pages_from_json(doc["page_json"])],
                    "extraction": dict(extraction) if extraction else None,
                    "issues": issues, "decisions": decisions,
                }
                path = base / "invoice.json"
                files = self._write_export_files([(path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode())])
            else:
                fields = record["fields"]
                header = io.StringIO(newline="")
                writer = csv.writer(header)
                writer.writerow(["document_id", "revision", *(
                    column for name in HEADER_FIELDS for column in
                    (name, f"{name}_origin", f"{name}_missing_reason", f"{name}_evidence_ids")
                )])
                writer.writerow([document_id, revision, *(
                    value for name in HEADER_FIELDS for value in (
                        _csv_number(fields[name]["value"]) if name in ("subtotal", "tax", "discount", "shipping", "total") else _csv_text(fields[name]["value"]),
                        fields[name]["origin"], fields[name]["missing_reason"] or "",
                        "|".join(fields[name]["evidence_ids"]),
                    )
                )])
                items = io.StringIO(newline="")
                writer = csv.writer(items)
                row_fields = ("description", "quantity", "unit_price", "line_total", "tax")
                writer.writerow(["document_id", "revision", "row_id", *(
                    column for name in row_fields for column in
                    (name, f"{name}_origin", f"{name}_missing_reason", f"{name}_evidence_ids")
                )])
                for row in record["line_items"]:
                    writer.writerow([document_id, revision, row["row_id"], *(
                        value for name in row_fields for value in (
                            _csv_text(row[name]["value"]) if name == "description" else _csv_number(row[name]["value"]),
                            row[name]["origin"], row[name]["missing_reason"] or "",
                            "|".join(row[name]["evidence_ids"]),
                        )
                    )])
                files = self._write_export_files([(base / name, content.encode("utf-8")) for name, content in
                                                  (("header.csv", header.getvalue()), ("line-items.csv", items.getvalue()))])
            manifest = {
                "document_id": document_id, "revision": revision, "format": format,
                "schema_version": EXPORT_SCHEMA_VERSION, "approval_hash": approved["approval_hash"],
                "decision_hash": approved["decision_hash"],
                "files": files,
            }
            db.execute("INSERT INTO exports VALUES (?,?,?,?,?,?)",
                       (document_id, revision, format, EXPORT_SCHEMA_VERSION, _json(manifest), _now()))
            self._event(db, document_id, revision, "exported", approved["actor"], format)
            return manifest

    def history(self, document_id: str) -> list[dict]:
        with self._connect() as db:
            self._current(db, document_id)
            return [dict(row) for row in db.execute("SELECT revision, kind, actor, detail, created_at FROM review_events WHERE document_id=? ORDER BY id", (document_id,))]

    def exported_file(self, document_id: str, revision: int, format: str, filename: str) -> tuple[bytes, str]:
        """Return only a file recorded by an immutable export manifest."""
        if filename not in ("invoice.json", "header.csv", "line-items.csv"):
            raise KeyError("Unknown export file")
        with self._connect() as db:
            self._current(db, document_id)
            row = db.execute(
                "SELECT manifest_json FROM exports WHERE document_id=? AND revision=? AND format=? AND schema_version=?",
                (document_id, revision, format, EXPORT_SCHEMA_VERSION),
            ).fetchone()
            if row is None:
                raise KeyError("Unknown export")
            manifest = json.loads(row["manifest_json"])
            entry = next((file for file in manifest["files"] if Path(file["path"]).name == filename), None)
            if entry is None:
                raise KeyError("File is not part of this export")
            path = Path(entry["path"])
            if not path.resolve().is_relative_to(self.export_root) or path.is_symlink() or not path.is_file():
                raise ReviewConflict("Export file is missing or replaced")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise ReviewConflict("Export file failed checksum verification")
            return content, "application/json" if filename.endswith(".json") else "text/csv; charset=utf-8"
