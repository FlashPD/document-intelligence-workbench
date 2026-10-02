"""Real container smoke check; run only when Docker daemon and image are ready."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from docwork.intake import IntakeStore
from docwork.worker import process_one


def two_page_pdf() -> bytes:
    """Minimal self-authored PDF for an actual renderer/OCR smoke run."""
    def stream(lines: tuple[str, ...]) -> bytes:
        operations = [b"BT /F1 16 Tf 50 750 Td 22 TL"]
        for line in lines:
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            operations.append(f"({escaped}) Tj T*".encode("ascii"))
        operations.append(b"ET")
        return b"\n".join(operations) + b"\n"

    first = stream(("Aster Studio LLC", "INVOICE", "Invoice Number: AST-1001",
                    "Date: 2026-09-12", "Due Date: 2026-10-12", "Currency: USD",
                    "Subtotal: 250.00", "Tax: 20.00", "Discount: 0.00",
                    "Shipping: 0.00", "Total: 270.00"))
    second = stream(("INVOICE", "Research workshop 2 125.00 250.00"))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(first)} >>\nstream\n".encode() + first + b"endstream",
        f"<< /Length {len(second)} >>\nstream\n".encode() + second + b"endstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(result)


class ParserSmoke(unittest.TestCase):
    def test_container_processes_committed_png(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "clean.png"
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            store = IntakeStore(root / "review.sqlite", root / "objects")
            document_id = store.submit(io.BytesIO(sample.read_bytes()), "clean.png", "image/png")
            process_one(store, "smoke")
            self.assertEqual(store.status(document_id)["status"], "REVIEW_READY", store.status(document_id))
            self.assertTrue(store.get(document_id)["page"]["spans"])

    def test_container_processes_two_page_pdf(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            store = IntakeStore(root / "review.sqlite", root / "objects")
            document_id = store.submit(io.BytesIO(two_page_pdf()), "two-page.pdf", "application/pdf")
            process_one(store, "smoke")
            self.assertEqual(store.status(document_id)["status"], "REVIEW_READY", store.status(document_id))
            detail = store.get(document_id)
            self.assertEqual(len(detail["pages"]), 2)
            self.assertTrue(any(span["page"] == 2 for span in detail["pages"][1]["spans"]))
            self.assertEqual(detail["record"]["fields"]["total"]["value"], "270.00")
            self.assertTrue(store.page_image_path(document_id, 2).is_file())


if __name__ == "__main__":
    unittest.main()
