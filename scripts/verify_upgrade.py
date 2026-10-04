"""Create a disposable workbench using a prior committed version, then upgrade it.

Uses recorded fictional development candidates. Automated fixture approvals
exercise persistence only; this never opens a user's workbench or measures quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from docwork.backup import create_backup, restore_backup, verify_backup
from docwork.intake import IntakeStore
from docwork.review import ReviewConflict
from verify_release_checkout import committed_files, copy_committed_files


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = "v0.1.0-experimental"

SEED = """
import hashlib, io, json, sqlite3, sys
from dataclasses import asdict
from pathlib import Path
from docwork.demo_replay import prepare_replay
from docwork.intake import IntakeStore
root, output = (Path(value).resolve() for value in sys.argv[1:])
replay = prepare_replay(root, output)
store = IntakeStore(output / 'review.sqlite', output / 'objects')
for case in replay['cases'][:2]:
    doc = case['document_id']
    store.edit(doc, 1, 'fields.invoice_number', 'UPGRADE-FIXTURE', 'automated-upgrade-fixture')
    for issue in store.get(doc)['issues']:
        if issue['blocking']:
            store.acknowledge(doc, 2, issue['code'], issue['path'],
                              'Automated persistence fixture, not quality review', 'automated-upgrade-fixture')
    store.approve(doc, 2, 'automated-upgrade-fixture')
    for kind in ('json', 'csv'):
        store.export(doc, kind)
queued = store.submit(io.BytesIO((root / 'samples/clean.png').read_bytes()), 'clean.png', 'image/png')
active = store.claim('previous-version-worker', lease_seconds=120)
pending = store.submit(io.BytesIO((root / 'samples/clean.png').read_bytes()), 'duplicate.png', 'image/png')
with store._connect() as db:
    schema = {row['name']: [dict(column) for column in db.execute('PRAGMA table_info('+row['name']+')')]
              for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
exports = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
           for path in store.export_root.rglob('*') if path.is_file()}
expected = {'cases': replay['cases'], 'active': asdict(active), 'pending': pending,
            'schema': schema, 'exports': exports,
            'revisions': {case['document_id']: [store.get(case['document_id'], n)
                          for n in range(1, store.get(case['document_id'])['current_revision'] + 1)]
                          for case in replay['cases']},
            'history': {case['document_id']: store.history(case['document_id']) for case in replay['cases']}}
(output / 'expected.json').write_text(json.dumps(expected, indent=2)+'\\n')
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(condition, message):
    if not condition:
        raise ValueError(message)


def check_preserved(store, workbench, expected):
    for case in expected["cases"]:
        doc = case["document_id"]
        for original in expected["revisions"][doc]:
            actual = store.get(doc, original["revision"])
            for name in ("record", "record_hash", "pages", "issues", "approval", "decisions"):
                check(json.loads(json.dumps(actual[name])) == original[name],
                      f"Historical {name} changed during upgrade/restore")
        events = store.history(doc)
        check(events[:len(expected["history"][doc])] == expected["history"][doc], "Historical events changed")
        for revision in expected["revisions"][doc]:
            if revision["approval"]:
                for kind in ("json", "csv"):
                    for file in store.export(doc, kind)["files"]:
                        relative = str(Path(file["path"]).relative_to(workbench))
                        check(digest(Path(file["path"])) == expected["exports"][relative], "Export bytes changed")
        check(digest(store.object_path(doc)) == case["source_sha256"], "Original bytes changed")
    check(store.get(expected["cases"][2]["document_id"])["approval"] is None,
          "Unapproved record inherited approval")
    audit = store.reconcile()
    check(not (audit["missing"] or audit["corrupt"] or audit["metadata_errors"]), "Artifact audit failed")


