"""Development-only scoring for the self-authored feasibility corpus."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .baseline import BASELINE_VERSION
from .contracts import Box, HEADER_FIELDS, REQUIRED_FIELDS
from .geometry import DisplayTransform, PixelBox
from .ocr import png_dimensions


def _box(mapping: dict) -> Box:
    return Box(**mapping)


def box_iou(left: Box, right: Box) -> float:
    intersection = max(0.0, min(left.right, right.right) - max(left.left, right.left)) * max(
        0.0, min(left.bottom, right.bottom) - max(left.top, right.top)
    )
    if intersection == 0:
        return 0.0
    area_left = (left.right - left.left) * (left.bottom - left.top)
    area_right = (right.right - right.left) * (right.bottom - right.top)
    return intersection / (area_left + area_right - intersection)


def verify_development_manifest(manifest: dict, repo_root: Path) -> None:
    if manifest.get("dataset_id") != "self-authored-development-v0" or manifest.get("split") != "development":
        raise ValueError("expected the frozen self-authored development manifest")
    documents = manifest.get("documents", [])
    if len(documents) != 12 or len({item["id"] for item in documents}) != 12:
        raise ValueError("development set must have 12 unique documents")
    if sorted(Counter(item["family_group"] for item in documents).values()) != [2] * 6:
        raise ValueError("development set must contain two documents per layout family")
    for item in documents:
        if item["split"] != "development" or set(item["fields"]) != set(HEADER_FIELDS):
            raise ValueError(f"invalid labels for {item['id']}")
        image_path = (repo_root / item["image"]).resolve(strict=True)
        if not image_path.is_relative_to((repo_root / "samples" / "development").resolve()):
            raise ValueError("manifest image escapes the development assets")
        if hashlib.sha256(image_path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"image hash differs from manifest: {item['id']}")
        geometry = item["geometry"]
        if list(png_dimensions(image_path)) != geometry["display_size"]:
            raise ValueError(f"display size differs from image: {item['id']}")
        raw_width, raw_height = geometry["raw_size"]
        transform = DisplayTransform(raw_width, raw_height, PixelBox(**geometry["crop"]), geometry["rotation_clockwise"])
        if list(transform.display_size) != geometry["display_size"]:
            raise ValueError(f"transform size differs from image: {item['id']}")
        for key in HEADER_FIELDS:
            expected = transform.box(PixelBox(**geometry["raw_field_boxes"][key]))
            observed = _box(geometry["field_boxes"][key])
            if any(abs(a - b) > 1e-9 for a, b in zip(asdict(expected).values(), asdict(observed).values())):
                raise ValueError(f"field geometry differs from transform: {item['id']} {key}")
        if len(item["line_items"]) != len(geometry["raw_row_boxes"]) or len(item["line_items"]) != len(geometry["row_boxes"]):
            raise ValueError(f"row geometry count differs from labels: {item['id']}")
        for raw_row, display_row in zip(geometry["raw_row_boxes"], geometry["row_boxes"]):
            for key in ("description", "quantity", "unit_price", "line_total"):
                expected = transform.box(PixelBox(**raw_row[key]))
                observed = _box(display_row[key])
                if any(abs(a - b) > 1e-9 for a, b in zip(asdict(expected).values(), asdict(observed).values())):
                    raise ValueError(f"row geometry differs from transform: {item['id']} {key}")


def score_document(gold: dict, result: dict | None) -> dict:
    """Score every eligible slot; a processing failure is a missing prediction."""
    predicted = result["record"] if result is not None else None
    predicted_fields = predicted["fields"] if predicted else {}
    predicted_rows = predicted["line_items"] if predicted else []
    spans = {span["id"]: span for span in result["page"]["spans"]} if result else {}
    field_matches = {key: predicted_fields.get(key, {}).get("value") == value for key, value in gold["fields"].items()}
    field_iou = {}
    for key in HEADER_FIELDS:
        field = predicted_fields.get(key, {})
        target = _box(gold["geometry"]["field_boxes"][key])
        referenced = (spans[span_id] for span_id in field.get("evidence_ids", []) if span_id in spans)
        field_iou[key] = max((box_iou(_box(span["box"]), target) for span in referenced if span["box"]), default=0.0)
    gold_rows = gold["line_items"]
    row_amount_matches = [index < len(predicted_rows) and predicted_rows[index]["line_total"]["value"] == row["line_total"] for index, row in enumerate(gold_rows)]
    issue_codes = {issue["code"] for issue in result["issues"]} if result else set()
    return {
        "id": gold["id"],
        "family": gold["family"],
        "treatment": gold["treatment"],
        "processed": result is not None,
        "header_exact": field_matches,
        "required_all_exact": all(field_matches[key] for key in REQUIRED_FIELDS),
        "header_evidence_iou": {key: round(value, 4) for key, value in field_iou.items()},
        "gold_row_count": len(gold_rows),
        "predicted_row_count": len(predicted_rows),
        "row_amount_exact_by_order": row_amount_matches,
        "expected_issue_codes": gold["expected_issue_codes"],
        "observed_issue_codes": sorted(issue_codes),
        "total_mismatch_detected": "TOTAL_MISMATCH" in issue_codes,
        "ocr_seconds": result["runtime_seconds"]["ocr"] if result else None,
    }


def summarize_document_scores(scores: list[dict]) -> dict:
    if not scores:
        raise ValueError("cannot summarize an empty run")
    header_slots = sum(len(item["header_exact"]) for item in scores)
    required_slots = len(scores) * len(REQUIRED_FIELDS)
    row_slots = sum(item["gold_row_count"] for item in scores)
    injected = [item for item in scores if "TOTAL_MISMATCH" in item["expected_issue_codes"]]
    normal = [item for item in scores if "TOTAL_MISMATCH" not in item["expected_issue_codes"]]
    return {
        "documents_scheduled": len(scores),
        "documents_processed": sum(item["processed"] for item in scores),
        "header_exact": {"correct": sum(sum(item["header_exact"].values()) for item in scores), "eligible": header_slots},
        "required_header_exact": {
            "correct": sum(sum(item["header_exact"][key] for key in REQUIRED_FIELDS) for item in scores),
            "eligible": required_slots,
        },
        "all_required_correct": {"documents": sum(item["required_all_exact"] for item in scores), "eligible": len(scores)},
        "line_total_exact_by_order": {"correct": sum(sum(item["row_amount_exact_by_order"]) for item in scores), "eligible": row_slots},
        "row_count_exact": {"documents": sum(item["gold_row_count"] == item["predicted_row_count"] for item in scores), "eligible": len(scores)},
        "header_evidence_iou_mean_all_slots": round(sum(sum(item["header_evidence_iou"].values()) for item in scores) / header_slots, 4),
        "injected_total_conflicts_detected": {"correct": sum(item["total_mismatch_detected"] for item in injected), "eligible": len(injected)},
        "false_total_conflict_warnings": {"count": sum(item["total_mismatch_detected"] for item in normal), "eligible": len(normal)},
        "sum_ocr_seconds": round(sum(item["ocr_seconds"] or 0 for item in scores), 3),
    }


def evaluate_development(repo_root: Path, run_baseline: Callable[[Path], dict]) -> dict:
    manifest_path = repo_root / "datasets" / "development-v0.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    verify_development_manifest(manifest, repo_root)
    scores = []
    runtime_versions = None
    for gold in manifest["documents"]:
        try:
            result = run_baseline(repo_root / gold["image"])
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            score = score_document(gold, None)
            score["failure_type"] = type(exc).__name__
        else:
            score = score_document(gold, result)
            if runtime_versions is None:
                runtime_versions = {"python": result["python_version"], "tesseract": result["tesseract_version"]}
        scores.append(score)
    return {
        "mode": "fresh_development_ocr_rules",
        "baseline_version": BASELINE_VERSION,
        "dataset_id": manifest["dataset_id"],
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "runtime_versions": runtime_versions,
        "summary": summarize_document_scores(scores),
        "documents": scores,
        "interpretation": "Self-authored development data used for tuning; not held-out evidence or real-invoice accuracy.",
    }
