"""Manual, inspection-only graph packages. Run one operator mutation at a time."""

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path

from pi.alexandria.runtime.graph_access import (
    MAX_GRAPH,
    PACKAGE_FILES,
    SHA256,
    GraphReader,
    OptionalGraph,
    digest,
    encoded,
    package_reader,
    read_bytes,
    read_json,
    require,
    selection_state,
)

PROVENANCE_FIELDS = (
    "version",
    "source",
    "model",
    "skill_sha256",
    "runner_sha256",
    "contracts_sha256",
    "request_limit",
    "automatic_acceptance",
    "actual_upstream_route",
)


def atomic_state(store, state):
    """Same-filesystem atomic replacement; failed replacement preserves old pointer."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=store, prefix=".selection-", delete=False
        ) as f:
            temporary = Path(f.name)
            f.write(encoded(state))
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, store / "graph-selection.json")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def initialize(store):
    store = Path(os.path.abspath(store))
    require(not store.is_symlink() and not any(p.is_symlink() for p in store.parents))
    if store.exists():
        selection_state(store)
        require((store / "packages").is_dir() and not (store / "packages").is_symlink())
    else:
        store.mkdir(mode=0o700)  # Parent must already exist; never infer storage roots.
        (store / "packages").mkdir(mode=0o700)
        atomic_state(
            store,
            {
                "version": "2026.09.19",
                "inspection_only": True,
                "current": None,
                "previous": None,
                "withdrawn_source_hashes": [],
            },
        )
    return store


def package(bundle, store):
    """Copy only the fixed reader contract, omitting prompts/config/unknown files."""
    reader = GraphReader(bundle)  # Fail before creating storage for incomplete runs.
    store = initialize(store)
    identity = uuid.uuid4().hex
    names = list(PACKAGE_FILES) + ["source/" + name for name in reader.texts]
    with tempfile.TemporaryDirectory(
        dir=store / "packages", prefix=".package-"
    ) as work:
        temporary = Path(work)
        for name in names:
            raw = read_bytes(reader.root / name, MAX_GRAPH)
            if name == "run-manifest.json":
                original = read_json(reader.root / name)
                raw = encoded(
                    {k: original[k] for k in PROVENANCE_FIELDS if k in original}
                )
            destination = temporary / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            destination.chmod(0o600)
        GraphReader(
            temporary
        )  # Copied bytes, not the mutable input, are authoritative.
        manifest = {
            "version": "2026.09.19",
            "inspection_only": True,
            "files": {
                name: digest(read_bytes(temporary / name, MAX_GRAPH)) for name in names
            },
        }
        (temporary / "package.json").write_bytes(encoded(manifest))
        # Rename only this new package. Selection is a separate explicit operation.
        temporary.rename(store / "packages" / identity)
    package_reader(store, identity, selection_state(store)["withdrawn_source_hashes"])
    return {"package_id": identity, "inspection_only": True, "selected": False}


def select(store, identity):
    store = Path(store)
    state = selection_state(store)
    reader = package_reader(store, identity, state["withdrawn_source_hashes"])
    if identity != state["current"]:
        state["previous"], state["current"] = state["current"], identity
        atomic_state(store, state)
    return {**state, "graph": reader.status()}


def restore_previous(store):
    state = selection_state(store)
    require(state["previous"] is not None)
    return select(store, state["previous"])


def withdraw_source(store, checksum):
    """Durably block future load/selection/restore of this exact source version."""
    require(isinstance(checksum, str) and SHA256.fullmatch(checksum))
    store = Path(store)
    state = selection_state(store)
    if checksum not in state["withdrawn_source_hashes"]:
        require(len(state["withdrawn_source_hashes"]) < 10000)
        state["withdrawn_source_hashes"].append(checksum)
        atomic_state(store, state)
    return state


def status(store):
    state = selection_state(store)
    return {**state, "graph": OptionalGraph(store=store).status()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("package", "select", "status", "restore-previous", "withdraw-source"):
        command = commands.add_parser(name)
        command.add_argument("--store", type=Path, required=True)
        if name == "package":
            command.add_argument("--bundle", type=Path, required=True)
        elif name == "select":
            command.add_argument("--package-id", required=True)
        elif name == "withdraw-source":
            command.add_argument("--sha256", required=True)
    args = parser.parse_args()
    if args.command == "package":
        result = package(args.bundle, args.store)
    elif args.command == "select":
        result = select(args.store, args.package_id)
    elif args.command == "restore-previous":
        result = restore_previous(args.store)
    elif args.command == "withdraw-source":
        result = withdraw_source(args.store, args.sha256)
    else:
        result = status(args.store)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
