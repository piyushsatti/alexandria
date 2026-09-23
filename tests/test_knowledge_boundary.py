import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_knowledge_boundary.py"
SPEC = importlib.util.spec_from_file_location("check_knowledge_boundary", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class KnowledgeBoundaryTests(unittest.TestCase):
    def write(self, root: Path, relative: str, content: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_valid_relative_and_remote_links_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "docs/target.md", "target\n")
            self.write(
                root,
                "docs/index.md",
                """---\nlinks:\n  to: [target.md]\n---\n\n[inside](target.md)\n[remote](https://example.com)\n\n```text\n[fixture](/Users/example/private.md)\n```\n\n[^note]: preserved footnote text\n""",
            )
            self.assertEqual(MODULE.scan(root), [])

    def test_local_inline_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "index.md", "[private](/Users/example/private.md)\n")
            findings = MODULE.scan(root)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].reason, "local target")

    def test_absolute_reference_target_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "index.md", "[source]: /Users/example/source.md\n")
            findings = MODULE.scan(root)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].reason, "local target")

    def test_missing_and_escaping_relative_targets_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(
                root,
                "index.md",
                "[missing](missing.md)\n[escape](../outside.md)\n",
            )
            findings = MODULE.scan(root)
            self.assertEqual(
                [item.reason for item in findings],
                ["relative target is missing", "target escapes checkout"],
            )

    def test_excluded_prefix_is_not_scanned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "evidence/source.md", "[missing](missing.md)\n")
            self.assertEqual(MODULE.scan(root, ["evidence"]), [])

    def test_existing_directory_target_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs" / "topic").mkdir(parents=True)
            self.write(root, "docs/index.md", "[topic](topic/)\n")
            self.assertEqual(MODULE.scan(root), [])

    def test_inline_code_link_example_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "index.md", "`([citation](placeholder))`\n")
            self.assertEqual(MODULE.scan(root), [])


if __name__ == "__main__":
    unittest.main()
