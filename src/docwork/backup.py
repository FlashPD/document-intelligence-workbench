"""SQLite snapshots with a verified, portable inventory of referenced artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .review import _json, _now

BACKUP_VERSION = "workbench-backup-v1"
TABLES = {
    "documents", "revisions", "decisions", "approvals", "exports", "review_events",
    "extraction_runs", "document_objects", "jobs", "document_pages",
    "parser_checkpoints", "parser_checkpoint_pages",
}
LIFECYCLE_TABLES = {"revision_sources", "processing_attempts", "deletion_jobs", "batches", "batch_items", "storage_policy"}


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _connect(path: Path, *, mode: str = "ro", immutable: bool = False) -> sqlite3.Connection:
    db = sqlite3.connect(path.as_uri() + f"?mode={mode}" + ("&immutable=1" if immutable else ""),
                         uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA trusted_schema=OFF")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _regular(root: Path, relative: str) -> Path:
    parts = relative.split("/")
    if not relative or any(part in ("", ".", "..") for part in parts) or "\\" in relative:
        raise ValueError("Unsafe backup artifact path")
    path = root
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Artifact root must be a regular directory")
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Symlinks are forbidden in backup artifacts")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Missing or unsafe backup artifact: {relative}")
    return path


def _check_database(db: sqlite3.Connection) -> None:
    schema = db.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    tables = {row["name"] for row in schema if row["type"] == "table"}
    if (tables not in (TABLES, TABLES | {"revision_sources"}, TABLES | LIFECYCLE_TABLES)
            or any(row["type"] not in ("table", "index") for row in schema)):
        raise ValueError("Unsupported workbench database schema")
    if [row[0] for row in db.execute("PRAGMA integrity_check")] != ["ok"]:
        raise ValueError("Workbench database failed integrity check")
    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Workbench database has broken references")
    if "deletion_jobs" in tables and db.execute("SELECT 1 FROM deletion_jobs WHERE status='PENDING'").fetchone():
        raise ValueError("Finish pending document deletions before creating a portable backup")


def _references(db: sqlite3.Connection, *, export_root: Path | None = None) -> dict[str, tuple[str, int | None]]:
    """Derive the complete inventory from the snapshot, never from a directory walk."""
    refs: dict[str, tuple[str, int | None]] = {}

    def add(relative: str, digest: str, size: int | None = None) -> None:
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid artifact checksum in database")
        if size is not None and (type(size) is not int or size < 1):
            raise ValueError("Invalid artifact size in database")
        prior = refs.get(relative)
        if prior and (prior[0] != digest or (size is not None and prior[1] not in (None, size))):
            raise ValueError("Conflicting artifact references")
        refs[relative] = (digest, size if size is not None else prior[1] if prior else None)

    def intake(relative: str, digest: str, size: int | None = None) -> None:
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid artifact checksum in database")
        category = relative.split("/", 1)[0]
        if category not in ("objects", "renders"):
            raise ValueError("Invalid intake artifact category")
        expected = f"{category}/{digest[:2]}/{digest}" + (".png" if category == "renders" else "")
        if relative != expected:
            raise ValueError("Unsafe content-addressed artifact reference")
        add(f"intake/{relative}", digest, size)

    for row in db.execute("SELECT relative_path,sha256,size_bytes FROM document_objects"):
        intake(*row)
    for row in db.execute("SELECT object_relpath,source_sha256,size_bytes,page_image_relpath,page_image_sha256 FROM documents"):
        if row["object_relpath"]:
            intake(row["object_relpath"], row["source_sha256"], row["size_bytes"])
        if row["page_image_relpath"]:
            intake(row["page_image_relpath"], row["page_image_sha256"])
    for table in ("document_pages", "parser_checkpoint_pages"):
        for row in db.execute(f"SELECT image_relpath,image_sha256 FROM {table}"):
            intake(*row)
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='revision_sources'").fetchone():
        for row in db.execute("SELECT images_json FROM revision_sources"):
            for image in json.loads(row[0]):
                intake(image["image_relpath"], image["image_sha256"])
    for row in db.execute("SELECT result_json,result_sha256 FROM parser_checkpoints"):
        if hashlib.sha256(row["result_json"].encode()).hexdigest() != row["result_sha256"]:
            raise ValueError("Parser checkpoint failed integrity verification")
    for row in db.execute("SELECT document_id,revision,format,manifest_json FROM exports"):
        if not re.fullmatch(r"[0-9a-f]{32}", row["document_id"]) or row["revision"] < 1:
            raise ValueError("Invalid export identity")
        manifest = json.loads(row["manifest_json"])
        names = {"invoice.json"} if row["format"] == "json" else {"header.csv", "line-items.csv"} if row["format"] == "csv" else set()
        files = manifest["files"]
        if not names or len(files) != len(names) or {Path(file["path"]).name for file in files} != names:
            raise ValueError("Invalid export file inventory")
        for file in files:
            tail = f"{row['document_id']}/revision-{row['revision']}/{Path(file['path']).name}"
            expected = str(export_root / tail) if export_root else f"exports/{tail}"
            if file["path"] != expected:
                raise ValueError("Unsafe export artifact reference")
            add(f"exports/{tail}", file["sha256"])
    return refs


def _counts(db: sqlite3.Connection) -> dict:
    return {**{table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
               for table in ("documents", "revisions", "approvals", "exports", "parser_checkpoints")},
            "documents_without_stored_original": db.execute(
                "SELECT COUNT(*) FROM documents WHERE object_relpath IS NULL").fetchone()[0]}


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _copy(source: Path, target: Path, digest: str, size: int | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst)
        dst.flush()
        os.fsync(dst.fileno())
    if _digest(target) != digest or (size is not None and target.stat().st_size != size):
        raise ValueError("Artifact changed or failed checksum verification during copy")


def _destination(path: Path) -> Path:
    if path.is_symlink() or path.exists():
        raise ValueError("Destination must be a new directory")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _publish(stage: Path, destination: Path) -> None:
    # Reserve without clobbering a directory created by another invocation.
    destination.mkdir()
    try:
        stage.replace(destination)
    except BaseException:
        destination.rmdir()
        raise


def create_backup(database: Path, object_root: Path, destination: Path, *,
                  export_root: Path | None = None) -> dict:
    destination = _destination(destination)
    database = database.resolve(strict=True)
    object_root = object_root.absolute()
    export_root = (export_root or database.parent / "exports").absolute()
    if destination.is_relative_to(object_root) or destination.is_relative_to(export_root):
        raise ValueError("Backup destination must be outside artifact stores")
    with tempfile.TemporaryDirectory(prefix=".backup-", dir=destination.parent) as temp:
        stage = Path(temp) / "bundle"
        stage.mkdir(mode=0o700)
        snapshot = stage / "database.sqlite"
        # Block publishing/pruning artifact references while copying. A separate
        # reader uses SQLite's backup API; backing up the writer would deadlock.
        with closing(_connect(database, mode="rw")) as guard:
            guard.execute("BEGIN IMMEDIATE")
            with closing(_connect(database)) as source, closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
            with closing(_connect(snapshot, mode="rw")) as db:
                db.execute("PRAGMA journal_mode=DELETE")
                _check_database(db)
                refs = _references(db, export_root=export_root)
                for relative, (digest, size) in refs.items():
                    category, tail = relative.split("/", 1)
                    root = object_root if category == "intake" else export_root
                    _copy(_regular(root, tail), stage / relative, digest, size)
                # Store portable paths only in the backup copy, never the live DB.
                for row in db.execute("SELECT rowid,manifest_json FROM exports").fetchall():
                    manifest = json.loads(row["manifest_json"])
                    for file in manifest["files"]:
                        file["path"] = "exports/" + str(Path(file["path"]).relative_to(export_root))
                    db.execute("UPDATE exports SET manifest_json=? WHERE rowid=?", (_json(manifest), row["rowid"]))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='storage_policy'").fetchone():
                    db.execute("UPDATE storage_policy SET object_root='intake'")
                db.commit()
                counts = _counts(db)
            guard.rollback()
        inventory = [{"path": relative, "sha256": _digest(stage / relative),
                      "size_bytes": (stage / relative).stat().st_size}
                     for relative in sorted({"database.sqlite", *refs})]
        manifest = {"backup_version": BACKUP_VERSION, "created_at": _now(),
                    "counts": counts, "files": inventory}
        _write(stage / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
        verify_backup(stage)
        _publish(stage, destination)
    return {"backup": str(destination), **verify_backup(destination)}


def verify_backup(bundle: Path) -> dict:
    bundle = bundle.absolute()
    manifest_path = _regular(bundle, "manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("backup_version") != BACKUP_VERSION:
        raise ValueError("Unsupported backup version")
    inventory = {}
    for file in manifest["files"]:
        relative = file["path"]
        if relative in inventory or relative == "manifest.json":
            raise ValueError("Duplicate or recursive backup inventory")
        path = _regular(bundle, relative)
        if path.stat().st_size != file["size_bytes"] or _digest(path) != file["sha256"]:
            raise ValueError(f"Backup artifact failed checksum verification: {relative}")
        inventory[relative] = file
    actual = set()
    for parent, dirs, files in os.walk(bundle, followlinks=False):
        for name in dirs + files:
            if (Path(parent) / name).is_symlink():
                raise ValueError("Symlinks are forbidden in backups")
        actual.update(str((Path(parent) / name).relative_to(bundle)) for name in files)
    if actual != {"manifest.json", *inventory} or "database.sqlite" not in inventory:
        raise ValueError("Backup contains missing or unlisted files")
    with closing(_connect(bundle / "database.sqlite", immutable=True)) as db:
        _check_database(db)
        refs = _references(db)
        if {"database.sqlite", *refs} != set(inventory):
            raise ValueError("Backup inventory does not match database references")
        for relative, (digest, size) in refs.items():
            if inventory[relative]["sha256"] != digest or (size is not None and inventory[relative]["size_bytes"] != size):
                raise ValueError("Backup inventory disagrees with database checksums")
        counts = _counts(db)
        if counts != manifest["counts"]:
            raise ValueError("Backup counts disagree with database")
    return {"status": "verified", "backup_version": BACKUP_VERSION,
            "manifest_sha256": _digest(manifest_path), "files": len(inventory), "counts": counts}


def restore_backup(bundle: Path, destination: Path) -> dict:
    destination = _destination(destination)
    bundle = bundle.absolute()
    if destination.is_relative_to(bundle):
        raise ValueError("Restore destination must be outside the backup")
    verified = verify_backup(bundle)
    manifest = json.loads((bundle / "manifest.json").read_text())
    with tempfile.TemporaryDirectory(prefix=".restore-", dir=destination.parent) as temp:
        stage = Path(temp) / "workbench"
        stage.mkdir(mode=0o700)
        for file in manifest["files"]:
            _copy(_regular(bundle, file["path"]), stage / file["path"], file["sha256"], file["size_bytes"])
        # Verify the bytes actually copied, including a source modified during restore.
        _write(stage / "manifest.json", (bundle / "manifest.json").read_bytes())
        copied = verify_backup(stage)
        if copied != verified:
            raise ValueError("Backup changed during restoration")
        (stage / "manifest.json").unlink()
        with closing(_connect(stage / "database.sqlite", mode="rw")) as db:
            for row in db.execute("SELECT rowid,manifest_json FROM exports").fetchall():
                export = json.loads(row["manifest_json"])
                for file in export["files"]:
                    file["path"] = str(destination / file["path"])
                db.execute("UPDATE exports SET manifest_json=? WHERE rowid=?", (_json(export), row["rowid"]))
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            interrupted = db.execute("SELECT * FROM jobs WHERE status='PROCESSING'").fetchall()
            requeued = 0
            for row in interrupted:
                cancelled = "stop_requested" in columns and row["stop_requested"]
                status = "CANCELLED" if cancelled else "QUEUED"
                requeued += int(not cancelled)
                db.execute("UPDATE jobs SET status=?,worker_id=NULL,lease_until=NULL,fence=fence+1,error_code=NULL WHERE id=?", (status, row["id"]))
                db.execute("UPDATE documents SET status=? WHERE id=?", ("CANCELLED" if cancelled else "RECEIVED", row["document_id"]))
                if "stop_requested" in columns:
                    db.execute("UPDATE jobs SET stage=?,stop_requested=0 WHERE id=?", (status, row["id"]))
                db.execute("INSERT INTO review_events(document_id,revision,kind,actor,detail,created_at) VALUES (?,0,'backup_restored','operator',?,?)",
                           (row["document_id"], verified["manifest_sha256"], _now()))
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='processing_attempts'").fetchone():
                db.execute("UPDATE processing_attempts SET status='RESTORED',finished_at=? "
                           "WHERE status IN ('PROCESSING','ABANDONED','CLEANUP_REQUIRED')", (_now(),))
            db.commit()
            _check_database(db)
            _references(db, export_root=destination / "exports")
        (stage / "intake" / "quarantine").mkdir(parents=True, exist_ok=True)
        report = {"status": "restored", "manifest_sha256": verified["manifest_sha256"],
                  "database": str(destination / "database.sqlite"),
                  "objects": str(destination / "intake"), "counts": verified["counts"],
                  "requeued_jobs": requeued}
        _write(stage / "restore.json", (json.dumps(report, indent=2) + "\n").encode())
        _publish(stage, destination)
    return report
