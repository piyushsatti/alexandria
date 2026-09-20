"""Frozen evidence keeps its bytes; ordinary code and temporary state stay checked."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = Path(__file__).resolve().parents[1] / "ci/check_changes.py"


class HygieneTests(unittest.TestCase):
    def test_evidence_exemption_does_not_hide_code_or_temporary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                return subprocess.check_output(
                    [
                        "git",
                        "-C",
                        str(root),
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@example.invalid",
                        *args,
                    ],
                    stderr=subprocess.DEVNULL,
                    text=True,
                ).strip()

            git("init", "-q")
            git("commit", "--allow-empty", "-qm", "Baseline")
            base = git("rev-parse", "HEAD")
            for relative, expected in (
                ("Alexandria/graph/trials/example/source/note.md", 0),
                ("Alexandria/runtime/example.py", 1),
                ("Alexandria/graph/trials/example/.ruff_cache/state", 1),
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("PRIVATE_FIXTURE_SENTINEL  \n")
                git("add", relative)
                git("commit", "-qm", "Fixture")
                result = subprocess.run(
                    [sys.executable, str(CHECKER), "--base", base],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                with self.subTest(path=relative):
                    self.assertEqual(result.returncode, expected)
                    self.assertNotIn("PRIVATE_FIXTURE_SENTINEL", result.stdout)
                    self.assertNotIn("PRIVATE_FIXTURE_SENTINEL", result.stderr)
                base = git("rev-parse", "HEAD")
