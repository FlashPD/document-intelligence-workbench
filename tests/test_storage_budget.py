import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from docwork.storage_budget import StorageBudget


class StorageInventoryTests(unittest.TestCase):
    def test_disappearing_wal_uses_observed_metadata_without_spurious_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "review.sqlite"
            database.write_bytes(b"database-fixture")
            wal = Path(str(database) + "-wal")
            wal.write_bytes(b"wal-fixture")
            budget = StorageBudget(database, root / "objects", root / "exports")
            original = Path.lstat

            def read(path, *args, **kwargs):
                entry = original(path, *args, **kwargs)
                if path == wal:
                    wal.unlink()
                return entry

            with patch.object(Path, "lstat", read):
                inventory = budget.inventory()
            self.assertFalse(wal.exists())
            self.assertEqual(inventory["used_bytes"], len(b"database-fixturewal-fixture"))
            self.assertEqual(inventory["unsafe_entries"], 0)

    def test_link_accounting_and_nonregular_refusal_survive_metadata_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "review.sqlite"
            database.write_bytes(b"database-fixture")
            objects = root / "objects"
            objects.mkdir()
            os.link(database, objects / "same-file")
            budget = StorageBudget(database, objects, root / "exports")
            self.assertEqual(budget.inventory()["used_bytes"], database.stat().st_size)
            link = objects / "external-link"
            link.symlink_to(database)
            self.assertEqual(budget.inventory()["unsafe_entries"], 1)
            os.mkfifo(objects / "unexpected-pipe")
            with self.assertRaisesRegex(ValueError, "Unexpected artifact entry"):
                budget.inventory()
