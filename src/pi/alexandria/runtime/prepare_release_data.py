"""Stage only Alexandria's active index generation and matching model cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("Active database path escapes the data directory")
    return candidate


def reject_symlinks(root: Path) -> None:
    if root.is_symlink():
        raise ValueError(f"Symlink is not allowed in release data: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlink is not allowed in release data: {path}")


def validate_internal_symlinks(root: Path) -> None:
    """Allow model-cache links only when they resolve inside the model cache."""
    resolved_root = root.resolve()
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        target = path.resolve()
        if not target.exists() or not target.is_relative_to(resolved_root):
            raise ValueError(f"Model symlink escapes the release data: {path}")


def tree_digest(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    files = 0
    size = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        files += 1
    return digest.hexdigest(), files, size


def stage(
    source: Path, destination: Path, *, source_revision=None, embedding_model=None
) -> dict:
    source = source.resolve()
    if destination.exists():
        raise ValueError("Destination already exists; use a new empty path")
    manifest_path = source / "active.json"
    if manifest_path.is_symlink():
        raise ValueError("active.json must not be a symlink")
    manifest = json.loads(manifest_path.read_text())
    if source_revision is not None and manifest.get("revision") != source_revision:
        raise ValueError(
            "Active index revision does not match the selected source revision"
        )
    if (
        embedding_model is not None
        and manifest.get("embedding_model") != embedding_model
    ):
        raise ValueError(
            "Active index embedding model does not match the selected model"
        )
    database = Path(manifest["database"])
    if database.parent != Path("builds") or database.name in ("", ".", ".."):
        raise ValueError("Active database must be a direct child of builds/")
    active_build = safe_child(source, manifest["database"])
    models = source / "models"
    for required in (manifest_path, active_build, models):
        if not required.exists():
            raise ValueError(f"Missing release input: {required}")
    reject_symlinks(active_build)
    validate_internal_symlinks(models)

    destination.mkdir(parents=True)
    (destination / "builds").mkdir()
    shutil.copy2(manifest_path, destination / "active.json")
    shutil.copytree(active_build, destination / manifest["database"])
    shutil.copytree(
        models,
        destination / "models",
        symlinks=False,
        ignore=shutil.ignore_patterns(".locks", "*.lock"),
    )
    digest, files, size = tree_digest(destination)
    receipt = {
        "indexed_revision": manifest["revision"],
        "database": manifest["database"],
        "embedding_model": manifest["embedding_model"],
        "documents": manifest["documents"],
        "passages": manifest["passages"],
        "tree_sha256": digest,
        "files": files,
        "bytes": size,
    }
    (destination / "release-data-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--revision")
    parser.add_argument("--model")
    args = parser.parse_args()
    print(
        json.dumps(
            stage(
                args.source,
                args.destination,
                source_revision=args.revision,
                embedding_model=args.model,
            ),
            indent=2,
        )
    )
