"""Prepare fictional, recorded development candidates for an offline demo."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
from pathlib import Path

from .baseline import BASELINE_VERSION
from .intake import IntakeStore
from .invoice_run import _preview_paths, verify_invoice_run
from .model_runtime import file_hash
from .review import _atomic_write, page_from_dict, record_from_dict

CASES = (("inv-f02-02", "Clean invoice"),
         ("inv-f01-12", "Printed total conflict"),
         ("inv-f03-27", "Low-contrast scan"),
         ("inv-f06-04", "Two-page invoice"))


def prepare_replay(root: Path, output: Path) -> dict:
    """Verify saved evidence and publish a new workbench; never run extraction."""
    root, output = root.resolve(strict=True), output.resolve()
    if output.exists():
        raise ValueError("Replay needs a new output directory; existing reviews are preserved")
    manifest = root / "datasets/invoices-v1/manifest.json"
    baseline = root / "evals/invoice-freeze-2026-10-03/development"
    if output.is_relative_to(manifest.parent) or output.is_relative_to(baseline):
        raise ValueError("Replay output must be outside corpus and baseline evidence")
    verify_invoice_run(manifest, baseline)
    identity = json.loads((baseline / "run.json").read_text())
    if (identity["split"] != "development" or identity["baseline_version"] != BASELINE_VERSION
            or identity.get("extractor") != "ocr_rules"):
        raise ValueError("Replay requires recorded default development OCR/rules evidence")
    corpus = {doc["id"]: doc for doc in json.loads(manifest.read_text())["documents"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".demo-replay-", dir=output.parent))
    try:
        store = IntakeStore(temporary / "review.sqlite", temporary / "objects")
        cases = []
        for corpus_id, label in CASES:
            doc = corpus[corpus_id]
            if doc["split"] != "development":
                raise ValueError("Replay cases must belong to the development split")
            prediction_path = baseline / "predictions" / f"{corpus_id}.json"
            prediction = json.loads(prediction_path.read_text())
            if prediction.get("record") is None or not prediction.get("pages"):
                raise ValueError("Replay case needs recorded candidate and source pages")
            # Only source bytes, OCR, and candidates enter the workbench. Gold
            # fields and rows remain in the evaluation corpus.
            assets = {asset["path"]: asset["sha256"] for asset in doc["assets"]}
            original = manifest.parent / doc["assets"][0]["path"]
            previews = _preview_paths(doc, manifest.parent)
            for path in (original, *previews):
                if file_hash(path) != assets[str(path.relative_to(manifest.parent))]:
                    raise ValueError("Replay asset differs from its recorded hash")
            if file_hash(original) != doc["source_sha256"]:
                raise ValueError("Replay source differs from its recorded original")
            pages = tuple(page_from_dict(page) for page in prediction["pages"])
            images = tuple(path.read_bytes() for path in previews)
            if len(pages) != len(images) or any(
                (page.width_px, page.height_px) != (int.from_bytes(image[16:20], "big"), int.from_bytes(image[20:24], "big"))
                for page, image in zip(pages, images)
            ):
                raise ValueError("Recorded OCR and page image dimensions differ")
            mime = "application/pdf" if original.suffix == ".pdf" else "image/png"
            document_id = store.submit(io.BytesIO(original.read_bytes()), original.name, mime)
            claim = store.claim("recorded-demo-setup")
            store.complete(claim, pages, record_from_dict(prediction["record"]), images,
                           profile="replay_ocr_rules")
            cases.append({"corpus_id": corpus_id, "label": label, "document_id": document_id,
                          "source_sha256": doc["source_sha256"],
                          "prediction_sha256": file_hash(prediction_path)})
        replay = {"version": "portfolio-demo-replay-v1", "cases": cases,
                  "manifest_sha256": file_hash(manifest),
                  "baseline_report_sha256": file_hash(baseline / "report.json"),
                  "scope": "Fictional development invoices with recorded OCR/rules candidates. "
                           "No live OCR, parsing, inference, or human timing measurement."}
        _atomic_write(temporary / "replay.json", (json.dumps(replay, indent=2) + "\n").encode())
        temporary.rename(output)
        return replay
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
