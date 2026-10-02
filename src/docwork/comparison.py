"""Audited, paired comparisons for the frozen twelve-document development set.

These gates detect development regressions; they do not certify release quality.
The entire layout family is resampled together to keep related examples paired.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from .contracts import CONTRACT_VERSION, HEADER_FIELDS, REQUIRED_FIELDS
from .evaluation import REPORT_VERSION, SCORING_VERSION, summarize_document_scores

GATED_METRICS = ("required_header_exact", "line_total_exact_by_order", "row_count_exact")
METRICS = ("header_exact", *GATED_METRICS, "all_required_correct",
           "header_evidence_iou_mean_all_slots")
ALLOWED_CHANGES = ("extractor", "runtime_versions")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _number(value: object, upper: float | None = None) -> bool:
    return (type(value) in (int, float) and math.isfinite(value) and value >= 0
            and (upper is None or value <= upper))


def audit_report(report: dict, manifest: dict, manifest_sha256: str) -> None:
    """Reject missing or internally inconsistent evidence before doing arithmetic."""
    for key, expected in {"report_version": REPORT_VERSION, "scoring_version": SCORING_VERSION,
                          "schema_version": CONTRACT_VERSION, "dataset_id": manifest["dataset_id"],
                          "split": manifest["split"], "manifest_sha256": manifest_sha256}.items():
        _require(report.get(key) == expected, f"{key} missing or incompatible; rerun evaluation")
    _require(report.get("evidence_kind") in {"fresh", "replay", "test"}, "invalid evidence_kind")
    profile = report.get("extractor")
    _require(isinstance(profile, dict) and profile.get("variant") in {"ocr_rules", "span_llm"}
             and isinstance(profile.get("version"), str) and bool(profile["version"]), "missing extractor profile")
    source_hash = profile.get("implementation_sha256")
    _require(isinstance(source_hash, str) and len(source_hash) == 64
             and all(char in "0123456789abcdef" for char in source_hash), "missing pipeline source fingerprint")
    if profile["variant"] == "span_llm":
        _require(all(profile.get(key) for key in ("model_id", "prompt_sha256", "max_output_tokens",
                                                  "timeout_seconds")), "incomplete model profile")
    _require(isinstance(report.get("environment"), dict) and bool(report["environment"]),
             "missing execution environment")
    _require(isinstance(report.get("documents"), list), "documents must be a list")
    docs = report["documents"]
    gold_by_id = {doc["id"]: doc for doc in manifest["documents"]}
    ids = [doc["id"] for doc in docs]
    _require(len(ids) == len(set(ids)) and set(ids) == set(gold_by_id),
             "scheduled document IDs differ from manifest or contain duplicates")
    for doc in docs:
        gold = gold_by_id[doc["id"]]
        for key in ("family", "family_group", "treatment", "expected_issue_codes"):
            _require(doc.get(key) == gold[key], f"{doc['id']}: {key} differs from manifest")
        _require(type(doc["processed"]) is bool, "processed must be boolean")
        _require(set(doc["header_exact"]) == set(HEADER_FIELDS)
                 and all(type(value) is bool for value in doc["header_exact"].values()),
                 "invalid header eligibility or scores")
        _require(type(doc["required_all_exact"]) is bool and doc["required_all_exact"] == all(
            doc["header_exact"][key] for key in REQUIRED_FIELDS), "inconsistent required-field score")
        _require(set(doc["header_evidence_iou"]) == set(HEADER_FIELDS)
                 and all(_number(value, 1) for value in doc["header_evidence_iou"].values()),
                 "invalid evidence overlap")
        _require(type(doc["gold_row_count"]) is int and doc["gold_row_count"] == len(gold["line_items"]),
                 "row eligibility differs from manifest")
        _require(type(doc["predicted_row_count"]) is int and doc["predicted_row_count"] >= 0,
                 "invalid predicted row count")
        matches = doc["row_amount_exact_by_order"]
        _require(isinstance(matches, list) and len(matches) == doc["gold_row_count"]
                 and all(type(value) is bool for value in matches), "invalid row scores")
        _require(not any(matches[doc["predicted_row_count"]:]), "missing rows cannot have correct values")
        _require(isinstance(doc["observed_issue_codes"], list)
                 and all(isinstance(code, str) for code in doc["observed_issue_codes"]), "invalid issue codes")
        _require(type(doc["total_mismatch_detected"]) is bool and doc["total_mismatch_detected"] == (
            "TOTAL_MISMATCH" in doc["observed_issue_codes"]), "inconsistent conflict detection")
        for key in ("ocr_seconds", "model_seconds"):
            _require(doc.get(key) is None or _number(doc[key]), "invalid stage timing")
        if not doc["processed"]:
            _require(not any(doc["header_exact"].values()) and not any(matches)
                     and doc["predicted_row_count"] == 0
                     and not any(doc["header_evidence_iou"].values())
                     and not doc["observed_issue_codes"], "failed document contains successful predictions")
        else:
            _require(not doc.get("failure_type"), "processed document also reports failure")
    _require(report.get("summary") == summarize_document_scores(docs), "summary differs from document scores")
    if any(doc["processed"] for doc in docs):
        versions = report.get("runtime_versions")
        _require(isinstance(versions, dict) and all(versions.get(key) for key in ("python", "tesseract")),
                 "missing runtime versions")


def _rates(docs: list[dict]) -> dict[str, float]:
    summary = summarize_document_scores(docs)
    rates = {}
    for metric in METRICS:
        value = summary[metric]
        rates[metric] = (value.get("correct", value.get("documents")) / value["eligible"]
                         if isinstance(value, dict) else value)
    return rates


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def compare_reports(baseline: dict, candidate: dict, manifest: dict, manifest_sha256: str, *,
                    allow_changes: tuple[str, ...] = (), max_regression: float = .02,
                    bootstrap_samples: int = 2000, seed: int = 1729) -> dict:
    """Return pass, regression, or unusable_evidence, including diagnostic reasons."""
    if not _number(max_regression, 1):
        raise ValueError("max_regression must be a finite fraction in [0, 1]")
    if type(bootstrap_samples) is not int or not 100 <= bootstrap_samples <= 10000:
        raise ValueError("bootstrap_samples must be between 100 and 10000")
    if set(allow_changes) - set(ALLOWED_CHANGES):
        raise ValueError("unknown declared treatment change")
    result = {
        "comparison_version": "development-comparison-v1", "status": "unusable_evidence",
        "dataset_id": manifest["dataset_id"], "split": manifest["split"],
        "manifest_sha256": manifest_sha256, "reasons": [],
        "policy": {"max_absolute_regression": max_regression, "gated_metrics": list(GATED_METRICS),
                   "declared_changes": sorted(set(allow_changes)),
                   "decision": "point-estimate regression; confidence intervals are descriptive"},
        "interpretation": "Development diagnostics only; not held-out quality, release acceptance, or real-invoice accuracy.",
    }
    for name, report in (("baseline", baseline), ("candidate", candidate)):
        try:
            audit_report(report, manifest, manifest_sha256)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            result["reasons"].append(f"{name}: {exc}")
    if result["reasons"]:
        return result
    if baseline["evidence_kind"] != "fresh" or candidate["evidence_kind"] != "fresh":
        result["reasons"].append("Only two fresh runs can support this quality gate; replay/test evidence is excluded.")
    changes = [key for key in ALLOWED_CHANGES if baseline.get(key) != candidate.get(key)]
    result["actual_changes"] = changes
    for key in changes:
        if key not in allow_changes:
            result["reasons"].append(f"Undeclared treatment change: {key}")
    for name, report in (("baseline", baseline), ("candidate", candidate)):
        if not report["summary"]["documents_processed"]:
            result["reasons"].append(f"{name}: no successfully processed documents")
    if result["reasons"]:
        return result

    before = {doc["id"]: doc for doc in baseline["documents"]}
    after = {doc["id"]: doc for doc in candidate["documents"]}
    groups = defaultdict(list)
    for doc in manifest["documents"]:
        groups[doc["family_group"]].append(doc["id"])
    group_ids = sorted(groups)
    rng = random.Random(seed)
    samples = {metric: [] for metric in METRICS}
    for _ in range(bootstrap_samples):
        ids = [doc_id for _ in group_ids for doc_id in groups[rng.choice(group_ids)]]
        left, right = _rates([before[key] for key in ids]), _rates([after[key] for key in ids])
        for metric in METRICS:
            samples[metric].append(right[metric] - left[metric])
    left, right = _rates(list(before.values())), _rates(list(after.values()))
    result["metrics"] = {metric: {
        "baseline": left[metric], "candidate": right[metric], "delta": right[metric] - left[metric],
        "delta_ci95": [_percentile(samples[metric], .025), _percentile(samples[metric], .975)],
    } for metric in METRICS}
    result["bootstrap"] = {"unit": "layout_family", "groups": len(groups),
                           "samples": bootstrap_samples, "seed": seed,
                           "method": "paired percentile; related documents remain in the same family",
                           "limitation": "Six author-created families do not represent arbitrary unseen vendors."}
    result["by_family"] = [{"family": before[ids[0]]["family"], "documents": len(ids),
                            "baseline": _rates([before[key] for key in ids]),
                            "candidate": _rates([after[key] for key in ids])}
                           for _, ids in sorted(groups.items())]
    result["summaries"] = {"baseline": baseline["summary"], "candidate": candidate["summary"]}
    result["extractors"] = {"baseline": baseline["extractor"], "candidate": candidate["extractor"]}
    result["failures"] = {name: [{"id": doc["id"], "failure_type": doc.get("failure_type", "MissingPrediction")}
                                for doc in report["documents"] if not doc["processed"]]
                          for name, report in (("baseline", baseline), ("candidate", candidate))}
    result["timing_note"] = (
        "Stage totals are recorded in the input summaries. No latency gate or speedup is claimed: "
        "hardware capacity, inference runtime, warmup, and failed-stage timing are not fully captured."
    )
    regressions = [metric for metric in GATED_METRICS
                   if right[metric] - left[metric] < -max_regression - 1e-12]
    if candidate["summary"]["documents_processed"] < baseline["summary"]["documents_processed"]:
        regressions.append("documents_processed")
    result["status"] = "regression" if regressions else "pass"
    result["reasons"] = [f"Regression: {metric}" for metric in regressions]
    return result


def compare_files(baseline_path: Path, candidate_path: Path, repo_root: Path, **options) -> dict:
    manifest_bytes = (repo_root / "datasets" / "development-v0.json").read_bytes()
    inputs = {}
    reports = []
    for name, path in (("baseline", baseline_path), ("candidate", candidate_path)):
        data = path.read_bytes()
        reports.append(json.loads(data))
        inputs[name] = {"name": path.name, "sha256": hashlib.sha256(data).hexdigest()}
    result = compare_reports(*reports, json.loads(manifest_bytes), hashlib.sha256(manifest_bytes).hexdigest(), **options)
    result["inputs"] = inputs
    return result


def render_html(report: dict) -> str:
    """A portable, script-free report with escaped input labels."""
    escape = lambda value: html.escape(str(value), quote=True)
    parts = ["<!doctype html><html lang='en'><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<title>Document extraction comparison</title><style>",
             "body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 24px;color:#152b39;background:#f7f9fb}",
             "table{border-collapse:collapse;width:100%;background:white;margin:20px 0}th,td{padding:12px;text-align:left;border-bottom:1px solid #dce3e8}",
             "th{background:#eaf0f4}code{overflow-wrap:anywhere}p{line-height:1.6}caption{text-align:left;font-weight:600;padding:10px 0}",
             "</style><h1>Document extraction comparison</h1>",
             f"<p><strong>Gate: {escape(report['status'])}</strong> · {escape(report['split'])}</p>",
             f"<p>{escape(report['interpretation'])}</p>"]
    for reason in report["reasons"]:
        parts.append(f"<p>{escape(reason)}</p>")
    if "metrics" in report:
        parts.append("<table><caption>All scheduled documents; delta = candidate − baseline</caption>"
                     "<tr><th>Metric</th><th>Baseline</th><th>Candidate</th><th>Delta</th><th>95% paired interval</th></tr>")
        for metric, value in report["metrics"].items():
            low, high = value["delta_ci95"]
            parts.append(f"<tr><th>{escape(metric)}</th><td>{value['baseline']:.3f}</td>"
                         f"<td>{value['candidate']:.3f}</td><td>{value['delta']:+.3f}</td>"
                         f"<td>[{low:+.3f}, {high:+.3f}]</td></tr>")
        parts.append("</table><p>Intervals resample six layout families together. They describe this development set; "
                     "they do not establish generalization to unseen vendors.</p>")
        parts.append("<table><caption>Required header exact match by layout</caption>"
                     "<tr><th>Layout</th><th>Documents</th><th>Baseline</th><th>Candidate</th></tr>")
        for family in report["by_family"]:
            parts.append(f"<tr><th>{escape(family['family'])}</th><td>{family['documents']}</td>"
                         f"<td>{family['baseline']['required_header_exact']:.3f}</td>"
                         f"<td>{family['candidate']['required_header_exact']:.3f}</td></tr>")
        parts.append("</table><h2>Failures and eligibility</h2>")
        for name, summary in report["summaries"].items():
            parts.append(f"<p>{escape(name)}: {summary['documents_processed']} / {summary['documents_scheduled']} processed. "
                         f"Failures: {escape(json.dumps(summary['failures_by_type'], sort_keys=True))}</p>")
        parts.append(f"<p>{escape(report['timing_note'])}</p>")
    parts.append("<details><summary>Complete comparison, policy, and provenance</summary><pre><code>"
                 + escape(json.dumps(report, indent=2)) + "</code></pre></details></html>\n")
    return "".join(parts)
