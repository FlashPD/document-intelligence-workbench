"""Bounded process-crash drills on fictional input; never a human review study."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from docwork.intake import IntakeStore, JobClaim
from docwork.review import ReviewBlocked, ReviewConflict
from docwork.supervisor import WorkerSupervisor
import docwork.intake as intake
import docwork.review as review
import docwork.worker as worker

ROOT = Path(__file__).resolve().parents[1]
CHECKS = (
    "parser_active", "checkpoint_render_written", "rules_extracted", "candidate_transaction",
    "review_transaction", "review_committed", "approval_transaction", "approval_committed",
    "csv_first_file", "csv_transaction", "csv_committed", "json_transaction",
)
PROCESSING = CHECKS[:4]
LEASE_SECONDS = 8
EXIT_CODE = 73
ACTOR = "automated-recovery-fixture"
EDIT_VALUE = "RECOVERY-1001"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def wait_for(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.05)
    raise TimeoutError("Recovery probe did not reach its declared boundary")


def open_store(workbench):
    return IntakeStore(workbench / "review.sqlite", workbench / "intake")


def snapshot(store, document):
    status = store.status(document)
    with store._connect() as db:
        claim = db.execute("SELECT worker_id,lease_until FROM jobs WHERE document_id=?", (document,)).fetchone()
        status["job"].update(dict(claim))
        counts = {name: db.execute(f"SELECT COUNT(*) FROM {name} WHERE document_id=?", (document,)).fetchone()[0]
                  for name in ("revisions", "approvals", "exports", "parser_checkpoint_pages")}
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
    try:
        detail = store.get(document)
    except ReviewBlocked:
        detail = None
    files = {str(path.relative_to(store.export_root)): digest(path)
             for path in store.export_root.rglob("*") if path.is_file()}
    return {"status": status, "detail": detail, "history": store.history(document),
            "attempts": store.attempts(document), "counts": counts, "integrity": integrity,
            "export_files": files,
            "scratch": sorted(path.name for path in (store.object_root / "quarantine").iterdir())}


def interrupt(marker, case, **detail):
    write_json(marker, {"case": case, "boundary_reached": True, "pid": os.getpid(),
                        "time_utc": datetime.now(timezone.utc).isoformat(), **detail})
    # No finally blocks, Python destructors or SQLite context exits run.
    os._exit(EXIT_CODE)


def child(workbench, document, case, image, marker):
    store = open_store(workbench)
    worker.WORKER_LEASE_SECONDS = LEASE_SECONDS
    worker.HEARTBEAT_SECONDS = .25
    if case == "resume":
        checkpoint = store.status(document)["parser_checkpoint"] is not None
        parser_calls = []
        real_run = worker._docker_run

        def run(*args, **kwargs):
            require(not checkpoint, "A valid committed checkpoint must avoid fresh parsing")
            parser_calls.append(True)
            return real_run(*args, **kwargs)

        with patch.object(worker, "_docker_run", side_effect=run):
            supervisor = WorkerSupervisor(store)
            supervisor.start()
            try:
                wait_for(lambda: store.status(document)["status"] in ("REVIEW_READY", "FAILED"))
            finally:
                supervisor.close()
        require(store.status(document)["status"] == "REVIEW_READY", "Replacement supervisor failed")
        require(len(parser_calls) == (0 if checkpoint else 1), "Unexpected parser retry count")
        write_json(marker, {"resumed_in_new_process": True, "pid": os.getpid(),
                            "parser_calls": len(parser_calls), "supervisor_error": supervisor.last_error})
        return
    if case == "parser_active":
        command = worker.parser_command

        def controlled_command(source, media, output, claim, *, image):
            value = command(source, media, output, claim, image=image)
            index = value.index(image)
            probe = "from pathlib import Path; import time; Path('/output/started').write_text('started'); time.sleep(120)"
            return value[:index] + ["--entrypoint", "python", image, "-c", probe]

        with patch.object(worker, "parser_command", side_effect=controlled_command):
            worker.process_one(store, "crash-probe", image=image, honor_job_profile=True)
    elif case == "checkpoint_render_written":
        real_write = intake._atomic_write

        def write(path, data):
            result = real_write(path, data)
            interrupt(marker, case, written_file=str(path.relative_to(store.object_root)), sha256=digest(path))
            return result

        with patch.object(intake, "_atomic_write", side_effect=write):
            worker.process_one(store, "crash-probe", image=image, honor_job_profile=True)
    elif case == "rules_extracted":
        extract = worker.extract_invoice_pages

        def extracted(pages):
            record = extract(pages)
            interrupt(marker, case, extracted_record=record.to_dict())

        with patch.object(worker, "extract_invoice_pages", side_effect=extracted):
            worker.process_one(store, "crash-probe", image=image, honor_job_profile=True)
    elif case == "csv_first_file":
        real_write = review._atomic_write

        def write(path, data):
            result = real_write(path, data)
            interrupt(marker, case, written_file=path.name, sha256=digest(path))
            return result

        with patch.object(review, "_atomic_write", side_effect=write):
            store.export(document, "csv")
    else:
        target_event = {"candidate_transaction": "candidate_created", "review_transaction": "field_edited",
                        "approval_transaction": "approved", "csv_transaction": "exported",
                        "json_transaction": "exported"}.get(case)
        real_event = store._event

        def event(db, doc, revision, kind, actor, detail):
            real_event(db, doc, revision, kind, actor, detail)
            if kind == target_event:
                interrupt(marker, case, uncommitted_event=kind)

        with patch.object(store, "_event", side_effect=event):
            if case == "candidate_transaction":
                worker.process_one(store, "crash-probe", image=image, honor_job_profile=True)
            elif case.startswith("review_"):
                store.edit(document, 1, "fields.invoice_number", EDIT_VALUE, ACTOR)
            elif case.startswith("approval_"):
                store.approve(document, 1, ACTOR)
            else:
                store.export(document, "json" if case.startswith("json_") else "csv")
        interrupt(marker, case, operation_committed=True)
    raise RuntimeError("Fault boundary was not reached")


def launch(workbench, document, case, image, marker, log):
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    stream = log.open("w")
    try:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child", case,
                                    "--workbench", str(workbench), "--document", document,
                                    "--image", image, "--marker", str(marker)],
                                   cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    except BaseException:
        stream.close()
        raise
    return process, stream


def stop_session(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def deny_export(store, document, format, revision):
    try:
        store.exported_file(document, revision, format, "invoice.json" if format == "json" else "header.csv")
    except KeyError:
        return True
    raise ValueError("An uncommitted export became downloadable")


def validate_case(case, before, interrupted, recovered, evidence):
    for value in (before, interrupted, recovered):
        require(value["integrity"] == "ok", "SQLite integrity failed")
    require(evidence["boundary"]["case"] == case and evidence["boundary"]["boundary_reached"], "Missing fault boundary")
    require(evidence["returncode"] == (-signal.SIGKILL if case == "parser_active" else EXIT_CODE), "Fault process did not exit as declared")
    require(evidence["original_unchanged"], "Original changed")
    require(not recovered["scratch"], "Recovered parser scratch remains")
    if case in PROCESSING:
        require(interrupted["status"]["status"] == "PROCESSING", "Crash job lost its active state")
        require(interrupted["detail"] is None and interrupted["counts"]["revisions"] == 0, "Partial candidate published")
        checkpoint = case in ("rules_extracted", "candidate_transaction")
        require(bool(interrupted["status"]["parser_checkpoint"]) == checkpoint, "Checkpoint transaction was not atomic")
        require(evidence["lease_guarded"] and evidence["stale_claim_rejected"], "Lease/fence not verified")
        require(evidence["resume"]["resumed_in_new_process"] and evidence["resume"]["parser_calls"] == (0 if checkpoint else 1), "Wrong resume path")
        require(evidence["resume"]["pid"] != evidence["boundary"]["pid"] and
                evidence["resume"]["supervisor_error"] is None, "Replacement supervisor identity/error differs")
        require(recovered["status"]["status"] == "REVIEW_READY" and recovered["status"]["job"]["attempts"] == 2, "Job did not resume once")
        require(recovered["status"]["job"]["fence"] > interrupted["status"]["job"]["fence"], "Fence did not advance")
        require([attempt["status"] for attempt in recovered["attempts"]] == ["ABANDONED", "COMPLETE"], "Attempt history lost")
        require(recovered["counts"]["revisions"] == 1 and recovered["detail"]["approval"] is None, "Recovery inherited approval or duplicated candidate")
        require(recovered["detail"]["record"]["fields"]["invoice_number"]["value"] == "AST-1001", "Retry lost invoice")
        if case == "parser_active":
            require(evidence["container_running_at_crash"] and evidence["container_removed"] and interrupted["scratch"], "Active parser/cleanup not observed")
        if case == "checkpoint_render_written":
            require(interrupted["counts"]["parser_checkpoint_pages"] == 0 and evidence["orphan_render_observed"], "Artifact-before-commit fault missing")
            require(recovered["status"]["page_image_sha256"] == evidence["boundary"]["sha256"], "Recovered render differs from written artifact")
        if checkpoint:
            require(sum(event["kind"] == "parser_checkpoint_reused" for event in recovered["history"]) == 1, "Committed OCR was not reused")
    else:
        require(evidence["baseline_export_preserved"], "Previously committed export changed")
        require(all(recovered["export_files"].get(name) == sha for name, sha in before["export_files"].items()), "Baseline export bytes differ")
        require(recovered["detail"]["pages"] == before["detail"]["pages"] and
                recovered["detail"]["extraction"] == before["detail"]["extraction"], "Review crash changed source/extraction")
        require(recovered["detail"]["record"] == interrupted["detail"]["record"], "Committed record lost on reopen")
        require(recovered["history"] == interrupted["history"] or case.endswith("transaction") or case == "csv_first_file", "Committed history lost")
        if case.startswith("review_"):
            revision = 2 if case == "review_committed" else 1
            require(interrupted["detail"]["revision"] == revision, "Edit transaction was not atomic")
            expected = EDIT_VALUE if revision == 2 else "AST-1001"
            require(interrupted["detail"]["record"]["fields"]["invoice_number"]["value"] == expected, "Edit value incorrect")
            require(bool(interrupted["detail"]["approval"]) == (revision == 1), "Edit approval invalidation incorrect")
            require(evidence["stale_edit_rejected"] == (revision == 2), "Stale edit check missing")
            require(evidence["unapproved_export_rejected"] == (revision == 2), "Unapproved export check missing")
        elif case.startswith("approval_"):
            require(bool(interrupted["detail"]["approval"]) == (case == "approval_committed"), "Approval transaction was not atomic")
            require(evidence["approval_retry_idempotent"], "Approval retry not idempotent")
            require(recovered["counts"]["approvals"] == 1, "Duplicate approval")
            if case == "approval_committed":
                require(recovered["detail"]["approval"] == interrupted["detail"]["approval"], "Committed approval was replaced")
        else:
            expected_exports = 2 if case == "csv_committed" else 1
            require(interrupted["counts"]["exports"] == expected_exports, "Partial export manifest published")
            require(evidence["partial_download_rejected"] == (case != "csv_committed"), "Uncommitted export download check missing")
            require(evidence["export_retry_idempotent"] and evidence["partial_bytes_preserved"], "Export retry changed immutable bytes")
            require(recovered["counts"]["exports"] == 2, "Export retry did not commit exactly once")
            added = set(interrupted["export_files"]) - set(before["export_files"])
            require(len(added) == (1 if case in ("csv_first_file", "json_transaction") else 2), "Wrong file-write boundary")


def run_case(case, directory, image):
    with tempfile.TemporaryDirectory(prefix="docwork-recovery-", dir=ROOT / "artifacts") as temporary:
        workbench = Path(temporary)
        store = open_store(workbench)
        with (ROOT / "samples/clean.png").open("rb") as source:
            document = store.submit(source, "fictional-recovery.png", "image/png")
        if case not in PROCESSING:
            worker.process_one(store, "fixture-preparation", image=image, honor_job_profile=True)
            require(store.status(document)["status"] == "REVIEW_READY", "Preparation failed")
        baseline = None
        if case not in PROCESSING and not case.startswith("approval_"):
            store.approve(document, 1, ACTOR)
            # JSON interruption uses an existing CSV baseline; other faults preserve JSON.
            kind = "csv" if case == "json_transaction" else "json"
            baseline = store.export(document, kind)
        before = snapshot(store, document)
        write_json(directory / "before.json", before)
        process, stream = launch(workbench, document, case, image, directory / "boundary.json", directory / "fault.log")
        evidence = {"stale_edit_rejected": False, "unapproved_export_rejected": False}
        owned_name = None
        try:
            if case == "parser_active":
                def started():
                    if process.poll() is not None:
                        raise RuntimeError("Parser fault child exited before container startup")
                    return bool(list((store.object_root / "quarantine").glob("parse-*/started")))
                wait_for(started)
                job = store.status(document)["job"]
                owned_name = f"docwork-{job['id'][:16]}-{job['fence']}"
                inspected = json.loads(subprocess.check_output(["docker", "inspect", owned_name], text=True, timeout=10))[0]
                evidence["container_running_at_crash"] = inspected["State"]["Running"]
                write_json(directory / "container-at-crash.json", {key: inspected[key] for key in ("Id", "Name", "Image", "State", "HostConfig")})
                write_json(directory / "boundary.json", {"case": case, "boundary_reached": True, "pid": process.pid,
                                                         "time_utc": datetime.now(timezone.utc).isoformat(), "method": "SIGKILL host while isolated sleep probe runs"})
                process.kill()
            evidence["returncode"] = process.wait(timeout=90)
            evidence["boundary"] = json.loads((directory / "boundary.json").read_text())
            require(evidence["returncode"] == (-signal.SIGKILL if case == "parser_active" else EXIT_CODE), "Child did not reach abrupt-exit boundary; inspect fault.log")
            store = open_store(workbench)
            interrupted = snapshot(store, document)
            write_json(directory / "interrupted.json", interrupted)
            if case in PROCESSING:
                job = interrupted["status"]["job"]
                claim = JobClaim(job["id"], document, job["fence"], "crash-probe", job["lease_until"])
                require(time.time() < job["lease_until"], "Probe missed live lease observation")
                evidence["lease_guarded"] = store.claim("premature-replacement", reclaim_expired=False) is None
                store.recover_stops()
                require(store.status(document)["job"]["fence"] == job["fence"], "Recovery removed a live lease")
                if case == "checkpoint_render_written":
                    evidence["orphan_render_observed"] = bool(store.reconcile()["orphans"])
                wait_for(lambda: time.time() > job["lease_until"], timeout=LEASE_SECONDS + 2)
                try:
                    store.check_claim(claim)
                except ReviewConflict:
                    evidence["stale_claim_rejected"] = True
                resume, resume_stream = launch(workbench, document, "resume", image, directory / "resume.json", directory / "resume.log")
                try:
                    require(resume.wait(timeout=90) == 0, "Replacement process failed; inspect resume.log")
                finally:
                    stop_session(resume)
                    resume_stream.close()
                evidence["resume"] = json.loads((directory / "resume.json").read_text())
                if owned_name:
                    removed = subprocess.run(["docker", "inspect", owned_name], capture_output=True, timeout=10)
                    evidence["container_removed"] = removed.returncode != 0 and b"no such" in removed.stderr.lower()
            else:
                evidence["baseline_export_preserved"] = baseline is None or all(
                    digest(Path(file["path"])) == file["sha256"] for file in baseline["files"])
                if case == "review_committed":
                    try:
                        store.edit(document, 1, "fields.invoice_number", "STALE", ACTOR)
                    except ReviewConflict:
                        evidence["stale_edit_rejected"] = True
                    try:
                        store.export(document, "csv")
                    except ReviewConflict:
                        evidence["unapproved_export_rejected"] = True
                elif case.startswith("approval_"):
                    first = store.approve(document, 1, ACTOR)
                    evidence["approval_retry_idempotent"] = store.approve(document, 1, ACTOR) == first
                elif not case.startswith("review_"):
                    kind = "json" if case.startswith("json_") else "csv"
                    evidence["partial_download_rejected"] = deny_export(store, document, kind, 1) if case != "csv_committed" else False
                    first = store.export(document, kind)
                    evidence["export_retry_idempotent"] = store.export(document, kind) == first
                    evidence["partial_bytes_preserved"] = all(digest(store.export_root / name) == sha
                                                               for name, sha in interrupted["export_files"].items())
                    for file in first["files"]:
                        content, _ = store.exported_file(document, 1, kind, Path(file["path"]).name)
                        require(hashlib.sha256(content).hexdigest() == file["sha256"], "Download checksum mismatch")
                        (directory / Path(file["path"]).name).write_bytes(content)
            store = open_store(workbench)
            evidence["original_unchanged"] = digest(store.object_path(document)) == digest(ROOT / "samples/clean.png")
            recovered = snapshot(store, document)
            write_json(directory / "recovered.json", recovered)
            write_json(directory / "evidence.json", evidence)
            validate_case(case, before, interrupted, recovered, evidence)
        finally:
            stop_session(process)
            stream.close()
            # Faults are confined to owned temporary stores and their exact fence.
            job = store.status(document)["job"]
            worker.remove_owned_container(f"docwork-{job['id'][:16]}-{job['fence']}")
            if owned_name:
                worker.remove_owned_container(owned_name)


def source_files():
    return sorted([*ROOT.glob("src/docwork/*.py"), *[path for path in ROOT.glob("sandbox/*") if path.is_file()],
                   ROOT / ".dockerignore", ROOT / "samples/clean.png", Path(__file__).resolve(),
                   ROOT / "tests/test_stage_recovery.py"])


def verify_saved(directory):
    require(directory.is_dir() and not directory.is_symlink(), "Unsafe evidence directory")
    report = json.loads((directory / "report.json").read_text())
    require(report["report_version"] == "stage-recovery-v1" and report["status"] == "passed", "Incomplete recovery run")
    require(report["inputs_unchanged"] and report["lease_seconds"] == LEASE_SECONDS, "Source or lease protocol differs")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", report["parser_image_id"]) is not None, "Invalid immutable parser identity")
    require([check["id"] for check in report["checks"]] == list(CHECKS), "Incomplete/altered recovery schedule")
    require(all(check["status"] == "passed" for check in report["checks"]), "Failed recovery check")
    require(all(not path.is_symlink() for path in directory.rglob("*")), "Symlink evidence entry")
    actual = {str(path.relative_to(directory)): digest(path) for path in directory.rglob("*")
              if path.is_file() and path != directory / "report.json"}
    require(actual == report["artifacts"], "Recovery artifact inventory/checksum differs")
    saved = json.loads((directory / "source_snapshot.json").read_text())
    require({"src/docwork/intake.py", "src/docwork/review.py", "src/docwork/worker.py",
             "src/docwork/lifecycle.py", "src/docwork/supervisor.py", "scripts/verify_stage_recovery.py",
             "tests/test_stage_recovery.py", "sandbox/Dockerfile", "sandbox/parser-build-lock.json", ".dockerignore"} <= set(saved),
            "Source snapshot omits recovery/runtime inputs")
    require({name: hashlib.sha256(text.encode()).hexdigest() for name, text in saved.items()} ==
            {name: sha for name, sha in report["source_sha256"].items() if name != "samples/clean.png"}, "Source snapshot differs")
    require(report["source_sha256"]["samples/clean.png"] == digest(directory / "input.png"), "Input differs")
    for case in CHECKS:
        folder = directory / case
        values = [json.loads((folder / name).read_text()) for name in
                  ("before.json", "interrupted.json", "recovered.json", "evidence.json")]
        validate_case(case, *values)
        recovered = values[2]
        for name in ("invoice.json", "header.csv", "line-items.csv"):
            path = folder / name
            if path.exists():
                matches = [sha for relative, sha in recovered["export_files"].items() if Path(relative).name == name]
                require(matches == [digest(path)], "Saved export differs from recovered bytes")
    return {"status": "verified", "checks": len(CHECKS), "parser_image_id": report["parser_image_id"],
            "scope": report["scope"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--child", choices=(*CHECKS, "resume"), help=argparse.SUPPRESS)
    parser.add_argument("--workbench", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--document", help=argparse.SUPPRESS)
    parser.add_argument("--image", default=worker.PARSER_IMAGE, help=argparse.SUPPRESS)
    parser.add_argument("--marker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        child(args.workbench, args.document, args.child, args.image, args.marker)
        return 0
    if args.verify:
        print(json.dumps(verify_saved(args.verify.resolve())))
        return 0
    if not args.output_dir:
        parser.error("Choose --output-dir or --verify")
    directory = args.output_dir.resolve()
    if directory.exists():
        parser.error("Choose a new output directory")
    directory.mkdir(parents=True)
    paths = source_files()
    sources = {str(path.relative_to(ROOT)): digest(path) for path in paths}
    write_json(directory / "source_snapshot.json", {str(path.relative_to(ROOT)): path.read_text()
                                                  for path in paths if path.name != "clean.png"})
    (directory / "input.png").write_bytes((ROOT / "samples/clean.png").read_bytes())
    report = {"report_version": "stage-recovery-v1", "status": "failed", "checks": [],
              "started_at_utc": datetime.now(timezone.utc).isoformat(), "source_sha256": sources,
              "lease_seconds": LEASE_SECONDS,
              "scope": "Twelve subprocess crash boundaries on a self-authored fictional PNG, real Docker OCR/rules and SQLite WAL. "
                       "Active-parser fault substitutes a bounded sleep entrypoint under production container policy. "
                       "Other faults use abrupt os._exit after actual writes/extraction or inside transactions; replacement process runs the serial supervisor. "
                       "Eight-second leases with 0.25-second heartbeats; no machine power loss, real model restart, human review, scan quality or timing claim."}
    try:
        image = worker._docker_image_id(args.image)
        report["parser_image_id"] = image
        for case in CHECKS:
            folder = directory / case
            folder.mkdir()
            started = time.monotonic()
            try:
                run_case(case, folder, image)
                check = {"id": case, "status": "passed"}
            except (Exception, KeyboardInterrupt) as error:
                check = {"id": case, "status": "failed", "error": f"{type(error).__name__}: {error}"}
            check["seconds"] = round(time.monotonic() - started, 3)
            report["checks"].append(check)
            print(json.dumps(check), flush=True)
            if check["status"] != "passed":
                break
        report["inputs_unchanged"] = (sources == {str(path.relative_to(ROOT)): digest(path) for path in paths}
                                      and worker._docker_image_id(args.image) == image)
        if len(report["checks"]) == len(CHECKS) and all(check["status"] == "passed" for check in report["checks"]) and report["inputs_unchanged"]:
            report["status"] = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report["failure"] = f"{type(error).__name__}: {error}"
    report["artifacts"] = {str(path.relative_to(directory)): digest(path) for path in directory.rglob("*") if path.is_file()}
    write_json(directory / "report.json", report)
    print(json.dumps({"status": report["status"], "report": str(directory / "report.json")}))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
