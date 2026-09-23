#!/usr/bin/env python3
"""Check authored Markdown links without rewriting preserved provenance."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

DISALLOWED_PREFIXES = ("/", "~/", "file:", "//")
REMOTE_PREFIXES = ("http://", "https://", "mailto:", "data:")
INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
REFERENCE_LINK = re.compile(
    r"^\s*\[(?P<label>[^]]+)\]:\s*(?:<(?P<bracketed>[^>]+)>|(?P<plain>\S+))"
)
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"`+[^`]*`+")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    target: str
    reason: str


def _strip_line_suffix(target: str) -> str:
    base, separator, suffix = target.rpartition(":")
    if separator and suffix.isdigit():
        return base
    return target


def _is_local_target(target: str) -> bool:
    return (
        target.startswith(DISALLOWED_PREFIXES)
        or any(marker in target for marker in ("localhost", "127.0.0.1"))
        or target.startswith(("Source/Projects", "Source/TODO"))
    )


def _check_target(root: Path, source: Path, line: int, target: str) -> Finding | None:
    target = target.strip()
    if not target or target.startswith(("#", *REMOTE_PREFIXES)):
        return None
    if _is_local_target(target):
        return Finding(str(source.relative_to(root)), line, target, "local target")

    path_target = _strip_line_suffix(target.split("#", 1)[0])
    if not path_target:
        return None
    resolved = (source.parent / path_target).resolve()
    if resolved != root and root not in resolved.parents:
        return Finding(
            str(source.relative_to(root)), line, target, "target escapes checkout"
        )
    if not resolved.exists():
        return Finding(
            str(source.relative_to(root)), line, target, "relative target is missing"
        )
    return None


def _iter_targets(root: Path, source: Path) -> Iterable[tuple[int, str]]:
    fenced = False
    frontmatter = False
    for line_number, line in enumerate(
        source.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        stripped = line.strip()
        if line_number == 1 and stripped == "---":
            frontmatter = True
            continue
        if frontmatter:
            if stripped == "---":
                frontmatter = False
            continue
        fence = FENCE.match(line)
        if fence:
            fenced = not fenced
            continue
        if fenced:
            continue
        visible = INLINE_CODE.sub("", line)
        for match in INLINE_LINK.finditer(visible):
            yield line_number, match.group(1)
        reference = REFERENCE_LINK.match(visible)
        if reference and not reference.group("label").startswith("^"):
            yield line_number, reference.group("bracketed") or reference.group("plain")


def scan(root: Path, excludes: Sequence[str] = ()) -> list[Finding]:
    """Return portability findings for authored Markdown in ``root``."""

    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Knowledge root is not a directory: {root}")
    findings: list[Finding] = []
    for source in sorted(root.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in {".md", ".mdx"}:
            continue
        relative = source.relative_to(root).as_posix()
        if any(
            relative == excluded or relative.startswith(f"{excluded.rstrip('/')}/")
            for excluded in excludes
        ):
            continue
        for line, target in _iter_targets(root, source):
            finding = _check_target(root, source, line, target)
            if finding:
                findings.append(finding)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check authored Markdown links inside a Knowledge checkout"
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Repository-relative file or directory prefix to skip (repeatable)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        findings = scan(args.root, args.exclude)
    except ValueError as error:
        parser.error(str(error))
    if args.as_json:
        print(json.dumps([asdict(item) for item in findings], indent=2))
    else:
        for item in findings:
            print(f"{item.path}:{item.line}: {item.reason}: {item.target}")
        print(f"checked authored Markdown; findings={len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
