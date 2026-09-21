"""Locate graph pipeline assets in a checkout or an installed distribution."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGED_ASSETS = PACKAGE_DIR / "assets"
CHECKOUT_ASSETS = PACKAGE_DIR / "assets"


def _complete(root: Path) -> bool:
    return root.is_dir() and all(
        path.is_file()
        for path in (
            root / "fixtures/humanizer-SKILL.md",
            root / "fixtures/humanizer-source.json",
            root / "pi-trial/preservation.mjs",
            root / "pi-trial/preservation-trial.mjs",
            root / "pi-trial/package-lock.json",
        )
    )


def asset_root() -> Path:
    """Return the pinned graph assets without assuming a source checkout.

    A checkout override is useful for development and for an operator-managed
    Node dependency directory. Installed wheels use their packaged assets.
    """

    override = os.environ.get("ALEXANDRIA_GRAPH_ASSET_ROOT")
    if override:
        root = Path(override).expanduser().resolve()
        if not _complete(root):
            raise ValueError("ALEXANDRIA_GRAPH_ASSET_ROOT is not a complete asset set")
        return root
    if _complete(CHECKOUT_ASSETS):
        return CHECKOUT_ASSETS
    if not _complete(PACKAGED_ASSETS):
        raise ValueError("Alexandria graph assets are missing from the installation")
    return PACKAGED_ASSETS


def node_modules_root(root: Path | None = None) -> Path:
    """Locate operator-managed Pi dependencies without packaging node_modules."""

    override = os.environ.get("ALEXANDRIA_GRAPH_NODE_MODULES")
    if override:
        return Path(override).expanduser().resolve()
    roots = []
    if root is not None:
        roots.append(Path(root) / "pi-trial/node_modules")
    roots.append(CHECKOUT_ASSETS / "pi-trial/node_modules")
    for candidate in roots:
        if candidate.is_dir():
            return candidate
    return roots[0]
