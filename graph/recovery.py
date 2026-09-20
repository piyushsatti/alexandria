"""Explicit local graph backup/restore; one operator, immutable inputs per call.

Only selected packages travel. Restore always requires independently supplied
current withdrawal policy. This is local recovery, not off-host disaster recovery.
"""

import argparse
import json
import os
import tempfile
from pathlib import Path

import generation
from graph_access import (
    MAX_GRAPH,
    SHA256,
    digest,
    encoded,
    package_reader,
    read_bytes,
    read_json,
    require,
    safe_path,
    selection_state,
)

VERSION = "2026.09.19"
STATE_FIELDS = {
    "version",
    "inspection_only",
    "current",
    "previous",
    "withdrawn_source_hashes",
}


def _directory(path):
    path = Path(os.path.abspath(path))
    require(all(not p.is_symlink() for p in (path, *path.parents)))
    require(path.is_dir())
    return path


def _destination(path, *inputs):
    path = Path(os.path.abspath(path))
    require(all(not p.is_symlink() for p in (path, *path.parents)))
    require(not path.exists() and path.parent.is_dir())
    for source in inputs:
        require(
            path != source and source not in path.parents and path not in source.parents
        )
    return path


def _state(store):
    state = selection_state(store)
    return {key: state[key] for key in STATE_FIELDS}


def _selected_files(store, state):
    """Use the existing package/reader contracts, never discover other packages."""
    names = {"graph-selection.json"}
    for identity in {state["current"], state["previous"]} - {None}:
        package_reader(store, identity, state["withdrawn_source_hashes"])
        root = store / "packages" / identity
        provenance = read_json(root / "run-manifest.json")
        require(set(provenance) <= set(generation.PROVENANCE_FIELDS))
        manifest = read_json(root / "package.json")
        for name in {*manifest["files"], "package.json"}:
            names.add("packages/" + identity + "/" + safe_path(name))
    return names


def _tree_matches(root, names):
    """Reject unknown files/directories, symlinks and nonregular backup entries."""
    directories = {"packages"}  # Empty policy-only stores are valid.
    for name in names:
        safe_path(name)
        directories.update(str(p) for p in Path(name).parents if str(p) != ".")
    found = set()
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in dirs:
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            require(not path.is_symlink() and relative in directories)
        for name in files:
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            require(relative in names)
            read_bytes(path, MAX_GRAPH)  # Regular-file and ancestor checks.
            found.add(relative)
    require(found == names and (root / "packages").is_dir())


def _copy(source, destination, names):
    (destination / "packages").mkdir(mode=0o700)
    for name in sorted(names):
        raw = read_bytes(source / name, MAX_GRAPH)
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with target.open("xb") as stream:
            stream.write(raw)
        target.chmod(0o600)


def verify_backup(backup):
    """Check complete inventory, package/source/graph provenance, and policy."""
    root = _directory(backup)
    manifest = read_json(root / "backup.json")
    require(set(manifest) == {"version", "kind", "inspection_only", "files"})
    require(
        manifest["version"] == VERSION
        and manifest["kind"] == "local-selected-graph-backup"
        and manifest["inspection_only"] is True
    )
    files = manifest["files"]
    require(isinstance(files, dict) and 1 <= len(files) <= 29)
    for name, checksum in files.items():
        safe_path(name)
        require(isinstance(checksum, str) and SHA256.fullmatch(checksum))
    _tree_matches(root, set(files) | {"backup.json"})
    for name, checksum in files.items():
        require(digest(read_bytes(root / name, MAX_GRAPH)) == checksum)
    require(set(read_json(root / "graph-selection.json")) == STATE_FIELDS)
    state = _state(root)
    require(set(files) == _selected_files(root, state))
    return {
        "version": VERSION,
        "status": "verified-local-backup",
        "backup_sha256": digest(read_bytes(root / "backup.json", MAX_GRAPH)),
        "files": len(files),
        "selection": state,
        "inspection_only": True,
        "off_host_recovery_verified": False,
    }


def backup(store, destination):
    """Back up current/previous only; withdrawn selections fail before output."""
    store = _directory(store)
    destination = _destination(destination, store)
    original = read_bytes(store / "graph-selection.json", MAX_GRAPH)
    state = _state(store)
    names = _selected_files(store, state)
    with tempfile.TemporaryDirectory(
        dir=destination.parent, prefix=".graph-backup-"
    ) as directory:
        staged = Path(directory) / "complete"
        staged.mkdir(mode=0o700)
        _copy(store, staged, names - {"graph-selection.json"})
        generation.atomic_state(staged, state)
        manifest = {
            "version": VERSION,
            "kind": "local-selected-graph-backup",
            "inspection_only": True,
            "files": {
                name: digest(read_bytes(staged / name, MAX_GRAPH))
                for name in sorted(names)
            },
        }
        (staged / "backup.json").write_bytes(encoded(manifest))
        (staged / "backup.json").chmod(0o600)
        receipt = verify_backup(staged)
        require(read_bytes(store / "graph-selection.json", MAX_GRAPH) == original)
        require(not destination.exists())
        staged.rename(destination)
    return {**receipt, "backup": str(destination)}


def restore(backup_directory, destination, *, current_policy_store):
    """Restore fresh, preserving the union of old and current withdrawals.

    The caller identifies an authoritative current policy store. Freshness cannot
    be inferred from backup contents, timestamps, or an unavailable old machine.
    """
    source = _directory(backup_directory)
    policy = _directory(current_policy_store)
    require(
        source != policy
        and source not in policy.parents
        and policy not in source.parents
    )
    destination = _destination(destination, source, policy)
    verified = verify_backup(source)
    policy_bytes = read_bytes(policy / "graph-selection.json", MAX_GRAPH)
    latest = _state(policy)
    state = verified["selection"]
    state["withdrawn_source_hashes"] = sorted(
        set(state["withdrawn_source_hashes"]) | set(latest["withdrawn_source_hashes"])
    )
    require(len(state["withdrawn_source_hashes"]) <= 10000)
    # Reject current OR previous if withdrawn under fresh policy, before copying.
    names = _selected_files(source, state)
    with tempfile.TemporaryDirectory(
        dir=destination.parent, prefix=".graph-restore-"
    ) as directory:
        staged = Path(directory) / "complete"
        staged.mkdir(mode=0o700)
        _copy(source, staged, names - {"graph-selection.json"})
        generation.atomic_state(staged, state)
        require(_selected_files(staged, state) == names)
        _tree_matches(staged, names)
        require(verify_backup(source)["backup_sha256"] == verified["backup_sha256"])
        require(read_bytes(policy / "graph-selection.json", MAX_GRAPH) == policy_bytes)
        require(not destination.exists())
        staged.rename(destination)
    return {
        "version": VERSION,
        "status": "restored-local-inspection-store",
        "store": str(destination),
        "backup_sha256": verified["backup_sha256"],
        "current_policy_sha256": digest(policy_bytes),
        "selection": state,
        "inspection_only": True,
        "off_host_recovery_verified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("backup")
    create.add_argument("--store", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--backup", type=Path, required=True)
    recover = commands.add_parser("restore")
    recover.add_argument("--backup", type=Path, required=True)
    recover.add_argument("--output", type=Path, required=True)
    recover.add_argument("--current-policy-store", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "backup":
        result = backup(args.store, args.output)
    elif args.command == "verify":
        result = verify_backup(args.backup)
    else:
        result = restore(
            args.backup, args.output, current_policy_store=args.current_policy_store
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
