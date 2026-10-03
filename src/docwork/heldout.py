"""Explicitly frozen, experimental held-out invoice baseline evaluation.

Development/calibration preview commands remain sealed against test data. This
separate runner requires a hash-bound freeze selected from verified diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import random
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .baseline import BASELINE_VERSION
from .invoice_run import PIPELINE_FILES, _extract, _write_new, verify_invoice_run
from .model_runtime import file_hash
from .release_evaluation import score_saved_invoice_run, verify_invoice_manifest
from .release_scoring import summarize_invoices
from .review import page_from_dict, record_from_dict

FREEZE_VERSION = "experimental-invoice-test-freeze-v1"
RUN_VERSION = "frozen-invoice-test-baseline-v1"
SOURCE_FILES = (*PIPELINE_FILES, "heldout.py", "release_evaluation.py", "release_scoring.py")


def source_hashes() -> dict[str, str]:
    return {name: file_hash(Path(__file__).with_name(name)) for name in SOURCE_FILES}


def ocr_identity() -> dict:
    executable = shutil.which("tesseract")
    if executable is None:
        raise ValueError("Tesseract is required for freezing and running OCR")
    version = subprocess.check_output([executable, "--version"], text=True, timeout=10).splitlines()[0]
    listing = subprocess.check_output([executable, "--list-langs"], text=True, timeout=10)
    match = re.search(r'List of available languages in "([^"]+)"', listing)
    if match is None:
        raise ValueError("Cannot identify the Tesseract language asset directory")
    data = Path(match.group(1))
    return {"version": version, "executable_sha256": file_hash(Path(executable)),
            "language_assets": {name: file_hash(data / f"{name}.traineddata") for name in ("eng", "osd")}}


def _manifest(path: Path) -> tuple[dict, str]:
    encoded = path.read_bytes()
    manifest = json.loads(encoded)
    verify_invoice_manifest(manifest, path)
    return manifest, hashlib.sha256(encoded).hexdigest()


def create_freeze(root: Path, manifest_path: Path, output: Path, evidence_dirs: list[Path]) -> dict:
    """Record settings before test predictions exist; never select from test scores."""
    if output.exists() or output.is_symlink():
        raise ValueError("Freeze already exists; choose a fresh path")
    manifest_path = manifest_path.resolve(strict=True)
    manifest, digest = _manifest(manifest_path)
    if not evidence_dirs:
        raise ValueError("Development and calibration evidence are required before freezing")
    evidence = []
    splits = set()
    current = source_hashes()
    for directory in evidence_dirs:
        directory = directory.resolve(strict=True)
        verify_invoice_run(manifest_path, directory)
        report = json.loads((directory / "report.json").read_text())
        identity = report["run_identity"]
        if (identity["split"] not in ("development", "calibration") or
                identity.get("extractor", "ocr_rules") != "ocr_rules" or
                identity["baseline_version"] != BASELINE_VERSION or identity["ocr_psm"] != 1):
            raise ValueError("Freeze selection requires current default PSM 1 development/calibration evidence")
        snapshot = json.loads((directory / "source_snapshot.json").read_text())
        # A changed dispatch/evaluation module can be audited separately; the
        # actual OCR/extraction/validation code must match the measured choice.
        for name in ("baseline.py", "contracts.py", "ocr.py", "validation.py", "spatial_lines.py"):
            if hashlib.sha256(snapshot[name].encode()).hexdigest() != current[name]:
                raise ValueError(f"Selection evidence uses different extraction code: {name}")
        splits.add(identity["split"])
        path = directory / "report.json"
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Selection evidence must reside in the project")
        evidence.append({"path": str(path.relative_to(root.resolve())), "sha256": file_hash(path),
                         "split": identity["split"]})
    if splits != {"development", "calibration"}:
        raise ValueError("Both development and calibration evidence are required")
    scheduled = [doc["id"] for doc in manifest["documents"] if doc["split"] == "test"]
    if not scheduled:
        raise ValueError("Manifest has no held-out test invoices")
    freeze = {"freeze_version": FREEZE_VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "label": "experimental baseline; broader local-model comparison and release acceptance remain pending",
              "dataset_id": manifest["dataset_id"], "manifest_sha256": digest,
              "split": "test", "document_ids": scheduled,
              "extractor": "ocr_rules", "baseline_version": BASELINE_VERSION, "ocr_psm": 1,
              "input_mode": "verified_corpus_png_previews", "ocr_identity": ocr_identity(),
              "python_version": platform.python_version(), "source_sha256": current,
              "selection_evidence": evidence,
              "selection_reason": "Retain v0.3 default after the spatial candidate regressed on calibration. "
                                  "Measure all test invoices without changing extraction or review policy."}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(freeze, stream, indent=2)
        stream.write("\n")
    return freeze


def _validate_freeze(freeze: dict, manifest: dict, digest: str) -> None:
    if (freeze.get("freeze_version") != FREEZE_VERSION or freeze.get("split") != "test" or
            freeze.get("manifest_sha256") != digest or freeze.get("dataset_id") != manifest["dataset_id"] or
            freeze.get("extractor") != "ocr_rules" or freeze.get("baseline_version") != BASELINE_VERSION or
            freeze.get("ocr_psm") != 1 or freeze.get("input_mode") != "verified_corpus_png_previews" or
            set(freeze.get("source_sha256", {})) != set(SOURCE_FILES)):
        raise ValueError("Freeze identity, source contract, or corpus differs")
    scheduled = [doc["id"] for doc in manifest["documents"] if doc["split"] == "test"]
    if not scheduled or freeze.get("document_ids") != scheduled:
        raise ValueError("Freeze must schedule every held-out test document exactly once")


def _timings(predictions: list[dict]) -> dict:
    return {"ocr_sum": round(sum(prediction["runtime_seconds"]["ocr"] for prediction in predictions), 3),
            "total_sum": round(sum(prediction["runtime_seconds"]["total"] for prediction in predictions), 3)}


def quality_breakdown(manifest: dict, scores: list[dict], *, draws: int = 1000, seed: int = 1729) -> dict:
    """Resample parent groups within fixed families; never split degraded pairs."""
    labels = {doc["id"]: doc for doc in manifest["documents"] if doc["split"] == "test"}
    families: dict[str, dict[str, list[dict]]] = {}
    for score in scores:
        label = labels[score["id"]]
        family = families.setdefault(label["family_group"], {})
        family.setdefault(label.get("parent_id") or label["id"], []).append(score)
    summaries = {family: summarize_invoices([score for group in parents.values() for score in group])
                 for family, parents in sorted(families.items())}
    randomizer = random.Random(seed)
    metrics = {"header_macro_f1": [], "row_detection_f1": [], "exact_row_f1": []}
    for _ in range(draws):
        sampled = []
        for family in sorted(families):
            groups = list(families[family].values())
            for _ in groups:
                group = randomizer.choice(groups)
                start = len(sampled)
                sampled.extend({**score, "id": f"bootstrap-{start + offset}"}
                               for offset, score in enumerate(group))
        summary = summarize_invoices(sampled)
        for name, value in (("header_macro_f1", summary["header_macro_f1"]),
                            ("row_detection_f1", summary["row_detection"]["f1"]),
                            ("exact_row_f1", summary["row_exact"]["f1"])):
            if value is not None:
                metrics[name].append(value)
    intervals = {}
    for name, values in metrics.items():
        values.sort()
        intervals[name] = ([values[int(.025 * (len(values) - 1))], values[int(.975 * (len(values) - 1))]]
                           if values else None)
    return {"families": summaries, "uncertainty": {"draws": draws, "seed": seed,
            "parent_groups": sum(len(groups) for groups in families.values()), "percentile_95_intervals": intervals,
            "method": "Parent-group bootstrap stratified within fixed test layout families. "
                      "Degraded/base documents remain together. No unseen-family or real-invoice population claim."}}


def run_heldout(manifest_path: Path, freeze_path: Path, output: Path, *, resume: bool = False) -> dict:
    manifest_path = manifest_path.resolve(strict=True)
    manifest, digest = _manifest(manifest_path)
    freeze_bytes = freeze_path.read_bytes()
    freeze = json.loads(freeze_bytes)
    _validate_freeze(freeze, manifest, digest)
    if (freeze["source_sha256"] != source_hashes() or freeze["ocr_identity"] != ocr_identity() or
            freeze["python_version"] != platform.python_version()):
        raise ValueError("Frozen implementation or OCR runtime changed; do not retune from test results")
    output = output.resolve()
    if output.is_relative_to(manifest_path.parent):
        raise ValueError("Output must be outside the corpus")
    if resume:
        if (output / "freeze.json").read_bytes() != freeze_bytes:
            raise ValueError("Resume requires the original freeze")
        snapshot = json.loads((output / "source_snapshot.json").read_text())
        if {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()} != freeze["source_sha256"]:
            raise ValueError("Resume source snapshot differs from freeze")
        if (output / "report.json").exists():
            verify_heldout(manifest_path, output)
            return json.loads((output / "report.json").read_text())
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "predictions").mkdir()
        (output / "freeze.json").write_bytes(freeze_bytes)
        _write_new(output / "source_snapshot.json", {name: Path(__file__).with_name(name).read_text() for name in SOURCE_FILES})
    predictions_dir = output / "predictions"
    expected = {f"{identifier}.json" for identifier in freeze["document_ids"]}
    temporary = {f".{name}.tmp" for name in expected}
    if {path.name for path in predictions_dir.iterdir()} - expected - temporary:
        raise ValueError("Unexpected held-out prediction files")
    for name in temporary:
        path = predictions_dir / name
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise ValueError("Unsafe interrupted prediction write")
            path.unlink()
    freeze_hash = hashlib.sha256(freeze_bytes).hexdigest()
    ledger = output / "completed.json"
    completed = json.loads(ledger.read_text()) if ledger.exists() else {}
    if set(completed) - expected:
        raise ValueError("Completion ledger has unexpected documents")
    predictions = []
    for document in manifest["documents"]:
        if document["split"] != "test":
            continue
        path = predictions_dir / f"{document['id']}.json"
        if path.exists() and path.name in completed:
            if path.is_symlink() or not path.is_file() or completed.get(path.name) != file_hash(path):
                raise ValueError("Existing held-out prediction differs from its completion ledger")
            prediction = json.loads(path.read_text())
        else:
            if path.name in completed:
                raise ValueError("A completed held-out prediction is missing")
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError("Unsafe uncommitted prediction")
            # A crash between prediction rename and ledger commit leaves an
            # uncommitted file. Recompute it instead of trusting its bytes.
            print(f"Held-out OCR: {document['id']}", flush=True)
            # Pass asset metadata only. Extraction never receives gold fields,
            # rows, expected issues, or label boxes.
            input_metadata = {key: document[key] for key in ("id", "source_sha256", "assets")}
            input_metadata["pages"] = [{"number": page["number"]} for page in document["pages"]]
            prediction = _extract(input_metadata, manifest_path.parent, 1, freeze_hash, "ocr_rules")
            prediction["freeze_sha256"] = freeze_hash
            _write_new(path, prediction)
            completed[path.name] = file_hash(path)
            _write_new(ledger, completed)
        if prediction.get("freeze_sha256") != freeze_hash or prediction.get("source_sha256") != document["source_sha256"]:
            raise ValueError("Prediction does not belong to the frozen document run")
        predictions.append(prediction)
    if (source_hashes() != freeze["source_sha256"] or file_hash(manifest_path) != digest or
            freeze_path.read_bytes() != freeze_bytes or ocr_identity() != freeze["ocr_identity"]):
        raise ValueError("Frozen inputs changed during the test run")
    report = score_saved_invoice_run(manifest_path, predictions_dir, "test")
    report.update({"heldout_run_version": RUN_VERSION, "freeze_sha256": freeze_hash,
                   "label": freeze["label"], "runtime_seconds": _timings(predictions),
                   "issue_codes": dict(Counter(code for prediction in predictions for code in prediction.get("issue_codes", []))),
                   "scope": "All frozen test PNG previews; excludes PDF rendering and model inference. "
                            "Synthetic author-created invoices, not arbitrary real-invoice accuracy."})
    report["quality_breakdown"] = quality_breakdown(manifest, report["documents"])
    _write_new(output / "report.json", report)
    return report


def verify_heldout(manifest_path: Path, output: Path) -> dict:
    manifest_path = manifest_path.resolve(strict=True)
    manifest, digest = _manifest(manifest_path)
    freeze_bytes = (output / "freeze.json").read_bytes()
    freeze = json.loads(freeze_bytes)
    _validate_freeze(freeze, manifest, digest)
    snapshot = json.loads((output / "source_snapshot.json").read_text())
    if ({name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()} != freeze["source_sha256"]):
        raise ValueError("Held-out source snapshot differs from freeze")
    recorded = json.loads((output / "report.json").read_text())
    freeze_hash = hashlib.sha256(freeze_bytes).hexdigest()
    completed = json.loads((output / "completed.json").read_text())
    if set(completed) != {f"{identifier}.json" for identifier in freeze["document_ids"]}:
        raise ValueError("Held-out completion ledger is incomplete")
    predictions = []
    for name, checksum in completed.items():
        path = output / "predictions" / name
        if path.is_symlink() or not path.is_file() or file_hash(path) != checksum:
            raise ValueError("Held-out prediction checksum differs from completion ledger")
        prediction = json.loads(path.read_text())
        if (prediction.get("freeze_sha256") != freeze_hash or prediction.get("extractor") != freeze["extractor"] or
                prediction.get("baseline_version") != freeze["baseline_version"] or prediction.get("ocr_psm") != 1 or
                prediction.get("input_mode") != freeze["input_mode"] or prediction.get("pipeline_sha256") != freeze_hash):
            raise ValueError("Held-out prediction identity differs from freeze")
        if prediction.get("record") is not None:
            pages = tuple(page_from_dict(page) for page in prediction["pages"])
            if not pages or [page.number for page in pages] != list(range(1, len(pages) + 1)):
                raise ValueError("Held-out pages are missing or out of sequence")
            known = {span.id for page in pages for span in page.spans}
            record = record_from_dict(prediction["record"])
            fields = list(record.fields.values()) + [getattr(row, name) for row in record.line_items
                      for name in ("description", "quantity", "unit_price", "line_total", "tax")]
            if any(set(field.evidence_ids) - known for field in fields):
                raise ValueError("Held-out prediction contains unknown OCR evidence")
        predictions.append(prediction)
    rescored = score_saved_invoice_run(manifest_path, output / "predictions", "test")
    for key in ("report_version", "dataset_id", "split", "manifest_sha256", "implementation_sha256",
                "status", "missing_prediction_ids", "summary", "documents"):
        if recorded.get(key) != rescored[key]:
            raise ValueError(f"Held-out report differs from predictions: {key}")
    if (recorded.get("heldout_run_version") != RUN_VERSION or recorded.get("freeze_sha256") != freeze_hash or
            recorded.get("label") != freeze["label"] or recorded.get("runtime_seconds") != _timings(predictions) or
            recorded.get("issue_codes") != dict(Counter(code for p in predictions for code in p.get("issue_codes", [])))):
        raise ValueError("Held-out report identity or diagnostics differ")
    if recorded.get("quality_breakdown") != quality_breakdown(manifest, rescored["documents"]):
        raise ValueError("Held-out family breakdown or uncertainty differs")
    if recorded.get("reporting_correction") is not None:
        correction = recorded["reporting_correction"]
        reporting = (output / "reporting_source_snapshot.py").read_bytes()
        if (correction.get("version") != "bootstrap-replicate-ids-v1" or
                correction.get("original_driver_sha256") != freeze["source_sha256"]["heldout.py"] or
                correction.get("reporting_driver_sha256") != hashlib.sha256(reporting).hexdigest()):
            raise ValueError("Reporting correction provenance differs")
    return {"status": "verified", "documents": len(predictions), "freeze_sha256": freeze_hash}


def finalize_saved_heldout(manifest_path: Path, output: Path) -> dict:
    """Finalize fully committed predictions after a reporting-only failure.

    The original freeze, driver snapshot and predictions stay immutable. Record
    the corrected reporting source separately and verify before publication.
    """
    if (output / "report.json").exists():
        raise ValueError("Held-out report already exists")
    manifest_path = manifest_path.resolve(strict=True)
    manifest, digest = _manifest(manifest_path)
    freeze_bytes = (output / "freeze.json").read_bytes()
    freeze = json.loads(freeze_bytes)
    _validate_freeze(freeze, manifest, digest)
    snapshot = json.loads((output / "source_snapshot.json").read_text())
    if {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()} != freeze["source_sha256"]:
        raise ValueError("Original held-out source snapshot differs from freeze")
    # Reporting fixes cannot substitute a new extractor or scorer.
    if any(source_hashes()[name] != checksum for name, checksum in freeze["source_sha256"].items() if name != "heldout.py"):
        raise ValueError("Frozen extraction/scoring source changed")
    completed = json.loads((output / "completed.json").read_text())
    if set(completed) != {f"{identifier}.json" for identifier in freeze["document_ids"]}:
        raise ValueError("Cannot finalize an incomplete held-out run")
    predictions = []
    for name, checksum in completed.items():
        path = output / "predictions" / name
        if path.is_symlink() or not path.is_file() or file_hash(path) != checksum:
            raise ValueError("Committed prediction changed before reporting")
        predictions.append(json.loads(path.read_text()))
    report = score_saved_invoice_run(manifest_path, output / "predictions", "test")
    reporting = Path(__file__).read_bytes()
    correction = {"version": "bootstrap-replicate-ids-v1",
                  "reason": "Bootstrap replicate IDs must be unique for the scorer; no OCR/extraction rerun or retuning.",
                  "original_driver_sha256": freeze["source_sha256"]["heldout.py"],
                  "reporting_driver_sha256": hashlib.sha256(reporting).hexdigest()}
    report.update({"heldout_run_version": RUN_VERSION, "freeze_sha256": hashlib.sha256(freeze_bytes).hexdigest(),
                   "label": freeze["label"], "runtime_seconds": _timings(predictions),
                   "issue_codes": dict(Counter(code for p in predictions for code in p.get("issue_codes", []))),
                   "scope": "All frozen test PNG previews; excludes PDF rendering and model inference. "
                            "Synthetic author-created invoices, not arbitrary real-invoice accuracy.",
                   "quality_breakdown": quality_breakdown(manifest, report["documents"]),
                   "reporting_correction": correction})
    with (output / "reporting_source_snapshot.py").open("xb") as stream:
        stream.write(reporting)
    _write_new(output / "report.json", report)
    try:
        verify_heldout(manifest_path, output)
    except Exception:
        (output / "report.json").unlink()
        raise
    return report
