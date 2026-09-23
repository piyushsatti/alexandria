"""Validate privacy-safe owner classifications for structured provenance."""

from __future__ import annotations

import re
from typing import Any

VERSION = "2026.09.22"
DECISION_TYPES = {
    "provenance-only",
    "historical-run-locator",
    "external-collection",
    "global-task-edge",
    "imported-source-provenance",
    "held",
}
SAFE_DECISIONS = {
    "provenance-only",
    "historical-run-locator",
    "imported-source-provenance",
}
PROPOSED_CATEGORIES = {
    "provenance-only-candidate",
    "historical-run-locator-candidate",
    "external-collection-candidate",
    "global-task-edge-candidate",
    "imported-source-provenance-candidate",
}
CLASSIFICATIONS = {"local_outside_checkout", "relative_outside_checkout"}
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_ITEM_KEYS = {
    "value",
    "raw_value",
    "raw_path",
    "resolved_path",
}


def _finding(kind: str, reason: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "reason": reason, **fields}


def validate_provenance_review(review: dict[str, Any]) -> dict[str, Any]:
    """Validate owner decisions without exposing or rewriting path values.

    A complete review proves that every privacy-safe finding was considered.
    It does not make a release eligible when the owner leaves an external or
    global edge, or an unresolved finding, in the decision set.
    """

    result: dict[str, Any] = {
        "version": VERSION,
        "status": "hold",
        "complete": False,
        "release_ready": False,
        "automatic_acceptance": False,
        "findings": [],
        "release_blockers": [],
    }
    if not isinstance(review, dict):
        result["findings"].append(
            _finding("review_not_object", "Provenance review must be an object.")
        )
        result["finding_count"] = 1
        return result

    for field in ("review_version", "reviewer", "reviewed_at"):
        if not isinstance(review.get(field), str) or not review[field].strip():
            result["findings"].append(
                _finding(
                    "review_metadata_missing",
                    "Provenance review metadata must be explicit.",
                    field=field,
                )
            )
    if review.get("automatic_rewrite") is not False:
        result["findings"].append(
            _finding(
                "automatic_rewrite_invalid",
                "Classification must never authorize automatic record rewriting.",
            )
        )
    for field in ("raw_values_stored", "raw_scanner_report_retained"):
        if review.get(field) is not False:
            result["findings"].append(
                _finding(
                    "privacy_boundary_invalid",
                    "The review must retain hashes and pointers, not raw outside values.",
                    field=field,
                )
            )
    if review.get("owner_decision_required") is not True:
        result["findings"].append(
            _finding(
                "owner_decision_contract_invalid",
                "Every provenance item requires an explicit owner decision.",
            )
        )
    if review.get("classification_items_are_the_replayable_sample") is not True:
        result["findings"].append(
            _finding(
                "sample_contract_invalid",
                "The reviewed items must be the bounded replayable scanner sample.",
            )
        )

    items = review.get("items")
    if not isinstance(items, list):
        result["findings"].append(
            _finding("items_missing", "Provenance review items must be a list.")
        )
        items = []
    scope = review.get("scope")
    expected_count = scope.get("findings_included") if isinstance(scope, dict) else None
    if isinstance(expected_count, int) and expected_count != len(items):
        result["findings"].append(
            _finding(
                "item_count_mismatch",
                "The review must cover the complete bounded scanner sample.",
                expected=expected_count,
                observed=len(items),
            )
        )

    identities: set[tuple[str, str, str, str]] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            result["findings"].append(
                _finding(
                    "item_invalid",
                    "Every provenance review item must be an object.",
                    item_index=index,
                )
            )
            continue
        forbidden = sorted(FORBIDDEN_ITEM_KEYS.intersection(item))
        if forbidden:
            result["findings"].append(
                _finding(
                    "raw_value_present",
                    "Review items may not store raw or resolved outside paths.",
                    item_index=index,
                    fields=forbidden,
                )
            )
        required = (
            "file",
            "pointer",
            "key",
            "classification",
            "value_sha256",
            "value_length",
            "proposed_category",
            "owner_decision",
            "owner_reason",
        )
        missing = [field for field in required if field not in item]
        if missing:
            result["findings"].append(
                _finding(
                    "item_field_missing",
                    "Every item needs immutable identity fields and an owner reason.",
                    item_index=index,
                    fields=missing,
                )
            )
            continue
        file = item["file"]
        pointer = item["pointer"]
        key = item["key"]
        value_hash = item["value_sha256"]
        identity = (str(file), str(pointer), str(key), str(value_hash))
        if identity in identities:
            result["findings"].append(
                _finding(
                    "item_duplicate",
                    "Each bounded scanner finding must have one decision.",
                    item_index=index,
                    file=file,
                    pointer=pointer,
                )
            )
        identities.add(identity)
        if not isinstance(file, str) or not file or file.startswith("/"):
            result["findings"].append(
                _finding(
                    "item_file_invalid",
                    "Finding files must be non-empty repository-relative paths.",
                    item_index=index,
                )
            )
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            result["findings"].append(
                _finding(
                    "item_pointer_invalid",
                    "Finding pointers must be absolute JSON pointers.",
                    item_index=index,
                )
            )
        if not isinstance(key, str) or not key:
            result["findings"].append(
                _finding(
                    "item_key_invalid",
                    "Finding keys must be non-empty strings.",
                    item_index=index,
                )
            )
        if item["classification"] not in CLASSIFICATIONS:
            result["findings"].append(
                _finding(
                    "classification_invalid",
                    "Finding classification is outside the scanner vocabulary.",
                    item_index=index,
                    classification=item["classification"],
                )
            )
        if not isinstance(value_hash, str) or HEX_SHA256.fullmatch(value_hash) is None:
            result["findings"].append(
                _finding(
                    "value_hash_invalid",
                    "Finding identity must use a SHA-256 hash.",
                    item_index=index,
                )
            )
        if not isinstance(item["value_length"], int) or item["value_length"] < 0:
            result["findings"].append(
                _finding(
                    "value_length_invalid",
                    "Finding value_length must be a non-negative integer.",
                    item_index=index,
                )
            )
        if item["proposed_category"] not in PROPOSED_CATEGORIES:
            result["findings"].append(
                _finding(
                    "proposed_category_invalid",
                    "Finding proposed_category is outside the bounded vocabulary.",
                    item_index=index,
                    proposed_category=item["proposed_category"],
                )
            )
        decision = item["owner_decision"]
        if decision not in DECISION_TYPES:
            result["findings"].append(
                _finding(
                    "owner_decision_invalid",
                    "Owner decision is outside the bounded classification vocabulary.",
                    item_index=index,
                    owner_decision=decision,
                )
            )
        reason = item["owner_reason"]
        if not isinstance(reason, str) or not reason.strip():
            result["findings"].append(
                _finding(
                    "owner_reason_missing",
                    "Every owner classification needs a non-empty reason.",
                    item_index=index,
                )
            )
        if decision not in SAFE_DECISIONS:
            result["release_blockers"].append(
                {
                    "item_index": index,
                    "file": file,
                    "pointer": pointer,
                    "owner_decision": decision,
                }
            )

    result["expected_items"] = (
        expected_count if isinstance(expected_count, int) else None
    )
    result["observed_items"] = len(items)
    result["decision_counts"] = {
        decision: sum(
            item.get("owner_decision") == decision
            for item in items
            if isinstance(item, dict)
        )
        for decision in sorted(DECISION_TYPES)
    }
    result["finding_count"] = len(result["findings"])
    result["complete"] = not result["findings"] and bool(items)
    result["release_ready"] = result["complete"] and not result["release_blockers"]
    if result["release_ready"]:
        result["status"] = "complete"
    return result
