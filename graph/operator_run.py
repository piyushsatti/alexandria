"""One explicit operator command: offline plan, prepare, or held candidate run."""

import argparse
import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

import graph
import preparation as prep
import render_preservation

HERE = Path(__file__).resolve().parent
TIMEOUT_SECONDS = 2000  # Four existing 480-second stages plus local overhead.


def write_receipt(root, name, data):
    """Exclusive append-only stage receipt; never print model/source output."""
    prep._write(root / name, data)


def verify_prepared(root):
    root = graph.no_symlinks(root).resolve()
    plan = graph.read_json(root / "preparation.json")
    config = graph.read_json(root / "runner-config.json")
    manifest = graph.read_json(root / "source/manifest.json")
    paths = prep._paths(config.get("approved_external_paths", []))
    if config != {
        "model": prep.MODEL,
        "execute": False,
        "approved_external_paths": paths,
    }:
        raise ValueError("Prepared configuration mismatch")
    if (
        plan.get("status") != "prepared-not-executed"
        or plan.get("model") != prep.MODEL
        or plan.get("approved_external_paths") != paths
        or manifest.get("revision") != plan.get("revision")
        or [r["path"] for r in manifest["files"]] != paths
        or [r["path"] for r in plan["files"]] != paths
    ):
        raise ValueError("Prepared source identity mismatch")
    for row, expected in zip(manifest["files"], plan["files"]):
        raw = graph.read_bytes(root / "source" / row["path"], prep.MAX_DOCUMENT_BYTES)
        raw.decode("utf-8")
        if (
            not raw.strip()
            or prep.digest(raw) != row["sha256"]
            or row["sha256"] != expected["sha256"]
            or len(raw) != expected["bytes"]
        ):
            raise ValueError("Prepared source hash mismatch")
    skill, provenance = prep._skill()
    if (
        graph.read_bytes(root / "humanizer-SKILL.md", 100000) != skill
        or graph.read_bytes(root / "humanizer-source.json", 10000) != provenance
    ):
        raise ValueError("Prepared guidance mismatch")
    return {**config, "execute": True}


def run_process(command, config, timeout=TIMEOUT_SECONDS):
    """No shell, no captured provider output; kill the process group on timeout."""
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    ) as process:
        try:
            process.communicate(json.dumps(config).encode(), timeout=timeout)
        except (Exception, KeyboardInterrupt) as error:
            # Only this freshly created session is ours to terminate. Do this
            # before Popen.__exit__, which otherwise waits on an interrupted child.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            if isinstance(error, subprocess.TimeoutExpired):
                raise TimeoutError("Bounded pipeline deadline exceeded") from None
            raise
        if process.returncode != 0:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return process.returncode


def execute_prepared(root, *, process_runner=run_process):
    """Explicit execution API. CLI calls only for --execute; no implicit retry."""
    root = graph.no_symlinks(root).resolve()
    config = verify_prepared(root)
    # A prepared bundle contains exactly these top-level entries; any attempt
    # artifact means retain the attempt and prepare a fresh directory.
    allowed = {
        "source",
        "preparation.json",
        "runner-config.json",
        "humanizer-SKILL.md",
        "humanizer-source.json",
    }
    if {p.name for p in root.iterdir()} != allowed:
        raise ValueError("Fresh prepared bundle required")
    node = shutil.which("node")
    if not node:
        raise ValueError("Node prerequisite missing")
    for package in ("pi-agent-core", "pi-ai"):
        metadata = graph.read_json(
            HERE / "pi-trial/node_modules/@earendil-works" / package / "package.json"
        )
        if metadata.get("version") != "0.85.1":
            raise ValueError("Pinned Pi prerequisite mismatch")
    identities = {
        name: prep.digest(graph.read_bytes(HERE / name, 2_000_000))
        for name in (
            "operator_run.py",
            "preparation.py",
            "pi-trial/preservation-trial.mjs",
            "pi-trial/preservation.mjs",
            "pi-trial/package-lock.json",
            "render_preservation.py",
            "graph.py",
        )
    }
    write_receipt(
        root,
        "operator-start.json",
        {
            "status": "running",
            "model": prep.MODEL,
            "code_sha256": identities,
            "timeout_seconds": TIMEOUT_SECONDS,
            "automatic_acceptance": False,
        },
    )
    stage = "pipeline"
    try:
        code = process_runner(
            [node, str(HERE / "pi-trial/preservation-trial.mjs"), str(root)], config
        )
        if code != 0 or (root / "failure.json").exists():
            raise RuntimeError("Pipeline incomplete")
        verify_prepared(root)
        hold = graph.read_json(root / "hold-receipt.json")
        if (
            hold.get("status") != "held-pending-owner-review"
            or hold.get("automatic_acceptance") is not False
        ):
            raise ValueError("Held receipt required")
        write_receipt(
            root,
            "operator-pipeline.json",
            {
                "status": "completed-held",
                "model_review_pass": hold.get("model_review_pass"),
            },
        )
        stage = "render"
        rendered = render_preservation.render(root)
        write_receipt(root, "operator-render.json", rendered)
        stage = "graph"
        output = root / "candidate-graph"
        if output.exists():
            raise ValueError("Fresh candidate output required")
        built = graph.build(root / "source", output, root / "assertions.json")
        receipt = {
            "status": "completed-held",
            "automatic_acceptance": False,
            "model_review_pass": hold.get("model_review_pass"),
            "candidate_hash": hold["candidate_hash"],
            "graph_sha256": built["graph_sha256"],
            "source_revision": graph.read_json(root / "source/manifest.json")[
                "revision"
            ],
            "artifacts": {
                "graph": "candidate-graph",
                "pages": "candidate-pages.md",
                "review": "review-queue.md",
                "coverage": "coverage-ledger.md",
            },
        }
        write_receipt(root, "operator-result.json", receipt)
        return receipt
    except (Exception, KeyboardInterrupt) as error:
        write_receipt(
            root,
            "operator-failure.json",
            {
                "status": "incomplete-held",
                "stage": stage,
                "category": type(error).__name__,
                "recovery": "Retain this attempt; prepare a fresh output. No automatic resume or activation.",
            },
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--path", action="append", required=True)
    parser.add_argument("--approved-path", action="append", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        operation = prep.prepare if args.prepare or args.execute else prep.preflight
        result = operation(
            args.repo,
            args.revision,
            args.path,
            args.model,
            args.approved_path,
            args.output,
        )
        if args.execute:
            result = execute_prepared(args.output)
    except KeyboardInterrupt:
        parser.exit(
            130, "Operator interrupted; attempt retained. Inspect local receipts.\n"
        )
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(
            1,
            f"Operator stopped ({type(error).__name__}); attempt retained, inspect local receipts.\n",
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
