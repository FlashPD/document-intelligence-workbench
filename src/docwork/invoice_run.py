"""Reproducible OCR/rules evaluation on the trusted invoice corpus previews."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .baseline import BASELINE_VERSION, extract_invoice, extract_invoice_pages
from .spatial_baseline import (BASELINE_VERSION as SPATIAL_VERSION,
                               extract_invoice as extract_spatial_invoice,
                               extract_invoice_pages as extract_spatial_pages)
from .ocr import tesseract_page
from .release_evaluation import score_saved_invoice_run, verify_invoice_manifest
from .validation import validate_invoice
from .review import page_from_dict, record_from_dict

RUN_VERSION = "invoice-preview-ocr-rules-v4"
V3_PIPELINE_FILES = ("baseline.py", "contracts.py", "ocr.py", "spatial_lines.py", "validation.py", "invoice_run.py")
PIPELINE_FILES = (*V3_PIPELINE_FILES, "spatial_baseline.py")
EXTRACTORS = {"ocr_rules": (BASELINE_VERSION, extract_invoice_pages, extract_invoice),
              "spatial_rules": (SPATIAL_VERSION, extract_spatial_pages, extract_spatial_invoice)}


def _pipeline_snapshot() -> dict[str, str]:
    return {name: Path(__file__).with_name(name).read_text() for name in PIPELINE_FILES}


def _snapshot_hash(snapshot: dict) -> str:
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def _write_new(path: Path, value: dict) -> None:
    encoded = (json.dumps(value, indent=2) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        if temporary.is_symlink() or not temporary.is_file():
            raise ValueError(f"Unsafe temporary output: {temporary}")
        temporary.unlink()  # A prior interrupted write is never a prediction.
    with temporary.open("xb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _preview_paths(document: dict, root: Path) -> tuple[Path, ...]:
    assets = document["assets"]
    count = len(document["pages"])
    if count == 1 and len(assets) == 1 and assets[0]["path"].endswith(".png"):
        previews = assets
    elif (count > 1 and len(assets) == count + 1
          and assets[0]["path"].endswith(".pdf")
          and all(asset["path"].endswith(".png") for asset in assets[1:])):
        previews = assets[1:]
    else:
        raise ValueError(f"No complete PNG preview set: {document['id']}")
    return tuple(root / asset["path"] for asset in previews)


def _extract(document: dict, root: Path, ocr_psm: int, pipeline_sha256: str, extractor: str) -> dict:
    started = time.perf_counter()
    ocr_seconds = 0.0
    try:
        paths = _preview_paths(document, root)
        pages = []
        for number, path in enumerate(paths, start=1):
            page_start = time.perf_counter()
            pages.append(tesseract_page(path, page_number=number,
                                        page_segmentation_mode=ocr_psm))
            ocr_seconds += time.perf_counter() - page_start
        _, extract, header_extract = EXTRACTORS[extractor]
        record = extract(tuple(pages))
        issues = validate_invoice(record, tuple(pages), header_extractor=header_extract)
        result = {"record": record.to_dict(), "issue_codes": [issue.code for issue in issues],
                  "pages": [asdict(page) for page in pages]}
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        result = {"record": None, "failure_type": type(exc).__name__}
    result.update({
        "source_sha256": document["source_sha256"],
        "run_version": RUN_VERSION,
        "baseline_version": EXTRACTORS[extractor][0],
        "extractor": extractor,
        "pipeline_sha256": pipeline_sha256,
        "ocr_psm": ocr_psm,
        "input_mode": "verified_corpus_png_previews",
        "runtime_seconds": {"ocr": round(ocr_seconds, 3),
                            "total": round(time.perf_counter() - started, 3)},
    })
    return result


def run_invoice_baseline(manifest_path: Path, output_dir: Path, *, resume: bool = False,
                         ocr_psm: int = 1, split: str = "development",
                         extractor: str = "ocr_rules") -> dict:
    """Score development or calibration previews; leave held-out test sealed."""
    manifest_path = manifest_path.resolve(strict=True)
    root = manifest_path.parent
    output_dir = output_dir.resolve()
    if output_dir.is_relative_to(root):
        raise ValueError("Run output must be outside the corpus directory")
    if ocr_psm not in (1, 3):
        raise ValueError("OCR page segmentation mode must be 1 or 3")
    if split not in ("development", "calibration"):
        raise ValueError("Preview runs may score development or calibration only")
    if extractor not in EXTRACTORS:
        raise ValueError("Extractor must be ocr_rules or spatial_rules")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    verify_invoice_manifest(manifest, manifest_path)
    scheduled = [doc for doc in manifest["documents"] if doc["split"] == split]
    if not scheduled:
        raise ValueError(f"Manifest has no {split} documents")
    version = subprocess.run(["tesseract", "--version"], capture_output=True, text=True,
                             timeout=10, check=True).stdout.splitlines()[0]
    snapshot = _pipeline_snapshot()
    pipeline_hash = _snapshot_hash(snapshot)
    identity = {
        "run_version": RUN_VERSION,
        "dataset_id": manifest["dataset_id"],
        "split": split,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "baseline_version": EXTRACTORS[extractor][0],
        "extractor": extractor,
        "pipeline_sha256": pipeline_hash,
        "ocr_psm": ocr_psm,
        "tesseract_version": version,
        "python_version": platform.python_version(),
        "input_mode": "verified_corpus_png_previews",
        "documents_scheduled": len(scheduled),
    }
    if resume:
        if not output_dir.is_dir():
            raise ValueError("Resume directory does not exist")
        if json.loads((output_dir / "run.json").read_text()) != identity:
            raise ValueError("Run identity changed; resume requires the original corpus and runtime")
        if json.loads((output_dir / "source_snapshot.json").read_text()) != snapshot:
            raise ValueError("Pipeline source changed; resume requires the original implementation")
    else:
        output_dir.mkdir(parents=True, exist_ok=False)
        (output_dir / "predictions").mkdir()
        _write_new(output_dir / "run.json", identity)
        _write_new(output_dir / "source_snapshot.json", snapshot)
    report_path = output_dir / "report.json"
    if resume and report_path.exists():
        verify_invoice_run(manifest_path, output_dir)
        return json.loads(report_path.read_text())
    predictions_dir = output_dir / "predictions"
    expected = {f"{doc['id']}.json" for doc in scheduled}
    temporary_names = {f".{name}.tmp" for name in expected}
    if {path.name for path in predictions_dir.iterdir()} - expected - temporary_names:
        raise ValueError("Prediction directory has unexpected files")
    for name in temporary_names:
        temporary = predictions_dir / name
        if temporary.exists() or temporary.is_symlink():
            if temporary.is_symlink() or not temporary.is_file():
                raise ValueError(f"Unsafe temporary output: {temporary}")
            temporary.unlink()
    for document in scheduled:
        path = predictions_dir / f"{document['id']}.json"
        if path.exists():
            if not resume or path.is_symlink() or not path.is_file():
                raise ValueError(f"Existing prediction cannot be reused: {document['id']}")
            saved = json.loads(path.read_text())
            if (saved.get("source_sha256") != document["source_sha256"]
                    or saved.get("run_version") != RUN_VERSION
                    or saved.get("baseline_version") != EXTRACTORS[extractor][0]
                    or saved.get("extractor") != extractor
                    or saved.get("pipeline_sha256") != pipeline_hash
                    or saved.get("ocr_psm") != ocr_psm):
                raise ValueError(f"Existing prediction does not match run: {document['id']}")
            continue
        _write_new(path, _extract(document, root, ocr_psm, pipeline_hash, extractor))
    if _pipeline_snapshot() != snapshot:
        raise ValueError("Pipeline source changed during run; use a fresh output directory")
    report = score_saved_invoice_run(manifest_path, predictions_dir, split)
    report["run_identity"] = identity
    timing = [json.loads((predictions_dir / f"{doc['id']}.json").read_text())["runtime_seconds"]
              for doc in scheduled]
    report["runtime_seconds"] = {
        "ocr_sum": round(sum(item["ocr"] for item in timing), 3),
        "total_sum": round(sum(item["total"] for item in timing), 3),
    }
    report["issue_codes"] = dict(Counter(
        code for doc in scheduled
        for code in json.loads((predictions_dir / f"{doc['id']}.json").read_text()).get("issue_codes", [])
    ))
    _write_new(report_path, report)
    return report


def verify_invoice_run(manifest_path: Path, run_dir: Path) -> dict:
    """Recompute saved scores and check that the report still matches its predictions."""
    run_dir = run_dir.resolve(strict=True)
    identity = json.loads((run_dir / "run.json").read_text())
    recorded = json.loads((run_dir / "report.json").read_text())
    manifest_bytes = manifest_path.resolve(strict=True).read_bytes()
    if (identity.get("manifest_sha256") != hashlib.sha256(manifest_bytes).hexdigest()
            or identity.get("input_mode") != "verified_corpus_png_previews"
            or identity.get("split") not in ("development", "calibration")
            or recorded.get("run_identity") != identity):
        raise ValueError("Run identity or corpus hash differs from the saved report")
    predictions_dir = run_dir / "predictions"
    snapshot_run = identity.get("run_version") in ("invoice-preview-ocr-rules-v3", RUN_VERSION)
    if snapshot_run:
        snapshot = json.loads((run_dir / "source_snapshot.json").read_text())
        required = V3_PIPELINE_FILES if identity["run_version"] == "invoice-preview-ocr-rules-v3" else PIPELINE_FILES
        if set(snapshot) != set(required) or _snapshot_hash(snapshot) != identity.get("pipeline_sha256"):
            raise ValueError("Saved pipeline source snapshot differs from run identity")
    predictions = []
    for path in predictions_dir.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Prediction directory contains a non-regular file")
        prediction = json.loads(path.read_text())
        if (prediction.get("run_version") != identity.get("run_version")
                or prediction.get("baseline_version") != identity.get("baseline_version")
                or prediction.get("ocr_psm") != identity.get("ocr_psm")
                or prediction.get("input_mode") != identity.get("input_mode")):
            raise ValueError(f"Prediction run identity differs: {path.name}")
        if identity.get("run_version") == RUN_VERSION and (identity.get("extractor") not in EXTRACTORS
                or prediction.get("extractor") != identity["extractor"]):
            raise ValueError(f"Prediction extractor identity differs: {path.name}")
        if snapshot_run:
            if prediction.get("pipeline_sha256") != identity.get("pipeline_sha256"):
                raise ValueError(f"Prediction pipeline identity differs: {path.name}")
            if prediction.get("record") is not None:
                pages = tuple(page_from_dict(page) for page in prediction["pages"])
                if not pages or [page.number for page in pages] != list(range(1, len(pages) + 1)):
                    raise ValueError("Prediction pages are missing or out of sequence")
                record = record_from_dict(prediction["record"])
                known_ids = {span.id for page in pages for span in page.spans}
                fields = list(record.fields.values()) + [getattr(row, name) for row in record.line_items
                          for name in ("description", "quantity", "unit_price", "line_total", "tax")]
                if any(set(field.evidence_ids) - known_ids for field in fields):
                    raise ValueError("Prediction references unknown OCR evidence")
        predictions.append(prediction)
    rescored = score_saved_invoice_run(manifest_path, predictions_dir, identity["split"])
    if (rescored["status"] != "scored" or
            identity.get("documents_scheduled") != len(rescored["documents"])):
        raise ValueError("Saved invoice run is incomplete")
    for key in ("report_version", "dataset_id", "split", "manifest_sha256",
                "implementation_sha256", "status", "missing_prediction_ids", "summary", "documents"):
        if recorded.get(key) != rescored[key]:
            raise ValueError(f"Saved invoice report differs from predictions: {key}")
    timing = [prediction["runtime_seconds"] for prediction in predictions]
    runtime = {"ocr_sum": round(sum(item["ocr"] for item in timing), 3),
               "total_sum": round(sum(item["total"] for item in timing), 3)}
    issues = dict(Counter(code for prediction in predictions
                          for code in prediction.get("issue_codes", [])))
    if recorded.get("runtime_seconds") != runtime or recorded.get("issue_codes") != issues:
        raise ValueError("Saved invoice runtime or issue counts differ from predictions")
    return {"status": "verified", "documents": len(rescored["documents"]),
            "manifest_sha256": rescored["manifest_sha256"]}
