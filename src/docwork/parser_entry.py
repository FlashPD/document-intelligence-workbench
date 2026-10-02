"""Fixed entrypoint inside the network-denied parser image.

Only the trusted worker supplies paths. This process receives one original and
emits a bounded, rotation-corrected PNG and canonical OCR page.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .ocr import MAX_FILE_BYTES, MAX_PIXELS, tesseract_page
from .parser_protocol import MAX_PAGE_BYTES, MAX_PAGES, MAX_RESULT_BYTES, PARSER_VERSION


class ParserRejected(ValueError):
    pass


def _pdf_page_count(source: Path) -> int:
    result = subprocess.run(["pdfinfo", str(source)], capture_output=True, text=True, timeout=20, check=False)
    facts = dict(line.split(":", 1) for line in result.stdout.splitlines() if ":" in line)
    if facts.get("Encrypted", "").strip().lower().startswith("yes"):
        raise ParserRejected("PDF_ENCRYPTED")
    if result.returncode:
        raise ParserRejected("PDF_INFO_FAILED")
    try:
        count = int(facts["Pages"].strip())
    except (KeyError, ValueError) as exc:
        raise ParserRejected("PDF_PAGE_COUNT_UNKNOWN") from exc
    if count < 1 or count > MAX_PAGES:
        raise ParserRejected("PDF_PAGE_LIMIT")
    if count != 1:
        raise ParserRejected("MULTIPAGE_NOT_SUPPORTED")
    return count


def _render(source: Path, media_type: str, output: Path) -> None:
    if media_type == "application/pdf":
        _pdf_page_count(source)
        prefix = output / "rendered"
        result = subprocess.run(
            ["pdftoppm", "-f", "1", "-l", "1", "-singlefile", "-r", "150", "-png", str(source), str(prefix)],
            capture_output=True, timeout=90, check=False,
        )
        if result.returncode:
            raise ParserRejected("PDF_RENDER_FAILED")
        rendered = prefix.with_suffix(".png")
    elif media_type in ("image/png", "image/jpeg"):
        rendered = source
    else:
        raise ParserRejected("UNSUPPORTED_MEDIA_TYPE")
    try:
        with Image.open(rendered) as original:
            if original.width * original.height > MAX_PIXELS:
                raise ParserRejected("IMAGE_PIXEL_LIMIT")
            original.load()
            corrected = ImageOps.exif_transpose(original)
            if corrected.width * corrected.height > MAX_PIXELS:
                raise ParserRejected("IMAGE_PIXEL_LIMIT")
            corrected.convert("RGB").save(output / "page.png", format="PNG")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ParserRejected("IMAGE_DECODE_FAILED") from exc
    if rendered != source:
        rendered.unlink(missing_ok=True)
    if (output / "page.png").stat().st_size > MAX_PAGE_BYTES:
        raise ParserRejected("PAGE_OUTPUT_LIMIT")


def parse(source: Path, media_type: str, output: Path) -> None:
    if source.stat().st_size > MAX_FILE_BYTES:
        raise ParserRejected("SOURCE_SIZE_LIMIT")
    _render(source, media_type, output)
    page_file = output / "page.png"
    page = tesseract_page(page_file)
    if len(page.spans) > 5000:
        raise ParserRejected("SPAN_LIMIT")
    result = {
        "parser_version": PARSER_VERSION,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "page_sha256": hashlib.sha256(page_file.read_bytes()).hexdigest(),
        "page": asdict(page),
    }
    encoded = json.dumps(result, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_RESULT_BYTES:
        raise ParserRejected("RESULT_OUTPUT_LIMIT")
    (output / "result.json").write_bytes(encoded)


def main() -> int:
    if len(sys.argv) != 4:
        return 2
    try:
        parse(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
    except ParserRejected as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
