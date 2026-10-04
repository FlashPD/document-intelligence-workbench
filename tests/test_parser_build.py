import copy
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("parser_build", ROOT / "scripts/verify_parser_build.py")
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class ParserBuildTests(unittest.TestCase):
    def setUp(self):
        self.lock = BUILD.check_inputs(ROOT)
        self.inventory = {"python": self.lock["python"], "pillow": self.lock["pillow"]["version"],
                          "machine": "aarch64", "packages": {**self.lock["packages"], "transitive": "1.0"},
                          "ocr_assets": self.lock["ocr_assets"]}

    def test_changed_runtime_or_language_bytes_cannot_pass(self):
        BUILD.INSTALLER.verify_inventory(self.lock, self.inventory)
        for key, value in (("python", "3.12.13"), ("pillow", "12.0.0"), ("machine", "riscv64"),
                           ("packages", {**self.inventory["packages"], "poppler-utils": "unlocked"}),
                           ("ocr_assets", {**self.lock["ocr_assets"], "eng.traineddata": "0" * 64})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                BUILD.INSTALLER.verify_inventory(self.lock, {**self.inventory, key: value})

    def test_moving_base_or_bypassed_installer_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / "sandbox", root / "sandbox", ignore=shutil.ignore_patterns("__pycache__"))
            path = root / "sandbox/Dockerfile"
            original = path.read_text()
            for content in (original.replace(self.lock["base"], "python:3.12-slim"),
                            original.replace("RUN python /opt/docwork-build/install_parser.py", "RUN pip install Pillow")):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    BUILD.check_inputs(root)

    def test_missing_hash_unexpected_wheel_and_mutable_snapshot_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for change in ("hash", "wheel", "snapshot", "osd"):
                lock = copy.deepcopy(self.lock)
                if change == "hash":
                    lock["pillow"]["wheels"]["aarch64"]["sha256"] = ""
                elif change == "wheel":
                    lock["pillow"]["wheels"]["aarch64"]["url"] = "https://example.invalid/Pillow.tar.gz"
                elif change == "snapshot":
                    lock["debian_snapshot"] = "latest"
                else:
                    del lock["packages"]["tesseract-ocr-osd"]
                (root / "parser-build-lock.json").write_text(json.dumps(lock))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    BUILD.INSTALLER.read_lock(root)

    def test_image_source_or_copied_build_lock_drift_cannot_pass(self):
        probe = {"runtime": self.inventory,
                 "source_sha256": {p.name: BUILD.digest(p) for p in (ROOT / "src/docwork").glob("*.py")},
                 "build_sha256": {name: BUILD.digest(ROOT / "sandbox" / name)
                                  for name in ("install_parser.py", "parser-build-lock.json")}}
        BUILD.check_probe(ROOT, self.lock, probe)
        for key in ("source_sha256", "build_sha256"):
            changed = copy.deepcopy(probe)
            changed[key][next(iter(changed[key]))] = "0" * 64
            with self.subTest(key=key), self.assertRaises(ValueError):
                BUILD.check_probe(ROOT, self.lock, changed)


if __name__ == "__main__":
    unittest.main()
