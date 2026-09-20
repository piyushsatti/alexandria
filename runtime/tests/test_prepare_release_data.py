import json
import tempfile
import unittest
from pathlib import Path

from prepare_release_data import stage


class PrepareReleaseDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        active = self.source / "builds" / "active-build"
        inactive = self.source / "builds" / "inactive-build"
        active.mkdir(parents=True)
        inactive.mkdir()
        (active / "database.lance").write_text("active")
        (inactive / "database.lance").write_text("inactive")
        blobs = self.source / "models" / "blobs"
        snapshots = self.source / "models" / "snapshots" / "selected"
        blobs.mkdir(parents=True)
        snapshots.mkdir(parents=True)
        (blobs / "model.bin").write_text("model")
        (snapshots / "model.bin").symlink_to(Path("../../blobs/model.bin"))
        (self.source / "models" / ".locks").mkdir()
        (self.source / "models" / ".locks" / "download.lock").write_text("lock")
        (self.source / "active.json").write_text(
            json.dumps(
                {
                    "database": "builds/active-build",
                    "revision": "abc123",
                    "embedding_model": "test/model",
                    "documents": 3,
                    "passages": 7,
                }
            )
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_stages_only_active_build_and_dereferences_internal_model_links(self):
        destination = self.root / "release"
        receipt = stage(self.source, destination)

        self.assertEqual(receipt["indexed_revision"], "abc123")
        self.assertTrue((destination / "builds" / "active-build").is_dir())
        self.assertFalse((destination / "builds" / "inactive-build").exists())
        model = destination / "models" / "snapshots" / "selected" / "model.bin"
        self.assertEqual(model.read_text(), "model")
        self.assertFalse(model.is_symlink())
        self.assertFalse((destination / "models" / ".locks").exists())

    def test_rejects_model_link_outside_source(self):
        external = self.root / "external.bin"
        external.write_text("private")
        link = self.source / "models" / "snapshots" / "selected" / "external.bin"
        link.symlink_to(external)

        with self.assertRaisesRegex(ValueError, "escapes"):
            stage(self.source, self.root / "release")

    def test_rejects_database_outside_direct_build_child(self):
        manifest = json.loads((self.source / "active.json").read_text())
        manifest["database"] = "models"
        (self.source / "active.json").write_text(json.dumps(manifest))

        with self.assertRaisesRegex(ValueError, "direct child"):
            stage(self.source, self.root / "release")


if __name__ == "__main__":
    unittest.main()
