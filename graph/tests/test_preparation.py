"""Committed synthetic fixtures for offline preparation boundaries."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from preparation import MODEL, preflight, prepare


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "policy.md").write_text("# Synthetic\nRetain explicit approval.\n")
        (self.repo / "large.md").write_text("x" * 16001)
        (self.repo / "link.md").symlink_to("policy.md")
        self.git("add", ".")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        )
        self.revision = self.git("rev-parse", "HEAD").strip()
        self.output = self.root / "run"

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.repo), *args], stderr=subprocess.DEVNULL
        ).decode()

    def call(self, function=preflight, paths=None, approved=None, **kw):
        return function(
            self.repo,
            kw.get("revision", self.revision),
            paths or ["policy.md"],
            kw.get("model", MODEL),
            approved or ["policy.md"],
            kw.get("output", self.output),
        )

    def test_dry_run_no_writes_and_committed_not_working_content(self):
        (self.repo / "policy.md").write_text("UNCOMMITTED")
        before = sorted(str(p) for p in self.root.rglob("*"))
        plan = self.call()
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob("*")))
        self.assertEqual(plan["revision"], self.revision)
        self.assertEqual(plan["files"][0]["bytes"], 38)
        self.assertFalse(self.output.exists())

    def test_prepare_manifest_and_nonexecuting_config(self):
        plan = self.call(prepare)
        self.assertEqual(plan["status"], "prepared-not-executed")
        manifest = json.loads((self.output / "source/manifest.json").read_text())
        self.assertEqual(manifest["revision"], self.revision)
        self.assertEqual(manifest["files"][0]["sha256"], plan["files"][0]["sha256"])
        self.assertFalse(
            json.loads((self.output / "runner-config.json").read_text())["execute"]
        )
        with self.assertRaises(ValueError):
            self.call(prepare)

    def test_invalid_inputs_refused_before_output(self):
        for paths, approved in [
            (["../policy.md"], ["../policy.md"]),
            (["link.md"], ["link.md"]),
            (["large.md"], ["large.md"]),
            (["policy.md"], ["large.md"]),
            (["policy.md"] * 2, ["policy.md"]),
            (["missing.md"], ["missing.md"]),
        ]:
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                self.call(paths=paths, approved=approved)
        with self.assertRaises(ValueError):
            self.call(model="unapproved-model")
        self.assertFalse(self.output.exists())

    def test_symlink_output_parent_refused(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.call(output=alias / "run")


if __name__ == "__main__":
    unittest.main()
