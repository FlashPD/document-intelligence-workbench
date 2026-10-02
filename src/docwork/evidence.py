"""Offline integrity and rescoring checks for saved managed-model evaluations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .comparison import audit_report
from .evaluation import score_document
from .model_runtime import file_hash


def verify_model_evidence(directory: Path, repo_root: Path) -> dict:
    directory = directory.resolve(strict=True)
    report = json.loads((directory / "report.json").read_text())
    manifest_bytes = (repo_root / "datasets" / "development-v0.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    audit_report(report, manifest, hashlib.sha256(manifest_bytes).hexdigest())
    artifacts = report["artifacts"]
    for name, expected in artifacts.items():
        relative = Path(name)
        path = directory / relative
        if (relative.is_absolute() or ".." in relative.parts or path.is_symlink()
                or not path.resolve(strict=True).is_relative_to(directory) or not path.is_file()):
            raise ValueError("Evidence artifact is not a regular file inside the run directory")
        if file_hash(path) != expected:
            raise ValueError(f"Evidence artifact checksum differs: {name}")
    required = {"profile.json", "source_snapshot.json", "server.log"} | {
        f"predictions/{gold['id']}.json" for gold in manifest["documents"]
    }
    if not required <= artifacts.keys():
        raise ValueError("Model evidence is missing required artifacts")
    snapshot = json.loads((directory / "source_snapshot.json").read_text())
    implementation = hashlib.sha256()
    for name in sorted(("contracts.py", "geometry.py", "ocr.py", "validation.py", "local_model.py")):
        implementation.update(name.encode() + b"\0" + snapshot[name].encode() + b"\0")
    if implementation.hexdigest() != report["extractor"]["implementation_sha256"]:
        raise ValueError("Pipeline source fingerprint differs from saved source snapshot")
    profile = json.loads((directory / "profile.json").read_text())
    extractor, runtime = report["extractor"], report["managed_runtime"]
    if (extractor["profile_sha256"] != artifacts["profile.json"]
            or extractor["model_sha256"] != profile["model"]["sha256"]
            or runtime["model_sha256"] != extractor["model_sha256"]
            or extractor["runtime_archive_sha256"] != profile["runtime"]["sha256"]
            or runtime["runtime_archive_sha256"] != extractor["runtime_archive_sha256"]
            or runtime["shutdown_complete"] is not True):
        raise ValueError("Model evidence does not bind the pinned profile and completed server lifecycle")
    documents = {doc["id"]: doc for doc in report["documents"]}
    for gold in manifest["documents"]:
        result = json.loads((directory / "predictions" / f"{gold['id']}.json").read_text())
        if result["source_sha256"] != gold["sha256"]:
            raise ValueError("Prediction source hash differs from frozen document")
        score = score_document(gold, result if "page" in result else None)
        if "failure_type" in result:
            score["failure_type"] = result["failure_type"]
        if score != documents[gold["id"]]:
            raise ValueError(f"Saved predictions do not reproduce document scores: {gold['id']}")
    return {"status": "verified", "documents": len(documents), "artifacts": len(artifacts),
            "report_sha256": file_hash(directory / "report.json"),
            "interpretation": "Integrity and deterministic rescoring of recorded predictions; no inference was run."}
