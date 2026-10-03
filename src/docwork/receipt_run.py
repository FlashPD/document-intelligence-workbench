"""Sealed receipt evaluation with pinned inputs, saved OCR, and counted failures."""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
import time
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path

from .cord import verify_cord
from .invoice_run import _write_new
from .local_model import ModelUnavailable, ModelOutputInvalid, ModelContextOverflow, ModelRequestRejected
from .model_runtime import file_hash, load_profile, managed_server
from .ocr import tesseract_page
from .receipt import (PROMPT_SHA256, RECEIPT_VERSION, extract_receipt_rules, extract_receipt_model,
                      receipt_evidence, score_receipt, summarize_receipts)
from .review import _now, page_from_dict

RUN_VERSION = "cord-receipt-evaluation-v1"
FILES = ("receipt_run.py", "receipt.py", "cord.py", "ocr.py", "contracts.py", "local_model.py",
         "model_runtime.py", "release_scoring.py")


def _snapshot():
    return {name: Path(__file__).with_name(name).read_text() for name in FILES}


def _report(manifest: dict, output: Path) -> dict:
    run = json.loads((output / "run.json").read_text())
    selected = {doc["id"]: doc for doc in manifest["documents"] if doc["id"] in run["document_ids"]}
    predictions, scores = [], []
    ledger = json.loads((output / "completed.json").read_text())
    if set(ledger) != {f"{id}.json" for id in selected}:
        raise ValueError("Receipt prediction evidence is incomplete")
    for id, doc in selected.items():
        path = output / "predictions" / f"{id}.json"
        if path.is_symlink() or not path.is_file() or file_hash(path) != ledger[path.name]:
            raise ValueError("Receipt prediction checksum differs from completion ledger")
        prediction = json.loads(path.read_text())
        if prediction["source_sha256"] != doc["source_sha256"] or prediction["run_sha256"] != file_hash(output / "run.json"):
            raise ValueError("Receipt prediction provenance differs from frozen run")
        predictions.append(prediction)
        score = score_receipt(doc, prediction)
        score["prediction_sha256"] = ledger[path.name]
        scores.append(score)
    summary = summarize_receipts(scores)
    rng = random.Random(42)
    bootstrap = []
    for _ in range(1000):
        sampled = [{**rng.choice(scores), "id": f"bootstrap-{i}"} for i in range(len(scores))]
        bootstrap.append(summarize_receipts(sampled)["eligible_exact_rows"]["f1"])
    bootstrap = sorted(value for value in bootstrap if value is not None)
    artifacts = {str(path.relative_to(output)): file_hash(path) for path in sorted(output.rglob("*"))
                 if path.is_file() and path.name != "report.json" and not path.name.endswith(".tmp")}
    return {"report_version": RUN_VERSION, "created_at": run["created_at"], "split": run["split"],
            "variant": run["variant"], "run_sha256": file_hash(output / "run.json"),
            "manifest_sha256": run["manifest_sha256"], "documents": scores, "summary": summary,
            "scope": run["scope"], "artifacts": artifacts,
            "runtime_seconds": {stage: round(sum(p["runtime_seconds"].get(stage, 0) for p in predictions), 3) for stage in ("ocr", "model")},
            "evidence": {key: sum(p.get("evidence", {}).get(key, 0) for p in predictions)
                         for key in ("observed_values", "valid_reference_values", "aligned_values")},
            "uncertainty": {"draws": 1000, "seed": 42,
                            "eligible_exact_row_f1_95_interval": [bootstrap[int(q * (len(bootstrap) - 1))] for q in (.025, .975)] if bootstrap else None,
                            "method": "Receipt bootstrap within the official split; no unseen-domain or vendor generalization claim."}}


