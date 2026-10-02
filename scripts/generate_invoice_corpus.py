"""Generate the self-authored 540-document invoice corpus.

Run with the generator-only Python environment that has Pillow installed.
The command refuses to overwrite a frozen corpus; render to a new directory
when changing layouts, fonts, or labels.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import random
import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[1]
FONT = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
SEED = 1729
GENERATOR_VERSION = "fictional-invoices-v1"
SPLITS = ("development", "calibration", "test")
VENDORS = (
    "Aster Studio LLC", "Juniper Labs", "Cobalt Paper Co", "Maple Lantern Works",
    "Fable Transit Design", "Northline Atelier", "Copper Finch Media", "Birch Signal Works",
    "Harbor Tile Studio", "Elm Beacon Design", "Cedar Orbit Labs", "Quartz Letter Co",
    "Marigold Frame Works", "Blue Heron Research", "Linden Field Studio",
    "Amber Compass Lab", "Riverstone Drafting", "Willow Index Co",
)
DESCRIPTIONS = (
    "Research workshop", "Archive preparation", "Design review", "Prototype analysis",
    "Accessibility audit", "Paper mockup", "Field interview", "Data cleanup",
    "Illustration set", "Usability session", "Technical editing", "Layout study",
)
TREATMENTS = ("blur", "low_contrast", "jpeg_compression", "skew_2deg", "rotate_90ccw")


@dataclass(frozen=True)
class Layout:
    name: str
    width: int
    height: int
    meta_x: int
    meta_y: int
    table_y: int
    summary_x: int
    summary_y: int
    title_x: int
    accent: str
    table_box: bool


# Each split has six distinct families and its own page format. The parameters
# change hierarchy, alignment, table placement, and summary placement.
LAYOUTS = (
    Layout("ledger_left", 1200, 1600, 80, 270, 610, 80, 1210, 80, "#17365d", False),
    Layout("ledger_right", 1200, 1600, 700, 255, 620, 700, 1200, 820, "#663399", True),
    Layout("split_header", 1200, 1600, 680, 275, 690, 80, 1220, 80, "#226b68", False),
    Layout("summary_first", 1200, 1600, 80, 270, 800, 700, 520, 730, "#7b451d", True),
    Layout("central_grid", 1200, 1600, 470, 265, 650, 700, 1220, 80, "#394f75", True),
    Layout("continuation_ledger", 1200, 1600, 690, 260, 650, 80, 1220, 800, "#31553a", False),
    Layout("wide_panel_left", 1500, 1100, 65, 205, 495, 1160, 545, 650, "#554273", True),
    Layout("wide_panel_right", 1500, 1100, 610, 210, 515, 1160, 565, 65, "#1f6371", False),
    Layout("wide_banner", 1500, 1100, 970, 200, 530, 1160, 580, 70, "#725b1e", True),
    Layout("wide_columns", 1500, 1100, 60, 215, 560, 1160, 565, 1080, "#5b384f", False),
    Layout("wide_compact", 1500, 1100, 590, 205, 505, 1160, 520, 80, "#264e36", True),
    Layout("wide_continuation", 1500, 1100, 990, 200, 520, 1160, 560, 70, "#3b5782", False),
    Layout("tall_column_left", 1000, 1800, 60, 295, 760, 60, 1380, 60, "#6e3f3b", True),
    Layout("tall_column_right", 1000, 1800, 510, 280, 750, 530, 1370, 570, "#285e65", False),
    Layout("tall_summary_first", 1000, 1800, 60, 295, 880, 530, 570, 60, "#674c76", True),
    Layout("tall_offset", 1000, 1800, 490, 300, 735, 60, 1370, 70, "#466c32", False),
    Layout("tall_grid", 1000, 1800, 60, 310, 790, 530, 1370, 560, "#71552c", True),
    Layout("tall_continuation", 1000, 1800, 500, 285, 760, 60, 1370, 60, "#3a5271", False),
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _box(box: tuple[float, float, float, float], width: int, height: int) -> dict[str, float]:
    left, top, right, bottom = box
    values = (left / width, top / height, right / width, bottom / height)
    if not (0 <= values[0] < values[2] <= 1 and 0 <= values[1] < values[3] <= 1):
        raise ValueError(f"Box falls outside page: {box} in {width}x{height}")
    return dict(zip(("left", "top", "right", "bottom"), values))


def _transform_box(box: tuple[float, float, float, float], width: int, height: int,
                   treatment: str) -> tuple[tuple[float, float, float, float], int, int]:
    if treatment == "rotate_90ccw":
        return (box[1], width - box[2], box[3], width - box[0]), height, width
    if treatment == "skew_2deg":
        angle = math.radians(2)
        cosine, sine = math.cos(angle), math.sin(angle)
        corners = ((box[0], box[1]), (box[0], box[3]), (box[2], box[1]), (box[2], box[3]))
        shifted = [(width / 2 + cosine * (x - width / 2) + sine * (y - height / 2),
                    height / 2 - sine * (x - width / 2) + cosine * (y - height / 2))
                   for x, y in corners]
        return (min(x for x, _ in shifted), min(y for _, y in shifted),
                max(x for x, _ in shifted), max(y for _, y in shifted)), width, height
    return box, width, height


class Page:
    def __init__(self, layout: Layout, number: int):
        self.layout = layout
        self.number = number
        self.image = Image.new("RGB", (layout.width, layout.height), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.body = ImageFont.truetype(str(FONT), 26 if layout.width != 1000 else 24)
        self.heading = ImageFont.truetype(str(FONT), 43 if layout.width != 1000 else 38)
        self.field_boxes: dict[str, tuple[float, float, float, float] | None] = {}
        self.row_boxes: list[dict] = []

    def text(self, value: str, x: int, y: int, *, heading: bool = False) -> tuple[int, int, int, int]:
        font = self.heading if heading else self.body
        self.draw.text((x, y), value, fill="#111111", font=font)
        return self.draw.textbbox((x, y), value, font=font)

    def field(self, name: str, label: str, value: str | None, x: int, y: int) -> None:
        if value is None:
            self.field_boxes[name] = None
            return
        prefix = f"{label}: "
        self.text(prefix + value, x, y)
        value_x = x + self.draw.textlength(prefix, font=self.body)
        self.field_boxes[name] = self.draw.textbbox((value_x, y), value, font=self.body)

    def frame(self, *, continued: bool = False) -> None:
        layout = self.layout
        self.draw.rectangle((0, 0, layout.width, 18), fill=layout.accent)
        self.draw.line((55, 190, layout.width - 55, 190), fill=layout.accent, width=3)
        title = "INVOICE CONTINUED" if continued else "INVOICE"
        max_x = layout.width - 60 - self.draw.textlength(title, font=self.heading)
        self.text(title, min(layout.title_x, int(max_x)), 95, heading=True)
        self.draw.text((layout.width - 230, layout.height - 65), f"Page {self.number}",
                       font=self.body, fill="#555555")

    def table(self, rows: list[dict], start_index: int, y: int) -> None:
        layout = self.layout
        columns = ((80, 600, 790, 1000) if layout.width == 1200 else
                   (70, 600, 770, 960) if layout.width == 1500 else (55, 490, 650, 815))
        if layout.table_box:
            self.draw.rectangle((columns[0] - 20, y - 18, columns[3] + 145,
                                 y + 72 + len(rows) * 81), outline=layout.accent, width=2)
        for text, x in zip(("Description", "Qty", "Unit price", "Amount"), columns):
            self.text(text, x, y)
        self.draw.line((columns[0], y + 44, columns[3] + 130, y + 44), fill=layout.accent, width=2)
        for offset, row in enumerate(rows):
            row_y = y + 72 + offset * 81
            boxes = {}
            for key, x in zip(("description", "quantity", "unit_price", "line_total"), columns):
                value = row[key]
                if key == "description" and self.draw.textlength(value, font=self.body) > columns[1] - x - 35:
                    words = value.split()
                    first = []
                    while words and self.draw.textlength(" ".join(first + words[:1]), font=self.body) <= columns[1] - x - 35:
                        first.append(words.pop(0))
                    if not first:
                        first = [words.pop(0)]
                    top = self.text(" ".join(first), x, row_y)
                    bottom = self.text(" ".join(words), x, row_y + 29)
                    boxes[key] = (top[0], top[1], max(top[2], bottom[2]), bottom[3])
                else:
                    boxes[key] = self.text(value, x, row_y)
            self.row_boxes.append({"row_index": start_index + offset, "boxes": boxes})


def _values(family: int, base: int) -> tuple[dict, list[dict], list[str], dict]:
    rng = random.Random(SEED + family * 100 + base)
    issue = date(2026, 1, 1) + timedelta(days=family * 13 + base)
    ambiguous = base in (0, 13) and issue.month <= 12 and issue.day <= 12 and issue.month != issue.day
    if base == 0:
        issue = date(2026, 5, 6)
        ambiguous = True
    due = issue + timedelta(days=30)
    multi = family in (5, 11, 17)
    row_count = 5 if multi else 1 + (family + base) % 4
    rows = []
    for index in range(row_count):
        description = DESCRIPTIONS[rng.randrange(len(DESCRIPTIONS))]
        if base % 8 == 0 and index == 1:
            description = rows[0]["description"]
        if base % 10 == 3 and index == 0:
            description = "Extended technical documentation review"
        quantity = 1 + rng.randrange(4)
        price = Decimal(25 + rng.randrange(12) * 7 + family)
        rows.append({"description": description, "quantity": str(quantity),
                     "unit_price": f"{price:.2f}", "line_total": f"{price * quantity:.2f}",
                     "tax": None})
    subtotal = sum((Decimal(row["line_total"]) for row in rows), Decimal("0"))
    conflict = base % 11 == 0
    tax_visible = conflict or base % 7 != 0
    discount_visible = conflict or base % 4 != 0
    shipping_visible = conflict or base % 6 != 0
    tax = Decimal("0") if base % 7 == 0 else Decimal(5 + family % 5)
    discount = Decimal("3") if discount_visible and base % 5 == 0 else Decimal("0")
    shipping = Decimal("2") if shipping_visible and base % 8 == 1 else Decimal("0")
    total = subtotal + tax - discount + shipping + (Decimal("5") if conflict else 0)
    fields = {
        "supplier_name": VENDORS[family], "invoice_number": f"F{family + 1:02d}-{base + 1:03d}",
        "issue_date": issue.isoformat(), "due_date": None if base % 9 == 0 else due.isoformat(),
        "currency": "USD", "subtotal": f"{subtotal:.2f}",
        "tax": None if not tax_visible else f"{tax:.2f}",
        "discount": None if not discount_visible else f"{discount:.2f}",
        "shipping": None if not shipping_visible else f"{shipping:.2f}",
        "total": f"{total:.2f}",
    }
    issues = (["TOTAL_MISMATCH"] if conflict else []) + (["AMBIGUOUS_DATE"] if ambiguous else [])
    return fields, rows, issues, {"ambiguous_date": ambiguous, "multi_page": multi}


def _render(layout: Layout, fields: dict, rows: list[dict], flags: dict) -> list[Page]:
    pages = [Page(layout, 1)]
    first = pages[0]
    first.frame()
    first.field_boxes["supplier_name"] = first.text(fields["supplier_name"], 65, 35, heading=True)
    labels = (("invoice_number", "Invoice No."), ("issue_date", "Date"),
              ("due_date", "Due date"), ("currency", "Currency"))
    for offset, (name, label) in enumerate(labels):
        value = fields[name]
        if name == "issue_date" and flags["ambiguous_date"]:
            year, month, day = value.split("-")
            value = f"{month}/{day}/{year}"
        first.field(name, label, value, layout.meta_x, layout.meta_y + offset * 59)
    if flags["multi_page"]:
        first.table(rows[:3], 0, layout.table_y)
        second = Page(layout, 2)
        second.frame(continued=True)
        second.field_boxes["supplier_name"] = second.text(fields["supplier_name"], 65, 35, heading=True)
        second.field("invoice_number", "Invoice No.", fields["invoice_number"],
                     65, 235 if layout.height <= 1200 else 270)
        second.table(rows[3:], 3, 360 if layout.height <= 1200 else 520)
        pages.append(second)
        summary_page = second
        summary_y = 640 if layout.height <= 1200 else layout.summary_y
    else:
        first.table(rows, 0, layout.table_y)
        summary_page = first
        summary_y = layout.summary_y
    summary_labels = (("subtotal", "Subtotal"), ("tax", "Tax"), ("discount", "Discount"),
                      ("shipping", "Shipping"), ("total", "Total"))
    for offset, (name, label) in enumerate(summary_labels):
        summary_page.field(name, label, fields[name], layout.summary_x, summary_y + offset * 57)
    return pages


def _treat(image: Image.Image, treatment: str) -> Image.Image:
    if treatment == "blur":
        return image.filter(ImageFilter.GaussianBlur(.9))
    if treatment == "low_contrast":
        return ImageEnhance.Contrast(image).enhance(.52)
    if treatment == "jpeg_compression":
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=24, subsampling=2)
        buffer.seek(0)
        return Image.open(buffer).convert("RGB")
    if treatment == "skew_2deg":
        return image.rotate(2, expand=False, fillcolor="white")
    if treatment == "rotate_90ccw":
        return image.rotate(90, expand=True, fillcolor="white")
    return image


def _page_labels(page: Page, image: Image.Image, treatment: str) -> dict:
    raw_width, raw_height = page.image.size
    width, height = image.size

    def convert(box):
        if box is None:
            return None
        transformed, expected_width, expected_height = _transform_box(box, raw_width, raw_height, treatment)
        if (expected_width, expected_height) != (width, height):
            raise ValueError("Treatment image size differs from geometry")
        return _box(transformed, width, height)

    return {
        "number": page.number, "width_px": width, "height_px": height,
        "raw_width_px": raw_width, "raw_height_px": raw_height,
        "field_boxes": {name: convert(box) for name, box in page.field_boxes.items()},
        "row_boxes": [{"row_index": entry["row_index"],
                       "boxes": {name: convert(box) for name, box in entry["boxes"].items()}}
                      for entry in page.row_boxes],
    }


def _document(output: Path, family: int, number: int) -> dict:
    layout = LAYOUTS[family]
    base = number if number < 25 else number - 25
    treatment = "none" if number < 25 else TREATMENTS[number - 25]
    fields, rows, issues, flags = _values(family, base)
    pages = _render(layout, fields, rows, flags)
    images = [_treat(page.image, treatment) for page in pages]
    identifier = f"inv-f{family + 1:02d}-{number + 1:02d}"
    asset_root = output / "assets"
    assets = []
    if len(images) == 1:
        source = asset_root / f"{identifier}.png"
        images[0].save(source, optimize=True)
        source_bytes = source.read_bytes()
    else:
        source = asset_root / f"{identifier}.pdf"
        fixed_date = time.gmtime(0)
        images[0].save(source, "PDF", save_all=True, append_images=images[1:], resolution=120,
                       creationDate=fixed_date, modDate=fixed_date)
        source_bytes = source.read_bytes()
    assets.append({"path": str(source.relative_to(output)), "sha256": _sha(source_bytes)})
    if len(images) > 1:
        for index, image in enumerate(images, start=1):
            path = asset_root / f"{identifier}-page-{index}.png"
            image.save(path, optimize=True)
            assets.append({"path": str(path.relative_to(output)), "sha256": _sha(path.read_bytes())})
    split = SPLITS[family // 6]
    return {
        "id": identifier, "split": split, "family": layout.name,
        "family_group": f"family-{family + 1:02d}",
        "parent_id": f"inv-f{family + 1:02d}-{base + 1:02d}" if number >= 25 else None,
        "treatment": treatment, "source_sha256": _sha(source_bytes), "assets": assets,
        "fields": fields, "field_exclusions": {"issue_date": "ambiguous_printed_date"} if flags["ambiguous_date"] else {},
        "line_items": rows,
        "expected_issue_codes": issues,
        "pages": [_page_labels(page, image, treatment) for page, image in zip(pages, images)],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "datasets" / "invoices-v1")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        raise FileExistsError(f"Corpus directory already exists: {output}")
    if not FONT.is_file():
        raise FileNotFoundError(f"Required generator font is unavailable: {FONT}")
    (output / "assets").mkdir(parents=True)
    documents = [_document(output, family, number) for family in range(18) for number in range(30)]
    manifest = {
        "manifest_version": "invoice-corpus-v1",
        "dataset_id": "self-authored-invoices-v1",
        "author_created": True,
        "rights": "Self-authored fictional invoices; no third-party logos or document data",
        "generator": {"version": GENERATOR_VERSION, "seed": SEED,
                      "pillow_version": pillow_version, "font_sha256": _sha(FONT.read_bytes())},
        "documents": documents,
    }
    path = output / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(path)


if __name__ == "__main__":
    main()
