"""Archive, restore, and independently verify a stopped author review pilot.

Portable backups preserve original records, approvals and export bytes. Timing,
protocol and source snapshots are copied byte-for-byte. Verification restores
only into a temporary directory; it never starts a review trial or changes the
archived evidence. A completed session is not proof of error-free review.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from docwork.backup import create_backup, restore_backup, verify_backup
from docwork.pilot_bundle import DOCUMENTS, report_pilot, verify_pilot_setup
from docwork.review import ReviewConflict

VERSION = "author-pilot-archive-v1"
SESSION_FILES = ("protocol.json", "source_snapshot.json", "timing.json")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(directory: Path) -> list[dict]:
    files = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Pilot archives cannot contain symlinks")
        if path.is_file() and path != directory / "manifest.json":
            files.append({"path": str(path.relative_to(directory)), "sha256": digest(path),
                          "size_bytes": path.stat().st_size})
    return files


def check_archive(directory: Path) -> dict:
    directory = directory.resolve(strict=True)
    if (directory / "manifest.json").is_symlink():
        raise ValueError("Pilot archive manifest cannot be a symlink")
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("archive_version") != VERSION or manifest["files"] != inventory(directory):
        raise ValueError("Pilot archive inventory changed")
    if manifest["source_report_sha256"] != digest(directory / "report.json"):
        raise ValueError("Pilot archive report differs from the supplied author report")
    verify_backup(directory / "backup")
    return manifest


def restore_session(directory: Path, output: Path) -> None:
    restore_backup(directory / "backup", output)
    # Restore already rebases absolute export paths to the destination. Only
    # the database/object-root names differ from the pilot's dedicated layout.
    (output / "database.sqlite").rename(output / "review.sqlite")
    (output / "intake").rename(output / "objects")
    with sqlite3.connect(output / "review.sqlite") as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='storage_policy'").fetchone():
            db.execute("UPDATE storage_policy SET object_root='objects' WHERE id=1")
    for name in SESSION_FILES:
        shutil.copyfile(directory / name, output / name)


def verify_archive(root: Path, directory: Path) -> dict:
    directory = directory.resolve(strict=True)
    manifest = check_archive(directory)
    protocol = json.loads((directory / "protocol.json").read_text())
    if (tuple(d["corpus_id"] for d in protocol["documents"]) != DOCUMENTS or
            protocol["participant"] != "project_author" or protocol["mode"] != "assisted_only"):
        raise ValueError("Archive must retain the six declared author-pilot cases")
    with tempfile.TemporaryDirectory(prefix="docwork-pilot-audit-") as temporary:
        session = Path(temporary) / "session"
        restore_session(directory, session)
        report = report_pilot(root.resolve(strict=True), session)
    if report != json.loads((directory / "report.json").read_text()):
        raise ValueError("Restored pilot does not reproduce its report")
    if check_archive(directory) != manifest:
        raise ValueError("Pilot archive changed during verification")
    return {"status": "verified", "archive_version": VERSION,
            "manifest_sha256": digest(directory / "manifest.json"),
            "pilot_status": report["status"], "completed": report["completed"],
            "scheduled": report["scheduled"], "timing": report["timing"],
            "scope": "Portable restore reproduces the original report, approved records and immutable exports. "
                     "No inference, new human review, authenticated participation or time-saved claim."}


def archive_pilot(root: Path, source: Path, report_path: Path, output: Path) -> dict:
    root, source = root.resolve(strict=True), source.resolve(strict=True)
    report_path, output = report_path.resolve(strict=True), output.absolute()
    if (output.exists() or output.is_symlink() or ".." in output.parts or
            any(parent.is_symlink() for parent in output.parents)):
        raise ValueError("Pilot archival requires a new output directory without symlink traversal")
    if (not output.is_relative_to(root / "evals") and not output.is_relative_to(root / "artifacts") or
            output.is_relative_to(source)):
        raise ValueError("Pilot archive belongs under evals or artifacts, outside the source session")
    output.parent.mkdir(parents=True, exist_ok=True)
    # A live pilot owns this same advisory lock. Removing its lock file would
    # bypass that protection; hold the original inode throughout the snapshot.
    with (source / ".pilot.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("Stop the pilot server before archiving its session") from exc
        verify_pilot_setup(root, source)
        before = {name: digest(source / name) for name in SESSION_FILES}
        supplied = report_path.read_bytes()
        if report_pilot(root, source) != json.loads(supplied):
            raise ValueError("Supplied report does not match the current session")
        with tempfile.TemporaryDirectory(prefix=".pilot-archive-", dir=output.parent) as temporary:
            stage = Path(temporary) / "archive"
            stage.mkdir()
            create_backup(source / "review.sqlite", source / "objects", stage / "backup")
            # Compact the portable copy after path rebasing to remove obsolete
            # absolute paths from unused SQLite pages; the live DB is untouched.
            with sqlite3.connect(stage / "backup/database.sqlite") as db:
                db.execute("VACUUM")
            backup_manifest = stage / "backup/manifest.json"
            backup = json.loads(backup_manifest.read_text())
            for entry in backup["files"]:
                if entry["path"] == "database.sqlite":
                    entry["sha256"] = digest(stage / "backup/database.sqlite")
                    entry["size_bytes"] = (stage / "backup/database.sqlite").stat().st_size
            backup_manifest.write_text(json.dumps(backup, indent=2) + "\n")
            verify_backup(stage / "backup")
            for name in SESSION_FILES:
                shutil.copyfile(source / name, stage / name)
            (stage / "report.json").write_bytes(supplied)
            manifest = {"archive_version": VERSION, "source_report_sha256": hashlib.sha256(supplied).hexdigest(),
                        "files": inventory(stage),
                        "scope": "Original author protocol, timing events and supplied report preserved byte-for-byte. "
                                 "SQLite is a portable backup with rebased export paths and compacted pages; "
                                 "original suggestions, revisions, approvals and export bytes are preserved. "
                                 "Local hashes are not signed proof of human participation."}
            (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            result = verify_archive(root, stage)
            if before != {name: digest(source / name) for name in SESSION_FILES} or supplied != report_path.read_bytes():
                raise ValueError("Pilot source evidence changed during archival")
            # Reserve without replacing a concurrently created destination.
            output.mkdir()
            try:
                stage.replace(output)
            except BaseException:
                output.rmdir()
                raise
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create")
    create.add_argument("session", type=Path)
    create.add_argument("--report", type=Path, required=True)
    create.add_argument("--output-dir", type=Path, required=True)
    verify = actions.add_parser("verify")
    verify.add_argument("archive", type=Path)
    verify.add_argument("--require-complete", action="store_true", help="Fail if any declared author trial is incomplete")
    restore = actions.add_parser("restore")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "create":
            result = archive_pilot(root, args.session, args.report, args.output_dir)
        else:
            result = verify_archive(root, args.archive)
            if args.action == "verify" and args.require_complete and result["pilot_status"] != "complete":
                raise ValueError("Release verification requires all six author trials to be complete")
            if args.action == "restore":
                output = args.output_dir.absolute()
                if not output.is_relative_to(root / "artifacts"):
                    raise ValueError("Restored pilot belongs in a new artifacts directory")
                restore_session(args.archive.resolve(strict=True), output)
                if report_pilot(root, output) != json.loads((args.archive / "report.json").read_text()):
                    raise ValueError("Restored pilot report differs")
                result["restored_session"] = str(args.output_dir)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, ReviewConflict, sqlite3.Error) as exc:
        parser.exit(2, f"Pilot archive failed: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
