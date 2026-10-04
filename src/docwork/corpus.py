"""Integrity and label checks for the frozen self-authored invoice corpus."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

from .ocr import png_dimensions
from .release_evaluation import verify_invoice_manifest

CORPUS_ID = "self-authored-invoices-v1"
TREATMENTS = frozenset({"blur", "low_contrast", "jpeg_compression", "skew_2deg", "rotate_90ccw"})


def _check_box(box: dict, document_id: str) -> None:
    if (not isinstance(box, dict) or set(box) != {"left", "top", "right", "bottom"}
            or not 0 <= box["left"] < box["right"] <= 1
            or not 0 <= box["top"] < box["bottom"] <= 1):
        raise ValueError(f"Invalid source box: {document_id}")


def verify_synthetic_corpus(manifest_path: Path) -> dict:
    """Verify all 540 assets, split groups, parent links, arithmetic, and boxes."""
    manifest_path = manifest_path.resolve(strict=True)
    encoded = manifest_path.read_bytes()
    manifest = json.loads(encoded)
    verify_invoice_manifest(manifest, manifest_path)
    if manifest["dataset_id"] != CORPUS_ID or not manifest.get("author_created"):
        raise ValueError("Expected the self-authored invoice corpus")
    documents = manifest["documents"]
    counts = Counter(document["split"] for document in documents)
    if counts != {"development": 180, "calibration": 180, "test": 180}:
        raise ValueError("Corpus must have 180 documents per split")
    families: dict[tuple[str, str], list[dict]] = defaultdict(list)
    by_id = {document["id"]: document for document in documents}
    for document in documents:
        families[(document["split"], document["family_group"])].append(document)
        fields = document["fields"]
        subtotal = sum((Decimal(row["line_total"]) for row in document["line_items"]), Decimal(0))
        if subtotal != Decimal(fields["subtotal"]):
            raise ValueError(f"Row sum differs from subtotal: {document['id']}")
        for row in document["line_items"]:
            if Decimal(row["quantity"]) * Decimal(row["unit_price"]) != Decimal(row["line_total"]):
                raise ValueError(f"Row arithmetic differs from labels: {document['id']}")
        declared = subtotal + Decimal(fields["tax"] or "0") - Decimal(fields["discount"] or "0") + Decimal(fields["shipping"] or "0")
        conflict = "TOTAL_MISMATCH" in document["expected_issue_codes"]
        if conflict and any(fields[name] is None for name in ("subtotal", "tax", "discount", "shipping", "total")):
            raise ValueError(f"Total conflict lacks a printed component: {document['id']}")
        if Decimal(fields["total"]) - declared != (Decimal(5) if conflict else Decimal(0)):
            raise ValueError(f"Total conflict label differs from arithmetic: {document['id']}")
        ambiguous = "AMBIGUOUS_DATE" in document["expected_issue_codes"]
        expected_exclusions = {"issue_date": "ambiguous_printed_date"} if ambiguous else {}
        if document.get("field_exclusions") != expected_exclusions:
            raise ValueError(f"Ambiguous date eligibility differs from label: {document['id']}")
        pages = document["pages"]
        if (len(pages) not in (1, 2) or
                [page["number"] for page in pages] != list(range(1, len(pages) + 1))):
            raise ValueError(f"Invalid page count or order: {document['id']}")
        covered_rows = []
        field_evidence: set[str] = set()
        for page in pages:
            if (page["width_px"] <= 0 or page["height_px"] <= 0 or
                    page["width_px"] * page["height_px"] > 20_000_000):
                raise ValueError(f"Invalid page size: {document['id']}")
            for name, box in page["field_boxes"].items():
                if box is not None:
                    _check_box(box, document["id"])
                    field_evidence.add(name)
            for entry in page["row_boxes"]:
                covered_rows.append(entry["row_index"])
                if set(entry["boxes"]) != {"description", "quantity", "unit_price", "line_total"}:
                    raise ValueError(f"Incomplete row boxes: {document['id']}")
                for box in entry["boxes"].values():
                    _check_box(box, document["id"])
        if sorted(covered_rows) != list(range(len(document["line_items"]))):
            raise ValueError(f"Rows lack unique page evidence: {document['id']}")
        if any(name not in field_evidence for name, value in fields.items() if value is not None):
            raise ValueError(f"Nonempty field lacks a source box: {document['id']}")
        if len(pages) == 2 and not document["assets"][0]["path"].endswith(".pdf"):
            raise ValueError(f"Multi-page source must be PDF: {document['id']}")
        assets = document["assets"]
        if len(pages) == 1:
            if len(assets) != 1 or not assets[0]["path"].endswith(".png"):
                raise ValueError(f"Single-page source must be one PNG: {document['id']}")
            previews = assets
        else:
            if len(assets) != len(pages) + 1 or not (manifest_path.parent / assets[0]["path"]).read_bytes().startswith(b"%PDF-"):
                raise ValueError(f"Multi-page source lacks PDF or page previews: {document['id']}")
            previews = assets[1:]
        for page, asset in zip(pages, previews):
            if (not asset["path"].endswith(".png") or
                    png_dimensions(manifest_path.parent / asset["path"]) !=
                    (page["width_px"], page["height_px"])):
                raise ValueError(f"Page preview dimensions differ from labels: {document['id']}")
        parent_id = document["parent_id"]
        if parent_id is not None:
            parent = by_id.get(parent_id)
            if (parent is None or parent["parent_id"] is not None or
                    parent["split"] != document["split"] or
                    parent["family_group"] != document["family_group"] or
                    parent["fields"] != fields or parent["line_items"] != document["line_items"] or
                    parent["field_exclusions"] != document["field_exclusions"] or
                    parent["expected_issue_codes"] != document["expected_issue_codes"] or
                    len(parent["pages"]) != len(pages)):
                raise ValueError(f"Derivative does not match parent: {document['id']}")
    groups_by_split = Counter(split for split, _ in families)
    if groups_by_split != {"development": 6, "calibration": 6, "test": 6}:
        raise ValueError("Corpus must have six families per split")
    for (split, family), group in families.items():
        if len(group) != 30 or sum(item["parent_id"] is None for item in group) != 25:
            raise ValueError(f"Family needs 25 bases and five derivatives: {split}/{family}")
        if {item["treatment"] for item in group if item["parent_id"] is not None} != TREATMENTS:
            raise ValueError(f"Family lacks a degradation treatment: {split}/{family}")
    return {
        "dataset_id": CORPUS_ID,
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
        "documents": len(documents), "split_counts": dict(counts),
        "families": len(families),
        "multi_page_documents": sum(len(document["pages"]) == 2 for document in documents),
        "degraded_documents": sum(document["parent_id"] is not None for document in documents),
        "intentional_total_conflicts": sum("TOTAL_MISMATCH" in document["expected_issue_codes"] for document in documents),
        "ambiguous_date_cases": sum("AMBIGUOUS_DATE" in document["expected_issue_codes"] for document in documents),
    }
