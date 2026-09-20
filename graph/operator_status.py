"""Read local receipts without displaying documents, prompts or model responses."""

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import graph

STAGES = ("01-extraction", "02-verification", "03-humanizer-edits", "04-meaning-check")
CATEGORIES = {
    "TimeoutError": "timeout",
    "KeyboardInterrupt": "interrupted",
    "ConnectionError": "transport_or_gateway_unavailable",
    "ConnectionRefusedError": "transport_or_gateway_unavailable",
    "FileNotFoundError": "missing_prerequisite_or_artifact",
    "JSONDecodeError": "invalid_json",
    "ValueError": "invalid_or_incomplete_artifact",
    "RuntimeError": "pipeline_incomplete",
    "OSError": "local_io_failure",
}
PROVIDER_CATEGORIES = {"transport_or_harness_error", "rate_limit", "provider_error"}
RECOVERY = "Retain this attempt. Inspect private evidence locally; prepare a fresh output for an explicitly authorized rerun. No automatic retry, resume or activation."


def status(root):
    root = graph.no_symlinks(root).resolve()
    result = {
        "status": "unknown",
        "current_stage": None,
        "last_completed_stage": None,
        "failure_category": None,
        "output": str(root),
        "liveness": "not_checked_no_live_process_claim",
        "recovery": RECOVERY,
        "automatic_acceptance": False,
    }

    def receipt(name):
        path = root / name
        if not path.exists():
            return None
        value = graph.read_json(path, 65536)
        if not isinstance(value, dict):
            raise TypeError("Invalid receipt")
        return value

    try:
        prepared = receipt("preparation.json")
        started = receipt("operator-start.json")
        result["status"] = "prepared-not-executed" if prepared else "unknown"
        if started:
            result["status"] = "running-or-interrupted-unverified"
            result["current_stage"] = "pipeline"
            result["last_completed_stage"] = "preparation"
        for stage in STAGES:
            # File presence signals progress, never successful parsing or liveness.
            if (root / (stage + ".prompt.txt")).exists():
                result["current_stage"] = stage
            stage_receipt = receipt(stage + ".receipt.json")
            if stage_receipt:
                result["current_stage"] = stage
                category = stage_receipt.get("error_category")
                if category in PROVIDER_CATEGORIES:
                    result["failure_category"] = category
                elif (
                    stage_receipt.get("stop_reason") == "stop"
                    and not (root / (stage + ".json")).exists()
                ):
                    result["failure_category"] = "invalid_json_or_interrupted_write"
                elif stage_receipt.get("stop_reason") not in ("stop", None):
                    result["failure_category"] = "provider_incomplete"
                if (
                    stage_receipt.get("stop_reason") == "stop"
                    and (root / (stage + ".json")).exists()
                ):
                    parsed = graph.read_json(root / (stage + ".json"))
                    if not isinstance(parsed, dict):
                        raise TypeError("Invalid stage artifact")
                    result["last_completed_stage"] = stage
        pipeline = receipt("operator-pipeline.json")
        rendered = receipt("operator-render.json")
        if pipeline:
            result.update(current_stage="render", last_completed_stage="pipeline")
        if rendered:
            result.update(current_stage="graph", last_completed_stage="render")
        failed = receipt("operator-failure.json")
        pi_failed = receipt("failure.json")
        if failed or pi_failed:
            result["status"] = "incomplete-held"
            if failed:
                category = CATEGORIES.get(failed.get("category"), "operator_failure")
                if (
                    category in ("timeout", "interrupted")
                    or not result["failure_category"]
                ):
                    result["failure_category"] = category
                if failed.get("stage") in ("render", "graph"):
                    result["current_stage"] = failed["stage"]
            elif not result["failure_category"]:
                result["failure_category"] = "pipeline_incomplete"
        complete = receipt("operator-result.json")
        if complete:
            if failed or pi_failed or complete.get("status") != "completed-held":
                raise ValueError("Conflicting terminal receipts")
            if complete.get("automatic_acceptance") is not False:
                raise ValueError("Invalid acceptance state")
            held = receipt("hold-receipt.json")
            if (
                not held
                or held.get("candidate_hash") != complete.get("candidate_hash")
                or held.get("status") != "held-pending-owner-review"
                or held.get("automatic_acceptance") is not False
            ):
                raise ValueError("Missing held identity")
            for name in (
                "candidate-pages.md",
                "review-queue.md",
                "coverage-ledger.md",
                "assertions.json",
            ):
                artifact = graph.no_symlinks(root / name)
                if not artifact.is_file() or not artifact.stat().st_size:
                    raise ValueError("Missing completed artifact")
            output = graph.no_symlinks(root / "candidate-graph")
            built = graph.inspect(output)
            if built.get("graph_sha256") != complete.get("graph_sha256"):
                raise ValueError("Graph identity mismatch")
            sqlite_path = graph.no_symlinks(output / "graph.sqlite")
            with closing(
                sqlite3.connect(sqlite_path.as_uri() + "?mode=ro", uri=True)
            ) as db:
                rows = [
                    json.loads(r[0])
                    for r in db.execute("SELECT data FROM records ORDER BY id")
                ]
            if (
                graph.digest(b"".join(graph.encoded(r) + b"\n" for r in rows))
                != built["graph_sha256"]
            ):
                raise ValueError("SQLite integrity mismatch")
            result.update(
                status="completed-held",
                current_stage=None,
                last_completed_stage="graph",
                failure_category=None,
                recovery="Inspect the held candidate. No acceptance or activation has occurred.",
            )
        elif result["failure_category"]:
            result["status"] = "incomplete-held"
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error):
        result.update(
            status="incomplete-held", failure_category="corrupt_or_missing_artifact"
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(status(args.output), indent=2))
    except (ValueError, OSError):
        parser.exit(1, "Status unavailable: unsafe or inaccessible output path.\n")


if __name__ == "__main__":
    main()