def verify(root, output, ref=REFERENCE):
    if output.exists() or output.is_symlink():
        raise ValueError("Choose a new upgrade evidence directory")
    git_source, files = committed_files(root, ref)
    # This protocol targets the actual prior release, not current-source fixtures.
    check(git_source["commit"] == "6c2b3c524baee68e8598408e415bdc7a043f3d73", "Expected the experimental release commit")
    output.mkdir(parents=True)
    current_paths = [Path(__file__), *sorted((root / "src/docwork").glob("*.py"))]
    source = {str(path.relative_to(root)): digest(path) for path in current_paths}
    report = {"version": "prior-release-upgrade-v1", "status": "failed", "prior_source": git_source,
              "source_sha256": source, "checks": [], "scope": "Disposable database created by the committed "
              "experimental release with fictional recorded candidates and automated fixture approvals. "
              "Checks in-place schema upgrade, historical edits/decisions/approvals/export bytes, unapproved records, "
              "pending jobs, current backup/relocated restore and fencing. No fresh OCR/inference, human review, "
              "machine-crash or old-process/new-process concurrent-upgrade claim."}
    try:
        with tempfile.TemporaryDirectory(prefix="docwork-upgrade-") as temporary:
            directory = Path(temporary).resolve()
            prior = directory / "prior"
            prior.mkdir()
            hashes = copy_committed_files(root, files, prior)
            workbench = directory / "workbench"
            with (output / "seed.log").open("x") as log:
                subprocess.run([sys.executable, "-c", SEED, str(prior), str(workbench)], cwd=prior,
                               env={**os.environ, "PYTHONPATH": str(prior / "src")},
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
            check(all(digest(prior / name) == value for name, value in hashes.items()), "Prior source changed")
            expected = json.loads((workbench / "expected.json").read_text())
            shutil.copyfile(workbench / "expected.json", output / "prior-state.json")
            check("storage_policy" not in expected["schema"] and "processing_attempts" not in expected["schema"],
                  "Fixture already uses new lifecycle schema")
            store = IntakeStore(workbench / "review.sqlite", workbench / "objects")
            check_preserved(store, workbench, expected)
            report["checks"].append("prior-version revisions, decisions, approvals, sources and exports preserved")
            active = expected["active"]["document_id"]
            pending = expected["pending"]
            for document in (active, pending):
                with store._connect() as db:
                    job = dict(db.execute("SELECT * FROM jobs WHERE document_id=?", (document,)).fetchone())
                check(json.loads(job["profile_json"]) == {"extractor": "ocr_rules"}, "Legacy profile default changed")
                check(not job["stop_requested"] and not job["reparse"], "Upgrade invented cancellation or reparse")
            check(store.status(active)["job"]["status"] == "PROCESSING" and
                  store.status(pending)["job"]["status"] == "QUEUED", "Legacy pending state changed")
            report["checks"].append("queued/active legacy jobs retain state and explicit rules defaults")
            with store._connect() as db:
                report["upgraded_tables"] = sorted(row["name"] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"))
                check(db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite integrity failed")
                check(not db.execute("PRAGMA foreign_key_check").fetchall(), "Foreign key check failed")
            reopened = IntakeStore(workbench / "review.sqlite", workbench / "objects")
            check_preserved(reopened, workbench, expected)
            report["checks"].append("migration reopens idempotently with valid SQLite integrity and references")
            backup = directory / "backup"
            create_backup(store.database, store.object_root, backup)
            report["backup_manifest_sha256"] = verify_backup(backup)["manifest_sha256"]
            restored_dir = directory / "relocated"
            result = restore_backup(backup, restored_dir)
            restored = IntakeStore(restored_dir / "database.sqlite", restored_dir / "intake")
            check_preserved(restored, restored_dir, expected)
            check(result["requeued_jobs"] == 1, "Active prior job was not requeued")
            with restored._connect() as db:
                job = dict(db.execute("SELECT * FROM jobs WHERE document_id=?", (active,)).fetchone())
            check(job["status"] == "QUEUED" and job["worker_id"] is None and
                  job["fence"] > expected["active"]["fence"], "Restore did not fence legacy worker")
            report["checks"].append("relocated backup restore preserves approved bytes and fences the old active worker")
            edited = expected["cases"][0]["document_id"]
            restored.edit(edited, 2, "fields.invoice_number", "UPGRADE-NEW-REVISION", "automated-upgrade-fixture")
            try:
                restored.export(edited, "json")
            except ReviewConflict:
                pass
            else:
                raise ValueError("New revision inherited old approval")
            check(restored.get(edited, 2)["approval"] == expected["revisions"][edited][1]["approval"],
                  "New edit changed historical approval")
            report["checks"].append("post-upgrade edit preserves historical approval and requires new approval")
        report["inputs_unchanged"] = all(digest(root / name) == value for name, value in source.items())
        if report["inputs_unchanged"]:
            report["status"] = "passed"
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, sqlite3.Error) as error:
        report["failure"] = (f"Prior-version seed exited {error.returncode}; see seed.log"
                             if isinstance(error, subprocess.CalledProcessError)
                             else f"{type(error).__name__}: {error}")
    report["artifacts"] = {p.name: digest(p) for p in output.iterdir() if p.is_file()}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = verify(ROOT, args.output_dir.resolve())
    print(json.dumps({"status": report["status"], "checks": report["checks"], "failure": report.get("failure"),
                      "report": str(args.output_dir / "report.json")}))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