def verify_receipt_run(manifest_path: Path, output: Path) -> dict:
    verify_cord(manifest_path)
    run = json.loads((output / "run.json").read_text())
    snapshot = json.loads((output / "source_snapshot.json").read_text())
    if run["manifest_sha256"] != file_hash(manifest_path) or run["source_sha256"] != {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()}:
        raise ValueError("Receipt corpus or source snapshot differs from run")
    recorded = json.loads((output / "report.json").read_text())
    for name, digest in recorded["artifacts"].items():
        relative = Path(name)
        path = output / relative
        if (relative.is_absolute() or ".." in relative.parts or path.is_symlink() or
                not path.resolve().is_relative_to(output.resolve()) or not path.is_file() or file_hash(path) != digest):
            raise ValueError("Receipt evidence artifact differs from report")
    if run["variant"] == "span_llm":
        if file_hash(output / "profile.json") != run["profile_sha256"]:
            raise ValueError("Receipt model profile has changed")
        profile = load_profile(output / "profile.json")
        sessions = list((output / "sessions").glob("*/runtime.json"))
        if not sessions:
            raise ValueError("Receipt model runtime evidence is missing")
        for path in sessions:
            runtime = json.loads(path.read_text())
            if (runtime["shutdown_complete"] is not True or runtime["model_sha256"] != profile["model"]["sha256"] or
                    runtime["runtime_archive_sha256"] != profile["runtime"]["sha256"] or runtime["inference"] != profile["inference"]):
                raise ValueError("Receipt model lifecycle or identity differs")
    if _report(json.loads(manifest_path.read_text()), output) != recorded:
        raise ValueError("Receipt report does not reproduce its predictions")
    return {"status": "verified", "documents": len(recorded["documents"]), "report_sha256": file_hash(output / "report.json")}


def freeze_receipts(manifest_path: Path, rules_development: Path, model_development: Path,
                    profile_path: Path, output: Path) -> dict:
    verify_cord(manifest_path)
    evidence = []
    profile_hash = file_hash(profile_path)
    hashes = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in _snapshot().items()}
    for variant, directory in (("ocr_rules", rules_development), ("span_llm", model_development)):
        verify_receipt_run(manifest_path, directory)
        run = json.loads((directory / "run.json").read_text())
        if run["split"] != "validation" or run["variant"] != variant or run["source_sha256"] != hashes or run["profile_sha256"] != profile_hash:
            raise ValueError("Receipt freeze requires development evidence from the current pipeline/profile")
        evidence.append({"variant": variant, "documents": len(run["document_ids"]),
                         "report_sha256": file_hash(directory / "report.json")})
    if output.exists() or output.is_symlink():
        raise ValueError("Receipt freeze must use a new path")
    freeze = {"freeze_version": RUN_VERSION, "created_at": _now(), "manifest_sha256": file_hash(manifest_path),
              "source_sha256": hashes, "profile_sha256": profile_hash, "prompt_sha256": PROMPT_SHA256,
              "development_evidence": evidence, "ocr_language": "eng", "ocr_psm": 1,
              "scope": "Experimental English-OCR receipt adapters. Settings frozen using the official validation split; "
                       "top-level purchased items and released labeled amounts only. "
                       "No currency, vendor, invoice number, date, submenu, void-item, or payment-label scoring. "
                       "No automatic approval or production receipt workflow claim."}
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_new(output, freeze)
    return freeze


