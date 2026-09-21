"""Check maintenance changes without printing private document content."""

import argparse
import re
import subprocess
from pathlib import PurePosixPath


def forbidden(path):
    parts = PurePosixPath(path).parts
    return (
        any(
            part
            in {
                ".git",
                ".alexandria-data",
                ".alexandria-delivery",
                ".alexandria-recovery",
                ".foundry",
                ".rag",
                "__pycache__",
                ".ruff_cache",
                ".pytest_cache",
                "node_modules",
            }
            for part in parts
        )
        or path.endswith((".pyc", "/monitor.heartbeat"))
        or (
            "/.obsidian/" in path
            and parts[-1] in {"workspace.json", "workspace-mobile.json"}
        )
        or ("/test-output/" in path and parts[-1] == ".last-run.json")
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args()
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", args.base, args.head], check=True
    )
    names = (
        subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                "--diff-filter=ACMRT",
                "-z",
                args.base,
                args.head,
            ]
        )
        .decode()
        .split("\0")
    )
    bad = [name for name in names if name and forbidden(name)]
    whitespace = subprocess.run(
        [
            "git",
            "diff",
            "--check",
            args.base,
            args.head,
            "--",
            ".",
            # Frozen source snapshots and review evidence preserve original bytes,
            # including whitespace. Temporary-file checks above still cover them.
            ":(glob,exclude)Alexandria/graph/trials/**",
            ":(glob,exclude)Alexandria/graph/reviews/**",
            ":(glob,exclude)Alexandria/graph/delivery-*/live-*/**",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if whitespace.returncode not in (0, 2):
        raise RuntimeError("Could not inspect whitespace")
    # git diff --check includes offending source lines; emit only locations.
    locations = [
        line.split(":", 2)[:2]
        for line in whitespace.stdout.splitlines()
        if re.match(r"^.+:\d+: ", line)
    ]
    for path in bad:
        print(f"Temporary artifact is tracked: {path}")
    for location in locations:
        print("Whitespace error at " + ":".join(location))
    if bad or whitespace.returncode:
        raise SystemExit(1)
    print("Change hygiene passed; no source contents emitted.")


if __name__ == "__main__":
    main()
