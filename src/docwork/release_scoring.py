"""Versioned, order-independent invoice scoring for future frozen corpora.

This module scores labels and saved candidate records. It does not run OCR or
inference, and it deliberately leaves the development-v1 scorer unchanged.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal, InvalidOperation

from .contracts import HEADER_FIELDS, REQUIRED_FIELDS

SCORING_VERSION = "invoice-release-scoring-v1"
ROW_FIELDS = ("description", "quantity", "unit_price", "line_total", "tax")
NUMERIC_FIELDS = frozenset({"subtotal", "tax", "discount", "shipping", "total",
                            "quantity", "unit_price", "line_total"})
MAX_ROWS = 200


def _normalize(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    clean = " ".join(value.split())
    if not clean:
        return None
    if name in NUMERIC_FIELDS:
        try:
            number = Decimal(clean)
        except InvalidOperation:
            return clean
        return format(number.normalize(), "f") if number.is_finite() else clean
    return clean.casefold()


def _predicted_value(field: object) -> str | None:
    if field is None:
        return None
    if not isinstance(field, dict):
        raise ValueError("Predicted fields must be objects")
    return field.get("value")


def _counts(gold: str | None, predicted: str | None) -> dict[str, int]:
    if gold is None:
        return {"tp": 0, "fp": int(predicted is not None), "fn": 0}
    if predicted == gold:
        return {"tp": 1, "fp": 0, "fn": 0}
    return {"tp": 0, "fp": int(predicted is not None), "fn": 1}


def _metrics(counts: dict[str, int]) -> dict:
    tp, fp, fn = (counts[key] for key in ("tp", "fp", "fn"))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
    return {**counts, "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None}


def _assignment(cost: list[list[int]]) -> list[int]:
    """Minimum-cost square assignment; returns a column for every row."""
    count = len(cost)
    if count == 0:
        return []
    u = [0] * (count + 1)
    v = [0] * (count + 1)
    assigned_row = [0] * (count + 1)
    previous = [0] * (count + 1)
    for row in range(1, count + 1):
        assigned_row[0] = row
        column = 0
        minimum = [float("inf")] * (count + 1)
        used = [False] * (count + 1)
        while True:
            used[column] = True
            current_row = assigned_row[column]
            delta = float("inf")
            next_column = 0
            for candidate in range(1, count + 1):
                if used[candidate]:
                    continue
                reduced = cost[current_row - 1][candidate - 1] - u[current_row] - v[candidate]
                if reduced < minimum[candidate]:
                    minimum[candidate] = reduced
                    previous[candidate] = column
                if minimum[candidate] < delta:
                    delta = minimum[candidate]
                    next_column = candidate
            for candidate in range(count + 1):
                if used[candidate]:
                    u[assigned_row[candidate]] += delta
                    v[candidate] -= delta
                else:
                    minimum[candidate] -= delta
            column = next_column
            if assigned_row[column] == 0:
                break
        while True:
            old_column = previous[column]
            assigned_row[column] = assigned_row[old_column]
            column = old_column
            if column == 0:
                break
    result = [-1] * count
    for column in range(1, count + 1):
        result[assigned_row[column] - 1] = column - 1
    return result


def _match_rows(gold: list[dict], predicted: list[dict]) -> list[tuple[int, int]]:
    count = max(len(gold), len(predicted))
    if count == 0:
        return []
    weights = {"description": 3, "quantity": 1, "unit_price": 1, "line_total": 2, "tax": 1}
    # One extra matched row always wins over a better partial match elsewhere.
    match_bonus = (sum(weights.values()) + 1) * count + 1
    benefit = [[0] * count for _ in range(count)]
    for gi, left in enumerate(gold):
        for pi, right in enumerate(predicted):
            description = left["description"] is not None and left["description"] == right["description"]
            amounts = all(left[key] is not None and left[key] == right[key]
                          for key in ("quantity", "unit_price", "line_total"))
            if description or amounts:
                benefit[gi][pi] = match_bonus + sum(weight for key, weight in weights.items()
                                                    if left[key] is not None and left[key] == right[key])
    selected = _assignment([[-value for value in row] for row in benefit])
    return [(gi, pi) for gi, pi in enumerate(selected[:len(gold)])
            if pi < len(predicted) and benefit[gi][pi] > 0]


def score_invoice(gold: dict, prediction: dict | None) -> dict:
    """Score one invoice; `None` is a failed extraction, not an omitted document."""
    if not isinstance(gold, dict) or set(gold.get("fields", {})) != set(HEADER_FIELDS):
        raise ValueError("Gold invoice must label every header field, using null for absent values")
    exclusions = gold.get("field_exclusions", {})
    if (not isinstance(exclusions, dict) or not set(exclusions).issubset(HEADER_FIELDS) or
            any(not isinstance(reason, str) or not reason.strip() for reason in exclusions.values())):
        raise ValueError("Field exclusions need named invoice fields and nonempty reasons")
    record = prediction.get("record") if prediction is not None and "record" in prediction else prediction
    if record is not None and (not isinstance(record, dict) or not isinstance(record.get("fields"), dict)):
        raise ValueError("Prediction must be an invoice record or contain a record")
    gold_rows = gold.get("line_items")
    predicted_rows = record.get("line_items", []) if record is not None else []
    if not isinstance(gold_rows, (list, tuple)) or not isinstance(predicted_rows, (list, tuple)):
        raise ValueError("Line items must be lists or tuples")
    if len(gold_rows) > MAX_ROWS or len(predicted_rows) > MAX_ROWS:
        raise ValueError("Invoice row count exceeds scoring limit")
    if not isinstance(gold.get("id"), str) or not gold["id"]:
        raise ValueError("Gold document ID is required")

    gold_fields = {name: _normalize(name, gold["fields"][name]) for name in HEADER_FIELDS}
    for name in sorted(NUMERIC_FIELDS & set(HEADER_FIELDS)):
        if gold_fields[name] is not None:
            try:
                number = Decimal(gold_fields[name])
            except InvalidOperation as exc:
                raise ValueError(f"Invalid gold amount: {name}") from exc
            if not number.is_finite():
                raise ValueError(f"Invalid gold amount: {name}")
    predicted_fields = {name: _normalize(name, _predicted_value(record["fields"].get(name)))
                        for name in HEADER_FIELDS} if record is not None else {name: None for name in HEADER_FIELDS}
    header = {name: ({"tp": 0, "fp": 0, "fn": 0} if name in exclusions else
                     _counts(gold_fields[name], predicted_fields[name])) for name in HEADER_FIELDS}

    def normalize_row(row: dict, *, candidate: bool) -> dict:
        if not isinstance(row, dict):
            raise ValueError("Each line item must be an object")
        return {name: _normalize(name, _predicted_value(row.get(name)) if candidate else row.get(name))
                for name in ROW_FIELDS}

    expected = [normalize_row(row, candidate=False) for row in gold_rows]
    actual = [normalize_row(row, candidate=True) for row in predicted_rows]
    for row in expected:
        for name in sorted(NUMERIC_FIELDS & set(ROW_FIELDS)):
            if row[name] is not None:
                try:
                    number = Decimal(row[name])
                except InvalidOperation as exc:
                    raise ValueError(f"Invalid gold row amount: {name}") from exc
                if not number.is_finite():
                    raise ValueError(f"Invalid gold row amount: {name}")
    matches = _match_rows(expected, actual)
    paired_gold = {gi for gi, _ in matches}
    paired_predicted = {pi for _, pi in matches}
    row_cells = {name: {"tp": 0, "fp": 0, "fn": 0} for name in ROW_FIELDS}
    exact_rows = 0
    for gi, pi in matches:
        exact_rows += int(all(expected[gi][name] == actual[pi][name] for name in ROW_FIELDS))
        for name in ROW_FIELDS:
            row_cells[name].update({key: row_cells[name][key] + value for key, value in
                                    _counts(expected[gi][name], actual[pi][name]).items()})
    for gi, row in enumerate(expected):
        if gi not in paired_gold:
            for name in ROW_FIELDS:
                row_cells[name]["fn"] += int(row[name] is not None)
    for pi, row in enumerate(actual):
        if pi not in paired_predicted:
            for name in ROW_FIELDS:
                row_cells[name]["fp"] += int(row[name] is not None)
    return {
        "id": gold["id"], "family_group": gold.get("family_group"),
        "processed": record is not None,
        "header": header,
        "excluded_header_fields": dict(exclusions),
        "required_all_exact": None if set(REQUIRED_FIELDS) & set(exclusions) else
                              (record is not None and all(gold_fields[name] is not None and
                                                       gold_fields[name] == predicted_fields[name]
                                                       for name in REQUIRED_FIELDS)),
        "gold_rows": len(expected), "predicted_rows": len(actual),
        "matched_rows": len(matches), "exact_rows": exact_rows,
        "row_matches": [{"gold_index": gi, "predicted_index": pi} for gi, pi in matches],
        "row_cells": row_cells,
        "failure_type": prediction.get("failure_type", "MissingPrediction")
                        if record is None and isinstance(prediction, dict) else
                        "MissingPrediction" if record is None else None,
    }


def summarize_invoices(scores: list[dict]) -> dict:
    """Aggregate a scheduled split, including failed documents in all counts."""
    if not scores or len({score["id"] for score in scores}) != len(scores):
        raise ValueError("A nonempty set of unique document scores is required")
    header = {name: {key: sum(score["header"][name][key] for score in scores)
                     for key in ("tp", "fp", "fn")} for name in HEADER_FIELDS}
    row_cells = {name: {key: sum(score["row_cells"][name][key] for score in scores)
                        for key in ("tp", "fp", "fn")} for name in ROW_FIELDS}
    row_detection = {"tp": sum(score["matched_rows"] for score in scores),
                     "fp": sum(score["predicted_rows"] - score["matched_rows"] for score in scores),
                     "fn": sum(score["gold_rows"] - score["matched_rows"] for score in scores)}
    exact_rows = {"tp": sum(score["exact_rows"] for score in scores),
                  "fp": sum(score["predicted_rows"] - score["exact_rows"] for score in scores),
                  "fn": sum(score["gold_rows"] - score["exact_rows"] for score in scores)}
    field_metrics = {name: _metrics(counts) for name, counts in header.items()}
    eligible_f1 = [2 * counts["tp"] / denominator for counts in header.values()
                   if (denominator := 2 * counts["tp"] + counts["fp"] + counts["fn"])]
    return {
        "scoring_version": SCORING_VERSION,
        "documents_scheduled": len(scores),
        "documents_processed": sum(score["processed"] for score in scores),
        "all_required_exact": {"correct": sum(score["required_all_exact"] is True for score in scores),
                               "eligible": sum(score["required_all_exact"] is not None for score in scores)},
        "header_field_exclusions": {name: sum(name in score["excluded_header_fields"] for score in scores)
                                    for name in HEADER_FIELDS},
        "header_fields": field_metrics,
        "header_macro_f1": round(sum(eligible_f1) / len(eligible_f1), 4) if eligible_f1 else None,
        "row_detection": _metrics(row_detection),
        "row_exact": _metrics(exact_rows),
        "row_fields": {name: _metrics(counts) for name, counts in row_cells.items()},
        "failures_by_type": dict(sorted(Counter(score["failure_type"] for score in scores
                                                if not score["processed"]).items())),
    }
