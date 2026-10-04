"""Prepare and audit a declared assisted-review pilot without running inference."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import statistics
import tempfile
from pathlib import Path

from .baseline import BASELINE_VERSION
from .intake import IntakeStore
from .invoice_run import _preview_paths, verify_invoice_run
from .model_runtime import file_hash
from .release_scoring import score_invoice, summarize_invoices
from .review import _atomic_write, _hash, _now, page_from_dict, record_from_dict
from .review_pilot import PILOT_VERSION, durations

DOCUMENTS = ("inv-f02-02", "inv-f01-12", "inv-f03-27", "inv-f04-29", "inv-f05-30", "inv-f06-04")
SOURCES = ("src/docwork/review_pilot.py", "src/docwork/pilot_bundle.py", "src/docwork/review.py",
           "src/docwork/intake.py", "src/docwork/web.py", "scripts/review_pilot.py",
           "ui/app.js", "ui/pilot.js", "ui/index.html", "ui/style.css")


def write(path: Path, value):
    _atomic_write(path, (json.dumps(value, indent=2) + "\n").encode())


def prepare_pilot(root: Path, manifest: Path, baseline: Path, output: Path, *, document_ids=DOCUMENTS) -> dict:
    root, manifest, baseline, output = root.resolve(), manifest.resolve(strict=True), baseline.resolve(strict=True), output.resolve()
    if output.exists() or output.is_relative_to(manifest.parent) or output.is_relative_to(baseline):
        raise ValueError("Pilot needs a new directory outside the corpus and baseline evidence")
    verify_invoice_run(manifest, baseline)
    identity = json.loads((baseline / "run.json").read_text())
    if identity["baseline_version"] != BASELINE_VERSION or identity.get("extractor") != "ocr_rules":
        raise ValueError("Pilot requires the frozen default OCR/rules run")
    corpus = json.loads(manifest.read_text())
    selected = {doc["id"]: doc for doc in corpus["documents"] if doc["id"] in document_ids}
    if len(set(document_ids)) != len(document_ids) or set(selected) != set(document_ids) or not document_ids:
        raise ValueError("Pilot document selection is missing or duplicated")
    if any(doc["split"] != "development" for doc in selected.values()):
        raise ValueError("Author pilot selection must use declared development documents")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".review-pilot-", dir=output.parent))
    try:
        store = IntakeStore(temporary / "review.sqlite", temporary / "objects")
        documents = []
        for ordinal, corpus_id in enumerate(document_ids, 1):
            doc = selected[corpus_id]
            prediction_path = baseline / "predictions" / f"{corpus_id}.json"
            prediction = json.loads(prediction_path.read_text())
            if prediction.get("record") is None or not prediction.get("pages"):
                raise ValueError("Pilot requires a saved candidate and canonical OCR pages")
            original = manifest.parent / doc["assets"][0]["path"]
            if file_hash(original) != doc["source_sha256"]:
                raise ValueError("Pilot original differs from its corpus source")
            mime = "application/pdf" if original.suffix == ".pdf" else "image/png"
            document_id = store.submit(io.BytesIO(original.read_bytes()), original.name, mime)
            claim = store.claim("recorded-pilot-setup")
            pages = tuple(page_from_dict(p) for p in prediction["pages"])
            previews = _preview_paths(doc, manifest.parent)
            images = tuple(p.read_bytes() for p in previews)
            if len(pages) != len(images) or any((p.width_px, p.height_px) != (int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")) for p, b in zip(pages, images)):
                raise ValueError("Pilot OCR coordinates differ from preview dimensions")
            store.complete(claim, pages, record_from_dict(prediction["record"]), images, profile="replay_ocr_rules")
            detail = store.get(document_id)
            documents.append({"ordinal": ordinal, "corpus_id": corpus_id, "document_id": document_id,
                              "family_group": doc["family_group"], "treatment": doc["treatment"],
                              "pages": len(pages), "initial_record_hash": detail["record_hash"],
                              "source_sha256": doc["source_sha256"], "prediction_sha256": file_hash(prediction_path)})
        snapshot = {name: (root / name).read_text() for name in SOURCES}
        write(temporary / "source_snapshot.json", snapshot)
        protocol = {"pilot_version": PILOT_VERSION, "created_at": _now(), "documents": documents,
                    "idle_cutoff_seconds": 60, "mode": "assisted_only", "participant": "project_author",
                    "manifest": str(manifest.relative_to(root)), "manifest_sha256": file_hash(manifest),
                    "baseline_report": str((baseline / "report.json").relative_to(root)),
                    "baseline_report_sha256": file_hash(baseline / "report.json"),
                    "source_sha256": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()},
                    "selection": "One declared document per development family; clean, conflict, contrast, skew, rotation, multi-page. "
                                 "Fixed selection by corpus ID; not a random sample and not screened for successful review.",
                    "scope": "Author assisted-review pilot of recorded OCR/rules suggestions. "
                             "Processing excluded. No manual-entry baseline, independent workforce study, or productivity claim."}
        write(temporary / "protocol.json", protocol)
        write(temporary / "timing.json", {"pilot_version": PILOT_VERSION,
                                          "protocol_sha256": file_hash(temporary / "protocol.json"), "trials": []})
        # The database contains no exports yet and all object references are
        # relative. Publish only the complete prepared directory.
        temporary.rename(output)
        return protocol
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def verify_pilot_setup(root: Path, directory: Path, *, require_current_source: bool = False) -> dict:
    protocol = json.loads((directory / "protocol.json").read_text())
    snapshot = json.loads((directory / "source_snapshot.json").read_text())
    if protocol["pilot_version"] != PILOT_VERSION or set(snapshot) != set(SOURCES):
        raise ValueError("Pilot source inventory differs")
    if {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()} != protocol["source_sha256"]:
        raise ValueError("Pilot source snapshot differs from protocol")
    if require_current_source and any((root / name).read_text() != text for name, text in snapshot.items()):
        raise ValueError("Pilot source changed; prepare a fresh session")
    for name, key in (("manifest", "manifest_sha256"), ("baseline_report", "baseline_report_sha256")):
        path = (root / protocol[name]).resolve(strict=True)
        if not path.is_relative_to(root.resolve()) or file_hash(path) != protocol[key]:
            raise ValueError("Pilot corpus or extraction report changed")
    return protocol


def report_pilot(root: Path, directory: Path) -> dict:
    directory = directory.resolve(strict=True)
    protocol = verify_pilot_setup(root, directory)
    timing = json.loads((directory / "timing.json").read_text())
    if timing["protocol_sha256"] != file_hash(directory / "protocol.json"):
        raise ValueError("Pilot timing belongs to another protocol")
    corpus = json.loads((root / protocol["manifest"]).read_text())
    gold = {d["id"]: d for d in corpus["documents"]}
    store = IntakeStore(directory / "review.sqlite", directory / "objects")
    trials = {t["document_id"]: t for t in timing["trials"]}
    if len(trials) != len(timing["trials"]) or set(trials) - {d["document_id"] for d in protocol["documents"]}:
        raise ValueError("Pilot has repeated or undeclared trials")
    documents, scores = [], []
    for doc in protocol["documents"]:
        initial = store.get(doc["document_id"], 1)
        if (_hash(initial["record"]) != doc["initial_record_hash"] or initial["record_hash"] != doc["initial_record_hash"] or
                initial["source_sha256"] != doc["source_sha256"]):
            raise ValueError("Pilot initial candidate changed")
        trial = trials.get(doc["document_id"])
        row = {"corpus_id": doc["corpus_id"], "document_id": doc["document_id"],
               "status": trial["status"] if trial else "NOT_STARTED"}
        if trial:
            row.update(durations(trial["events"], protocol["idle_cutoff_seconds"]))
        if trial and trial["status"] == "COMPLETE":
            final = trial["final"]
            detail = store.get(doc["document_id"], final["revision"])
            exported, _ = store.exported_file(doc["document_id"], final["revision"], "json", "invoice.json")
            if (_hash(detail["record"]) != final["record_hash"] or detail["record_hash"] != final["record_hash"] or not detail["approval"] or
                    detail["approval"]["approval_hash"] != final["approval_hash"] or
                    detail["approval"]["actor"] != trial["actor"] or hashlib.sha256(exported).hexdigest() != final["export_sha256"]):
                raise ValueError("Pilot completion differs from the approved export")
            row.update(final)
            scores.append(score_invoice(gold[doc["corpus_id"]], {"record": detail["record"]}))
        documents.append(row)
    complete = [d for d in documents if d["status"] == "COMPLETE"]
    return {"report_version": PILOT_VERSION, "status": "complete" if len(complete) == len(documents) else "incomplete",
            "scheduled": len(documents), "completed": len(complete), "documents": documents,
            "timing": {"completed_only_active_median_seconds": statistics.median(d["active_seconds"] for d in complete) if complete else None,
                       "completed_only_elapsed_median_seconds": statistics.median(d["elapsed_seconds"] for d in complete) if complete else None},
            "completed_only_quality": summarize_invoices(scores) if scores else None,
            "protocol_sha256": file_hash(directory / "protocol.json"), "timing_sha256": file_hash(directory / "timing.json"),
            "scope": protocol["scope"], "limitations": "Author familiarity, synthetic development layouts, fixed sample, "
                "unauthenticated audit labels and browser activity heuristics. Reading beyond the 60-second idle cutoff "
                "can be undercounted; server receipt times include network overhead. Interruptions remain incomplete. "
                "Local hash checks are integrity checks, not signed evidence of human participation."}
