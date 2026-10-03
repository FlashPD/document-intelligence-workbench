"""Paired, frozen model extraction over the baseline's verified test OCR."""

from __future__ import annotations

import hashlib
import json
import platform
import random
import time
from collections import Counter
from pathlib import Path

from .evidence import verify_model_evidence
from .heldout import verify_heldout
from .invoice_run import _write_new
from .local_model import (PROMPT_SHA256, PROMPT_VERSION, ModelContextOverflow,
                          ModelOutputInvalid, ModelRequestRejected, ModelUnavailable, extract_pages)
from .model_runtime import file_hash, load_profile, managed_server
from .release_evaluation import score_saved_invoice_run
from .release_scoring import summarize_invoices
from .review import _now, page_from_dict
from .validation import validate_invoice

RUN_VERSION = "paired-invoice-span-model-v1"
SOURCE_FILES = ("invoice_model_run.py", "local_model.py", "model_runtime.py", "contracts.py",
                "validation.py", "review.py", "baseline.py", "release_scoring.py", "release_evaluation.py")


def _snapshot() -> dict:
    return {name: Path(__file__).with_name(name).read_text() for name in SOURCE_FILES}


def _artifacts(output: Path) -> dict:
    return {str(path.relative_to(output)): file_hash(path) for path in sorted(output.rglob("*"))
            if path.is_file() and path.name != "report.json" and not path.name.endswith(".tmp")}


def paired_comparison(manifest: dict, baseline: dict, model: dict, *, draws: int = 1000, seed: int = 42) -> dict:
    left = {score["id"]: score for score in baseline["documents"]}
    right = {score["id"]: score for score in model["documents"]}
    if set(left) != set(right):
        raise ValueError("Paired reports must contain identical document sets")
    families = {}
    for doc in manifest["documents"]:
        if doc["id"] in left:
            families.setdefault(doc["family_group"], {}).setdefault(doc.get("parent_id") or doc["id"], []).append(doc["id"])

    def metrics(summary):
        return {"header_macro_f1": summary["header_macro_f1"],
                "row_detection_f1": summary["row_detection"]["f1"],
                "exact_row_f1": summary["row_exact"]["f1"]}

    before, after = metrics(baseline["summary"]), metrics(model["summary"])
    differences = {name: after[name] - before[name] if after[name] is not None and before[name] is not None else None
                   for name in before}
    samples = {name: [] for name in differences}
    rng = random.Random(seed)
    for _ in range(draws):
        ids = []
        for family in sorted(families):
            groups = list(families[family].values())
            for _ in groups:
                ids.extend(rng.choice(groups))
        summaries = [summarize_invoices([{**scores[id], "id": f"bootstrap-{i}"} for i, id in enumerate(ids)])
                     for scores in (left, right)]
        a, b = map(metrics, summaries)
        for name in samples:
            if a[name] is not None and b[name] is not None:
                samples[name].append(b[name] - a[name])
    intervals = {}
    for name, values in samples.items():
        values.sort()
        intervals[name] = [round(values[int(q * (len(values) - 1))], 4) for q in (.025, .975)] if values else None
    return {"delta_model_minus_rules": {name: round(value, 4) if value is not None else None for name, value in differences.items()},
            "paired_95_intervals": intervals, "draws": draws, "seed": seed,
            "families": {family: {variant: summarize_invoices([scores[id] for ids in groups.values() for id in ids])
                                   for variant, scores in (("ocr_rules", left), ("span_llm", right))}
                         for family, groups in sorted(families.items())},
            "method": "Paired parent-group bootstrap within the six fixed test families; derivatives stay together. "
                      "Does not establish accuracy on unseen vendors or arbitrary real invoices.",
            "status": "regression" if any(value is not None and value < -.02 for value in differences.values()) else "no_regression",
            "max_absolute_regression": .02,
            "promotion": "No default promotion or threshold tuning from this test comparison."}


