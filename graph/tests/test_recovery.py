"""Local recovery tests; all mutation stays in new temporary directories."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "runtime/tests"))
import generation
import recovery
from graph_access import OptionalGraph, digest, encoded, read_json, selected_reader
from test_graph_access import builder, fixture


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        fixture(self.bundle)
        self.store = self.root / "store"
        self.first = generation.package(self.bundle, self.store)["package_id"]
        self.second = generation.package(self.bundle, self.store)["package_id"]
        generation.select(self.store, self.first)
        generation.select(self.store, self.second)
        self.saved = self.root / "backup"
        self.target = self.root / "restored"

    def make_backup(self):
        return recovery.backup(self.store, self.saved)

    def restore(self, policy=None):
        return recovery.restore(
            self.saved, self.target, current_policy_store=policy or self.store
        )

    def test_round_trip_selection_evidence_and_new_process(self):
        before = selected_reader(self.store)
        result = before.search("approval")
        relation_id = result["matches"][0]["id"]
        backup = self.make_backup()
        restored = self.restore()
        after = selected_reader(self.target)
        self.assertEqual(before.status(), after.status())
        self.assertEqual(result, after.search("approval"))
        self.assertEqual(before.read(relation_id), after.read(relation_id))
        self.assertEqual(restored["selection"]["current"], self.second)
        self.assertEqual(restored["selection"]["previous"], self.first)
        self.assertEqual(backup["backup_sha256"], restored["backup_sha256"])
        self.assertFalse(restored["off_host_recovery_verified"])
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(HERE / "generation.py"),
                "status",
                "--store",
                str(self.target),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(json.loads(completed.stdout)["graph"], before.status())
        generation.restore_previous(self.target)
        self.assertEqual(generation.status(self.target)["current"], self.first)
        self.assertFalse(
            OptionalGraph(store=self.target).status()["review"]["accepted"]
        )

    def test_unselected_packages_and_unknown_store_files_never_travel(self):
        alternate = self.root / "alternate-bundle"
        alternate.mkdir()
        fixture(alternate)
        source = alternate / "source/policy.md"
        source.write_text(source.read_text() + "Additional withdrawn source context.\n")
        manifest = read_json(alternate / "source/manifest.json")
        checksum = digest(source.read_bytes())
        manifest["files"][0]["sha256"] = checksum
        (alternate / "source/manifest.json").write_bytes(encoded(manifest))
        (alternate / "run-manifest.json").write_bytes(encoded({"source": manifest}))
        graph = builder.build(
            alternate / "source",
            alternate / "candidate-graph",
            alternate / "assertions.json",
        )
        result = read_json(alternate / "operator-result.json")
        result["graph_sha256"] = graph["graph_sha256"]
        (alternate / "operator-result.json").write_bytes(encoded(result))
        third = generation.package(alternate, self.store)["package_id"]
        generation.withdraw_source(self.store, checksum)
        (self.store / "secret.txt").write_text("do not copy")
        self.make_backup()
        self.restore()
        for root in (self.saved, self.target):
            self.assertEqual(
                {p.name for p in (root / "packages").iterdir()},
                {self.first, self.second},
            )
            self.assertFalse((root / "packages" / third).exists())
            self.assertFalse((root / "secret.txt").exists())

    def test_rehashed_but_inconsistent_provenance_is_rejected(self):
        self.make_backup()
        package = self.saved / "packages" / self.first
        provenance = package / "run-manifest.json"
        altered = read_json(provenance)
        altered["source"]["revision"] = "wrong-source-revision"
        provenance.write_bytes(encoded(altered))
        package_manifest = read_json(package / "package.json")
        package_manifest["files"]["run-manifest.json"] = digest(provenance.read_bytes())
        (package / "package.json").write_bytes(encoded(package_manifest))
        manifest = read_json(self.saved / "backup.json")
        for name in ("run-manifest.json", "package.json"):
            relative = "packages/" + self.first + "/" + name
            manifest["files"][relative] = digest((self.saved / relative).read_bytes())
        (self.saved / "backup.json").write_bytes(encoded(manifest))
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse(self.target.exists())

    def test_union_preserves_backup_and_newer_policy_restrictions(self):
        generation.withdraw_source(self.store, "a" * 64)
        self.make_backup()
        # A separate authoritative policy may omit an old entry; union retains it.
        policy = generation.initialize(self.root / "current-policy")
        generation.withdraw_source(policy, "b" * 64)
        restored = self.restore(policy)
        self.assertEqual(
            restored["selection"]["withdrawn_source_hashes"], ["a" * 64, "b" * 64]
        )
        self.assertEqual(
            generation.status(self.target)["withdrawn_source_hashes"],
            ["a" * 64, "b" * 64],
        )

    def test_new_withdrawal_blocks_restore_before_copy(self):
        checksum = selected_reader(self.store).hashes["policy.md"]
        self.make_backup()
        generation.withdraw_source(self.store, checksum)
        with (
            patch.object(
                recovery, "_copy", side_effect=AssertionError("must not copy")
            ),
            self.assertRaises(ValueError),
        ):
            self.restore()
        self.assertFalse(self.target.exists())

    def test_withdrawn_current_or_previous_blocks_backup(self):
        checksum = selected_reader(self.store).hashes["policy.md"]
        generation.withdraw_source(self.store, checksum)
        with patch.object(
            recovery, "_copy", side_effect=AssertionError("must not copy")
        ):
            with self.assertRaises(ValueError):
                self.make_backup()
            state = generation.selection_state(self.store)
            state["current"] = None  # Previous alone is still checked.
            generation.atomic_state(self.store, state)
            with self.assertRaises(ValueError):
                self.make_backup()
        self.assertFalse(self.saved.exists())

    def test_missing_or_backup_only_policy_is_rejected(self):
        self.make_backup()
        with self.assertRaises(TypeError):
            recovery.restore(self.saved, self.target)
        for policy in (self.root / "missing-policy", self.saved):
            with self.assertRaises((ValueError, OSError)):
                self.restore(policy)
        self.assertFalse(self.target.exists())

    def test_tampered_partial_unknown_and_traversal_backups_rejected(self):
        self.make_backup()
        manifest_file = self.saved / "backup.json"
        original = manifest_file.read_bytes()
        manifest = read_json(manifest_file)
        source = self.saved / "packages" / self.first / "source/policy.md"
        content = source.read_bytes()
        source.write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            self.restore()
        source.write_bytes(content)
        manifest_file.unlink()
        with self.assertRaises((ValueError, OSError)):
            self.restore()
        manifest_file.write_bytes(original)
        (self.saved / "unexpected.txt").write_text("extra")
        with self.assertRaises(ValueError):
            self.restore()
        (self.saved / "unexpected.txt").unlink()
        for name in ("../escape", "/absolute", "packages/../escape", "a\\b"):
            changed = {**manifest, "files": {**manifest["files"], name: "a" * 64}}
            manifest_file.write_bytes(encoded(changed))
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.restore()
        self.assertFalse(self.target.exists())

    def test_symlink_inputs_outputs_and_members_rejected(self):
        self.make_backup()
        alias = self.root / "alias"
        alias.symlink_to(self.saved, target_is_directory=True)
        with self.assertRaises(ValueError):
            recovery.verify_backup(alias)
        alias.unlink()
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            recovery.backup(self.store, alias / "new-backup")
        member = self.saved / "packages" / self.first / "source/policy.md"
        member.unlink()
        member.symlink_to(self.bundle / "source/policy.md")
        with self.assertRaises(ValueError):
            self.restore()
        self.assertFalse(self.target.exists())

    def test_existing_outputs_preserved_and_copy_failure_unpublished(self):
        self.make_backup()
        sentinel = self.saved / "backup.json"
        original = sentinel.read_bytes()
        with self.assertRaises(ValueError):
            self.make_backup()
        self.assertEqual(sentinel.read_bytes(), original)
        with (
            patch.object(
                recovery, "_copy", side_effect=OSError("synthetic interruption")
            ),
            self.assertRaises(OSError),
        ):
            self.restore()
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob(".graph-restore-*")), [])
        self.restore()
        with self.assertRaises(ValueError):
            self.restore()

    def test_policy_change_during_restore_aborts_unpublished(self):
        self.make_backup()
        original = recovery._copy

        def change_policy(*args):
            original(*args)
            generation.withdraw_source(self.store, "c" * 64)

        with (
            patch.object(recovery, "_copy", side_effect=change_policy),
            self.assertRaises(ValueError),
        ):
            self.restore()
        self.assertFalse(self.target.exists())

    def test_policy_only_empty_store_round_trip(self):
        empty = generation.initialize(self.root / "empty")
        generation.withdraw_source(empty, "d" * 64)
        recovery.backup(empty, self.saved)
        restored = self.restore(empty)
        self.assertIsNone(restored["selection"]["current"])
        self.assertEqual(restored["selection"]["withdrawn_source_hashes"], ["d" * 64])
        self.assertEqual(list((self.target / "packages").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
