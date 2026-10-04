"""Frozen production-input diagnostics and manually assessed semantic evidence.

Labels never enter extraction. A completed run accounts for failures; it does
not mean good extraction or complete human review. Historical corpora stay intact.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .contracts import HEADER_FIELDS, REQUIRED_FIELDS
from .model_runtime import file_hash, load_profile
from .ocr import MAX_FILE_BYTES
from .ocr import PNG_SIGNATURE
from .release_scoring import ROW_FIELDS, score_invoice, summarize_invoices
from .review import _hash, record_from_dict, page_from_dict

VERSION = "production-invoice-study-v1"
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
SHA = re.compile(r"[0-9a-f]{64}")
AUDIT_HEADERS = REQUIRED_FIELDS
AUDIT_ROWS = ("description", "line_total")
SEMANTICS = {"supported", "wrong_field", "ambiguous", "absent_support", "no_citation", "extraction_failed"}
GEOMETRY = {"aligned_line_region", "imprecise", "wrong_page_or_region", "unavailable", "not_applicable"}
TREATMENTS = {"clean", "degraded", "rotated", "multi_page"}
DIAGNOSTIC_CASES = ("inv-f02-02", "inv-f01-12", "inv-f03-27", "inv-f05-30", "inv-f06-04")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def bounded_file(directory: Path, relative: str) -> Path:
    if not isinstance(relative, str):
        raise ValueError("Artifact path must be text")
    name = Path(relative)
    if name.is_absolute() or not name.parts or any(part in (".", "..") for part in name.parts) or str(name) != relative:
        raise ValueError("Artifact path must be canonical and relative")
    path = directory / name
    if any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file():
        raise ValueError("Artifact must be a regular file without symlinks")
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError("Artifact escapes its directory")
    return path


def inventory(directory: Path, *, exclude: tuple[str, ...] = ()) -> dict:
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Evidence directories cannot contain symlinks")
        if path.is_file() and str(path.relative_to(directory)) not in exclude:
            result[str(path.relative_to(directory))] = file_hash(path)
    return result


def verify_inventory(directory: Path, expected: dict, *, exclude: tuple[str, ...] = ()) -> None:
    if not isinstance(expected, dict) or inventory(directory, exclude=exclude) != expected:
        raise ValueError("Artifact inventory differs")
    for name in expected:
        bounded_file(directory, name)


def validate_cases(cases: list, *, mode: str) -> None:
    if mode not in ("production", "diagnostic") or not isinstance(cases, list) or not 1 <= len(cases) <= 20:
        raise ValueError("Use 1–20 cases and an explicit production or diagnostic mode")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Case IDs must be unique")
    for case in cases:
        if not ID.fullmatch(case["id"]) or not SHA.fullmatch(case["sha256"]):
            raise ValueError("Case ID/hash is invalid")
        if not isinstance(case.get("family_group"), str) or not case["family_group"].strip():
            raise ValueError("Every case needs a source-family group")
        if case.get("capture_kind") not in ("scanner", "digital", "synthetic"):
            raise ValueError("Declare scanner, digital or synthetic acquisition")
        treatments = case.get("treatments")
        if not isinstance(treatments, list) or not treatments or not set(treatments) <= TREATMENTS:
            raise ValueError("Declare supported treatments")
        pages = case.get("page_count")
        if type(pages) is not int or not 1 <= pages <= 10 or (pages > 1) != ("multi_page" in treatments):
            raise ValueError("Page count and multi-page treatment must agree")
        permission = case.get("permission", {})
        if (permission.get("basis") not in ("self_authored", "license", "owner_permission") or
                not isinstance(permission.get("reference"), str) or not permission["reference"].strip() or
                type(permission.get("redistribution")) is not bool):
            raise ValueError("Permission basis, reference and redistribution decision are required")
        annotation = case.get("annotation", {})
        if (not isinstance(annotation.get("author"), str) or not annotation["author"].strip() or
                annotation.get("source_sha256") != case["sha256"] or
                annotation.get("method") not in ("source_inspection", "generator") or
                annotation.get("before_predictions") is not True):
            raise ValueError("Labels must bind inspected sources before predictions")
        if case["capture_kind"] != "synthetic" and annotation["method"] != "source_inspection":
            raise ValueError("Real inputs require labels from source inspection")
        if case["capture_kind"] == "scanner" and not case.get("capture_reference", "").strip():
            raise ValueError("Scanner capture needs acquisition provenance")
        # Existing frozen scoring checks types, finite amounts and eligibility.
        score_invoice(case, None)
        for name in HEADER_FIELDS:
            if case["fields"][name] is None and name in REQUIRED_FIELDS and name not in case.get("field_exclusions", {}):
                raise ValueError("Unresolvable required gold fields need declared exclusions")
        for row in case["line_items"]:
            if set(row) != set(ROW_FIELDS):
                raise ValueError("Gold rows must label every supported row field")
    if mode == "production":
        kinds = Counter(case["capture_kind"] for case in cases)
        covered = set().union(*(set(case["treatments"]) for case in cases))
        if len(cases) < 8 or kinds["scanner"] < 2 or not TREATMENTS <= covered:
            raise ValueError("Production requires at least eight cases, two genuine scans, and all four treatments")


def prepare_diagnostic(root: Path, output: Path) -> dict:
    """Known development originals to exercise tooling; never genuine-scan evidence."""
    if output.exists() or output.is_symlink():
        raise ValueError("Use a new diagnostic-spec directory")
    manifest = root / "datasets/invoices-v1/manifest.json"
    documents = {case["id"]: case for case in json.loads(manifest.read_text())["documents"]}
    cases = []
    for case_id in DIAGNOSTIC_CASES:
        gold = documents[case_id]
        if gold["split"] != "development":
            raise ValueError("Diagnostic preparation uses known development cases only")
        asset = gold["assets"][0]
        source = bounded_file(manifest.parent, asset["path"])
        if file_hash(source) != asset["sha256"]:
            raise ValueError("Diagnostic source differs from the corpus")
        relative = "inputs/" + source.name
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(source.read_bytes())
        treatments = ["clean"] if gold["treatment"] == "none" else ["degraded"]
        if case_id == "inv-f05-30":
            treatments.append("rotated")
        if len(gold["pages"]) > 1:
            treatments.append("multi_page")
        cases.append({"id": case_id, "asset": relative, "sha256": asset["sha256"],
                      "family_group": gold["family_group"], "capture_kind": "synthetic",
                      "page_count": len(gold["pages"]), "treatments": treatments,
                      "permission": {"basis": "self_authored", "reference": "Project fictional invoice corpus; generator labels",
                                     "redistribution": True},
                      "annotation": {"author": "project invoice generator", "method": "generator",
                                     "source_sha256": asset["sha256"], "before_predictions": True},
                      "fields": gold["fields"], "line_items": gold["line_items"],
                      "field_exclusions": gold.get("field_exclusions", {})})
    spec = {"study_id": "known-development-production-tooling", "documents": cases,
            "scope": "Known self-authored development cases, including prior pilot cases. No genuine capture, held-out generalization or human review claim.",
            "source_manifest_sha256": file_hash(manifest)}
    write_json(output / "spec.json", spec)
    return spec


def freeze(root: Path, spec_path: Path, output: Path, *, parser_image: str, mode: str) -> dict:
    spec = json.loads(spec_path.read_text())
    cases = spec.get("documents")
    validate_cases(cases, mode=mode)
    if not ID.fullmatch(spec.get("study_id", "")) or not re.fullmatch(r"sha256:[0-9a-f]{64}", parser_image):
        raise ValueError("Study ID and immutable parser image are required")
    if output.exists() or output.is_symlink():
        raise ValueError("Use a new freeze directory")
    profile = load_profile(root / "config/model-mac-instruct.json")
    assets = []
    for case in cases:
        source = bounded_file(spec_path.parent, case["asset"])
        if source.suffix.lower() not in (".pdf", ".png", ".jpg", ".jpeg") or not 0 < source.stat().st_size <= MAX_FILE_BYTES:
            raise ValueError("Original must be a bounded supported file")
        if file_hash(source) != case["sha256"]:
            raise ValueError("Original differs from declared hash")
        assets.append(source)
    output.mkdir(parents=True)
    documents = []
    for case, source in zip(cases, assets):
        relative = f"inputs/{case['id']}{source.suffix.lower()}"
        target = output / relative
        target.parent.mkdir(exist_ok=True)
        with target.open("xb") as stream:
            stream.write(source.read_bytes())
        documents.append({key: value for key, value in case.items() if key not in (
            "asset", "fields", "line_items", "field_exclusions") } | {"asset": relative})
    write_json(output / "labels.json", [{key: case[key] for key in (
        "id", "family_group", "fields", "line_items", "field_exclusions") if key in case} for case in cases])
    write_json(output / "profile.json", profile)
    names = sorted([*root.glob("src/docwork/*.py"), *root.glob("ui/*"), root / "scripts/production_study.py"])
    write_json(output / "source_snapshot.json", {str(path.relative_to(root)): path.read_text() for path in names})
    protocol = {"version": VERSION, "study_id": spec["study_id"], "mode": mode,
                "parser_image": parser_image, "documents": documents,
                "variants": ["ocr_rules", "span_llm"], "document_deadline_seconds": 1800,
                "audit": {"headers": list(AUDIT_HEADERS), "row_fields": list(AUDIT_ROWS),
                          "rows": "first_and_last_gold_row_using_frozen_matcher", "all_failures_included": True},
                "acceptance": "Complete schedule, preserve original suggestions and failures, assess every audit target. Diagnostic quality is reported without default promotion or a generalization claim.",
                "artifacts": inventory(output)}
    write_json(output / "protocol.json", protocol)
    return protocol


def read_protocol(directory: Path, *, root: Path | None = None) -> dict:
    protocol = json.loads(bounded_file(directory, "protocol.json").read_text())
    if protocol.get("version") != VERSION:
        raise ValueError("Unsupported production-study protocol")
    verify_inventory(directory, protocol["artifacts"], exclude=("protocol.json",))
    gold = json.loads((directory / "labels.json").read_text())
    labels = {case["id"]: case for case in gold}
    if len(labels) != len(gold) or set(labels) != {case["id"] for case in protocol["documents"]}:
        raise ValueError("Labels differ from the frozen schedule")
    validate_cases([{**case, **labels[case["id"]]} for case in protocol["documents"]], mode=protocol["mode"])
    for case in protocol["documents"]:
        if file_hash(bounded_file(directory, case["asset"])) != case["sha256"]:
            raise ValueError("Frozen asset differs from document hash")
    if protocol["audit"] != {"headers": list(AUDIT_HEADERS), "row_fields": list(AUDIT_ROWS),
                             "rows": "first_and_last_gold_row_using_frozen_matcher", "all_failures_included": True}:
        raise ValueError("Audit selection differs from protocol version")
    if protocol["variants"] != ["ocr_rules", "span_llm"] or protocol["document_deadline_seconds"] != 1800:
        raise ValueError("Run profiles/deadline differ from frozen protocol")
    if root is not None:
        snapshot = json.loads((directory / "source_snapshot.json").read_text())
        if any((root / name).read_text() != value for name, value in snapshot.items()):
            raise ValueError("Implementation changed after freeze; make a new study identity")
        if json.loads((root / "config/model-mac-instruct.json").read_text()) != json.loads((directory / "profile.json").read_text()):
            raise ValueError("Model profile changed after freeze")
    return protocol


def read_run(protocol_dir: Path, run_dir: Path) -> tuple[dict, dict, dict]:
    protocol = read_protocol(protocol_dir)
    report = json.loads(bounded_file(run_dir, "report.json").read_text())
    if (report.get("version") != VERSION or report.get("status") != "complete" or
            report.get("protocol_sha256") != file_hash(protocol_dir / "protocol.json") or
            report.get("variant") not in protocol["variants"] or report.get("parser_image") != protocol["parser_image"]):
        raise ValueError("Run is incomplete or belongs to a different protocol/profile")
    verify_inventory(run_dir, report["artifacts"], exclude=("report.json",))
    if report["variant"] == "span_llm":
        profile = load_profile(protocol_dir / "profile.json")
        runtime = report.get("model_runtime") or {}
        if (runtime.get("shutdown_complete") is not True or
                runtime.get("model_sha256") != profile["model"]["sha256"] or
                runtime.get("runtime_archive_sha256") != profile["runtime"]["sha256"] or
                runtime.get("inference") != profile["inference"]):
            raise ValueError("Run does not bind completed pinned inference")
    results = report["documents"]
    expected = [case["id"] for case in protocol["documents"]]
    if [result["id"] for result in results] != expected:
        raise ValueError("Run must account for every scheduled case exactly once in source order")
    predictions = {}
    for case, result in zip(protocol["documents"], results):
        if result.get("failure_type"):
            if not isinstance(result["failure_type"], str):
                raise ValueError("Failure category must be text")
            predictions[case["id"]] = {"record": None, "failure_type": result["failure_type"]}
            continue
        detail = json.loads(bounded_file(run_dir, result["prediction"]).read_text())
        if (detail["source_sha256"] != case["sha256"] or detail["revision"] != 1 or
                detail["current_revision"] != 1 or detail["approval"] is not None or detail["decisions"] or
                detail["record_hash"] != _hash(detail["record"]) or
                detail["extraction"]["profile"] != report["variant"] or
                result.get("parser_image") != protocol["parser_image"] or
                len(detail["pages"]) != case["page_count"]):
            raise ValueError("Original candidate/source/profile differs from the frozen schedule")
        record_from_dict(detail["record"])
        pages = [page_from_dict(page) for page in detail["pages"]]
        if [page.number for page in pages] != list(range(1, case["page_count"] + 1)):
            raise ValueError("Candidate page order differs")
        for page in pages:
            path = bounded_file(run_dir, f"pages/{case['id']}-{page.number}.png")
            if file_hash(path) != result["page_sha256"][str(page.number)]:
                raise ValueError("Rendered page differs from extraction")
            data = path.read_bytes()
            if (not data.startswith(PNG_SIGNATURE) or len(data) < 24 or
                    (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")) !=
                    (page.width_px, page.height_px)):
                raise ValueError("Rendered dimensions differ from canonical evidence")
        if report["variant"] == "span_llm":
            profile = load_profile(protocol_dir / "profile.json")
            if detail["extraction"]["model_id"] != profile["inference"]["model_id"]:
                raise ValueError("Candidate model differs from frozen model")
        predictions[case["id"]] = detail
    labels = json.loads((protocol_dir / "labels.json").read_text())
    return protocol, report, {case["id"]: (case, predictions[case["id"]]) for case in labels}


def score_run(protocol_dir: Path, run_dir: Path) -> dict:
    protocol, report, cases = read_run(protocol_dir, run_dir)
    scores = [score_invoice(gold, prediction) for gold, prediction in cases.values()]
    return {"version": VERSION, "protocol_sha256": file_hash(protocol_dir / "protocol.json"),
            "run_sha256": file_hash(run_dir / "report.json"), "mode": protocol["mode"],
            "variant": report["variant"], "original_suggestions": summarize_invoices(scores),
            "documents": scores, "approved_results": "Not collected; original predictions are never replaced."}


def audit_template(protocol_dir: Path, run_dir: Path) -> dict:
    protocol, report, cases = read_run(protocol_dir, run_dir)
    items = []
    for case_id, (gold, prediction) in cases.items():
        record = prediction.get("record")
        score = score_invoice(gold, prediction)
        matches = {item["gold_index"]: item["predicted_index"] for item in score["row_matches"]}
        targets = [(f"fields.{name}", gold["fields"][name], record["fields"][name] if record else None,
                    gold.get("field_exclusions", {}).get(name)) for name in AUDIT_HEADERS]
        for index in sorted({0, len(gold["line_items"]) - 1}):
            if index < 0 or not gold["line_items"]:
                continue
            row = record["line_items"][matches[index]] if record and index in matches else None
            for name in AUDIT_ROWS:
                targets.append((f"gold_rows.{index}.{name}", gold["line_items"][index][name],
                                row[name] if row else None, None))
        spans = {span["id"]: span for page in prediction.get("pages", []) for span in page["spans"]}
        for path, gold_value, field, exclusion in targets:
            refs = field.get("evidence_ids", []) if field else []
            items.append({"id": f"{case_id}:{path}", "document_id": case_id, "path": path,
                          "gold_value": gold_value, "excluded_from_value_scoring": exclusion,
                          "predicted_value": field["value"] if field else None,
                          "candidate_missing": field is None,
                          "extraction_failed": record is None,
                          "citations": [{"id": ref, "span": spans.get(ref)} for ref in refs],
                          "semantic_status": None, "geometry_status": None,
                          "inspected_pages": [], "rationale": ""})
    return {"version": VERSION, "protocol_sha256": file_hash(protocol_dir / "protocol.json"),
            "run_sha256": file_hash(run_dir / "report.json"), "variant": report["variant"],
            "auditor": "", "method": "manual_source_and_render_inspection", "items": items,
            "scope": "Every critical header and first/last gold-row description/amount. Matching chooses audit targets; it does not establish semantic support. Failure and absent-candidate targets remain in the denominator. Extra predicted rows are counted by extraction scoring, outside this declared semantic sample."}


def summarize_audit(protocol_dir: Path, run_dir: Path, assessment: dict) -> dict:
    template = audit_template(protocol_dir, run_dir)
    protocol = read_protocol(protocol_dir)
    if (not isinstance(assessment.get("auditor"), str) or not assessment["auditor"].strip() or
            {key: value for key, value in assessment.items() if key not in ("items", "auditor")} !=
            {key: value for key, value in template.items() if key not in ("items", "auditor")} or
            len(assessment.get("items", [])) != len(template["items"])):
        raise ValueError("Assessment must bind the exact declared sample and an auditor")
    editable = {"semantic_status", "geometry_status", "inspected_pages", "rationale"}
    for expected, item in zip(template["items"], assessment["items"]):
        if {key: value for key, value in item.items() if key not in editable} != {
                key: value for key, value in expected.items() if key not in editable}:
            raise ValueError("Assessment changed or omitted a frozen target")
        if item["semantic_status"] not in SEMANTICS or item["geometry_status"] not in GEOMETRY or not item["rationale"].strip():
            raise ValueError("Every target needs semantic/geometry classification and a rationale")
        if expected["extraction_failed"]:
            if item["semantic_status"] != "extraction_failed" or item["geometry_status"] != "not_applicable":
                raise ValueError("Failed extraction must remain a failed audit target")
        else:
            if item["semantic_status"] == "extraction_failed":
                raise ValueError("Successful extraction cannot be relabeled failed")
            if not expected["citations"] and item["semantic_status"] != "no_citation":
                raise ValueError("An absent citation cannot be labeled supported")
            if expected["citations"] and item["semantic_status"] == "no_citation":
                raise ValueError("A present citation cannot be relabeled absent")
            if item["semantic_status"] == "supported" and any(ref["span"] is None for ref in expected["citations"]):
                raise ValueError("Unknown span IDs cannot establish support")
            inspected = item["inspected_pages"]
            pages = next(case["page_count"] for case in protocol["documents"] if case["id"] == item["document_id"])
            if not isinstance(inspected, list) or not inspected or any(type(page) is not int or not 1 <= page <= pages for page in inspected):
                raise ValueError("Record the source pages inspected for each assessment")
            if item["geometry_status"] == "aligned_line_region" and (not expected["citations"] or any(
                    ref["span"] is None or ref["span"]["box"] is None for ref in expected["citations"])):
                raise ValueError("Unavailable geometry cannot be called aligned")
            if item["geometry_status"] == "aligned_line_region" and any(
                    ref["span"]["page"] not in inspected for ref in expected["citations"]):
                raise ValueError("Alignment requires inspection of every cited page")
    return {"version": VERSION, "mode": protocol["mode"],
            "variant": template["variant"], "auditor": assessment["auditor"],
            "protocol_sha256": template["protocol_sha256"], "run_sha256": template["run_sha256"],
            "assessment_sha256": _hash(assessment), "targets_scheduled": len(template["items"]),
            "semantic_counts": dict(sorted(Counter(item["semantic_status"] for item in assessment["items"]).items())),
            "geometry_counts": dict(sorted(Counter(item["geometry_status"] for item in assessment["items"]).items())),
            "scope": "Declared manual assessment, not independently verified human participation or automatic semantic verification. Counts include extraction failures and absent citations."}


def approved_results(protocol_dir: Path, run_dir: Path, store) -> dict:
    protocol, report, cases = read_run(protocol_dir, run_dir)
    results, scores = [], []
    documents = {result["id"]: result for result in report["documents"]}
    for case_id, (gold, prediction) in cases.items():
        original = prediction.get("record")
        if original is None:
            detail = {"record": None, "failure_type": prediction["failure_type"]}
        else:
            document_id = documents[case_id]["document_id"]
            initial = store.get(document_id, 1)
            if initial["record_hash"] != prediction["record_hash"] or initial["source_sha256"] != prediction["source_sha256"]:
                raise ValueError("Workbench differs from original study candidate")
            if sum(event["kind"] == "candidate_created" for event in store.history(document_id)) != 1:
                raise ValueError("Reprocessed candidates are outside this frozen review study")
            detail = store.get(document_id)
            approval = detail["approval"]
            if approval is None:
                detail = {"record": None, "failure_type": "NotApproved"}
            else:
                payload = {key: approval[key] for key in (
                    "document_id", "revision", "record_hash", "decision_hash", "policy_version", "actor")}
                payload["source_sha256"] = detail["source_sha256"]
                if (approval["record_hash"] != _hash(detail["record"]) or
                        approval["revision"] != detail["current_revision"] or
                        approval["decision_hash"] != _hash(detail["decisions"]) or
                        approval["approval_hash"] != _hash(payload)):
                    raise ValueError("Approval does not bind the reviewed current record")
        results.append({"id": case_id, "result": detail})
        scores.append(score_invoice(gold, detail))
    return {"version": VERSION, "mode": protocol["mode"], "variant": report["variant"],
            "protocol_sha256": file_hash(protocol_dir / "protocol.json"),
            "original_run_sha256": file_hash(run_dir / "report.json"),
            "original_suggestions": score_run(protocol_dir, run_dir)["original_suggestions"],
            "approved_results": summarize_invoices(scores), "documents": results,
            "scope": "Separate approved-quality snapshot; all scheduled failures and unapproved cases remain accounted for. No approval is created; original suggestions and historical studies are unchanged. Local audit actors do not independently prove human participation."}
