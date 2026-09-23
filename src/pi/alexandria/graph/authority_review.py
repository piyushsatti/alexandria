"""Validate explicit authority and rendering decisions for held corpus records."""

from __future__ import annotations

from typing import Any

VERSION = "2026.09.22"
HELD_STATUSES = {"held_authority", "held_duplicate"}
DECISION_TYPES = {"accepted", "held", "excluded"}


def _expected_records(
    manifest: dict[str, Any],
) -> dict[tuple[str, int], dict[str, Any]]:
    expected: dict[tuple[str, int], dict[str, Any]] = {}
    for collection in ("records", "final_context_additions"):
        records = manifest.get(collection, [])
        if not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            if record.get("release_status") in HELD_STATUSES:
                expected[(collection, index)] = record
    return expected


def _finding(kind: str, reason: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, "reason": reason, **fields}


def validate_authority_review(
    manifest: dict[str, Any], review: dict[str, Any]
) -> dict[str, Any]:
    """Validate a review without changing the manifest or review receipt."""

    result: dict[str, Any] = {
        "version": VERSION,
        "status": "hold",
        "complete": False,
        "release_ready": False,
        "automatic_acceptance": False,
        "findings": [],
    }
    if not isinstance(manifest, dict):
        result["findings"].append(
            _finding("manifest_not_object", "Corpus manifest must be an object.")
        )
        result["finding_count"] = 1
        return result
    if not isinstance(review, dict):
        result["findings"].append(
            _finding("review_not_object", "Authority review must be an object.")
        )
        result["finding_count"] = 1
        return result

    for field in ("review_version", "reviewer", "reviewed_at"):
        if not isinstance(review.get(field), str) or not review[field].strip():
            result["findings"].append(
                _finding(
                    "review_metadata_missing",
                    "Authority review metadata must be explicit.",
                    field=field,
                )
            )
    if review.get("automatic_acceptance") is not False:
        result["findings"].append(
            _finding(
                "automatic_acceptance_invalid",
                "Authority decisions must never claim automatic acceptance.",
            )
        )

    expected = _expected_records(manifest)
    decisions = review.get("decisions")
    if not isinstance(decisions, list):
        result["findings"].append(
            _finding(
                "review_decisions_missing",
                "Authority review decisions must be a list.",
            )
        )
        decisions = []

    observed: dict[tuple[str, int], dict[str, Any]] = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            result["findings"].append(
                _finding(
                    "decision_invalid",
                    "Every authority decision must be an object.",
                )
            )
            continue
        collection = decision.get("collection")
        index = decision.get("record_index")
        key = (
            (collection, index)
            if isinstance(collection, str) and isinstance(index, int)
            else None
        )
        if key is None:
            result["findings"].append(
                _finding(
                    "decision_identity_missing",
                    "Every decision needs a collection and integer record_index.",
                )
            )
            continue
        if key not in expected:
            result["findings"].append(
                _finding(
                    "decision_unknown_record",
                    "A decision may only address a held manifest record.",
                    collection=collection,
                    record_index=index,
                )
            )
            continue
        if key in observed:
            result["findings"].append(
                _finding(
                    "decision_duplicate",
                    "Each held manifest record must have exactly one decision.",
                    collection=collection,
                    record_index=index,
                )
            )
            continue
        observed[key] = decision
        record = expected[key]

        for field in ("file", "source", "source_sha256", "release_status"):
            if decision.get(field) != record.get(field):
                result["findings"].append(
                    _finding(
                        "decision_source_mismatch",
                        "A review decision must retain the manifest's source binding.",
                        collection=collection,
                        record_index=index,
                        field=field,
                    )
                )
        choice = decision.get("decision")
        if choice not in DECISION_TYPES:
            result["findings"].append(
                _finding(
                    "decision_type_invalid",
                    "Decision must be accepted, held, or excluded.",
                    collection=collection,
                    record_index=index,
                    decision=choice,
                )
            )
            continue
        reason = decision.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            result["findings"].append(
                _finding(
                    "decision_reason_missing",
                    "Every authority decision needs an owner reason.",
                    collection=collection,
                    record_index=index,
                )
            )
        if choice == "accepted":
            if record.get("release_status") == "held_duplicate":
                result["findings"].append(
                    _finding(
                        "duplicate_cannot_be_accepted",
                        "A held duplicate cannot enter the committed release.",
                        collection=collection,
                        record_index=index,
                    )
                )
            if (
                not isinstance(decision.get("canonical_variant"), str)
                or not decision["canonical_variant"].strip()
            ):
                result["findings"].append(
                    _finding(
                        "canonical_variant_missing",
                        "An accepted record needs an explicit canonical source or variant.",
                        collection=collection,
                        record_index=index,
                    )
                )
            evidence = decision.get("rendering_equivalence_evidence")
            if not isinstance(evidence, list) or not evidence:
                result["findings"].append(
                    _finding(
                        "rendering_evidence_missing",
                        "An accepted record needs rendering-equivalence evidence.",
                        collection=collection,
                        record_index=index,
                    )
                )

    missing = sorted(set(expected) - set(observed))
    for collection, index in missing:
        result["findings"].append(
            _finding(
                "record_without_decision",
                "Every held manifest record needs one explicit authority decision.",
                collection=collection,
                record_index=index,
            )
        )

    result["expected_records"] = len(expected)
    result["observed_records"] = len(observed)
    result["finding_count"] = len(result["findings"])
    result["complete"] = not result["findings"] and len(observed) == len(expected)
    result["release_ready"] = result["complete"] and all(
        decision.get("decision") == "accepted" for decision in observed.values()
    )
    if result["release_ready"]:
        result["status"] = "complete"
    return result
