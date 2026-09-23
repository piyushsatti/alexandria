"""The release command binds a data block to one exact Knowledge revision."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from pi.alexandria.runtime.prepare_data_block import prepare


class DataBlockPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.knowledge = self.root / "knowledge"
        self.knowledge.mkdir()
        self.git("init", "-q")
        corpus = self.knowledge / "Alexandria/corpus"
        corpus.mkdir(parents=True)
        (corpus / "note.md").write_text("Accepted note\n")
        sources = self.knowledge / "Alexandria/sources"
        sources.mkdir(parents=True)
        (sources / "note.md").write_text("Original note\n")
        (corpus / "corpus-manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "2026.09.22",
                    "snapshot_root": "Alexandria/corpus",
                    "records": [
                        {
                            "file": "note.md",
                            "sha256": hashlib.sha256(b"Accepted note\n").hexdigest(),
                            "source": "Alexandria/sources/note.md",
                            "source_sha256": hashlib.sha256(
                                b"Original note\n"
                            ).hexdigest(),
                            "source_status": "present",
                            "provenance_status": "source_present",
                            "authority_status": "source_record",
                            "release_status": "eligible",
                        }
                    ],
                    "final_context_additions": [],
                    "selection_policy": {
                        "eligible_release_status": "eligible",
                        "held_release_statuses": [
                            "held_authority",
                            "held_duplicate",
                            "held_provenance",
                        ],
                        "require_snapshot_hash_match": True,
                        "require_source_status": True,
                    },
                    "exclusions": [],
                }
            )
        )
        (self.knowledge / "ignored.json").write_text("{}")
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
        self.revision = self.git("rev-parse", "HEAD")
        self.active = self.root / "active-data"
        build = self.active / "builds/generation/source/Alexandria/corpus"
        build.mkdir(parents=True)
        (build / "note.md").write_text("Accepted note\n")
        (self.active / "models").mkdir()
        (self.active / "models/model.bin").write_text("model")
        (self.active / "active.json").write_text(
            json.dumps(
                {
                    "database": "builds/generation",
                    "revision": self.revision,
                    "embedding_model": "fixture/model",
                    "documents": 1,
                    "passages": 1,
                    "corpus": {
                        "manifest_sha256": hashlib.sha256(
                            (
                                self.knowledge
                                / "Alexandria/corpus/corpus-manifest.json"
                            ).read_bytes()
                        ).hexdigest(),
                        "selected_documents": 1,
                    },
                }
            )
        )

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.knowledge), *args], text=True
        ).strip()

    def test_verifies_and_initializes_hidden_inbound_mount(self):
        output = self.root / "release"
        receipt = prepare(
            self.knowledge,
            self.active,
            self.revision,
            "fixture/model",
            output,
        )
        self.assertEqual(receipt["knowledge_revision"], self.revision)
        self.assertEqual(receipt["source_documents"], 1)
        self.assertTrue(
            (output / ".alexandria-inbound/catalog.sqlite3").parent.exists()
        )
        self.assertEqual(
            receipt["source_hashes"]["Alexandria/corpus/note.md"],
            hashlib.sha256(b"Accepted note\n").hexdigest(),
        )

    def test_revision_mismatch_is_refused(self):
        with self.assertRaisesRegex(ValueError, "revision"):
            prepare(
                self.knowledge,
                self.active,
                "0" * 40,
                "fixture/model",
                self.root / "out",
            )


if __name__ == "__main__":
    unittest.main()
