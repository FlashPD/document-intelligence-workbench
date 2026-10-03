from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docwork.cord import MAPPING_VERSION, fetch_cord, verify_cord


class CordTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def manifest(self):
        docs = []
        for split in ("validation", "test"):
            for index in range(100):
                id = f"cord-{split}-{index:03d}"
                asset = self.root / f"{id}.png"
                asset.write_bytes(id.encode())
                digest = hashlib.sha256(asset.read_bytes()).hexdigest()
                docs.append({"id": id, "split": split, "source_sha256": digest,
                             "asset": {"path": asset.name, "sha256": digest}})
        path = self.root / "manifest.json"
        path.write_text(json.dumps({"manifest_version": MAPPING_VERSION, "documents": docs}))
        return path

    def test_official_counts_and_image_integrity_are_required(self):
        path = self.manifest()
        self.assertEqual(verify_cord(path)["counts"], {"validation": 100, "test": 100})
        data = json.loads(path.read_text())
        data["documents"].pop()
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "all 100"):
            verify_cord(path)

    def test_cross_split_duplicate_and_traversal_are_rejected(self):
        path = self.manifest()
        data = json.loads(path.read_text())
        original = path.read_text()
        data["documents"][100]["source_sha256"] = data["documents"][0]["source_sha256"]
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "crosses"):
            verify_cord(path)
        data = json.loads(original)
        data["documents"][0]["asset"]["path"] = "../escape.png"
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "path or checksum"):
            verify_cord(path)

    def test_explicit_fetch_verifies_size_hash_and_reuses_only_matching_bytes(self):
        payload = b"pinned dataset fixture"
        profile = self.root / "profile.json"
        profile.write_text(json.dumps({"dataset_id": "fixture", "revision": "pinned", "files": [{
            "split": "validation", "path": "data/validation.parquet", "size_bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()}]}))
        cache = self.root / "cache"
        with patch("docwork.cord.urllib.request.urlopen", return_value=io.BytesIO(payload)) as download:
            self.assertEqual(fetch_cord(profile, cache)["status"], "verified")
            self.assertEqual(download.call_count, 1)
            self.assertIn("/resolve/pinned/", download.call_args.args[0])
        with patch("docwork.cord.urllib.request.urlopen", side_effect=AssertionError("No repeat download")):
            fetch_cord(profile, cache)
        (cache / "validation.parquet").write_bytes(b"modified")
        with self.assertRaisesRegex(ValueError, "differs from its pin"):
            fetch_cord(profile, cache)

    def test_failed_fetch_cannot_publish_partial_shard(self):
        profile = self.root / "profile.json"
        profile.write_text(json.dumps({"dataset_id": "fixture", "revision": "pinned", "files": [{
            "split": "test", "path": "data/test.parquet", "size_bytes": 1, "sha256": "0" * 64}]}))
        cache = self.root / "cache"
        with patch("docwork.cord.urllib.request.urlopen", return_value=io.BytesIO(b"bad")):
            with self.assertRaisesRegex(ValueError, "exceeds"):
                fetch_cord(profile, cache)
        self.assertEqual(list(cache.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
