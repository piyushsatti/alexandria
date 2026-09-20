"""Synthetic packages only; mutations are confined to fresh temporary directories."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "runtime/tests"))
import generation
from graph_access import OptionalGraph, read_json
from test_graph_access import fixture


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.text = fixture(self.bundle)
        self.store = self.root / "store"

    def tearDown(self):
        self.temp.cleanup()

    def package(self):
        return generation.package(self.bundle, self.store)["package_id"]

    def pointer(self):
        return (self.store / "graph-selection.json").read_bytes()

    def test_copy_allowlist_and_provenance(self):
        (self.bundle / "secret.txt").write_text("must not travel")
        manifest = read_json(self.bundle / "run-manifest.json")
        manifest.update(
            model="synthetic",
            runner_sha256="a" * 64,
            endpoint="must not travel",
            prompt="must not travel",
            actual_upstream_route="synthetic; not an inference result",
        )
        (self.bundle / "run-manifest.json").write_text(json.dumps(manifest))
        identity = self.package()
        root = self.store / "packages" / identity
        self.assertFalse((root / "secret.txt").exists())
        copied = read_json(root / "run-manifest.json")
        self.assertNotIn("endpoint", copied)
        self.assertNotIn("prompt", copied)
        self.assertEqual(copied["model"], "synthetic")
        self.assertEqual(copied["runner_sha256"], "a" * 64)
        self.assertEqual((root / "source/policy.md").read_text(), self.text)
        self.assertIsNone(generation.status(self.store)["current"])

    def test_persisted_selection_and_previous_restore(self):
        first, second = self.package(), self.package()
        generation.select(self.store, first)
        generation.select(self.store, second)
        graph = OptionalGraph(store=self.store)
        self.assertTrue(graph.status()["available"])
        self.assertFalse(graph.status()["review"]["accepted"])
        self.assertEqual(graph.reader.texts["policy.md"], self.text)
        restored = generation.restore_previous(self.store)
        self.assertEqual(restored["current"], first)
        self.assertEqual(restored["previous"], second)
        self.assertTrue(OptionalGraph(store=self.store).status()["available"])

    def test_incomplete_input_does_not_initialize_store(self):
        (self.bundle / "hold-receipt.json").unlink()
        with self.assertRaises((ValueError, OSError)):
            self.package()
        self.assertFalse(self.store.exists())

    def test_tampered_selection_and_rollback_keep_pointer(self):
        first, second = self.package(), self.package()
        generation.select(self.store, first)
        generation.select(self.store, second)
        before = self.pointer()
        (self.store / "packages" / first / "source/policy.md").write_text("changed")
        for action in (
            lambda: generation.select(self.store, first),
            lambda: generation.restore_previous(self.store),
        ):
            with self.assertRaises(ValueError):
                action()
            self.assertEqual(self.pointer(), before)
        self.assertTrue(OptionalGraph(store=self.store).status()["available"])

    def test_failed_atomic_selection_keeps_previous(self):
        first, second = self.package(), self.package()
        generation.select(self.store, first)
        before = self.pointer()
        with (
            patch.object(
                generation.os, "replace", side_effect=OSError("synthetic failure")
            ),
            self.assertRaises(OSError),
        ):
            generation.select(self.store, second)
        self.assertEqual(self.pointer(), before)
        self.assertEqual(list(self.store.glob(".selection-*")), [])
        self.assertTrue(OptionalGraph(store=self.store).status()["available"])

    def test_withdrawn_source_blocks_restart_selection_and_restore(self):
        first, second = self.package(), self.package()
        generation.select(self.store, first)
        generation.select(self.store, second)
        checksum = OptionalGraph(store=self.store).reader.hashes["policy.md"]
        generation.withdraw_source(self.store, checksum)
        before = self.pointer()
        self.assertFalse(OptionalGraph(store=self.store).status()["available"])
        with self.assertRaises(ValueError):
            generation.restore_previous(self.store)
        with self.assertRaises(ValueError):
            generation.select(self.store, first)
        self.assertEqual(self.pointer(), before)
        self.assertIn(
            checksum,
            read_json(self.store / "graph-selection.json")["withdrawn_source_hashes"],
        )

    def test_foreign_store_and_symlink_rejected(self):
        self.store.mkdir()
        (self.store / "precious").write_text("preserve")
        with self.assertRaises((ValueError, OSError)):
            self.package()
        self.assertEqual((self.store / "precious").read_text(), "preserve")
        alias = self.root / "alias"
        alias.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(ValueError):
            generation.package(alias, self.root / "other")


if __name__ == "__main__":
    unittest.main()
