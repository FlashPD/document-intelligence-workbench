"""Tesseract adapter for trusted, self-authored Phase 0 PNG fixtures only."""

from __future__ import annotations

import csv
import io
import struct
import subprocess
from collections import defaultdict
from pathlib import Path

from .contracts import Box, DocumentPage, TextSpan

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 20_000_000


def png_dimensions(path: Path) -> tuple[int, int]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("fixture exceeds 20 MB")
    with path.open("rb") as source:
        header = source.read(24)
    if len(header) != 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        raise ValueError("fixture is not a PNG")
    width, height = struct.unpack(">II", header[16:24])
    if not width or not height or width * height > MAX_PIXELS:
        raise ValueError("fixture exceeds the 20-megapixel limit")
    return width, height


def tesseract_page(path: Path, *, page_number: int = 1, timeout_seconds: int = 90) -> DocumentPage:
    """Read one trusted PNG; untrusted originals need the planned container boundary."""
    width, height = png_dimensions(path)
    result = subprocess.run(
        ["tesseract", str(path), "stdout", "-l", "eng", "tsv"],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Tesseract failed with exit code {result.returncode}")

    groups: dict[tuple[int, int, int], list[dict[str, str]]] = defaultdict(list)
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t"):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
        groups[key].append(row)

    spans: list[TextSpan] = []
    for index, (key, words) in enumerate(groups.items(), start=1):
        left = min(int(word["left"]) for word in words)
        top = min(int(word["top"]) for word in words)
        right = max(int(word["left"]) + int(word["width"]) for word in words)
        bottom = max(int(word["top"]) + int(word["height"]) for word in words)
        confidences = [float(word["conf"]) for word in words if float(word["conf"]) >= 0]
        spans.append(
            TextSpan(
                id=f"p{page_number}-l{index:04d}",
                page=page_number,
                text=" ".join(word["text"].strip() for word in words),
                box=Box(left / width, top / height, right / width, bottom / height),
                method="tesseract-eng",
                confidence=round(sum(confidences) / len(confidences), 2) if confidences else None,
            )
        )
    return DocumentPage(number=page_number, width_px=width, height_px=height, spans=tuple(spans))
