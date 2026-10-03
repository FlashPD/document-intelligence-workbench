import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("release_coordinator", Path(__file__).resolve().parents[1] / "scripts/complete_release_evaluations.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleaseCoordinatorTests(unittest.TestCase):
    def test_resume_cannot_advance_to_receipts_without_a_complete_invoice_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            arguments = ["coordinator", "--invoice-run", str(root / "invoice"), "--receipt-root", str(root / "receipts"), "--resume-invoice"]
            with patch.object(MODULE, "__file__", str(root / "scripts/coordinator.py")), \
                    patch("sys.argv", arguments), \
                    patch.object(MODULE.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run, \
                    patch.object(MODULE, "verify_invoice_model") as verify:
                with self.assertRaisesRegex(RuntimeError, "stopped without a report"):
                    MODULE.main()
            self.assertEqual(run.call_count, 1)
            self.assertIn("--resume", run.call_args.args[0])
            verify.assert_not_called()
            self.assertFalse((root / "receipts").exists())
