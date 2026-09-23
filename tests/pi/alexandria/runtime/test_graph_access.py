"""Offline graph inspection fixtures. No inference, credentials or active data."""

import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from pi.alexandria.graph import graph as builder
from pi.alexandria.runtime import app
from pi.alexandria.runtime.graph_access import GraphReader, OptionalGraph


def dump(root, name, value):
    (root / name).write_text(json.dumps(value))


def fixture(root):
    (root / "source").mkdir()
    text = "# Proposed policy\nRelease requires owner approval.\nThis remains a proposal.\n"
    (root / "source/policy.md").write_text(text)
    manifest = {
        "revision": "synthetic-revision",
        "files": [
            {"path": "policy.md", "sha256": hashlib.sha256(text.encode()).hexdigest()}
        ],
    }
    dump(root, "source/manifest.json", manifest)
    claim = {
        "id": "c1",
        "subject": "Release",
        "predicate": "requires",
        "object": "owner approval",
        "path": "policy.md",
        "quote": "Release requires owner approval.",
        "source_status": "proposed",
        "scope": {"text": "Unaccepted proposal", "path": "policy.md", "quote": text},
        "annotation_ids": ["a1"],
    }
    note = {
        "id": "a1",
        "kind": "open_decision",
        "path": "policy.md",
        "quote": "This remains a proposal.",
        "reason": "Not approved",
        "resolution_question": "Will the owner accept it?",
    }
    candidate = {"claims": [claim], "annotations": [note], "pages": [], "coverage": []}
    for name in ("02-frozen.json", "03-humanizer.json"):
        dump(root, name, candidate)
    dump(root, "run-manifest.json", {"source": manifest})
    assertion = {
        k: claim[k] for k in ("subject", "predicate", "object", "path", "quote")
    }
    assertion.update(
        start=text.index(claim["quote"]),
        status="extracted",
        extraction={"method": "synthetic"},
        qualification={
            "source_status": "proposed",
            "scope": {**claim["scope"], "start": 0},
            "annotations": [{**note, "start": text.index(note["quote"])}],
        },
    )
    dump(root, "assertions.json", [assertion])
    receipt = builder.build(
        root / "source", root / "candidate-graph", root / "assertions.json"
    )
    candidate_hash = hashlib.sha256(
        json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    dump(
        root,
        "hold-receipt.json",
        {
            "status": "held-pending-owner-review",
            "automatic_acceptance": False,
            "model_review_pass": False,
            "candidate_hash": candidate_hash,
        },
    )
    dump(
        root,
        "operator-result.json",
        {
            "status": "completed-held",
            "automatic_acceptance": False,
            "model_review_pass": False,
            "candidate_hash": candidate_hash,
            "graph_sha256": receipt["graph_sha256"],
            "source_revision": manifest["revision"],
        },
    )
    return text


class GraphAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.text = fixture(self.root)

    def test_discovery_navigation_qualifiers_and_exact_pagination(self):
        reader = GraphReader(self.root)
        result = reader.search("approval")
        self.assertFalse(result["review"]["accepted"])
        self.assertFalse(result["review"]["model_review_pass"])
        relation = result["matches"][0]
        self.assertEqual(relation["qualification"]["source_status"], "proposed")
        self.assertEqual(
            relation["qualification"]["annotations"][0]["quote"],
            "This remains a proposal.",
        )
        entity = reader.read(relation["subject"]["id"])
        self.assertEqual(entity["entity"]["label"], "Release")
        self.assertEqual(reader.search(entity["entity"]["id"])["total_matches"], 1)
        self.assertEqual(reader.search("approval", 1, 1)["matches"], [])
        parts, start = [], 0
        while True:
            response = reader.read(relation["id"], start, 7)
            parts.append(response["source"]["text"])
            start = response["source"]["next_start"]
            if start is None:
                break
        self.assertEqual("".join(parts), self.text)
        self.assertEqual(
            reader.read(relation["document_id"])["source"]["text"], self.text
        )
        self.assertEqual(response["source_revision"], "synthetic-revision")

    def test_bounds_paths_and_no_mutation(self):
        before = {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }
        reader = GraphReader(self.root)
        for value in ("../policy.md", "/etc/passwd", "policy.md", "assertion:' OR 1=1"):
            with self.assertRaises(ValueError):
                reader.read(value)
        for args in (("x", 21, 0), ("x", 1, -1), ("", 1, 0), ("x", True, 0)):
            with self.assertRaises(ValueError):
                reader.search(*args)
        after = {
            str(p.relative_to(self.root)): p.read_bytes()
            for p in self.root.rglob("*")
            if p.is_file()
        }
        self.assertEqual(before, after)

    def test_rejects_partial_tampered_source_graph_sqlite_and_qualification(self):
        cases = [
            "operator-result.json",
            "source/policy.md",
            "candidate-graph/graph.jsonl",
            "candidate-graph/graph.sqlite",
            "03-humanizer.json",
            "02-frozen.json",
        ]
        for name in cases:
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                path.write_bytes(b"tampered")
                self.assertFalse(OptionalGraph(self.root).status()["available"])
                path.write_bytes(original)
        path = self.root / "operator-result.json"
        path.unlink()
        self.assertFalse(OptionalGraph(self.root).status()["available"])

    def test_sqlite_jsonl_mismatch_rejected(self):
        with sqlite3.connect(self.root / "candidate-graph/graph.sqlite") as db:
            db.execute("UPDATE records SET kind='entity' WHERE kind='assertion'")
        self.assertFalse(OptionalGraph(self.root).status()["available"])

    def test_symlink_source_and_bundle_rejected(self):
        path = self.root / "source/policy.md"
        path.rename(self.root / "elsewhere.md")
        path.symlink_to(self.root / "elsewhere.md")
        self.assertFalse(OptionalGraph(self.root).status()["available"])
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.assertFalse(OptionalGraph(alias).status()["available"])

    def test_explicit_failure_overrides_completion(self):
        for name in ("failure.json", "operator-failure.json"):
            dump(self.root, name, {"status": "failed"})
            self.assertFalse(OptionalGraph(self.root).status()["available"])
            (self.root / name).unlink()

    def test_resealed_graph_cannot_change_frozen_qualification(self):
        path = self.root / "candidate-graph/graph.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        relation = next(r for r in rows if r["kind"] == "assertion")
        relation["qualification"]["source_status"] = "accepted"
        raw = b"".join(builder.encoded(r) + b"\n" for r in rows)
        path.write_bytes(raw)
        for name in ("candidate-graph/receipt.json", "operator-result.json"):
            value = json.loads((self.root / name).read_text())
            value["graph_sha256"] = builder.digest(raw)
            dump(self.root, name, value)
        with sqlite3.connect(self.root / "candidate-graph/graph.sqlite") as db:
            db.execute(
                "UPDATE records SET data=? WHERE id=?",
                (builder.encoded(relation).decode(), relation["id"]),
            )
        self.assertFalse(OptionalGraph(self.root).status()["available"])

    def test_mutable_caller_results_do_not_change_loaded_evidence(self):
        reader = GraphReader(self.root)
        result = reader.search("Release")
        result["review"]["accepted"] = True
        result["matches"][0]["qualification"]["source_status"] = "accepted"
        self.assertFalse(reader.status()["review"]["accepted"])
        self.assertEqual(
            reader.search("Release")["matches"][0]["qualification"]["source_status"],
            "proposed",
        )

    def test_quality_findings_are_visible_without_accepting_candidate(self):
        dump(
            self.root,
            "quality-findings.json",
            {
                "version": "2026.09.21",
                "status": "held",
                "automatic_acceptance": False,
                "blocking_findings": 1,
                "findings": [{"kind": "ambiguity_requires_hold"}],
            },
        )
        status = GraphReader(self.root).status()
        self.assertTrue(status["available"])
        self.assertFalse(status["review"]["accepted"])
        self.assertEqual(status["review"]["blocking_findings"], 1)

    def test_optional_unavailable_and_document_tool_defaults(self):
        registered = {}

        class Server:
            def __init__(self, name):
                pass

            def tool(self):
                def register(fn):
                    registered[fn.__name__] = fn
                    return fn

                return register

            def run(self, **kwargs):
                pass

        class Table:
            def search(self, *_):
                return self

            def where(self, *_):
                return self

            def distance_type(self, *_):
                return self

            def limit(self, *_):
                return self

            def to_list(self):
                return [copy.deepcopy(row)]

        row = {
            "path": "doc.md",
            "revision": "document-index-revision",
            "sha256": hashlib.sha256(b"Original document index").hexdigest(),
            "text": "Original document index",
            "offset": 0,
        }
        data = self.root / "data"
        (data / "build/source").mkdir(parents=True)
        (data / "build/source/doc.md").write_text("Original document index")
        manifest = {
            "database": "build",
            "revision": row["revision"],
            "embedding_model": "fixture",
        }
        dump(data, "active.json", manifest)
        vector = types.SimpleNamespace(tolist=lambda: [0.0])
        engine = types.SimpleNamespace(query_embed=lambda _: iter([vector]))
        modules = {
            "lancedb": types.SimpleNamespace(
                connect=lambda _: types.SimpleNamespace(open_table=lambda _: Table())
            ),
            "mcp.server.fastmcp": types.SimpleNamespace(FastMCP=Server),
        }
        with (
            patch.dict(sys.modules, modules),
            patch.object(app, "embedder", return_value=engine),
            patch.object(app, "token_counter", return_value=len),
        ):
            with self.assertRaisesRegex(ValueError, "graph inspection flag"):
                app.serve(data, self.root)
            app.serve(data, self.root, enable_graph_inspection=True)
            self.assertEqual(
                set(registered),
                {"search", "read_document", "text_search", "status", "list_files"},
            )
            self.assertEqual(registered["status"]()["database"], manifest["database"])
            self.assertFalse(registered["status"]()["inbound"]["enabled"])
            self.assertEqual(
                registered["read_document"]("doc.md")["text"], "Original document index"
            )
            self.assertEqual(
                registered["search"]("Original")[0]["revision"], row["revision"]
            )
            self.assertEqual(registered["list_files"]()["entries"][0]["path"], "doc.md")
            relation = registered["search"]("approval", graph=True)["matches"][0]
            self.assertEqual(
                registered["read_document"](relation["id"], graph=True)["source"][
                    "text"
                ],
                self.text,
            )
            app.serve(data, self.root / "missing", enable_graph_inspection=True)
            self.assertFalse(registered["status"](graph=True)["available"])
            self.assertEqual(
                registered["read_document"]("doc.md")["text"], "Original document index"
            )
            with self.assertRaises(ValueError):
                registered["search"]("approval", graph=True)


if __name__ == "__main__":
    unittest.main()