def _report(manifest_path: Path, baseline_dir: Path, output: Path) -> dict:
    freeze = json.loads((output / "freeze.json").read_text())
    report = score_saved_invoice_run(manifest_path, output / "predictions", "test")
    report["created_at"] = freeze["created_at"]
    baseline = json.loads((baseline_dir / "report.json").read_text())
    predictions = [json.loads((output / "predictions" / f"{id}.json").read_text()) for id in freeze["document_ids"]]
    report.update(run_version=RUN_VERSION, freeze_sha256=file_hash(output / "freeze.json"),
                  baseline_summary=baseline["summary"],
                  comparison=paired_comparison(json.loads(manifest_path.read_text()), baseline, report),
                  runtime_seconds={"model_sum": round(sum(p["runtime_seconds"]["model"] for p in predictions), 3)},
                  issue_codes=dict(Counter(code for p in predictions for code in p.get("issue_codes", []))),
                  scope=freeze["scope"], sessions=[json.loads(path.read_text()) for path in sorted((output / "sessions").glob("*/runtime.json"))],
                  artifacts=_artifacts(output))
    return report


def run_invoice_model(root: Path, manifest_path: Path, baseline_dir: Path, development_dir: Path,
                      profile_path: Path, output: Path, *, resume: bool = False) -> dict:
    root, output = root.resolve(), output.resolve()
    manifest_path, baseline_dir = manifest_path.resolve(strict=True), baseline_dir.resolve(strict=True)
    verify_heldout(manifest_path, baseline_dir)
    verify_model_evidence(development_dir, root)
    dev = json.loads((development_dir / "report.json").read_text())
    profile = load_profile(profile_path)
    if (dev["extractor"]["prompt_sha256"] != PROMPT_SHA256 or
            dev["extractor"]["profile_sha256"] != file_hash(profile_path)):
        raise ValueError("Model prompt/profile must match measured development selection evidence")
    snapshot = _snapshot()
    baseline = json.loads((baseline_dir / "report.json").read_text())
    manifest = json.loads(manifest_path.read_text())
    ids = [doc["id"] for doc in manifest["documents"] if doc["split"] == "test"]
    inputs = {f"{id}.json": file_hash(baseline_dir / "predictions" / f"{id}.json") for id in ids}
    identity = {"run_version": RUN_VERSION, "manifest_sha256": file_hash(manifest_path),
                "baseline_report_sha256": file_hash(baseline_dir / "report.json"),
                "development_report_sha256": file_hash(development_dir / "report.json"),
                "profile_sha256": file_hash(profile_path), "prompt_version": PROMPT_VERSION,
                "prompt_sha256": PROMPT_SHA256, "document_ids": ids, "baseline_predictions": inputs,
                "source_sha256": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()},
                "python": platform.python_version(),
                "scope": "Fresh pinned model inference on the frozen baseline's saved OCR spans for all test invoices. "
                         "Shared-OCR extraction comparison; excludes fresh OCR and PDF rendering. "
                         "Test baseline results were already published before this model freeze. "
                         "Model and prompt selected on the earlier 12-document development set, without full-corpus model calibration. "
                         "Synthetic author-created invoices; every scheduled failure remains in scoring."}
    if output.is_relative_to(manifest_path.parent) or output.is_relative_to(baseline_dir):
        raise ValueError("Model output must be outside the corpus and baseline evidence")
    if resume:
        freeze = json.loads((output / "freeze.json").read_text())
        if {key: freeze.get(key) for key in identity} != identity or json.loads((output / "source_snapshot.json").read_text()) != snapshot:
            raise ValueError("Model run identity changed; resume requires original settings and inputs")
        if (output / "report.json").exists():
            verify_invoice_model(manifest_path, baseline_dir, output)
            return json.loads((output / "report.json").read_text())
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "predictions").mkdir()
        (output / "sessions").mkdir()
        _write_new(output / "freeze.json", {**identity, "created_at": _now()})
        _write_new(output / "source_snapshot.json", snapshot)
        (output / "profile.json").write_bytes(profile_path.read_bytes())
    ledger_path = output / "completed.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    expected = set(inputs)
    if set(ledger) - expected or {p.name for p in (output / "predictions").iterdir()} - expected - {f".{name}.tmp" for name in expected}:
        raise ValueError("Unexpected model prediction or completion ledger entry")
    for name, checksum in ledger.items():
        path = output / "predictions" / name
        if path.is_symlink() or not path.is_file() or file_hash(path) != checksum:
            raise ValueError("Completed model prediction has changed or disappeared")
    if set(ledger) != expected:
        session = output / "sessions" / f"{len(list((output / 'sessions').iterdir())) + 1:04d}"
        session.mkdir()
        runtime = None
        try:
            with managed_server(root, profile, session / "server.log") as (config, runtime):
                for id in ids:
                    name = f"{id}.json"
                    if name in ledger:
                        continue
                    saved = json.loads((baseline_dir / "predictions" / name).read_text())
                    print(f"Model test: {id} ({len(ledger) + 1}/{len(ids)})", flush=True)
                    started = time.perf_counter()
                    prediction = {"source_sha256": saved["source_sha256"], "record": None,
                                  "freeze_sha256": file_hash(output / "freeze.json"), "ocr_prediction_sha256": inputs[name]}
                    try:
                        pages = tuple(page_from_dict(page) for page in saved.get("pages", []))
                        if not pages:
                            raise ValueError("Baseline OCR produced no pages")
                        extracted = extract_pages(pages, config)
                        issues = (*validate_invoice(extracted.record, pages), *extracted.issues)
                        prediction.update(record=extracted.record.to_dict(), issue_codes=[issue.code for issue in issues],
                                          pages=saved["pages"])
                    except (ModelUnavailable, ModelRequestRejected, ModelOutputInvalid, ModelContextOverflow, ValueError) as exc:
                        prediction["failure_type"] = type(exc).__name__
                    prediction["runtime_seconds"] = {"model": round(time.perf_counter() - started, 3)}
                    _write_new(output / "predictions" / name, prediction)
                    ledger[name] = file_hash(output / "predictions" / name)
                    _write_new(ledger_path, ledger)
                    print(f"  {prediction.get('failure_type', 'processed')}: {prediction['runtime_seconds']}", flush=True)
        finally:
            if runtime is not None:
                _write_new(session / "runtime.json", runtime)
    if _snapshot() != snapshot or any(file_hash(baseline_dir / "predictions" / name) != digest for name, digest in inputs.items()):
        raise ValueError("Inputs or extraction source changed during model run")
    report = _report(manifest_path, baseline_dir, output)
    _write_new(output / "report.json", report)
    verify_invoice_model(manifest_path, baseline_dir, output)
    return report


