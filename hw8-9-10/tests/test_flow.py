import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PrefectFlowTests(unittest.TestCase):
    def test_failure_recovery_and_repeat_in_separate_processes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            subprocess.run(
                [sys.executable, "lab.py", "--workspace", str(root), "init"],
                cwd=ROOT, check=True, capture_output=True, text=True, timeout=20,
            )
            failed = subprocess.run(
                [sys.executable, "flow.py", "--workspace", str(root), "--fail-after-prepare"],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
            )
            self.assertNotEqual(failed.returncode, 0, failed.stdout)
            recovered = subprocess.run(
                [sys.executable, "flow.py", "--workspace", str(root)],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
            )
            self.assertEqual(recovered.returncode, 0, recovered.stdout)
            self.assertIn("FLOW_RESULT", recovered.stdout)
            repeated = subprocess.run(
                [sys.executable, "flow.py", "--workspace", str(root)],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
            )
            self.assertEqual(repeated.returncode, 0, repeated.stdout)
            self.assertIn("ALREADY_PUBLISHED", repeated.stdout)
            publications = (root / "publications.jsonl").read_text().splitlines()
            self.assertEqual(len(publications), 1)
            self.assertEqual(len(json.loads(publications[0])["release_id"]), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)
