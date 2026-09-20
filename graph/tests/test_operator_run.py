"""Offline subprocess-boundary fixtures; no provider or private documents."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import operator_run as operator
import test_preparation
from preparation import digest, prepare


class OperatorTests(test_preparation.PreparationTests):
    def fake_pipeline(self, command, config):
        self.assertEqual(config["model"], "gpt-5.6-sol")
        self.assertTrue(config["execute"])
        root = Path(command[-1])
        text = (root / "source/policy.md").read_text()
        data = {
            "claims": [
                {
                    "id": "c1",
                    "subject": "Release",
                    "predicate": "requires",
                    "object": "approval",
                    "path": "policy.md",
                    "quote": "Retain explicit approval.",
                    "source_status": "requirement",
                    "scope": {
                        "text": "Synthetic fixture policy",
                        "path": "policy.md",
                        "quote": "# Synthetic",
                    },
                    "annotation_ids": [],
                }
            ],
            "annotations": [],
            "coverage": [
                {
                    "id": "r1",
                    "disposition": "represented",
                    "claim_ids": ["c1"],
                    "annotation_ids": [],
                    "reason": "Synthetic fixture",
                }
            ],
            "pages": [
                {
                    "title": "Policy",
                    "sections": [
                        {
                            "heading": "Rule",
                            "paragraphs": [
                                {
                                    "id": "p1",
                                    "text": "Retain explicit approval.",
                                    "claim_ids": ["c1"],
                                    "annotation_ids": [],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        values = {
            "02-frozen.json": data,
            "03-humanizer.json": data,
            "run-manifest.json": {
                "source": json.loads((root / "source/manifest.json").read_text())
            },
            "source-regions.json": [
                {"id": "r1", "path": "policy.md", "line": 1, "quote": text}
            ],
            "review-queue.json": [],
            "hold-receipt.json": {
                "status": "held-pending-owner-review",
                "automatic_acceptance": False,
                "model_review_pass": False,
                "candidate_hash": digest(
                    json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
                ),
            },
        }
        for name, value in values.items():
            (root / name).write_text(json.dumps(value))
        return 0

    def test_complete_pipeline_real_render_and_graph_still_held(self):
        self.call(prepare)
        result = operator.execute_prepared(
            self.output, process_runner=self.fake_pipeline
        )
        self.assertEqual(result["status"], "completed-held")
        self.assertFalse(result["model_review_pass"])
        self.assertFalse(result["automatic_acceptance"])
        self.assertTrue((self.output / "candidate-graph/graph.sqlite").exists())
        with self.assertRaises(ValueError):
            operator.execute_prepared(self.output, process_runner=self.fake_pipeline)

    def test_tampered_source_stops_before_provider(self):
        self.call(prepare)
        (self.output / "source/policy.md").write_text("changed")
        with self.assertRaises(ValueError):
            operator.execute_prepared(
                self.output, process_runner=lambda *_: self.fail("provider called")
            )
        self.assertFalse((self.output / "operator-start.json").exists())

    def test_failed_process_records_sanitized_failure(self):
        self.call(prepare)
        with self.assertRaises(RuntimeError):
            operator.execute_prepared(self.output, process_runner=lambda *_: 1)
        failure = json.loads((self.output / "operator-failure.json").read_text())
        self.assertEqual(failure["stage"], "pipeline")
        self.assertEqual(failure["category"], "RuntimeError")
        self.assertFalse((self.output / "candidate-graph").exists())

    def test_cli_defaults_to_offline_plan(self):
        argv = [
            "operator_run",
            "--repo",
            str(self.repo),
            "--revision",
            self.revision,
            "--path",
            "policy.md",
            "--approved-path",
            "policy.md",
            "--model",
            "gpt-5.6-sol",
            "--output",
            str(self.output),
        ]
        with (
            patch.object(sys, "argv", argv),
            patch("builtins.print"),
            patch.object(
                operator, "execute_prepared", side_effect=AssertionError("execution")
            ),
        ):
            operator.main()
        self.assertFalse(self.output.exists())
