"""Prepare source-inspected post-pilot drafts without changing human results.

These two declared development-case corrections are reproducible maintenance,
not another human trial or extractor evaluation. New revisions require approval
in the normal workbench before export. The archived study remains unchanged.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path

from docwork.intake import IntakeStore
from docwork.model_runtime import file_hash
from docwork.pilot_bundle import report_pilot
from docwork.release_scoring import score_invoice, summarize_invoices
from docwork.review import ReviewConflict

SPEC = importlib.util.spec_from_file_location("pilot_archive_helper", Path(__file__).with_name("archive_review_pilot.py"))
ARCHIVE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARCHIVE)

ARCHIVE_PATH = "evals/author-review-pilot-2026-10-03"
ACTOR = "codex_post_pilot_audit"
CORRECTIONS = (
    ("inv-f03-27", "line_items.row-004.description", "Illustration set 3}", "Illustration set",
     "datasets/invoices-v1/assets/inv-f03-27.png"),
    ("inv-f06-04", "fields.invoice_number", "FO06-004", "F06-004",
     "datasets/invoices-v1/assets/inv-f06-04-page-1.png"),
)
SOURCES = ("scripts/correct_pilot_records.py", "scripts/archive_review_pilot.py",
           "src/docwork/review.py", "src/docwork/intake.py", "src/docwork/validation.py",
           "src/docwork/release_scoring.py", "src/docwork/pilot_bundle.py")


def prepare(root: Path, session: Path) -> dict:
    archive = root / ARCHIVE_PATH
    ARCHIVE.verify_archive(root, archive)
    before = ARCHIVE.inventory(archive)
    ARCHIVE.restore_session(archive, session)
    original = json.loads((archive / "report.json").read_text())
    protocol = json.loads((archive / "protocol.json").read_text())
    documents = {d["corpus_id"]: d for d in protocol["documents"]}
    store = IntakeStore(session / "review.sqlite", session / "objects")
    corrections = []
    for corpus_id, path, old_value, value, source in CORRECTIONS:
        document_id = documents[corpus_id]["document_id"]
        old = store.get(document_id)
        parts = path.split(".")
        field = (old["record"]["fields"][parts[1]] if parts[0] == "fields" else
                 next(row for row in old["record"]["line_items"] if row["row_id"] == parts[1])[parts[2]])
        if field["value"] != old_value or not old["approval"]:
            raise ValueError("Original approved pilot value differs from the declared correction")
        revision = store.edit(document_id, old["revision"], path, value, ACTOR)
        draft = store.get(document_id)
        if draft["approval"] is not None:
            raise ValueError("A corrected draft must require a new approval")
        try:
            store.export(document_id, "json")
        except ReviewConflict:
            pass
        else:
            raise ValueError("An unapproved correction must not be exportable")
        corrections.append({"corpus_id": corpus_id, "document_id": document_id,
                            "path": path, "before": old_value, "after": value,
                            "source": source, "source_sha256": file_hash(root / source),
                            "previous_revision": old["revision"], "revision": revision,
                            "previous_record_hash": old["record_hash"], "record_hash": draft["record_hash"],
                            "status": "draft_requires_human_approval", "approval": None, "record": draft["record"]})
    # Completed trials score their original bound revisions even after edits.
    if report_pilot(root, session) != original or ARCHIVE.inventory(archive) != before:
        raise ValueError("Post-pilot corrections changed the original study")
    gold = {d["id"]: d for d in json.loads((root / protocol["manifest"]).read_text())["documents"]}
    scores = [score_invoice(gold[corpus_id], {"record": store.get(d["document_id"])["record"]})
              for corpus_id, d in documents.items()]
    return {"report_version": "post-pilot-corrections-v1", "status": "drafts_require_human_approval",
            "actor": ACTOR, "archive": ARCHIVE_PATH,
            "archive_manifest_sha256": file_hash(archive / "manifest.json"),
            "original_report_sha256": file_hash(archive / "report.json"),
            "source_sha256": {name: file_hash(root / name) for name in SOURCES},
            "corrections": corrections, "draft_quality": summarize_invoices(scores),
            "original_pilot_unchanged": True,
            "scope": "Two source-inspected corrections by Codex on declared development cases. "
                     "Draft quality is a post-pilot audit using known labels, not human review quality, "
                     "held-out extraction quality or productivity. Original study timings, scores, "
                     "suggestions, approvals and exports remain unchanged. Corrected revisions are "
                     "unapproved; inspect and approve them in the normal workbench before export."}


def verify(root: Path, report: Path) -> dict:
    supplied = json.loads(report.read_text())
    with tempfile.TemporaryDirectory(prefix="docwork-corrections-") as temporary:
        reproduced = prepare(root, Path(temporary) / "session")
    if supplied != reproduced:
        raise ValueError("Correction report does not reproduce from the unchanged author archive")
    return {"status": "verified", "corrections": len(supplied["corrections"]),
            "original_pilot_unchanged": True, "corrected_revisions_approved": False}


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("prepare")
    create.add_argument("--output-dir", type=Path, required=True)
    create.add_argument("--report", type=Path, required=True)
    audit = actions.add_parser("verify")
    audit.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "verify":
            result = verify(root, args.report)
        else:
            session, report = args.output_dir.absolute(), args.report.absolute()
            for path, allowed in ((session, (root / "artifacts",)), (report, (root / "evals", root / "artifacts"))):
                if (path.exists() or path.is_symlink() or ".." in path.parts or
                        any(parent.is_symlink() for parent in path.parents) or
                        not any(path.is_relative_to(parent) for parent in allowed)):
                    raise ValueError("Use new paths under artifacts (session) and evals/artifacts (report)")
            if report.is_relative_to(session):
                raise ValueError("Correction report must be outside the working session")
            result = prepare(root, session)
            report.parent.mkdir(parents=True, exist_ok=True)
            with report.open("x") as stream:
                stream.write(json.dumps(result, indent=2) + "\n")
            result = {"status": result["status"], "session": str(args.output_dir), "report": str(args.report)}
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError, ReviewConflict) as exc:
        parser.exit(2, f"Post-pilot correction failed: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
