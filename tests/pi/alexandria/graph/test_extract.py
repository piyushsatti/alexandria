"""Independent safety checks for the optional host inference adapter."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pi.alexandria.graph import extract


class ExtractionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.text = "Saturn has a CPU."
        (self.source / "doc.md").write_text(self.text)
        (self.source / "manifest.json").write_text(
            json.dumps(
                {
                    "revision": "fixture",
                    "files": [
                        {
                            "path": "doc.md",
                            "sha256": hashlib.sha256(self.text.encode()).hexdigest(),
                        }
                    ],
                }
            )
        )
        self.config = {
            "model": "fixture:free",
            "privacy_approved": True,
            "paths": ["doc.md"],
            "max_requests": 1,
        }
        self.rows = [
            {
                "subject": "Saturn",
                "predicate": "has",
                "object": "CPU",
                "quote": self.text,
                "start": 0,
            }
        ]

    def provider(self, endpoint, key, payload=None):
        if endpoint == "models":
            return {
                "data": [
                    {
                        "id": "fixture:free",
                        "pricing": {"prompt": "0", "completion": "0"},
                    }
                ]
            }
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertEqual(
            payload["provider"]["max_price"], {"prompt": 0, "completion": 0}
        )
        return {
            "model": "fixture:free",
            "provider": "fixture",
            "choices": [
                {"finish_reason": "stop", "message": {"content": json.dumps(self.rows)}}
            ],
        }

    def execute(self, output):
        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "synthetic-test-key"}),
            patch.object(extract, "request_json", side_effect=self.provider),
        ):
            return extract.run(self.source, self.config, output, True)

    def test_offline_plan_never_calls_network(self):
        with patch.object(
            extract, "request_json", side_effect=AssertionError("network forbidden")
        ):
            result = extract.run(self.source, self.config, self.root / "out")
        self.assertEqual(result["external_requests"], 0)
        self.assertFalse((self.root / "out").exists())

    def test_source_overlap_rejected(self):
        before = (self.source / "manifest.json").read_bytes()
        with self.assertRaises(ValueError):
            self.execute(self.source)
        self.assertEqual((self.source / "manifest.json").read_bytes(), before)
        self.assertFalse((self.source / "assertions.json").exists())

    def test_output_symlink_cannot_overwrite_file(self):
        output = self.root / "out"
        output.mkdir()
        sentinel = self.root / "sentinel"
        sentinel.write_text("preserve me")
        (output / "assertions.json").symlink_to(sentinel)
        with self.assertRaises(ValueError):
            self.execute(output)
        self.assertEqual(sentinel.read_text(), "preserve me")

    def test_output_ancestor_symlink_rejected(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.execute(link / "out")
        self.assertFalse((target / "out").exists())

    def test_reject_forged_fields_and_quotes(self):
        for change in (
            {"command": "rm -rf /"},
            {"quote": "unsupported"},
            {"start": True},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                extract.validate([{**self.rows[0], **change}], self.text, "doc.md")

    def test_provider_tool_request_rejected(self):
        original = self.provider

        def malicious(endpoint, key, payload=None):
            response = original(endpoint, key, payload)
            if endpoint != "models":
                response["choices"][0]["message"]["tool_calls"] = [
                    {"function": {"name": "shell", "arguments": "rm -rf /"}}
                ]
            return response

        with (
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "synthetic-test-key"}),
            patch.object(extract, "request_json", side_effect=malicious),
            self.assertRaises(ValueError),
        ):
            extract.run(self.source, self.config, self.root / "out", True)
        self.assertFalse((self.root / "out" / "assertions.json").exists())

    def test_nonfree_model_rejected_without_network(self):
        self.config["model"] = "fixture:paid"
        with (
            patch.object(
                extract, "request_json", side_effect=AssertionError("network forbidden")
            ),
            self.assertRaises(ValueError),
        ):
            extract.run(self.source, self.config, self.root / "out", True)

    def test_success_and_cache(self):
        output = self.root / "out"
        self.assertEqual(self.execute(output)["requests"], 1)
        self.assertEqual(self.execute(output)["requests"], 0)
        self.assertEqual(
            json.loads((output / "assertions.json").read_text())[0]["quote"], self.text
        )


if __name__ == "__main__":
    unittest.main()
