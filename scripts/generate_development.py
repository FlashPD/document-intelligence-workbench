"""Generate 12 fictional development invoices and separate gold labels.

This author-created corpus is for feasibility and error analysis only. The
committed PNGs and manifest are the frozen inputs; rerendering can change
pixels with a different Pillow or font version.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from docwork.geometry import DisplayTransform, PixelBox  # noqa: E402

OUT = ROOT / "samples" / "development"
MANIFEST = ROOT / "datasets" / "development-v0.json"
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial.ttf"
FAMILIES = (
    "classic",
    "right_header",
    "compact",
    "two_column",
    "summary_first",
    "degraded_orientation",
)
SUPPLIERS = (
    "Aster Studio LLC",
    "Juniper Labs",
    "Cobalt Paper Co",
    "Maple Lantern Works",
    "Fable Transit Design",
    "Northline Atelier",
)


class InvoiceCanvas:
    def __init__(self, family: int) -> None:
        self.family = family
        self.width = 1200 if family == 5 else 1500
        self.height = 2000 if family == 5 else 1900
        self.image = Image.new("RGB", (self.width, self.height), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.body = ImageFont.truetype(FONT_PATH, 40 if family != 5 else 36)
        self.heading = ImageFont.truetype(FONT_PATH, 64 if family != 5 else 56)
        self.field_boxes: dict[str, PixelBox] = {}
        self.row_boxes: list[dict[str, PixelBox]] = []

    def text(self, value: str, x: int, y: int, *, heading: bool = False) -> PixelBox:
        font = self.heading if heading else self.body
        self.draw.text((x, y), value, font=font, fill="black")
        left, top, right, bottom = self.draw.textbbox((x, y), value, font=font)
        return PixelBox(left, top, right, bottom)

    def field(self, key: str, label: str, value: str, x: int, y: int, *, colon: bool = True) -> None:
        prefix = f"{label}: " if colon else f"{label}  "
        self.text(prefix + value, x, y)
        value_x = x + self.draw.textlength(prefix, font=self.body)
        left, top, right, bottom = self.draw.textbbox((value_x, y), value, font=self.body)
        self.field_boxes[key] = PixelBox(left, top, right, bottom)

    def row(self, values: dict[str, str], y: int) -> None:
        positions = (65, 620, 805, 1030) if self.family == 5 else (95, 745, 960, 1210)
        row_boxes = {}
        for name, x in zip(("description", "quantity", "unit_price", "line_total"), positions):
            row_boxes[name] = self.text(values[name], x, y)
        self.row_boxes.append(row_boxes)


def invoice_values(family: int, variant: int) -> tuple[dict[str, str], list[dict[str, str]], bool]:
    issue = date(2026, 9, 1 + family * 2 + variant)
    due = date(2026, 10, 1 + family * 2 + variant)
    unit_price = Decimal(75 + family * 10 + variant * 5)
    quantity = Decimal(2 if variant == 0 else 3)
    rows = [{
        "description": "Research workshop" if family != 4 else "Design review",
        "quantity": str(quantity),
        "unit_price": f"{unit_price:.2f}",
        "line_total": f"{quantity * unit_price:.2f}",
    }]
    if family in (1, 3, 4):
        rows.append({
            "description": "Archive prep" if family != 4 else "Design review",
            "quantity": "1",
            "unit_price": "45.00",
            "line_total": "45.00",
        })
    subtotal = sum(Decimal(row["line_total"]) for row in rows)
    tax = Decimal(10 + family)
    discount = Decimal("5.00") if family == 3 and variant == 1 else Decimal("0.00")
    conflict = family == 4 and variant == 1
    total = subtotal + tax - discount + (Decimal("5.00") if conflict else 0)
    fields = {
        "supplier_name": SUPPLIERS[family],
        "invoice_number": f"F{family + 1}-{variant + 1:03d}",
        "issue_date": issue.isoformat(),
        "due_date": due.isoformat(),
        "currency": "USD",
        "subtotal": f"{subtotal:.2f}",
        "tax": f"{tax:.2f}",
        "discount": f"{discount:.2f}",
        "shipping": "0.00",
        "total": f"{total:.2f}",
    }
    return fields, rows, conflict


def render(family: int, variant: int) -> dict:
    values, rows, conflict = invoice_values(family, variant)
    canvas = InvoiceCanvas(family)
    vendor_x, vendor_y = (65, 95) if family == 5 else (95, 90)
    canvas.field_boxes["supplier_name"] = canvas.text(values["supplier_name"], vendor_x, vendor_y, heading=True)
    title_x, title_y = (65, 205) if family == 5 else ((980, 90) if family in (1, 3) else (95, 195))
    canvas.text("INVOICE", title_x, title_y, heading=True)

    if family == 0:
        meta = [("invoice_number", 95, 330), ("issue_date", 95, 405), ("due_date", 95, 480), ("currency", 95, 555)]
        summary_y, table_y, summary_x = 1160, 760, 95
    elif family == 1:
        meta = [("invoice_number", 780, 280), ("issue_date", 780, 355), ("due_date", 780, 430), ("currency", 780, 505)]
        summary_y, table_y, summary_x = 1180, 730, 780
    elif family == 2:
        meta = [("invoice_number", 95, 295), ("issue_date", 95, 370), ("due_date", 95, 445), ("currency", 95, 520)]
        summary_y, table_y, summary_x = 1290, 870, 95
    elif family == 3:
        meta = [("invoice_number", 95, 280), ("issue_date", 95, 355), ("due_date", 95, 430), ("currency", 820, 280)]
        summary_y, table_y, summary_x = 1180, 710, 820
    elif family == 4:
        meta = [("invoice_number", 95, 300), ("issue_date", 95, 375), ("due_date", 95, 450), ("currency", 95, 525)]
        summary_y, table_y, summary_x = 670, 1130, 820
    else:
        meta = [("invoice_number", 65, 325), ("issue_date", 65, 400), ("due_date", 65, 475), ("currency", 65, 550)]
        summary_y, table_y, summary_x = 1330, 790, 65

    labels = {"invoice_number": "Invoice Number", "issue_date": "Date", "due_date": "Due Date", "currency": "Currency"}
    for name, x, y in meta:
        rendered = values[name]
        if family == 5 and name in ("issue_date", "due_date"):
            year, month, day = rendered.split("-")
            rendered = f"{month}/{day}/{year}"
        canvas.field(name, labels[name], rendered, x, y, colon=family != 3)

    if family in (3, 4):
        canvas.draw.rectangle((70, table_y - 20, canvas.width - 60, table_y + 105 + 100 * len(rows)), outline="#777777", width=2)
    header = {"description": "Description", "quantity": "Qty", "unit_price": "Unit Price", "line_total": "Line Total"}
    positions = (65, 620, 805, 1030) if family == 5 else (95, 745, 960, 1210)
    for key, x in zip(header, positions):
        canvas.text(header[key], x, table_y)
    for row_index, row in enumerate(rows):
        canvas.row(row, table_y + 100 + row_index * 100)

    for index, (name, label) in enumerate((("subtotal", "Subtotal"), ("tax", "Tax"), ("discount", "Discount"), ("shipping", "Shipping"), ("total", "Total"))):
        canvas.field(name, label, values[name], summary_x, summary_y + index * 75)

    crop = PixelBox(0, 0, canvas.width, canvas.height)
    rotation = 0
    image = canvas.image
    if family == 5 and variant == 0:
        image = ImageEnhance.Contrast(image.filter(ImageFilter.GaussianBlur(.7))).enhance(.78)
    if family == 5 and variant == 1:
        crop = PixelBox(20, 20, canvas.width - 20, canvas.height - 20)
        image = image.crop((20, 20, canvas.width - 20, canvas.height - 20)).rotate(90, expand=True)
        rotation = 270
    transform = DisplayTransform(canvas.width, canvas.height, crop, rotation)
    assert image.size == tuple(int(size) for size in transform.display_size)
    OUT.mkdir(parents=True, exist_ok=True)
    name = f"dev-{family + 1:02d}-{variant + 1:02d}.png"
    path = OUT / name
    image.save(path, optimize=True)

    return {
        "id": name.removesuffix(".png"),
        "family": FAMILIES[family],
        "family_group": f"family-{family + 1:02d}",
        "split": "development",
        "image": f"samples/development/{name}",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "fields": values,
        "line_items": rows,
        "expected_issue_codes": ["TOTAL_MISMATCH"] if conflict else [],
        "geometry": {
            "raw_size": [canvas.width, canvas.height],
            "display_size": list(image.size),
            "crop": asdict(crop),
            "rotation_clockwise": rotation,
            "raw_field_boxes": {key: asdict(box) for key, box in canvas.field_boxes.items()},
            "raw_row_boxes": [{key: asdict(box) for key, box in row.items()} for row in canvas.row_boxes],
            "field_boxes": {key: asdict(transform.box(box)) for key, box in canvas.field_boxes.items()},
            "row_boxes": [{key: asdict(transform.box(box)) for key, box in row.items()} for row in canvas.row_boxes],
        },
        "treatment": "blur_low_contrast" if family == 5 and variant == 0 else ("crop_rotate_270_clockwise" if rotation else "none"),
    }


def main() -> None:
    documents = [render(family, variant) for family in range(len(FAMILIES)) for variant in range(2)]
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({
        "dataset_id": "self-authored-development-v0",
        "split": "development",
        "author_created": True,
        "generator": {"pillow_version": pillow_version, "font": FONT_PATH},
        "documents": documents,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