def run_receipts(root: Path, manifest_path: Path, profile_path: Path, output: Path, *,
                 split: str = "validation", variant: str = "ocr_rules", ocr_run: Path | None = None,
                 freeze_path: Path | None = None, limit: int | None = None, resume: bool = False) -> dict:
    verify_cord(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if split not in ("validation", "test") or variant not in ("ocr_rules", "span_llm"):
        raise ValueError("Receipt evaluation needs a supported split and variant")
    if split == "test" and (freeze_path is None or limit is not None):
        raise ValueError("Receipt test evaluation requires a freeze and all 100 documents")
    snapshot = _snapshot()
    hashes = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()}
    profile = load_profile(profile_path)
    freeze = json.loads(freeze_path.read_text()) if freeze_path else None
    if freeze and (freeze["manifest_sha256"] != file_hash(manifest_path) or freeze["source_sha256"] != hashes or
                   freeze["profile_sha256"] != file_hash(profile_path) or freeze["prompt_sha256"] != PROMPT_SHA256):
        raise ValueError("Frozen receipt pipeline, corpus, or profile changed")
    docs = [doc for doc in manifest["documents"] if doc["split"] == split]
    if limit is not None:
        if type(limit) is not int or not 1 <= limit <= len(docs):
            raise ValueError("Development limit must be within the official validation split")
        docs = docs[:limit]
    if variant == "span_llm" and ocr_run is None:
        raise ValueError("Receipt model comparison requires a verified shared-OCR rules run")
    if ocr_run:
        verify_receipt_run(manifest_path, ocr_run)
        ocr_identity = json.loads((ocr_run / "run.json").read_text())
        if ocr_identity["variant"] != "ocr_rules" or ocr_identity["split"] != split or not {doc["id"] for doc in docs} <= set(ocr_identity["document_ids"]):
            raise ValueError("Shared OCR run is not compatible with the selected receipts")
    identity = {"run_version": RUN_VERSION, "split": split, "variant": variant, "receipt_version": RECEIPT_VERSION,
                "manifest_sha256": file_hash(manifest_path), "document_ids": [doc["id"] for doc in docs],
                "source_sha256": hashes, "profile_sha256": file_hash(profile_path), "prompt_sha256": PROMPT_SHA256,
                "ocr_run_sha256": file_hash(ocr_run / "report.json") if ocr_run else None,
                "freeze_sha256": file_hash(freeze_path) if freeze_path else None,
                "scope": freeze["scope"] if freeze else "Official validation development diagnostic; not held-out quality.",
                "ocr_runtime": subprocess.check_output(["tesseract", "--version"], text=True, timeout=10).splitlines()[0],
                "ocr_language": "eng", "ocr_psm": 1}
    output = output.resolve()
    if output.is_relative_to(manifest_path.resolve().parent) or (ocr_run and output.is_relative_to(ocr_run.resolve())):
        raise ValueError("Receipt output must be outside corpus and shared OCR evidence")
    if resume:
        saved = json.loads((output / "run.json").read_text())
        if {key: saved.get(key) for key in identity} != identity or json.loads((output / "source_snapshot.json").read_text()) != snapshot:
            raise ValueError("Receipt run identity changed; cannot resume")
        if (output / "report.json").exists():
            verify_receipt_run(manifest_path, output)
            return json.loads((output / "report.json").read_text())
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "predictions").mkdir()
        (output / "sessions").mkdir()
        _write_new(output / "run.json", {**identity, "created_at": _now()})
        _write_new(output / "source_snapshot.json", snapshot)
        (output / "profile.json").write_bytes(profile_path.read_bytes())
        if freeze_path:
            (output / "freeze.json").write_bytes(freeze_path.read_bytes())
    ledger_path = output / "completed.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    expected = {f"{doc['id']}.json" for doc in docs}
    if set(ledger) - expected or {p.name for p in (output / "predictions").iterdir()} - expected - {f".{name}.tmp" for name in expected}:
        raise ValueError("Receipt prediction inventory differs from schedule")
    for name, digest in ledger.items():
        if (output / "predictions" / name).is_symlink() or file_hash(output / "predictions" / name) != digest:
            raise ValueError("Completed receipt prediction has changed")
    session = output / "sessions" / f"{len(list((output / 'sessions').iterdir())) + 1:04d}"
    runtime = None
    context = nullcontext((None, None))
    if variant == "span_llm" and set(ledger) != expected:
        session.mkdir()
        context = managed_server(root, profile, session / "server.log")
    try:
        with context as (config, runtime):
            for doc in docs:
                name = f"{doc['id']}.json"
                if name in ledger:
                    continue
                print(f"CORD {split} {variant}: {doc['id']} ({len(ledger) + 1}/{len(docs)})", flush=True)
                prediction = {"record": None, "source_sha256": doc["source_sha256"],
                              "run_sha256": file_hash(output / "run.json"), "runtime_seconds": {"ocr": 0, "model": 0}}
                stage = "ocr"
                started = time.perf_counter()
                try:
                    if ocr_run:
                        source = ocr_run / "predictions" / name
                        saved = json.loads(source.read_text())
                        prediction["ocr_prediction_sha256"] = file_hash(source)
                        if saved.get("page") is None:
                            raise RuntimeError("Shared OCR failed")
                        page = page_from_dict(saved["page"])
                    else:
                        page = tesseract_page(manifest_path.parent / doc["asset"]["path"])
                        prediction["runtime_seconds"]["ocr"] = round(time.perf_counter() - started, 3)
                    prediction["page"] = asdict(page)
                    if variant == "span_llm":
                        stage, started = "model", time.perf_counter()
                        record = extract_receipt_model(page, config)
                        prediction["runtime_seconds"]["model"] = round(time.perf_counter() - started, 3)
                    else:
                        record = extract_receipt_rules(page)
                    prediction.update(record=record, evidence=receipt_evidence(record, page))
                except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired, ModelUnavailable,
                        ModelOutputInvalid, ModelContextOverflow, ModelRequestRejected) as exc:
                    prediction["failure_type"] = type(exc).__name__
                    prediction["runtime_seconds"][stage] = round(time.perf_counter() - started, 3)
                _write_new(output / "predictions" / name, prediction)
                ledger[name] = file_hash(output / "predictions" / name)
                _write_new(ledger_path, ledger)
    finally:
        if runtime is not None:
            _write_new(session / "runtime.json", runtime)
    if _snapshot() != snapshot:
        raise ValueError("Receipt extraction code changed during run")
    report = _report(manifest, output)
    _write_new(output / "report.json", report)
    verify_receipt_run(manifest_path, output)
    return report
