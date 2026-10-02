"""Score saved invoice predictions against a frozen, split-safe corpus manifest."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .release_scoring import score_invoice, summarize_invoices

MANIFEST_VERSION = "invoice-corpus-v1"
REPORT_VERSION = "invoice-release-report-v1"
SPLITS = frozenset({"development", "calibration", "test"})
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
DOCUMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def verify_invoice_manifest(manifest: dict, manifest_path: Path) -> None:
    """Check asset integrity and prevent family/vendor/content overlap across splits."""
    if (not isinstance(manifest, dict) or manifest.get("manifest_version") != MANIFEST_VERSION
            or not isinstance(manifest.get("dataset_id"), str) or not manifest["dataset_id"]):
        raise ValueError("Expected an invoice-corpus-v1 manifest with a dataset ID")
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents or len(documents) > 10000:
        raise ValueError("Manifest must contain 1 to 10000 documents")
    root = manifest_path.parent.resolve()
    ids: set[str] = set()
    family_splits: dict[str, set[str]] = defaultdict(set)
    vendor_splits: dict[str, set[str]] = defaultdict(set)
    content_splits: dict[str, set[str]] = defaultdict(set)
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("Each document must be an object")
        identifier = document.get("id")
        split = document.get("split")
        family = document.get("family_group")
        if (not isinstance(identifier, str) or not DOCUMENT_ID.fullmatch(identifier) or identifier in ids
                or split not in SPLITS or not isinstance(family, str) or not family.strip()):
            raise ValueError(f"Invalid document ID, split, or family: {identifier}")
        ids.add(identifier)
        family_splits[family].add(split)
        fields = document.get("fields")
        if not isinstance(fields, dict):
            raise ValueError(f"Missing field labels: {identifier}")
        score_invoice(document, None)  # Validate every split's labels before scoring one split.
        vendor = fields.get("supplier_name")
        if isinstance(vendor, str) and vendor.strip():
            vendor_splits[" ".join(vendor.casefold().split())].add(split)
        assets = document.get("assets")
        source_hash = document.get("source_sha256")
        if (not isinstance(assets, list) or not assets or
                not isinstance(source_hash, str) or not HEX_SHA256.fullmatch(source_hash)):
            raise ValueError(f"Missing assets or source hash: {identifier}")
        asset_hashes: set[str] = set()
        for asset in assets:
            if not isinstance(asset, dict):
                raise ValueError(f"Invalid asset: {identifier}")
            relative, digest = asset.get("path"), asset.get("sha256")
            if (not isinstance(relative, str) or not isinstance(digest, str)
                    or not HEX_SHA256.fullmatch(digest)):
                raise ValueError(f"Invalid asset path or digest: {identifier}")
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or str(path) != relative:
                raise ValueError(f"Asset path escapes manifest directory: {identifier}")
            candidate = root / path
            resolved = candidate.resolve(strict=True)
            if (not resolved.is_relative_to(root) or not resolved.is_file() or
                    any((root / Path(*path.parts[:index])).is_symlink()
                        for index in range(1, len(path.parts) + 1))):
                raise ValueError(f"Asset path escapes manifest directory: {identifier}")
            if hashlib.sha256(resolved.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Asset hash differs from manifest: {identifier}")
            asset_hashes.add(digest)
            content_splits[digest].add(split)
        if source_hash not in asset_hashes:
            raise ValueError(f"Source hash has no matching asset: {identifier}")
    for label, mapping in (("family", family_splits), ("vendor", vendor_splits),
                           ("asset", content_splits)):
        overlap = [key for key, splits in mapping.items() if len(splits) > 1]
        if overlap:
            raise ValueError(f"{label} appears in multiple splits: {overlap[0]}")


def score_saved_invoice_run(manifest_path: Path, predictions_dir: Path, split: str) -> dict:
    """Score every scheduled document; absent predictions remain counted failures."""
    if split not in SPLITS:
        raise ValueError("split must be development, calibration, or test")
    manifest_path = manifest_path.resolve(strict=True)
    predictions_dir = predictions_dir.resolve(strict=True)
    if not predictions_dir.is_dir():
        raise ValueError("Predictions path must be a directory")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    verify_invoice_manifest(manifest, manifest_path)
    scheduled = [document for document in manifest["documents"] if document["split"] == split]
    if not scheduled:
        raise ValueError(f"Manifest has no {split} documents")
    expected_files = {f"{document['id']}.json" for document in scheduled}
    actual_files = {path.name for path in predictions_dir.iterdir()}
    if actual_files - expected_files:
        raise ValueError(f"Unexpected prediction files: {', '.join(sorted(actual_files - expected_files))}")
    scores = []
    hashes: dict[str, str | None] = {}
    missing = []
    for document in scheduled:
        path = predictions_dir / f"{document['id']}.json"
        if not path.exists():
            prediction = None
            missing.append(document["id"])
            hashes[document["id"]] = None
        else:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Prediction is not a regular file: {document['id']}")
            encoded = path.read_bytes()
            hashes[document["id"]] = hashlib.sha256(encoded).hexdigest()
            prediction = json.loads(encoded)
            if not isinstance(prediction, dict):
                raise ValueError(f"Prediction must be a JSON object: {document['id']}")
            if prediction.get("source_sha256") != document["source_sha256"]:
                raise ValueError(f"Prediction source hash differs from manifest: {document['id']}")
        score = score_invoice(document, prediction)
        score["prediction_sha256"] = hashes[document["id"]]
        scores.append(score)
    implementation = hashlib.sha256()
    for name in ("release_evaluation.py", "release_scoring.py"):
        implementation.update(name.encode() + b"\0" + Path(__file__).with_name(name).read_bytes() + b"\0")
    return {
        "report_version": REPORT_VERSION,
        "dataset_id": manifest["dataset_id"],
        "split": split,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "implementation_sha256": implementation.hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "incomplete_evidence" if missing else "scored",
        "missing_prediction_ids": missing,
        "summary": summarize_invoices(scores),
        "documents": scores,
    }
