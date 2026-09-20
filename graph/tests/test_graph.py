import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

spec = importlib.util.spec_from_file_location(
    "graph", Path(__file__).parents[1] / "graph.py"
)
graph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graph)


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / "source"
        self.source.mkdir()
        self.output = self.base / "output"
        self.text = "# Saturn\nSaturn hosts Titan.\n## Evidence\n[Other](other.md)\n"
        self.write_source({"host.md": self.text, "other.md": "# Other\nOther notes.\n"})
        self.assertions = self.base / "assertions.json"
        self.assertion = {
            "subject": "Saturn",
            "predicate": "hosts",
            "object": "Titan",
            "path": "host.md",
            "quote": "Saturn hosts Titan.",
            "start": 9,
        }
        self.assertions.write_text(json.dumps([self.assertion]))

    def write_source(self, files):
        manifest = {"revision": "fixture-revision", "files": []}
        for path, text in files.items():
            (self.source / path).write_text(text)
            manifest["files"].append(
                {"path": path, "sha256": hashlib.sha256(text.encode()).hexdigest()}
            )
        (self.source / "manifest.json").write_text(json.dumps(manifest))

    def test_evidence_navigation_and_deterministic_repeat(self):
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        receipt = graph.build(self.source, self.output, self.assertions)
        files = {
            p.name: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in self.output.iterdir()
        }
        self.assertEqual(
            graph.build(self.source, self.output, self.assertions), receipt
        )
        self.assertEqual(
            files,
            {
                p.name: (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.output.iterdir()
            },
        )
        self.assertEqual(
            before, {p.name: p.read_bytes() for p in self.source.iterdir()}
        )
        found = graph.inspect(self.output, "Saturn")
        claim = next(r for r in found if r["kind"] == "assertion")
        self.assertEqual(claim["quote"], self.assertion["quote"])
        self.assertEqual(claim["revision"], "fixture-revision")
        records = [
            json.loads(line)
            for line in (self.output / "graph.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            next(r for r in records if r["kind"] == "link")["status"],
            "resolved-document",
        )
        self.assertEqual(len([r for r in records if r["kind"] == "section"]), 3)

    def test_changed_source_rejected(self):
        (self.source / "host.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            graph.build(self.source, self.output)
        self.assertFalse(self.output.exists())

    def qualification(self):
        return {
            "source_status": "proposed",
            "scope": {
                "text": "Historical fixture; not a live host observation",
                "path": "host.md",
                "quote": "# Saturn",
                "start": 0,
            },
            "annotations": [
                {
                    "id": "a1",
                    "kind": "interpretation",
                    "reason": "Fixture statement requires review",
                    "resolution_question": "Does this still hold?",
                    "path": "other.md",
                    "quote": "Other notes.",
                    "start": 8,
                }
            ],
        }

    def test_qualification_survives_isolated_retrieval(self):
        self.assertions.write_text(
            json.dumps([{**self.assertion, "qualification": self.qualification()}])
        )
        graph.build(self.source, self.output, self.assertions)
        claim = next(
            r for r in graph.inspect(self.output, "Saturn") if r["kind"] == "assertion"
        )
        self.assertEqual(claim["status"], "extracted")
        q = claim["qualification"]
        self.assertEqual(q["source_status"], "proposed")
        self.assertEqual(q["scope"]["text"], self.qualification()["scope"]["text"])
        self.assertEqual(q["annotations"][0]["quote"], "Other notes.")
        self.assertEqual(q["annotations"][0]["revision"], "fixture-revision")
        self.assertEqual(
            q["annotations"][0]["source_sha256"],
            hashlib.sha256(b"# Other\nOther notes.\n").hexdigest(),
        )

    def test_invalid_qualification_rejected_before_output(self):
        for mutation in ("quote", "scope", "path", "status", "annotation", "start"):
            q = self.qualification()
            if mutation == "quote":
                q["scope"]["quote"] = "Fabricated scope"
            elif mutation == "scope":
                q["scope"]["text"] = ""
            elif mutation == "path":
                q["annotations"][0]["path"] = "../secret"
            elif mutation == "status":
                q["source_status"] = "human-reviewed"
            elif mutation == "start":
                q["scope"]["start"] = True
            else:
                q["annotations"][0]["shell"] = "untrusted"
            self.assertions.write_text(
                json.dumps([{**self.assertion, "qualification": q}])
            )
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                graph.build(self.source, self.output, self.assertions)
            self.assertFalse(self.output.exists())

    def test_paths_and_symlinks_rejected(self):
        for path in ["../host.md", "/etc/passwd", "a/../host.md", "a\\b", "./host.md"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                graph.safe_path(path)
        (self.source / "host.md").unlink()
        (self.source / "host.md").symlink_to(self.source / "other.md")
        with self.assertRaisesRegex(ValueError, "Symlinks"):
            graph.build(self.source, self.output)

    def test_output_escape_and_symlink_rejected(self):
        with self.assertRaises(ValueError):
            graph.build(self.source, self.source / "out")
        self.output.mkdir()
        (self.output / "graph.sqlite").symlink_to(self.source / "host.md")
        with self.assertRaises(ValueError):
            graph.build(self.source, self.output)
        self.assertEqual((self.source / "host.md").read_text(), self.text)

    def test_invalid_model_output_rejected(self):
        for changes in [
            {"shell": "rm -rf /"},
            {"status": "human-reviewed"},
            {"quote": "Fabrication"},
            {"start": True},
            {"path": "../secret"},
            {"extraction": {"method": "model", "command": "delete"}},
        ]:
            with self.subTest(changes=changes):
                self.assertions.write_text(json.dumps([{**self.assertion, **changes}]))
                with self.assertRaises(ValueError):
                    graph.build(self.source, self.output, self.assertions)
                self.assertFalse(self.output.exists())

    def test_changed_inventory_removes_old_evidence(self):
        graph.build(self.source, self.output, self.assertions)
        self.write_source({"other.md": "# Other\nUpdated notes.\n"})
        graph.build(self.source, self.output)
        self.assertEqual(graph.inspect(self.output, "Saturn"), [])
        self.assertNotIn("host.md", graph.inspect(self.output)["sources"])

    def test_output_integrity_rejected(self):
        graph.build(self.source, self.output)
        (self.output / "graph.jsonl").write_text("{}\n")
        with self.assertRaises(ValueError):
            graph.inspect(self.output)

    def test_code_fences_not_structural_facts(self):
        self.write_source(
            {"host.md": "# Real\n```md\n# Fake\n[Fake](other.md)\n```\n## After\n"}
        )
        records, _ = graph.make_graph(self.source)
        self.assertEqual(
            sorted(r["title"] for r in records if r["kind"] == "section"),
            ["After", "Real"],
        )
        self.assertEqual([r for r in records if r["kind"] == "link"], [])

    def test_size_limit_and_duplicate_keys(self):
        with (
            mock.patch.object(graph, "MAX_FILE_BYTES", 1),
            self.assertRaises(ValueError),
        ):
            graph.build(self.source, self.output)
        (self.source / "manifest.json").write_text(
            '{"revision":"a","revision":"b","files":[]}'
        )
        with self.assertRaises(ValueError):
            graph.build(self.source, self.output)


if __name__ == "__main__":
    unittest.main()
