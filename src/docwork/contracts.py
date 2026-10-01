"""Small, strict contracts shared by OCR, extraction, and validation.

Coordinates refer to the displayed, rotation-corrected page, with a top-left
origin. These types are the Phase 0 contract; persistent schema comes later.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


HEADER_FIELDS = (
    "supplier_name",
    "invoice_number",
    "issue_date",
    "due_date",
    "currency",
    "subtotal",
    "tax",
    "discount",
    "shipping",
    "total",
)
REQUIRED_FIELDS = ("supplier_name", "invoice_number", "issue_date", "currency", "total")
CONTRACT_VERSION = "invoice-v1-spike"


@dataclass(frozen=True, slots=True)
class Box:
    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        if not (0 <= self.left < self.right <= 1 and 0 <= self.top < self.bottom <= 1):
            raise ValueError("box must be a nonempty normalized top-left rectangle")


@dataclass(frozen=True, slots=True)
class TextSpan:
    id: str
    page: int
    text: str
    box: Box | None
    method: str
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.id or self.page < 1 or not self.text.strip():
            raise ValueError("span requires an ID, page number, and text")
        if self.confidence is not None and not 0 <= self.confidence <= 100:
            raise ValueError("OCR confidence must be in [0, 100]")


@dataclass(frozen=True, slots=True)
class DocumentPage:
    number: int
    width_px: int
    height_px: int
    spans: tuple[TextSpan, ...]

    def __post_init__(self) -> None:
        if self.number < 1 or self.width_px < 1 or self.height_px < 1:
            raise ValueError("invalid page dimensions")
        ids = [span.id for span in self.spans]
        if len(ids) != len(set(ids)) or any(span.page != self.number for span in self.spans):
            raise ValueError("span IDs must be unique and belong to this page")


@dataclass(frozen=True, slots=True)
class FieldValue:
    value: str | None
    raw: str | None
    evidence_ids: tuple[str, ...] = ()
    origin: Literal["observed", "computed", "reviewer"] = "observed"
    missing_reason: str | None = None

    def __post_init__(self) -> None:
        if self.value is None and not self.missing_reason:
            raise ValueError("missing value requires a reason")
        if self.value is not None and self.missing_reason:
            raise ValueError("present value cannot have a missing reason")


@dataclass(frozen=True, slots=True)
class LineItem:
    row_id: str
    description: FieldValue
    quantity: FieldValue
    unit_price: FieldValue
    line_total: FieldValue
    tax: FieldValue


@dataclass(frozen=True, slots=True)
class InvoiceRecord:
    schema_version: str
    fields: dict[str, FieldValue]
    line_items: tuple[LineItem, ...]

    def __post_init__(self) -> None:
        if set(self.fields) != set(HEADER_FIELDS):
            raise ValueError("invoice header does not match schema")
        row_ids = [row.row_id for row in self.line_items]
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("line-item row IDs must be unique")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    path: str
    detail: str
    blocking: bool = True


def missing(reason: str = "not_observed") -> FieldValue:
    return FieldValue(value=None, raw=None, missing_reason=reason)
