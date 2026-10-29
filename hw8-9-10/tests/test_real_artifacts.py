import json
import unittest
from pathlib import Path

import release

ROOT = Path(__file__).resolve().parents[1]


class RealArtifactContractTests(unittest.TestCase):
    def test_accepted_baseline_is_bound_to_full_evaluation(self):
        root = ROOT / "artifacts" / "real"
        manifest = json.loads((root / "manifest.json").read_text())
        report = json.loads((root / "baseline-report.json").read_text())
        self.assertIsNone(release.validate_release(manifest, report))
        self.assertEqual(report["decision"], "serve-previous-baseline")
        self.assertEqual(manifest["runtime"]["generation"]["max_new_tokens"], 32)
        self.assertFalse(manifest["runtime"]["generation"]["do_sample"])

    def test_candidate_rejection_is_preserved(self):
        report = json.loads((ROOT / "artifacts" / "real" / "release-report.json").read_text())
        self.assertIs(report["passed"], False)
        self.assertIn("behavioral", report["reason"])
        for source in report["sources"].values():
            self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main(verbosity=2)
