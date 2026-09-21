"""Prepare a versioned data block from an exact Knowledge checkout and index."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from pi.alexandria.runtime.prepare_release_data import stage


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), "-c", f"safe.directory={root}", *args],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def committed_text(root: Path, revision: str) -> dict[str, bytes]:
    records = {}
    output = subprocess.check_output(
        [
            "git",
            "-C",
            str(root),
            "-c",
            f"safe.directory={root}",
            "ls-tree",
            "-rz",
            revision,
        ],
    )
    for entry in output.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, blob = metadata.decode().split()
        path = name.decode()
        if (
            mode not in ("100644", "100755")
            or kind != "blob"
            or not path.endswith((".md", ".txt"))
        ):
            continue
        records[path] = subprocess.check_output(
            [
                "git",
                "-C",
                str(root),
                "-c",
                f"safe.directory={root}",
                "cat-file",
                "blob",
                blob,
            ]
        )
    return records


def prepare(
    knowledge: Path, active_data: Path, revision: str, model: str, output: Path
) -> dict:
    knowledge = knowledge.resolve()
    active_data = active_data.resolve()
    if not revision or revision.startswith("-"):
        raise ValueError("An exact Knowledge revision is required")
    try:
        selected = git(knowledge, "rev-parse", "--verify", revision + "^{commit}")
    except subprocess.CalledProcessError as error:
        raise ValueError("Selected Knowledge revision is not a commit") from error
    head = git(knowledge, "rev-parse", "HEAD")
    if selected != head:
        raise ValueError("Knowledge checkout HEAD does not equal the selected revision")
    manifest = json.loads((active_data / "active.json").read_text())
    if manifest.get("revision") != selected:
        raise ValueError(
            "Active index is not built from the selected Knowledge revision"
        )
    if manifest.get("embedding_model") != model:
        raise ValueError("Active index uses a different embedding model")
    receipt = stage(
        active_data,
        output,
        source_revision=selected,
        embedding_model=model,
    )
    snapshot = output / manifest["database"] / "source"
    expected = committed_text(knowledge, selected)
    actual = {
        path.relative_to(snapshot).as_posix(): path.read_bytes()
        for path in snapshot.rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix in (".md", ".txt")
    }
    if actual.keys() != expected.keys():
        raise ValueError(
            "Data block source snapshot does not match the selected revision"
        )
    source_hashes = {}
    for path, data in expected.items():
        if actual[path] != data:
            raise ValueError(f"Data block source hash mismatch: {path}")
        source_hashes[path] = hashlib.sha256(data).hexdigest()
    inbound = output / ".alexandria-inbound"
    for directory in (
        inbound,
        inbound / "content",
        inbound / "queue",
        inbound / "receipts",
        inbound / "index",
    ):
        directory.mkdir(mode=0o700, exist_ok=True)
    receipt.update(
        {
            "knowledge_revision": selected,
            "knowledge_checkout": str(knowledge),
            "embedding_model": model,
            "source_hashes": source_hashes,
            "source_documents": len(source_hashes),
            "inbound_mount": ".alexandria-inbound",
        }
    )
    (output / "release-data-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge", type=Path, required=True)
    parser.add_argument("--active-data", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                args.knowledge, args.active_data, args.revision, args.model, args.output
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
