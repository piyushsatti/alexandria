"""Synthetic status/failure checks; never contact a gateway or load secrets."""

import json
import signal
import subprocess
import sys
from unittest.mock import MagicMock, patch

from pi.alexandria.graph import operator_run as operator
from pi.alexandria.graph import operator_status
from pi.alexandria.graph.preparation import prepare
from tests.pi.alexandria.graph import test_operator_run, test_preparation


class StatusTests(test_preparation.PreparationTests):
    fake_pipeline = test_operator_run.OperatorTests.fake_pipeline

    def test_unavailable_gateway_receipt_is_redacted(self):
        self.call(prepare)

        def failed(command, config):
            (self.output / "01-extraction.prompt.txt").write_text("PRIVATE DOCUMENT")
            (self.output / "01-extraction.receipt.json").write_text(
                json.dumps(
                    {
                        "error_category": "transport_or_harness_error",
                        "error_body": "PRIVATE ERROR",
                    }
                )
            )
            return 1

        with self.assertRaises(RuntimeError):
            operator.execute_prepared(self.output, process_runner=failed)
        found = operator_status.status(self.output)
        self.assertEqual(found["failure_category"], "transport_or_harness_error")
        self.assertEqual(found["current_stage"], "01-extraction")
        self.assertEqual(found["last_completed_stage"], "preparation")
        self.assertNotIn("PRIVATE", json.dumps(found))

    def test_interruption_retains_attempt_and_blocks_reuse(self):
        self.call(prepare)

        def interrupted(command, config):
            (self.output / "01-extraction.response.txt").write_text("partial")
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            operator.execute_prepared(self.output, process_runner=interrupted)
        before = {p.name: p.read_bytes() for p in self.output.iterdir() if p.is_file()}
        self.assertEqual(
            operator_status.status(self.output)["failure_category"], "interrupted"
        )
        with self.assertRaises(ValueError):
            operator.execute_prepared(self.output, process_runner=interrupted)
        self.assertEqual(
            before,
            {p.name: p.read_bytes() for p in self.output.iterdir() if p.is_file()},
        )

    def test_invalid_json_and_stale_marker_are_not_success(self):
        self.call(prepare)
        (self.output / "operator-start.json").write_text('{"status":"running"}')
        found = operator_status.status(self.output)
        self.assertEqual(found["status"], "running-or-interrupted-unverified")
        self.assertEqual(found["liveness"], "not_checked_no_live_process_claim")
        (self.output / "01-extraction.receipt.json").write_text(
            '{"stop_reason":"stop"}'
        )
        (self.output / "01-extraction.response.txt").write_text("not json PRIVATE")
        found = operator_status.status(self.output)
        self.assertEqual(found["failure_category"], "invalid_json_or_interrupted_write")
        (self.output / "operator-failure.json").write_text('{"partial":')
        self.assertEqual(
            operator_status.status(self.output)["failure_category"],
            "corrupt_or_missing_artifact",
        )

    def test_later_prompt_does_not_complete_missing_or_corrupt_prior_stage(self):
        self.call(prepare)
        (self.output / "operator-start.json").write_text('{"status":"running"}')
        (self.output / "02-verification.prompt.txt").write_text("private")
        found = operator_status.status(self.output)
        self.assertEqual(found["last_completed_stage"], "preparation")
        (self.output / "01-extraction.receipt.json").write_text(
            '{"stop_reason":"stop"}'
        )
        (self.output / "01-extraction.json").write_text('{"incomplete":')
        found = operator_status.status(self.output)
        self.assertEqual(found["failure_category"], "corrupt_or_missing_artifact")
        self.assertEqual(found["last_completed_stage"], "preparation")
        (self.output / "01-extraction.json").write_text('{"claims":[]}')
        self.assertEqual(
            operator_status.status(self.output)["last_completed_stage"], "01-extraction"
        )

    def test_completed_candidate_readable_from_new_process_then_detects_corruption(
        self,
    ):
        self.call(prepare)
        operator.execute_prepared(self.output, process_runner=self.fake_pipeline)
        command = [
            sys.executable,
            "-B",
            "-m",
            "pi.alexandria.graph.operator_status",
            "--output",
            str(self.output),
        ]
        found = json.loads(subprocess.check_output(command))
        self.assertEqual(found["status"], "completed-held")
        self.assertEqual(found["last_completed_stage"], "graph")
        (self.output / "candidate-graph/graph.sqlite").write_bytes(b"partial")
        found = json.loads(subprocess.check_output(command))
        self.assertEqual(found["status"], "incomplete-held")
        self.assertEqual(found["failure_category"], "corrupt_or_missing_artifact")

    def test_timeout_and_interrupt_cleanup_only_owned_group(self):
        for error in (
            subprocess.TimeoutExpired("fixture", 1),
            KeyboardInterrupt(),
            OSError("private error"),
        ):
            child = MagicMock()
            child.pid = 12345
            child.__enter__.return_value = child
            child.communicate.side_effect = error
            with (
                patch.object(operator.subprocess, "Popen", return_value=child) as start,
                patch.object(operator.os, "killpg") as kill,
            ):
                with self.assertRaises((TimeoutError, KeyboardInterrupt, OSError)):
                    operator.run_process(["synthetic-child"], {}, timeout=1)
                self.assertTrue(start.call_args.kwargs["start_new_session"])
                kill.assert_called_once_with(12345, signal.SIGKILL)
                child.wait.assert_called_once()

    def test_timeout_records_incomplete_status(self):
        self.call(prepare)
        with self.assertRaises(TimeoutError):
            operator.execute_prepared(
                self.output,
                process_runner=MagicMock(side_effect=TimeoutError("private")),
            )
        found = operator_status.status(self.output)
        self.assertEqual(found["failure_category"], "timeout")
        self.assertEqual(found["status"], "incomplete-held")
