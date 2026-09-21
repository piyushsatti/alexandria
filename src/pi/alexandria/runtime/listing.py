"""Bounded, read-only file-tree and Markdown frontmatter helpers."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

MAX_FRONTMATTER_BYTES = 16 * 1024


def validate_relative_path(value: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError("Path must be text")
    if not value and allow_empty:
        return ""
    if not value or value.startswith("/") or "\\" in value or "\x00" in value:
        raise ValueError("Path must be a relative POSIX path")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Path contains an unsafe component")
    return value


def frontmatter(text: str) -> tuple[str | None, str | None]:
    """Return the raw initial frontmatter block without evaluating YAML.

    Only a delimiter on the first line is recognized. The returned raw block
    includes both delimiters. A malformed initial block is reported alongside
    the bounded raw prefix so callers can show the problem without failing the
    complete listing.
    """

    first, _separator, _rest = text.partition("\n")
    if first.rstrip("\r") != "---":
        return None, None
    lines = text.splitlines(keepends=True)
    size = 0
    for index, line in enumerate(lines[1:], start=1):
        size += len(line.encode("utf-8"))
        if size > MAX_FRONTMATTER_BYTES:
            raw = "".join(lines[: index + 1])
            return raw[:MAX_FRONTMATTER_BYTES], "frontmatter_too_large"
        marker = line.rstrip("\r\n")
        if marker in ("---", "..."):
            return "".join(lines[: index + 1]), None
    raw = text[:MAX_FRONTMATTER_BYTES]
    return raw, "unterminated_frontmatter"


def _path_matches(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix + "/")


def _entry(
    path: Path,
    relative: str,
    *,
    revision=None,
    submission_id=None,
    include_frontmatter=False,
):
    data = path.read_bytes()
    result = {
        "path": relative,
        "kind": "file",
        "depth": len(relative.split("/")),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if revision is not None:
        result["revision"] = revision
    if submission_id is not None:
        result["submission_id"] = submission_id
    if include_frontmatter:
        raw, error = frontmatter(data.decode("utf-8"))
        result["frontmatter"] = raw
        if error:
            result["frontmatter_error"] = error
    return result


def _tree(entries: list[dict], *, prefix: str, limit: int) -> dict:
    paths = {entry["path"] for entry in entries}
    directories: set[str] = set()
    for path in paths:
        parts = path.split("/")
        for index in range(1, len(parts)):
            directories.add("/".join(parts[:index]))
    result: list[dict] = []
    for directory in sorted(directories):
        if _path_matches(directory, prefix):
            result.append(
                {
                    "path": directory,
                    "kind": "directory",
                    "depth": len(directory.split("/")),
                }
            )
    result.extend(
        entry
        for entry in sorted(entries, key=lambda item: item["path"])
        if _path_matches(entry["path"], prefix)
    )
    result.sort(key=lambda item: (item["path"], item["kind"] != "directory"))
    return {
        "entries": result[:limit],
        "truncated": len(result) > limit,
        "prefix": prefix,
        "limit": limit,
    }


def committed_tree(
    snapshot: Path,
    revision: str,
    *,
    prefix: str = "",
    limit: int = 500,
    include_frontmatter: bool = False,
) -> dict:
    prefix = validate_relative_path(prefix, allow_empty=True)
    if not 1 <= limit <= 500:
        raise ValueError("Limit must be between 1 and 500")
    snapshot = snapshot.resolve()
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise ValueError("Committed source snapshot is unavailable")
    entries: list[dict] = []
    for root, dirs, names in os.walk(snapshot, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(root) / d).is_symlink())
        for name in sorted(names):
            path = Path(root) / name
            if path.is_symlink() or path.suffix not in (".md", ".txt"):
                continue
            relative = path.relative_to(snapshot).as_posix()
            entries.append(
                _entry(
                    path,
                    relative,
                    revision=revision,
                    include_frontmatter=include_frontmatter,
                )
            )
    return _tree(entries, prefix=prefix, limit=limit)


def inbound_tree(entries: list[dict], *, prefix: str = "", limit: int = 500) -> dict:
    prefix = validate_relative_path(prefix, allow_empty=True)
    if not 1 <= limit <= 500:
        raise ValueError("Limit must be between 1 and 500")
    return _tree(entries, prefix=prefix, limit=limit)
