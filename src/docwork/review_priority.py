"""Transparent triage points for reviewer attention, never a correctness probability."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

PRIORITY_VERSION = "review-priority-v1"
WEIGHTS = {
    "REQUIRED_MISSING": 8,
    "EVIDENCE_MISSING": 6,
    "EVIDENCE_UNKNOWN": 6,
    "EVIDENCE_MISMATCH": 6,
    "HEADER_CONFLICT": 5,
    "MODEL_HEADER_CONFLICT": 5,
    "ROW_ARITHMETIC_MISMATCH": 5,
    "SUBTOTAL_MISMATCH": 5,
    "TOTAL_MISMATCH": 5,
    "AMBIGUOUS_DATE": 4,
    "DATE_ORDER": 4,
    "INVALID_MONEY": 4,
    "INVALID_ROW_AMOUNT": 4,
    "NO_LINE_ITEMS": 4,
    "UNSUPPORTED_CURRENCY": 4,
    "TOTAL_NOT_CHECKED": 2,
}
UNKNOWN_WEIGHT = 4
EXTRACTION_FAILURE_POINTS = 100


def score_review_priority(issues: Sequence[dict | str], *, failed: bool = False) -> dict:
    """Use only extractor/validator outputs; gold labels and reviewer decisions are excluded.

    Saved development runs retain issue codes, while live revisions retain full
    issue objects. Both yield the same points for the same issue sequence.
    """
    codes = []
    for issue in issues:
        code = issue if isinstance(issue, str) else issue.get("code") if isinstance(issue, dict) else None
        if not isinstance(code, str) or not code:
            raise ValueError("Review priority needs nonempty issue codes")
        codes.append(code)
    counts = Counter(codes)
    signals = [{"code": code, "count": count,
                "points": count * WEIGHTS.get(code, UNKNOWN_WEIGHT)}
               for code, count in counts.items()]
    if failed:
        signals.append({"code": "EXTRACTION_FAILED", "count": 1,
                        "points": EXTRACTION_FAILURE_POINTS})
    signals.sort(key=lambda signal: (-signal["points"], signal["code"]))
    return {"version": PRIORITY_VERSION, "points": sum(signal["points"] for signal in signals),
            "signals": signals}
