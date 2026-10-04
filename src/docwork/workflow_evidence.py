"""Offline integrity checks for the two-fixture real-model HTTP workflow bundle."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .model_runtime import file_hash, load_profile
from .review import _hash, page_from_dict, record_from_dict
from .validation import validate_invoice

FIXTURES = {"clean.png": "925a3b1858f6b322330ba16148cbd9e498117e89979182fe897af60a90e1a106",
            "conflicting-total.png": "13f2eef374c009a698a31e17639417d0b970df0feca6329e1053a59e8be331d3"}


def verify_workflow_evidence(directory: Path) -> dict:
    directory = directory.resolve(strict=True)
    report = json.loads((directory / "report.json").read_text())
    if (report.get("report_version") != "real-model-upload-workflow-v1" or report.get("status") != "passed" or
            report.get("inputs_unchanged") is not True):
        raise ValueError("Workflow evidence does not record a completed successful run")
    artifacts = report["artifacts"]
    actual = {str(path.relative_to(directory)) for path in directory.rglob("*") if path.is_file()}
    if actual != set(artifacts) | {"report.json"}:
        raise ValueError("Workflow artifact inventory differs")
    for relative, digest in artifacts.items():
        path = Path(relative)
        if (path.is_absolute() or ".." in path.parts or str(path) != relative or
                any((directory / Path(*path.parts[:index])).is_symlink() for index in range(1, len(path.parts) + 1)) or
                not (directory / path).is_file() or file_hash(directory / path) != digest):
            raise ValueError(f"Workflow artifact checksum or path differs: {relative}")
    snapshot = json.loads((directory / "source_snapshot.json").read_text())
    if {name: hashlib.sha256(text.encode()).hexdigest() for name, text in snapshot.items()} != report["source_sha256"]:
        raise ValueError("Workflow source snapshot differs")
    profile = load_profile(directory / "profile.json")
    runtime = report["managed_runtime"]
    if (runtime.get("shutdown_complete") is not True or runtime.get("model_sha256") != profile["model"]["sha256"] or
            runtime.get("runtime_archive_sha256") != profile["runtime"]["sha256"] or
            runtime.get("runtime_commit") != profile["runtime"]["commit"] or
            runtime.get("inference") != profile["inference"]):
        raise ValueError("Workflow model/runtime identity differs")
    checks = report["checks"]
    if len(checks) != 2 or {check["fixture"] for check in checks} != set(FIXTURES):
        raise ValueError("Workflow fixture coverage differs")
    for check in checks:
        name = check["fixture"]
        stem = Path(name).stem
        detail = json.loads((directory / "predictions" / f"{stem}.json").read_text())
        if (check["status"] != "passed" or check["source_sha256"] != FIXTURES[name] or
                detail["source_sha256"] != FIXTURES[name] or detail["approval"] is not None or
                detail["extraction"]["profile"] != "span_llm" or
                detail["extraction"]["model_id"] != profile["inference"]["model_id"] or
                check["parser_checkpoint"]["parser_identity"] != report["parser_image_id"]):
            raise ValueError("Workflow candidate provenance differs")
        pages = tuple(page_from_dict(page) for page in detail["pages"])
        record = record_from_dict(detail["record"])
        from dataclasses import asdict
        issues = [asdict(issue) for issue in validate_invoice(record, pages)]
        if detail["issues"] != issues or check["issues_before_review"] != issues:
            raise ValueError("Workflow validation issues differ from saved OCR/record")
        if any(issue["code"].startswith("EVIDENCE_") for issue in issues):
            raise ValueError("Workflow record cites unsupported evidence")
        for page in pages:
            raster = directory / "pages" / f"{stem}-{page.number}.png"
            data = raster.read_bytes()
            if (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")) != (page.width_px, page.height_px):
                raise ValueError("Workflow page dimensions differ")
        exported = json.loads((directory / "exports" / stem / "json/invoice.json").read_text())
        approved = detail
        if name == "conflicting-total.png":
            approved = json.loads((directory / "reviews" / f"{stem}.json").read_text())
            if (detail["record"]["fields"]["total"]["value"] != "275.00" or
                    approved["record"]["fields"]["total"]["value"] != "270.00" or
                    approved["revision"] != 2 or approved["approval"] is not None):
                raise ValueError("Workflow correction provenance differs")
        if (exported["document_id"] != detail["document_id"] or exported["source_sha256"] != FIXTURES[name] or
                exported["revision"] != check["approved_revision"] or exported["record"] != approved["record"] or
                exported["record_hash"] != _hash(exported["record"]) or exported["approval_hash"] != check["approval_hash"]):
            raise ValueError("Workflow export differs from the approved revision")
        for relative, checksum in check["export_sha256"].items():
            if artifacts.get(relative) != checksum:
                raise ValueError("Workflow download checksum differs")
        history = json.loads((directory / "history" / f"{stem}.json").read_text())
        approvals = [event for event in history if event["kind"] == "approved"]
        if len(approvals) != 1 or approvals[0]["revision"] != check["approved_revision"] or approvals[0]["detail"] != check["approval_hash"]:
            raise ValueError("Workflow approval history differs")
    return {"status": "verified", "fixtures": 2, "model_sha256": runtime["model_sha256"],
            "note": "Integrity and saved-record checks; no fresh inference, visual browser check, or signed attestation."}
