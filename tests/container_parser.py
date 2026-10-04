"""Real container smoke check; run only when Docker daemon and image are ready."""

from __future__ import annotations

import io
import hashlib
import json
import subprocess
import socket
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from docwork.backup import create_backup, restore_backup, verify_backup
from docwork.intake import IntakeStore, JobClaim
from docwork.local_model import LocalModelConfig
from docwork.review import ReviewBlocked, ReviewConflict
from docwork.worker import PARSER_IMAGE, parser_command, process_one


def two_page_pdf(page_count: int = 2) -> bytes:
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
    if page_count < 2:
        raise ValueError("This fixture needs at least two pages")
    font_id = page_count + 3
    content_id = font_id + 1
    kids = " ".join(f"{number} 0 R" for number in range(3, font_id))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode(),
        *(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id + min(index, 1)} 0 R >>".encode()
          for index in range(page_count)),
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
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = IntakeStore(self.root / "review.sqlite", self.root / "objects")
        self.evidence = {}

    def process(self, data, filename, mime):
        document_id = self.store.submit(io.BytesIO(data), filename, mime)
        self.assertEqual(process_one(self.store, "smoke"), document_id)
        status = self.store.status(document_id)
        self.evidence.update(source_sha256=status["source_sha256"],
                             status=status["status"], error_code=status["job"]["error_code"],
                             page_count=status["page_count"])
        self.assertEqual(list((self.store.object_root / "quarantine").iterdir()), [])
        return document_id

    def probe_command(self, code):
        sample = Path(__file__).resolve().parents[1] / "samples" / "clean.png"
        output = self.root / "probe-output"
        output.mkdir(exist_ok=True)
        output.chmod(0o777)
        claim = JobClaim(uuid.uuid4().hex, "probe", 1, "smoke", 0)
        command = parser_command(sample, "image/png", output, claim, image=PARSER_IMAGE)
        return command[:-4] + ["--entrypoint", "python", PARSER_IMAGE, "-c", code]

    def test_container_processes_committed_png(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "clean.png"
        document_id = self.process(sample.read_bytes(), "clean.png", "image/png")
        self.assertEqual(self.store.status(document_id)["status"], "REVIEW_READY")
        detail = self.store.get(document_id)
        self.assertTrue(detail["page"]["spans"])
        self.assertEqual(detail["issues"], [])
        self.assertEqual(detail["record"]["fields"]["invoice_number"]["value"], "AST-1001")
        self.assertEqual(detail["record"]["line_items"][0]["line_total"]["value"], "250.00")

    def test_container_processes_two_page_pdf(self):
        document_id = self.process(two_page_pdf(), "two-page.pdf", "application/pdf")
        self.assertEqual(self.store.status(document_id)["status"], "REVIEW_READY")
        detail = self.store.get(document_id)
        self.assertEqual(len(detail["pages"]), 2)
        row = detail["record"]["line_items"][0]
        refs = row["line_total"]["evidence_ids"]
        self.assertTrue(refs and all(ref.startswith("p2-") for ref in refs))
        self.assertEqual(row["line_total"]["value"], "250.00")
        self.assertEqual(detail["record"]["fields"]["total"]["value"], "270.00")
        self.assertTrue(self.store.page_image_path(document_id, 2).is_file())
        self.evidence["second_page_row_evidence"] = refs

    def test_container_jpeg_exif_rotation(self):
        # Use the image's pinned Pillow, without adding a host dependency.
        code = """
import io, sys
from PIL import Image
with Image.open('/input/original') as source:
    sideways = source.convert('RGB').transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[274] = 6
    result = io.BytesIO()
    sideways.save(result, format='JPEG', quality=95, exif=exif)
    sys.stdout.buffer.write(result.getvalue())
"""
        generated = subprocess.run(self.probe_command(code), capture_output=True, timeout=30, check=True)
        document_id = self.process(generated.stdout, "rotated.jpg", "image/jpeg")
        detail = self.store.get(document_id)
        self.assertEqual((detail["page"]["width_px"], detail["page"]["height_px"]), (1800, 2200))
        self.assertEqual(detail["record"]["fields"]["invoice_number"]["value"], "AST-1001")
        self.assertEqual(detail["issues"], [])
        self.evidence["render_dimensions"] = [detail["page"]["width_px"], detail["page"]["height_px"]]

    def test_container_rejects_malformed_pdf(self):
        document_id = self.process(b"%PDF-1.4\ninvalid\n", "broken.pdf", "application/pdf")
        self.assert_failed(document_id, "PDF_INFO_FAILED")

    def test_container_rejects_eleven_page_pdf(self):
        document_id = self.process(two_page_pdf(11), "eleven.pdf", "application/pdf")
        self.assert_failed(document_id, "PDF_PAGE_LIMIT")

    def test_container_rejects_truncated_png(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "clean.png"
        document_id = self.process(sample.read_bytes()[:40], "broken.png", "image/png")
        self.assert_failed(document_id, "IMAGE_DECODE_FAILED")

    def test_container_missing_image_retry(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "clean.png"
        document_id = self.store.submit(io.BytesIO(sample.read_bytes()), sample.name, "image/png")
        missing_image = f"docwork-parser:missing-{uuid.uuid4().hex}"
        process_one(self.store, "smoke", image=missing_image)
        self.assert_failed(document_id, "PARSER_IMAGE_MISSING")
        self.store.retry(document_id)
        process_one(self.store, "smoke")
        status = self.store.status(document_id)
        self.assertEqual(status["status"], "REVIEW_READY")
        self.assertEqual(status["job"]["attempts"], 2)
        self.assertIsNone(self.store.get(document_id)["approval"])
        self.evidence.update(source_sha256=status["source_sha256"],
                             attempts=status["job"]["attempts"], status=status["status"],
                             initial_error="PARSER_IMAGE_MISSING")

    def test_container_duplicate_submissions_have_independent_approval(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "clean.png"
        first = self.process(sample.read_bytes(), sample.name, "image/png")
        second = self.process(sample.read_bytes(), sample.name, "image/png")
        self.assertNotEqual(first, second)
        self.assertEqual(self.store.object_path(first), self.store.object_path(second))
        self.store.approve(first, 1, "smoke-reviewer")
        self.store.export(first, "json")
        self.assertIsNone(self.store.get(second)["approval"])
        with self.assertRaises(ReviewConflict):
            self.store.export(second, "json")
        self.evidence.update(shared_original=True, independent_approval=True)

    def assert_failed(self, document_id, code):
        status = self.store.status(document_id)
        self.assertEqual(status["status"], "FAILED")
        self.assertEqual(status["job"]["error_code"], code)
        self.assertEqual(status["current_revision"], 0)
        self.assertEqual(status["page_count"], 0)
        with self.assertRaises(ReviewBlocked):
            self.store.get(document_id)

    def test_container_conflict_review_export_and_reopen(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "conflicting-total.png"
        document_id = self.process(sample.read_bytes(), sample.name, "image/png")
        detail = self.store.get(document_id)
        self.assertEqual(detail["record"]["fields"]["total"]["value"], "275.00")
        self.assertIn("TOTAL_MISMATCH", [issue["code"] for issue in detail["issues"]])
        with self.assertRaises(ReviewBlocked):
            self.store.approve(document_id, 1, "smoke-reviewer")
        with self.assertRaises(ReviewConflict):
            self.store.export(document_id, "json")
        self.store.acknowledge(document_id, 1, "TOTAL_MISMATCH", "fields.total",
                               "Fictional fixture intentionally has a conflicting total", "smoke-reviewer")
        approval = self.store.approve(document_id, 1, "smoke-reviewer")
        manifests = {kind: self.store.export(document_id, kind) for kind in ("json", "csv")}
        reopened = IntakeStore(self.store.database, self.store.object_root)
        self.assertEqual(reopened.status(document_id)["status"], "APPROVED")
        for kind, manifest in manifests.items():
            self.assertEqual(reopened.export(document_id, kind), manifest)
            for file in manifest["files"]:
                content, _ = reopened.exported_file(document_id, 1, kind, Path(file["path"]).name)
                self.assertEqual(hashlib.sha256(content).hexdigest(), file["sha256"])
        original, _ = reopened.exported_file(document_id, 1, "json", "invoice.json")
        self.assertEqual(json.loads(original)["record"]["fields"]["total"]["value"], "275.00")
        revision = reopened.edit(document_id, 1, "fields.total", "270.00", "smoke-reviewer")
        self.assertEqual(revision, 2)
        self.assertIsNone(reopened.get(document_id)["approval"])
        with self.assertRaises(ReviewConflict):
            reopened.export(document_id, "json")
        self.assertEqual(reopened.exported_file(document_id, 1, "json", "invoice.json")[0], original)
        self.evidence.update(approved_revision=1, new_unapproved_revision=2,
                             approval_hash=approval["approval_hash"],
                             export_sha256=[file["sha256"] for manifest in manifests.values() for file in manifest["files"]],
                             historical_export_preserved=True)

    def test_model_outage_retry_reuses_real_two_page_parser_output(self):
        document_id = self.store.submit(io.BytesIO(two_page_pdf()), "two-page.pdf", "application/pdf")
        # A bound socket without listen deterministically refuses connections;
        # this exercises the real HTTP adapter without a model or external API.
        with socket.socket() as endpoint:
            endpoint.bind(("127.0.0.1", 0))
            config = LocalModelConfig(f"http://127.0.0.1:{endpoint.getsockname()[1]}", "offline-probe")
            process_one(self.store, "outage-worker", extractor="span_llm", model_config=config)
        self.assert_failed(document_id, "MODEL_UNAVAILABLE")
        checkpoint = self.store.status(document_id)["parser_checkpoint"]
        self.assertIsNotNone(checkpoint)
        self.assertEqual(self.store.reconcile(prune=True, min_age_seconds=0)["removed"], [])
        reopened = IntakeStore(self.store.database, self.store.object_root)
        reopened.retry(document_id)
        # An explicit profile change to rules is allowed. Only parsing is reused;
        # no model-generated record or prior review decision exists.
        with patch("docwork.worker._docker_run", side_effect=AssertionError("Parser must not rerun")):
            process_one(reopened, "resumed-worker")
        detail = reopened.get(document_id)
        self.assertEqual(len(detail["pages"]), 2)
        self.assertEqual(detail["record"]["line_items"][0]["line_total"]["evidence_ids"], ["p2-l0002"])
        self.assertIsNone(detail["approval"])
        reopened.approve(document_id, 1, "smoke-reviewer")
        reopened.export(document_id, "json")
        kinds = [event["kind"] for event in reopened.history(document_id)]
        self.assertEqual(kinds.count("parser_checkpoint_saved"), 1)
        self.assertEqual(kinds.count("parser_checkpoint_reused"), 1)
        self.evidence.update(parser_checkpoint=checkpoint, reused_pages=2, parser_rerun=False,
                             model_outage="Real HTTP connection refused on bound non-listening loopback socket",
                             resumed_extractor="ocr_rules", approved_revision=1)

    def test_backup_restores_real_pdf_review_and_immutable_exports(self):
        document_id = self.process(two_page_pdf(), "two-page.pdf", "application/pdf")
        self.store.approve(document_id, 1, "smoke-reviewer")
        self.store.export(document_id, "json")
        self.store.edit(document_id, 1, "fields.total", "275.00", "smoke-reviewer")
        self.store.acknowledge(document_id, 2, "TOTAL_MISMATCH", "fields.total",
                               "Fictional restoration drill", "smoke-reviewer")
        self.store.approve(document_id, 2, "smoke-reviewer")
        manifests = {kind: self.store.export(document_id, kind) for kind in ("json", "csv")}
        before = self.store.get(document_id)
        history = self.store.history(document_id)
        page_hashes = [hashlib.sha256(self.store.page_image_path(document_id, number).read_bytes()).hexdigest()
                       for number in (1, 2)]
        bundle, target = self.root / "backup", self.root / "restored"
        created = create_backup(self.store.database, self.store.object_root, bundle)
        self.assertEqual(verify_backup(bundle)["manifest_sha256"], created["manifest_sha256"])
        result = restore_backup(bundle, target)
        restored = IntakeStore(target / "database.sqlite", target / "intake")
        self.assertEqual(restored.get(document_id), before)
        self.assertEqual(restored.history(document_id), history)
        self.assertEqual(restored.object_path(document_id).read_bytes(), two_page_pdf())
        for number, digest in enumerate(page_hashes, start=1):
            self.assertEqual(hashlib.sha256(restored.page_image_path(document_id, number).read_bytes()).hexdigest(), digest)
        for kind, manifest in manifests.items():
            for file in manifest["files"]:
                filename = Path(file["path"]).name
                self.assertEqual(restored.exported_file(document_id, 2, kind, filename)[0], Path(file["path"]).read_bytes())
            self.assertEqual([file["sha256"] for file in restored.export(document_id, kind)["files"]],
                             [file["sha256"] for file in manifest["files"]])
        self.assertEqual(restored.exported_file(document_id, 1, "json", "invoice.json")[0],
                         self.store.exported_file(document_id, 1, "json", "invoice.json")[0])
        restored.edit(document_id, 2, "fields.total", "270.00", "smoke-reviewer")
        with self.assertRaises(ReviewConflict):
            restored.export(document_id, "json")
        self.evidence.update(backup_manifest_sha256=created["manifest_sha256"],
                             backup_counts=created["counts"], requeued_jobs=result["requeued_jobs"],
                             original_and_two_pages_verified=True, historical_export_preserved=True,
                             review_history_preserved=True, export_retries_idempotent=True,
                             export_sha256=[file["sha256"] for manifest in manifests.values() for file in manifest["files"]])

    def test_backup_restores_interrupted_real_parser_checkpoint(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "clean.png"
        document_id = self.store.submit(io.BytesIO(sample.read_bytes()), sample.name, "image/png")
        code = """
import os, sys
from pathlib import Path
from docwork.intake import IntakeStore
from docwork.local_model import LocalModelConfig
from docwork.worker import process_one
def terminate_after_checkpoint(config, payload):
    os._exit(73)
store = IntakeStore(Path(sys.argv[1]), Path(sys.argv[2]))
process_one(store, 'backup-exit-probe', extractor='span_llm',
    model_config=LocalModelConfig('http://127.0.0.1:8080', 'exit-probe'),
    model_request=terminate_after_checkpoint)
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.store.database), str(self.store.object_root)],
                                capture_output=True, text=True, timeout=90, check=False)
        self.assertEqual(result.returncode, 73, result.stderr)
        before = self.store.status(document_id)
        self.assertEqual(before["job"]["status"], "PROCESSING")
        bundle, target = self.root / "backup", self.root / "restored"
        created = create_backup(self.store.database, self.store.object_root, bundle)
        restored_result = restore_backup(bundle, target)
        self.assertEqual(restored_result["requeued_jobs"], 1)
        restored = IntakeStore(target / "database.sqlite", target / "intake")
        with patch("docwork.worker._docker_run", side_effect=AssertionError("Parser must not rerun")):
            self.assertEqual(process_one(restored, "restored-worker"), document_id)
        detail = restored.get(document_id)
        after = restored.status(document_id)
        self.assertEqual(detail["record"]["fields"]["invoice_number"]["value"], "AST-1001")
        self.assertIsNone(detail["approval"])
        self.assertGreater(after["job"]["fence"], before["job"]["fence"])
        self.assertEqual(self.store.status(document_id), before)
        self.evidence.update(backup_manifest_sha256=created["manifest_sha256"],
                             process_exit_code=73, requeued_jobs=1, parser_rerun=False,
                             fence_before=before["job"]["fence"], fence_after=after["job"]["fence"],
                             source_workbench_unchanged=True, current_revision=detail["revision"],
                             model_request="Exit callback; no model inference")

    def test_abrupt_worker_exit_resumes_committed_parser_checkpoint(self):
        sample = Path(__file__).resolve().parents[1] / "samples" / "clean.png"
        document_id = self.store.submit(io.BytesIO(sample.read_bytes()), sample.name, "image/png")
        code = """
import os, sys
from pathlib import Path
from docwork.intake import IntakeStore
from docwork.local_model import LocalModelConfig
import docwork.worker as worker
worker.WORKER_LEASE_SECONDS = 2
worker.HEARTBEAT_SECONDS = .25
def terminate_after_checkpoint(config, payload):
    os._exit(73)
store = IntakeStore(Path(sys.argv[1]), Path(sys.argv[2]))
worker.process_one(store, 'exit-probe', extractor='span_llm',
    model_config=LocalModelConfig('http://127.0.0.1:8080', 'exit-probe'),
    model_request=terminate_after_checkpoint)
"""
        result = subprocess.run([sys.executable, "-c", code, str(self.store.database), str(self.store.object_root)],
                                capture_output=True, text=True, timeout=90, check=False)
        self.assertEqual(result.returncode, 73, result.stderr)
        before = self.store.status(document_id)
        self.assertEqual(before["job"]["status"], "PROCESSING")
        self.assertIsNotNone(before["parser_checkpoint"])
        self.assertEqual(before["current_revision"], 0)
        self.assertEqual(list((self.store.object_root / "quarantine").iterdir()), [])
        deadline = time.monotonic() + 5
        resumed = None
        with patch("docwork.worker._docker_run", side_effect=AssertionError("Parser must not rerun")):
            while time.monotonic() < deadline and resumed is None:
                resumed = process_one(self.store, "recovery-probe")
                if resumed is None:
                    time.sleep(.1)
        self.assertEqual(resumed, document_id)
        after = self.store.status(document_id)
        self.assertEqual(after["job"]["attempts"], 2)
        self.assertGreater(after["job"]["fence"], before["job"]["fence"])
        self.assertEqual(after["status"], "REVIEW_READY")
        self.assertEqual(self.store.get(document_id)["record"]["fields"]["invoice_number"]["value"], "AST-1001")
        self.evidence.update(process_exit_code=73, lease_seconds=2,
                             fence_before=before["job"]["fence"], fence_after=after["job"]["fence"],
                             parser_checkpoint=after["parser_checkpoint"], parser_rerun=False,
                             orphaned_scratch_directories=len(list((self.store.object_root / "quarantine").iterdir())),
                             model_request="Exit callback; no model inference")

    def test_container_runtime_restrictions(self):
        code = """
import hashlib, json, os, socket, subprocess, sys
from pathlib import Path
import PIL
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
def cannot_write(path):
    try:
        with open(path, 'ab'):
            pass
    except OSError:
        return True
    return False
Path('/output/probe').write_text('allowed')
Path('/tmp/probe').write_text('allowed')
print(json.dumps({
    'uid': os.getuid(), 'gid': os.getgid(), 'cap_effective': status['CapEff'].strip(),
    'no_new_privileges': status['NoNewPrivs'].strip(),
    'interfaces': [name for _, name in socket.if_nameindex()],
    'up_interfaces': [name for _, name in socket.if_nameindex() if int(Path('/sys/class/net', name, 'flags').read_text(), 16) & 1],
    'ipv4_routes': Path('/proc/net/route').read_text().splitlines()[1:],
    'root_write_denied': cannot_write('/root-write-probe'),
    'input_write_denied': cannot_write('/input/original'),
    'python': sys.version.split()[0], 'pillow': PIL.__version__,
    'packages': subprocess.check_output(['dpkg-query', '-W', '-f=${Package}=${Version}\\n', 'poppler-utils', 'tesseract-ocr', 'tesseract-ocr-eng', 'tesseract-ocr-osd'], text=True).splitlines(),
    'ocr_assets': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/usr/share/tesseract-ocr/5/tessdata').glob('*.traineddata')},
    'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app/docwork').glob('*.py')}
}))
"""
        command = self.probe_command(code)
        # Inspect a real container made with the production policy, then execute
        # the probe in that same container. Creation does not start the process.
        command[1] = "create"
        command.remove("--rm")
        created = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
        container_id = created.stdout.strip()
        try:
            inspected = json.loads(subprocess.check_output(["docker", "inspect", container_id], timeout=15))[0]
            host = inspected["HostConfig"]
            self.assertEqual(host["NetworkMode"], "none")
            self.assertTrue(host["ReadonlyRootfs"])
            self.assertEqual(host["CapDrop"], ["ALL"])
            self.assertIn("no-new-privileges", host["SecurityOpt"])
            self.assertEqual(host["Memory"], 1024 ** 3)
            self.assertEqual(host["NanoCpus"], 2 * 10 ** 9)
            self.assertEqual(host["PidsLimit"], 64)
            self.assertEqual(inspected["Config"]["User"], "65534:65534")
            mounts = {mount["Destination"]: mount["RW"] for mount in inspected["Mounts"]}
            self.assertEqual(mounts, {"/input/original": False, "/output": True})
            self.assertEqual(host["Tmpfs"], {"/tmp": "rw,nosuid,noexec,size=64m"})
            result = subprocess.run(["docker", "start", "-a", container_id], capture_output=True,
                                    text=True, timeout=30, check=True)
            probe = json.loads(result.stdout)
            self.evidence.update(image_id=inspected["Image"], runtime=probe,
                                 container_policy={key: host[key] for key in
                                     ("NetworkMode", "ReadonlyRootfs", "CapDrop", "SecurityOpt", "Memory", "NanoCpus", "PidsLimit", "Tmpfs")},
                                 mounts=mounts)
            self.assertEqual((probe["uid"], probe["gid"]), (65534, 65534))
            self.assertEqual(int(probe["cap_effective"], 16), 0)
            self.assertEqual(probe["no_new_privileges"], "1")
            self.assertEqual(probe["up_interfaces"], ["lo"])
            self.assertEqual(probe["ipv4_routes"], [])
            self.assertTrue(probe["root_write_denied"] and probe["input_write_denied"])
            source_root = Path(__file__).resolve().parents[1] / "src/docwork"
            self.assertEqual(probe["source_sha256"], {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_root.glob("*.py")
            }, "Parser image is stale; rebuild it before verification")
        finally:
            subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, timeout=15, check=True)


if __name__ == "__main__":
    unittest.main()
