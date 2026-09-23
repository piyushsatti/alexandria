"""Quality receipts must carry replayable evidence."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pi.alexandria.graph.evidence import verify_receipt


class EvidenceTests(unittest.TestCase):
    def test_missing_provider_and_replay_artifacts_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "comparison.json").write_text(
                json.dumps(
                    {
                        "goal_id": "goal",
                        "phase": "step",
                        "status": "pass",
                        "source_revision": "source",
                        "source_manifest_sha256": "a" * 64,
                        "code_revision": "code",
                        "configuration_identity": "config",
                        "model_provider": "provider",
                        "model_identity": "model",
                        "expectation_revision": "expectations",
                        "output_manifest_sha256": "b" * 64,
                        "checks": {},
                        "active_index_changed": False,
                        "deployment_changed": False,
                        "next_action": "hold",
                        "provider_route": "Not independently verified; selected through local gateway alias",
                        "independent_comparison_pass": True,
                    }
                )
            )
            result = verify_receipt(root)
            self.assertEqual(result["status"], "hold")
            kinds = {finding["kind"] for finding in result["findings"]}
            self.assertIn("provider_identity_unverified", kinds)
            self.assertIn("output_manifest_missing", kinds)
            self.assertIn("comparison_script_missing", kinds)

    def test_replayable_receipt_passes_without_accepting_knowledge(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output.json"
            script = root / "compare.py"
            output.write_text('{"result":"held"}\n')
            script.write_text("print('compare')\n")
            receipt = {
                "goal_id": "goal",
                "phase": "step",
                "status": "pass",
                "source_revision": "source",
                "source_manifest_sha256": "a" * 64,
                "code_revision": "code",
                "configuration_identity": "config",
                "model_provider": "provider",
                "model_identity": "model",
                "expectation_revision": "expectations",
                "output_manifest_sha256": hashlib.sha256(
                    output.read_bytes()
                ).hexdigest(),
                "output_manifest_path": "output.json",
                "comparison_script_path": "compare.py",
                "comparison_script_sha256": hashlib.sha256(
                    script.read_bytes()
                ).hexdigest(),
                "checks": {"structural": "pass"},
                "active_index_changed": False,
                "deployment_changed": False,
                "next_action": "owner review",
                "provider_identity": {
                    "verified": True,
                    "provider": "provider",
                    "model": "model",
                },
                "provider_route": "https://provider.example.invalid/v1",
                "independent_comparison_pass": True,
            }
            (root / "comparison.json").write_text(json.dumps(receipt))
            result = verify_receipt(root)
            self.assertEqual(result["status"], "pass")
            self.assertFalse(result["automatic_acceptance"])


if __name__ == "__main__":
    unittest.main()