def verify_invoice_model(manifest_path: Path, baseline_dir: Path, output: Path) -> dict:
    verify_heldout(manifest_path, baseline_dir)
    report = json.loads((output / "report.json").read_text())
    freeze = json.loads((output / "freeze.json").read_text())
    snapshot = json.loads((output / "source_snapshot.json").read_text())
    if (freeze["manifest_sha256"] != file_hash(manifest_path) or
            freeze["baseline_report_sha256"] != file_hash(baseline_dir / "report.json") or
            freeze["source_sha256"] != {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()} or
            freeze["profile_sha256"] != file_hash(output / "profile.json")):
        raise ValueError("Saved model freeze differs from inputs or source snapshot")
    ledger = json.loads((output / "completed.json").read_text())
    if set(ledger) != {f"{id}.json" for id in freeze["document_ids"]}:
        raise ValueError("Model comparison is incomplete")
    for name, checksum in ledger.items():
        path = output / "predictions" / name
        if path.is_symlink() or not path.is_file() or file_hash(path) != checksum:
            raise ValueError("Model prediction differs from completion ledger")
        prediction = json.loads(path.read_text())
        if (prediction["freeze_sha256"] != file_hash(output / "freeze.json") or
                prediction["ocr_prediction_sha256"] != freeze["baseline_predictions"][name] or
                file_hash(baseline_dir / "predictions" / name) != prediction["ocr_prediction_sha256"]):
            raise ValueError("Model prediction does not bind the original OCR input")
    for name, digest in report["artifacts"].items():
        path = output / name
        if Path(name).is_absolute() or ".." in Path(name).parts or path.is_symlink() or not path.is_file() or file_hash(path) != digest:
            raise ValueError("Model evidence artifact has changed")
    profile = json.loads((output / "profile.json").read_text())
    if not report["sessions"] or any(session["shutdown_complete"] is not True or
            session["model_sha256"] != profile["model"]["sha256"] or
            session["runtime_archive_sha256"] != profile["runtime"]["sha256"] or
            session["inference"] != profile["inference"] for session in report["sessions"]):
        raise ValueError("Model runtime identity or shutdown was not verified")
    if _report(manifest_path, baseline_dir, output) != report:
        raise ValueError("Model comparison does not reproduce its saved report")
    return {"status": "verified", "documents": len(ledger), "report_sha256": file_hash(output / "report.json")}
