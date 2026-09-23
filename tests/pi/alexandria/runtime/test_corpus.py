"""Corpus manifests are the fail-closed release allowlist."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from pi.alexandria.runtime.corpus import load_selection, selection_summary


def manifest_for(records):
    return {
        "schema_version": "2026.09.22",
        "snapshot_root": "Alexandria/corpus",
        "records": records,
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


class CorpusSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.corpus = self.root / "Alexandria/corpus"
        self.corpus.mkdir(parents=True)
        self.write("eligible.md", "Eligible\n")
        self.write("held.md", "Held\n")
        self.write("unselected.md", "Never selected\n")
        self.write("source.md", "Original source\n")
        self.write(
            "corpus-manifest.json",
            json.dumps(
                manifest_for(
                    [
                        {
                            "file": "eligible.md",
                            "sha256": self.digest("Eligible\n"),
                            "source": "Alexandria/corpus/source.md",
                            "source_sha256": self.digest("Original source\n"),
                            "source_status": "present",
                            "provenance_status": "source_present",
                            "authority_status": "source_record",
                            "release_status": "eligible",
                        },
                        {
                            "file": "held.md",
                            "sha256": self.digest("Held\n"),
                            "source_status": "missing",
                            "provenance_status": "snapshot_only",
                            "authority_status": "unresolved",
                            "release_status": "held_provenance",
                        },
                    ]
                )
            ),
        )
        self.git("init", "-q")
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

    def write(self, name, value):
        (self.corpus / name).write_text(value)

    def digest(self, value):
        return hashlib.sha256(value.encode()).hexdigest()

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], text=True
        ).strip()

    def test_only_eligible_snapshot_is_selected(self):
        selection = load_selection(self.root)
        self.assertEqual(
            [item["path"] for item in selection["selected"]],
            ["Alexandria/corpus/eligible.md"],
        )
        self.assertEqual(
            [item["path"] for item in selection["held"]],
            ["Alexandria/corpus/held.md"],
        )
        self.assertEqual(selection_summary(selection)["selected_documents"], 1)

    def test_snapshot_hash_mismatch_is_rejected(self):
        manifest = json.loads((self.corpus / "corpus-manifest.json").read_text())
        manifest["records"][0]["sha256"] = "0" * 64
        (self.corpus / "corpus-manifest.json").write_text(json.dumps(manifest))
        self.git("add", "Alexandria/corpus/corpus-manifest.json")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "mismatch",
        )
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            load_selection(self.root)

    def test_unbounded_authority_status_is_rejected(self):
        manifest = json.loads((self.corpus / "corpus-manifest.json").read_text())
        manifest["records"][0]["authority_status"] = "made_up"
        (self.corpus / "corpus-manifest.json").write_text(json.dumps(manifest))
        self.git("add", "Alexandria/corpus/corpus-manifest.json")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "invalid-authority",
        )
        with self.assertRaisesRegex(ValueError, "authority status"):
            load_selection(self.root)

    def test_eligible_snapshot_only_record_is_rejected(self):
        manifest = json.loads((self.corpus / "corpus-manifest.json").read_text())
        manifest["records"][0]["provenance_status"] = "snapshot_only"
        (self.corpus / "corpus-manifest.json").write_text(json.dumps(manifest))
        self.git("add", "Alexandria/corpus/corpus-manifest.json")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "invalid-provenance",
        )
        with self.assertRaisesRegex(ValueError, "incomplete provenance"):
            load_selection(self.root)

    def test_source_blob_hash_mismatch_is_rejected(self):
        manifest = json.loads((self.corpus / "corpus-manifest.json").read_text())
        manifest["records"][0]["source_sha256"] = "0" * 64
        (self.corpus / "corpus-manifest.json").write_text(json.dumps(manifest))
        self.git("add", "Alexandria/corpus/corpus-manifest.json")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "source-mismatch",
        )
        with self.assertRaisesRegex(ValueError, "Source hash mismatch"):
            load_selection(self.root)

    def test_source_path_must_be_repository_relative(self):
        manifest = json.loads((self.corpus / "corpus-manifest.json").read_text())
        manifest["records"][0]["source"] = "../source.md"
        (self.corpus / "corpus-manifest.json").write_text(json.dumps(manifest))
        self.git("add", "Alexandria/corpus/corpus-manifest.json")
        self.git(
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "source-path",
        )
        with self.assertRaisesRegex(ValueError, "Unsafe source path"):
            load_selection(self.root)


if __name__ == "__main__":
    unittest.main()
