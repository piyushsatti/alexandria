"""Offline contracts for the attached inbound pool and bounded file view."""

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME))

import app
from inbound import MAX_CONTENT_BYTES, InboundStore
from listing import committed_tree, frontmatter


class AttachedPoolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inbound = InboundStore(self.root / "inbound")

    def test_server_generated_path_idempotency_and_provenance(self):
        result = self.inbound.submit(
            content="---\ntitle: Draft\n---\nUncommitted note.\n",
            session_label="chat-session",
            idempotency_key="first",
            title="Draft",
            facets=["draft"],
            source_ref="session://chat-session",
            subject="owner",
            client_id="client",
        )
        self.assertEqual(result["status"], "queued")
        self.assertRegex(result["submission_id"], r"^[0-9a-f]{32}$")
        self.assertTrue(
            (self.root / "inbound/content" / f"{result['submission_id']}.md").exists()
        )
        replay = self.inbound.submit(
            content="---\ntitle: Draft\n---\nUncommitted note.\n",
            session_label="different-label-is-ignored-on-replay",
            idempotency_key="first",
        )
        self.assertTrue(replay["idempotent_replay"])
        with self.assertRaisesRegex(ValueError, "different content"):
            self.inbound.submit(
                content="other",
                session_label="chat-session",
                idempotency_key="first",
            )
        receipt = self.inbound.receipt(result["submission_id"])
        self.assertEqual(receipt["subject"], "owner")
        self.assertEqual(receipt["client_id"], "client")
        self.assertEqual(
            receipt["content_sha256"],
            hashlib.sha256(result["path"].encode()).hexdigest()
            if False
            else hashlib.sha256(
                b"---\ntitle: Draft\n---\nUncommitted note.\n"
            ).hexdigest(),
        )

    def test_frontmatter_is_raw_and_malformed_is_reported(self):
        raw, error = frontmatter("---\ntitle: value\n---\nbody")
        self.assertEqual(raw, "---\ntitle: value\n---\n")
        self.assertIsNone(error)
        self.assertEqual(frontmatter("body"), (None, None))
        raw, error = frontmatter("---\ntitle: value\nbody")
        self.assertEqual(raw, "---\ntitle: value\nbody")
        self.assertEqual(error, "unterminated_frontmatter")

    def test_committed_tree_is_bounded_and_hashes_files(self):
        snapshot = self.root / "snapshot"
        (snapshot / "nested").mkdir(parents=True)
        (snapshot / "nested/doc.md").write_text("---\nkind: source\n---\ntext")
        (snapshot / "ignored.json").write_text("ignored")
        result = committed_tree(snapshot, "rev", include_frontmatter=True)
        self.assertEqual(
            [entry["path"] for entry in result["entries"]], ["nested", "nested/doc.md"]
        )
        file_entry = result["entries"][1]
        self.assertEqual(
            file_entry["sha256"],
            hashlib.sha256((snapshot / "nested/doc.md").read_bytes()).hexdigest(),
        )
        self.assertEqual(file_entry["frontmatter"], "---\nkind: source\n---\n")
        limited = committed_tree(snapshot, "rev", limit=1)
        self.assertTrue(limited["truncated"])

    def test_literal_search_can_cover_inbound_content_without_shell_interpolation(self):
        content = self.inbound.submit(
            content="needle a.b\n", session_label="s", idempotency_key="literal"
        )
        result = app.literal_search(
            None,
            "a.b",
            10,
            inbound_root=self.inbound.content,
            inbound_records={content["submission_id"]: content},
        )
        self.assertEqual(
            result["matches"][0]["path"], f"inbound/{content['submission_id']}.md"
        )
        self.assertFalse(result["truncated"])

    def test_size_and_server_path_boundaries_are_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "256 KiB"):
            self.inbound.submit(
                content="x" * (MAX_CONTENT_BYTES + 1),
                session_label="s",
                idempotency_key="oversize",
            )
        with self.assertRaises(ValueError):
            self.inbound.read_document("inbound/../escape.md", 0, 10, False)
        alias = self.root / "alias"
        alias.symlink_to(self.root / "inbound", target_is_directory=True)
        with self.assertRaises(ValueError):
            InboundStore(alias)


if __name__ == "__main__":
    unittest.main()
