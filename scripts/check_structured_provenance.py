#!/usr/bin/env python3
"""Classify path-like JSON provenance values without printing their contents."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = "2026.09.22"
PATH_KEYS = {
    "checkout",
    "cwd",
    "file",
    "file_path",
    "input",
    "location",
    "path",
    "repository",
    "root",
    "source",
    "source_path",
    "workspace",
}
REMOTE_PREFIXES = ("http://", "https://", "mailto:", "data:")
ABSOLUTE_PATH = re.compile(r"^(?:/|~/|[A-Za-z]:[\\/])")
LOCAL_MARKER = re.compile(r"(?:^|[/\\])(?:Users|private|tmp|var|Volumes|home)(?:[/\\])")
RELATIVE_PATH = re.compile(r"^(?:\.{1,2}[/\\]|file://|Source[/\\])")


def _pointer(parts: Iterable[str]) -> str:
    result = ""
    for part in parts:
        escaped = part.replace("~", "~0").replace("/", "~1")
        result += "/" + escaped
    return result or "/"


def _is_path_like(key: str, value: str) -> bool:
    lowered = key.casefold()
    return (
        lowered in PATH_KEYS
        or lowered.endswith("_path")
        or lowered.endswith("_directory")
        or ABSOLUTE_PATH.match(value) is not None
        or LOCAL_MARKER.search(value) is not None
        or RELATIVE_PATH.match(value) is not None
    )


def _classify(root: Path, source: Path, key: str, value: str) -> str | None:
    if value.startswith(REMOTE_PREFIXES):
        return None
    if not _is_path_like(key, value):
        return None
    candidate = value
    if candidate.startswith("file://"):
        candidate = candidate.removeprefix("file://")
    if candidate.startswith("~/"):
        resolved = (Path.home() / candidate[2:]).resolve()
    elif ABSOLUTE_PATH.match(candidate):
        resolved = Path(candidate).expanduser().resolve()
    else:
        resolved = (source.parent / PurePosixPath(candidate)).resolve()
    if resolved == root or root in resolved.parents:
        return "local_inside_checkout"
    if ABSOLUTE_PATH.match(candidate) or candidate.startswith("file://"):
        return "local_outside_checkout"
    return "relative_outside_checkout"


def _walk(value: Any, parts: tuple[str, ...] = ()) -> Iterable[tuple[str, str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, (*parts, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, (*parts, str(index)))
    elif isinstance(value, str) and parts:
        yield _pointer(parts), parts[-1], value


def scan(
    root: Path, excludes: Sequence[str] = (), max_findings: int = 1000
) -> dict[str, Any]:
    """Return a privacy-safe structured-provenance classification report."""

    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Knowledge root is not a directory: {root}")
    if max_findings < 0:
        raise ValueError("max_findings must be non-negative")
    findings: list[dict[str, Any]] = []
    parse_errors: list[dict[str, str]] = []
    files_scanned = 0
    path_like_values = 0
    outside_checkout_values = 0
    outside_by_file: Counter[str] = Counter()
    outside_by_key: Counter[str] = Counter()
    for source in sorted(root.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in {".json", ".jsonl"}:
            continue
        relative = source.relative_to(root).as_posix()
        if any(
            relative == excluded or relative.startswith(f"{excluded.rstrip('/')}/")
            for excluded in excludes
        ):
            continue
        files_scanned += 1
        try:
            if source.suffix.lower() == ".jsonl":
                values = [
                    (line_number, json.loads(line))
                    for line_number, line in enumerate(
                        source.read_text(encoding="utf-8").splitlines()
                    )
                    if line.strip()
                ]
            else:
                values = [(None, json.loads(source.read_text(encoding="utf-8")))]
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            parse_errors.append({"file": relative, "error": type(error).__name__})
            continue
        for line_number, value in values:
            prefix = (str(line_number),) if line_number is not None else ()
            for pointer, key, text in _walk(value, prefix):
                classification = _classify(root, source, key, text)
                if classification is None:
                    continue
                path_like_values += 1
                if classification != "local_inside_checkout":
                    outside_checkout_values += 1
                    outside_by_file[relative] += 1
                    outside_by_key[key] += 1
                if len(findings) < max_findings:
                    findings.append(
                        {
                            "file": relative,
                            "pointer": pointer,
                            "key": key,
                            "classification": classification,
                            "value_sha256": hashlib.sha256(text.encode()).hexdigest(),
                            "value_length": len(text),
                        }
                    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "hold" if outside_checkout_values or parse_errors else "pass",
        "automatic_rewrite": False,
        "files_scanned": files_scanned,
        "path_like_values": path_like_values,
        "outside_checkout_values": outside_checkout_values,
        "outside_by_file": [
            {"file": file, "count": count}
            for file, count in outside_by_file.most_common(50)
        ],
        "outside_by_key": [
            {"key": key, "count": count}
            for key, count in outside_by_key.most_common(50)
        ],
        "stored_findings": len(findings),
        "findings_truncated": path_like_values > len(findings),
        "parse_error_count": len(parse_errors),
        "findings": findings,
        "parse_errors": parse_errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--max-findings", type=int, default=1000)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        report = scan(args.root, args.exclude, args.max_findings)
    except ValueError as error:
        parser.error(str(error))
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"status={report['status']} files={report['files_scanned']} "
            f"path_like={report['path_like_values']} "
            f"outside={report['outside_checkout_values']} "
            f"parse_errors={report['parse_error_count']} "
            f"stored={report['stored_findings']}"
        )
    return 1 if report["status"] == "hold" else 0


if __name__ == "__main__":
    sys.exit(main())
