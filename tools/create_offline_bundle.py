"""Create a self-contained offline Alexandria release bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    return digest.hexdigest()


def validate_data_block(data: Path) -> dict:
    data = data.resolve()
    if not data.is_dir() or data.is_symlink():
        raise ValueError("Data block must be a real directory")
    manifest = data / "active.json"
    receipt = data / "release-data-receipt.json"
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError("Data block is missing a regular active.json")
    if not receipt.is_file() or receipt.is_symlink():
        raise ValueError("Data block is missing a release-data-receipt.json")
    for path in data.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Data block may not contain symlinks: {path}")
    active = json.loads(manifest.read_text())
    release = json.loads(receipt.read_text())
    if release.get("indexed_revision") != active.get("revision"):
        raise ValueError("Data-block receipt does not match active revision")
    return {
        "indexed_revision": active.get("revision"),
        "embedding_model": active.get("embedding_model"),
        "documents": active.get("documents"),
        "passages": active.get("passages"),
        "tree_sha256": tree_digest(data),
        "receipt": release,
    }


def create_bundle(
    image_archive: Path,
    data_block: Path,
    output: Path,
    *,
    product_revision: str,
    release: str,
    image_ref: str,
) -> dict:
    image_archive = image_archive.resolve()
    data_block = data_block.resolve()
    output = output.resolve()
    if not image_archive.is_file() or image_archive.is_symlink():
        raise ValueError("Image archive must be a regular file")
    if output.exists():
        raise ValueError("Output bundle already exists; choose a new path")
    if not product_revision or not release or not image_ref:
        raise ValueError("product revision, release, and image reference are required")

    data_info = validate_data_block(data_block)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="alexandria-bundle-") as temporary:
        root = Path(temporary)
        shutil.copy2(image_archive, root / "alexandria-image.tar")
        shutil.copytree(data_block, root / "data")
        config = {
            "mcpServers": {
                "alexandria": {
                    "command": "docker",
                    "args": [
                        "run",
                        "--rm",
                        "-i",
                        "--network",
                        "none",
                        "--read-only",
                        "--mount",
                        "type=bind,src=/path/to/alexandria-data,dst=/data,readonly",
                        image_ref,
                        "serve",
                        "--data",
                        "/data",
                    ],
                }
            }
        }
        (root / "stdio-config.json").write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )
        manifest = {
            "schema_version": 1,
            "release": release,
            "product_revision": product_revision,
            "image_reference": image_ref,
            "image_archive": "alexandria-image.tar",
            "image_archive_sha256": sha256(root / "alexandria-image.tar"),
            "data_directory": "data",
            "data_block": data_info,
            "network_required": False,
            "credentials_included": False,
        }
        (root / "release-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        with tarfile.open(output, "w:gz") as archive:
            for path in sorted(root.rglob("*")):
                archive.add(path, arcname=path.relative_to(root))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-archive", type=Path, required=True)
    parser.add_argument("--data-block", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--product-revision", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--image-reference", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            create_bundle(
                args.image_archive,
                args.data_block,
                args.output,
                product_revision=args.product_revision,
                release=args.release,
                image_ref=args.image_reference,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
