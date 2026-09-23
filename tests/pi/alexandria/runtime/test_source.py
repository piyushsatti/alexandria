"""Source fidelity checks; semantic retrieval requires the selected real model."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from pi.alexandria.runtime import app


class SourceTests(unittest.TestCase):
    def test_passages_preserve_offsets(self):
        text = "A longer paragraph.\n" * 100
        pieces = list(app.passages(text))
        self.assertEqual("".join(piece for _, piece in pieces), text)
        for offset, piece in pieces:
            self.assertEqual(text[offset : offset + len(piece)], piece)

    def test_only_committed_text_is_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                subprocess.run(
                    ["git", "-C", directory, *args],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

            git("init")
            corpus = root / "Alexandria/corpus"
            corpus.mkdir(parents=True)
            (corpus / "note.md").write_text("Accepted evidence")
            sources = root / "Alexandria/sources"
            sources.mkdir(parents=True)
            (sources / "note.md").write_text("Original evidence")
            (corpus / "corpus-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "2026.09.22",
                        "snapshot_root": "Alexandria/corpus",
                        "records": [
                            {
                                "file": "note.md",
                                "sha256": hashlib.sha256(
                                    b"Accepted evidence"
                                ).hexdigest(),
                                "source": "Alexandria/sources/note.md",
                                "source_sha256": hashlib.sha256(
                                    b"Original evidence"
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
            (root / "other.json").write_text("{}")
            (root / "unselected.md").write_text("Unselected text")
            git("add", ".")
            git(
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-m",
                "fixture",
            )
            (corpus / "note.md").write_text("Uncommitted edit")
            (root / "untracked.md").write_text("Unreviewed evidence")
            records = list(app.committed_documents(root))
            self.assertEqual(len(records), 1)
            self.assertEqual(
                records[0][1:], ("Alexandria/corpus/note.md", "Accepted evidence")
            )


class TokenBoundaryTests(unittest.TestCase):
    def test_long_identifiers_keep_all_text_without_over_budget_chunks(self):
        text = "x" * 1200
        chunks = list(app.bounded_passages(text, len, maximum_tokens=480))
        self.assertEqual("".join(piece for _, piece in chunks), text)
        self.assertTrue(all(len(piece) <= 480 for _, piece in chunks))
        for offset, piece in chunks:
            self.assertEqual(text[offset : offset + len(piece)], piece)


class LiteralSearchTests(unittest.TestCase):
    def test_literal_and_limit_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".note.md").write_text("a.b\naXb\na.b\n")
            result = app.literal_search(root, "a.b", 10)
            self.assertEqual([x["line"] for x in result["matches"]], [1, 3])
            self.assertFalse(result["truncated"])
            limited = app.literal_search(root, "a.b", 1)
            self.assertTrue(limited["truncated"])
            self.assertEqual(limited["reason"], "result_limit")


if __name__ == "__main__":
    unittest.main()
