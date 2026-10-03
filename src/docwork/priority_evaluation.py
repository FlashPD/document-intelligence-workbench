"""Offline risk-versus-coverage diagnostic on verified invoice predictions."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from .invoice_run import verify_invoice_run
from .review import _atomic_write
from .review_priority import PRIORITY_VERSION, score_review_priority

REPORT_VERSION = "invoice-review-priority-report-v1"
ONE_SIDED_95_Z = 1.6448536269514722
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 1729


def _wilson_upper(errors: int, total: int) -> float | None:
    if total == 0:
        return None
    rate = errors / total
    z2 = ONE_SIDED_95_Z**2
    denominator = 1 + z2 / total
    center = (rate + z2 / (2 * total)) / denominator
    margin = ONE_SIDED_95_Z * math.sqrt(rate * (1 - rate) / total + z2 / (4 * total**2)) / denominator
    return round(min(1.0, center + margin), 4)


def _parent_bootstrap_p95(documents: list[dict], threshold: int) -> float:
    """Resample parent groups within each fixed layout family."""
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for document in documents:
        grouped[document["family_group"]][document["parent_group"]].append(document)
    families = [list(groups.values()) for _, groups in sorted(grouped.items())]
    rng = random.Random(BOOTSTRAP_SEED)
    rates = []
    for _ in range(BOOTSTRAP_SAMPLES):
        errors = eligible = 0
        for groups in families:
            for _ in groups:
                for document in rng.choice(groups):
                    if (document["priority"]["points"] <= threshold
                            and document["required_all_exact"] is not None):
                        eligible += 1
                        errors += document["required_all_exact"] is False
        rates.append(errors / eligible if eligible else 1.0)
    rates.sort()
    return round(rates[math.ceil(.95 * len(rates)) - 1], 4)


def priority_report(manifest_path: Path, run_dir: Path) -> dict:
    """Use predictions to rank; use labels only to evaluate the fixed ranking."""
    verify_invoice_run(manifest_path, run_dir)
    run_dir = run_dir.resolve(strict=True)
    saved_bytes = (run_dir / "report.json").read_bytes()
    saved = json.loads(saved_bytes)
    split = saved["run_identity"]["split"]
    scored = {document["id"]: document for document in saved["documents"]}
    manifest = json.loads(manifest_path.read_text())
    metadata = {document["id"]: document for document in manifest["documents"]
                if document["split"] == split}
    if set(metadata) != set(scored) or any(
        document["parent_id"] and (document["parent_id"] not in metadata or
                                   metadata[document["parent_id"]]["family_group"] != document["family_group"])
        for document in metadata.values()
    ):
        raise ValueError("Parent groups differ from the verified score set")
    documents = []
    issue_detection = {code: {"expected": 0, "flagged": 0, "missed": 0, "false_flags": 0}
                       for code in ("TOTAL_MISMATCH", "AMBIGUOUS_DATE")}
    issue_detection["TOTAL_MISMATCH"]["not_checked_when_missed"] = 0
    for identifier in sorted(scored):
        prediction = json.loads((run_dir / "predictions" / f"{identifier}.json").read_text())
        issue_codes = prediction.get("issue_codes", [])
        if not isinstance(issue_codes, list) or (prediction.get("record") is not None
                                                  and "issue_codes" not in prediction):
            raise ValueError(f"Prediction lacks review issue evidence: {identifier}")
        priority = score_review_priority(issue_codes, failed=prediction.get("record") is None)
        score = scored[identifier]
        expected_codes = set(metadata[identifier].get("expected_issue_codes", ()))
        observed_codes = set(issue_codes)
        for code, counts in issue_detection.items():
            expected = code in expected_codes
            flagged = code in observed_codes
            counts["expected"] += expected
            counts["flagged"] += expected and flagged
            counts["missed"] += expected and not flagged
            counts["false_flags"] += flagged and not expected
            if code == "TOTAL_MISMATCH":
                counts["not_checked_when_missed"] += (expected and not flagged
                                                       and "TOTAL_NOT_CHECKED" in observed_codes)
        documents.append({
            "id": identifier, "family_group": metadata[identifier]["family_group"],
            "parent_group": metadata[identifier]["parent_id"] or identifier,
            "priority": priority,
            "required_all_exact": score["required_all_exact"],
            "row_exact": (score["gold_rows"] == score["exact_rows"] == score["predicted_rows"]),
        })
    thresholds = sorted({0, *(document["priority"]["points"] for document in documents)})
    curve = []
    for threshold in thresholds:
        accepted = [document for document in documents if document["priority"]["points"] <= threshold]
        eligible = [document for document in accepted if document["required_all_exact"] is not None]
        errors = sum(document["required_all_exact"] is False for document in eligible)
        curve.append({
            "max_triage_points": threshold,
            "accepted": len(accepted),
            "coverage": round(len(accepted) / len(documents), 4),
            "manual_review": len(documents) - len(accepted),
            "critical_label_eligible": len(eligible),
            "critical_label_unknown": len(accepted) - len(eligible),
            "critical_errors": errors,
            "critical_error_rate": round(errors / len(eligible), 4) if eligible else None,
            "critical_error_upper_95_independent_docs": _wilson_upper(errors, len(eligible)),
            "critical_error_parent_bootstrap_p95": _parent_bootstrap_p95(documents, threshold),
            "row_errors": sum(not document["row_exact"] for document in accepted),
        })
    return {
        "report_version": REPORT_VERSION,
        "priority_version": PRIORITY_VERSION,
        "priority_implementation_sha256": hashlib.sha256(
            Path(__file__).with_name("review_priority.py").read_bytes()).hexdigest(),
        "evaluation_implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "bootstrap": {"samples": BOOTSTRAP_SAMPLES, "seed": BOOTSTRAP_SEED,
                      "grouping": "parent_id within fixed family_group"},
        "input_mode": f"verified_{split}_png_previews",
        "run_identity": saved["run_identity"],
        "source_report_sha256": hashlib.sha256(saved_bytes).hexdigest(),
        "documents_scheduled": len(documents),
        "injected_issue_detection": issue_detection,
        "documents": documents,
        "curve": curve,
    }


def write_priority_report(manifest_path: Path, run_dir: Path, output: Path) -> dict:
    report = priority_report(manifest_path, run_dir)
    encoded = (json.dumps(report, indent=2) + "\n").encode("utf-8")
    _atomic_write(output, encoded)
    return report


def verify_priority_report(manifest_path: Path, run_dir: Path, report_path: Path) -> dict:
    expected = priority_report(manifest_path, run_dir)
    actual = json.loads(report_path.read_text())
    if actual != expected:
        raise ValueError("Saved review priority report differs from verified predictions or implementation")
    return {"status": "verified", "documents": expected["documents_scheduled"],
            "source_report_sha256": expected["source_report_sha256"]}
