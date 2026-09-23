"""Fail-closed selection of the Knowledge corpus for indexing and release."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

DEFAULT_MANIFEST = "Alexandria/corpus/corpus-manifest.json"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
ELIGIBLE_STATUS = "eligible"
HELD_STATUSES = {"held_authority", "held_duplicate", "held_provenance"}
SOURCE_STATUSES = {"present", "missing", "duplicate_record"}
PROVENANCE_STATUSES = {"source_present", "snapshot_only", "duplicate_record"}
AUTHORITY_STATUSES = {
    "source_record",
    "supporting_context",
    "unresolved",
    "variant",
    "duplicate",
}


def _safe_relative(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ValueError(f"Invalid {label}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"Unsafe {label}")
    return path.as_posix()


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "-c", f"safe.directory={root}", *args],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as error:
        raise ValueError("Unable to read the selected Knowledge revision") from error


def git_revision(root: Path) -> str:
    revision = _git(root, "rev-parse", "HEAD").decode().strip()
    if not revision or revision.startswith("-"):
        raise ValueError("Knowledge checkout has no usable revision")
    return revision


def _blob(root: Path, revision: str, path: str) -> bytes:
    path = _safe_relative(path, "manifest path")
    return _git(root, "cat-file", "blob", f"{revision}:{path}")


def _verify_source_blob(
    root: Path, revision: str, record: dict[str, Any], path: str
) -> None:
    """Verify repository-local provenance for a source-backed record."""

    source = record.get("source")
    if not isinstance(source, str):
        raise ValueError(f"Source-backed record has no source path: {path}")
    source = _safe_relative(source, "source path")
    expected = record.get("source_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"Source-backed record has no source hash: {path}")
    try:
        data = _blob(root, revision, source)
    except ValueError as error:
        raise ValueError(f"Source blob is not present: {source}") from error
    observed = hashlib.sha256(data).hexdigest()
    if observed != expected:
        raise ValueError(f"Source hash mismatch: {source}")


def load_selection(
    root: Path, revision: str | None = None, manifest_path: str = DEFAULT_MANIFEST
) -> dict[str, Any]:
    """Load and verify the exact release allowlist from a Git revision."""

    root = Path(root).resolve()
    revision = revision or git_revision(root)
    manifest_path = _safe_relative(manifest_path, "corpus manifest path")
    raw = _blob(root, revision, manifest_path)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Corpus manifest exceeds the size limit")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("Corpus manifest is not valid JSON") from error
    if not isinstance(manifest, dict):
        raise ValueError("Corpus manifest must be an object")
    if manifest.get("schema_version") != "2026.09.22":
        raise ValueError("Corpus manifest schema is not supported")
    snapshot_root = _safe_relative(
        manifest.get("snapshot_root"), "corpus snapshot root"
    )
    policy = manifest.get("selection_policy")
    if not isinstance(policy, dict):
        raise ValueError("Corpus manifest has no selection policy")
    if policy.get("eligible_release_status") != ELIGIBLE_STATUS:
        raise ValueError("Corpus manifest has an invalid eligible status")
    if set(policy.get("held_release_statuses", ())) != HELD_STATUSES:
        raise ValueError(
            "Corpus manifest held statuses do not match the runtime contract"
        )
    if policy.get("require_snapshot_hash_match") is not True:
        raise ValueError("Corpus manifest must require snapshot hashes")
    if policy.get("require_source_status") is not True:
        raise ValueError("Corpus manifest must require source status")

    records = manifest.get("records")
    additions = manifest.get("final_context_additions", [])
    if not isinstance(records, list) or not isinstance(additions, list):
        raise ValueError("Corpus manifest records are not lists")

    selected: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in [*records, *additions]:
        if not isinstance(record, dict):
            raise ValueError("Corpus manifest contains a non-object record")
        status = record.get("release_status")
        if status not in {ELIGIBLE_STATUS, *HELD_STATUSES}:
            raise ValueError("Corpus manifest contains an invalid release status")
        if record.get("authority_status") not in AUTHORITY_STATUSES:
            raise ValueError("Corpus manifest has no bounded authority status")
        source_status = record.get("source_status")
        provenance_status = record.get("provenance_status")
        if source_status not in SOURCE_STATUSES:
            raise ValueError("Corpus manifest has no bounded source status")
        if provenance_status not in PROVENANCE_STATUSES:
            raise ValueError("Corpus manifest has no bounded provenance status")
        if source_status == "present":
            if provenance_status != "source_present":
                raise ValueError("Present source has incomplete provenance")
            _verify_source_blob(root, revision, record, str(record.get("file")))
        file = record.get("file")
        if file is None:
            if (
                status != "held_duplicate"
                or source_status != "duplicate_record"
                or provenance_status != "duplicate_record"
                or record.get("authority_status") != "duplicate"
            ):
                raise ValueError("Only held duplicate records may omit a file")
            held.append({"record": dict(record), "path": None})
            continue
        file = _safe_relative(file, "corpus file")
        path = f"{snapshot_root}/{file}"
        if path in seen:
            raise ValueError(f"Corpus manifest repeats {path}")
        seen.add(path)
        if not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64:
            raise ValueError(f"Corpus manifest has no snapshot hash for {path}")
        data = _blob(root, revision, path)
        observed = hashlib.sha256(data).hexdigest()
        if observed != record["sha256"]:
            raise ValueError(f"Corpus snapshot hash mismatch: {path}")
        item = {"record": dict(record), "path": path, "bytes": data}
        if status == ELIGIBLE_STATUS:
            if source_status != "present" or provenance_status != "source_present":
                raise ValueError(f"Eligible corpus file has missing provenance: {path}")
            selected.append(item)
        else:
            held.append(item)

    if not selected:
        raise ValueError("Corpus manifest selects no eligible documents")
    exclusions = manifest.get("exclusions", [])
    if not isinstance(exclusions, list):
        raise ValueError("Corpus manifest exclusions are not a list")
    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "snapshot_root": snapshot_root,
        "selected": selected,
        "held": held,
        "exclusions": [dict(item) for item in exclusions if isinstance(item, dict)],
    }


def selected_documents(
    root: Path, revision: str | None = None, manifest_path: str = DEFAULT_MANIFEST
):
    selection = load_selection(root, revision, manifest_path)
    revision = revision or git_revision(Path(root))
    for item in selection["selected"]:
        try:
            text = item["bytes"].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"Corpus file is not UTF-8: {item['path']}") from error
        yield revision, item["path"], text, item["record"]


def selection_summary(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest_path": selection["manifest_path"],
        "manifest_sha256": selection["manifest_sha256"],
        "snapshot_root": selection["snapshot_root"],
        "selected_documents": len(selection["selected"]),
        "held_documents": sum(item["path"] is not None for item in selection["held"]),
        "held_duplicate_records": sum(
            item["path"] is None for item in selection["held"]
        ),
        "excluded_documents": len(selection["exclusions"]),
        "selected_paths": [item["path"] for item in selection["selected"]],
        "held_paths": [item["path"] for item in selection["held"] if item["path"]],
    }
