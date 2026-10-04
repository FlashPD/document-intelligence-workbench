"""Generate the two fictional Phase 0 PNG fixtures with Pillow.

This script is a development aid; generated PNGs are committed so the baseline
does not need Pillow at runtime.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples"
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"


def generate(name: str, total: str) -> None:
    image = Image.new("RGB", (1800, 2200), "white")
    draw = ImageDraw.Draw(image)
    heading = ImageFont.truetype(FONT, 65)
    body = ImageFont.truetype(FONT, 44)
    lines = [
        ("Aster Studio LLC", heading),
        ("INVOICE", heading),
        ("Invoice Number: AST-1001", body),
        ("Date: 2026-09-12", body),
        ("Due Date: 2026-10-12", body),
        ("Currency: USD", body),
        ("Description  Qty  Unit Price  Line Total", body),
        ("Research workshop 2 125.00 250.00", body),
        ("Subtotal: 250.00", body),
        ("Tax: 20.00", body),
        ("Discount: 0.00", body),
        ("Shipping: 0.00", body),
        (f"Total: {total}", body),
    ]
    y = 110
    for index, (line, font) in enumerate(lines):
        if index in (2, 6, 8):
            y += 70
        draw.text((115, y), line, fill="black", font=font)
        y += 92
    OUT.mkdir(exist_ok=True)
    image.save(OUT / name, optimize=True)


if __name__ == "__main__":
    generate("clean.png", "270.00")
    generate("conflicting-total.png", "275.00")
