import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_structured_provenance.py"
SPEC = importlib.util.spec_from_file_location("check_structured_provenance", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class StructuredProvenanceTests(unittest.TestCase):
    def write(self, root: Path, relative: str, value: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")

    def test_classifies_local_values_without_returning_their_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "records/receipt.json",
                json.dumps(
                    {
                        "source_path": str(root / "docs/source.md"),
                        "external_path": "/Users/example/private.md",
                        "url": "https://example.com/reference",
                    }
                ),
            )
            report = MODULE.scan(root)
            self.assertEqual(report["status"], "hold")
            self.assertEqual(report["outside_checkout_values"], 1)
            self.assertEqual(len(report["findings"]), 2)
            self.assertNotIn("/Users/example/private.md", json.dumps(report))

    def test_relative_inside_and_remote_values_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "docs/source.md", "source\n")
            self.write(
                root,
                "records/receipt.json",
                json.dumps(
                    {
                        "source_path": "../docs/source.md",
                        "url": "https://example.com/reference",
                    }
                ),
            )
            report = MODULE.scan(root)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["outside_checkout_values"], 0)

    def test_parse_errors_are_reported_and_exclusions_are_respected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "history/bad.json", "not json\n")
            self.write(root, "history/ignored.json", json.dumps({"path": "/bad"}))
            report = MODULE.scan(root, ["history/ignored.json"])
            self.assertEqual(report["status"], "hold")
            self.assertEqual(report["parse_error_count"], 1)
            self.assertEqual(report["path_like_values"], 0)

    def test_finding_storage_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "records/receipt.json",
                json.dumps(
                    {"paths": [{"path": f"/Users/example/{i}"} for i in range(5)]}
                ),
            )
            report = MODULE.scan(root, max_findings=2)
            self.assertEqual(report["path_like_values"], 5)
            self.assertEqual(report["outside_checkout_values"], 5)
            self.assertEqual(report["stored_findings"], 2)
            self.assertTrue(report["findings_truncated"])
            self.assertEqual(
                report["outside_by_file"][0]["file"], "records/receipt.json"
            )

    def test_jsonl_pointers_include_record_line_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "records/receipts.jsonl",
                '{"source_path": "/Users/example/first.md"}\n'
                '{"source_path": "/Users/example/second.md"}\n',
            )
            report = MODULE.scan(root)
            pointers = [finding["pointer"] for finding in report["findings"]]
            self.assertEqual(pointers, ["/0/source_path", "/1/source_path"])


if __name__ == "__main__":
    unittest.main()
