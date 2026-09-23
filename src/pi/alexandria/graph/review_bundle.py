"""Combine Alexandria's independent owner-review gates safely."""

from __future__ import annotations

from typing import Any

from pi.alexandria.graph.authority_review import validate_authority_review
from pi.alexandria.graph.provenance_review import validate_provenance_review
from pi.alexandria.graph.quality_gate import validate_dispositions

VERSION = "2026.09.22"


def validate_review_bundle(
    *,
    quality_gate: dict[str, Any],
    quality_review: dict[str, Any],
    manifest: dict[str, Any],
    authority_review: dict[str, Any],
    provenance_review: dict[str, Any],
) -> dict[str, Any]:
    """Validate all owner-review surfaces without changing any input."""

    semantic = validate_dispositions(quality_gate, quality_review)
    authority = validate_authority_review(manifest, authority_review)
    provenance = validate_provenance_review(provenance_review)
    gates = {
        "semantic": semantic,
        "authority": authority,
        "provenance": provenance,
    }
    complete = all(gate.get("complete") is True for gate in gates.values())
    release_ready = complete and all(
        gate.get("status") == "complete" and gate.get("release_ready", True)
        for gate in gates.values()
    )
    return {
        "version": VERSION,
        "status": "complete" if release_ready else "hold",
        "complete": complete,
        "release_ready": release_ready,
        "automatic_acceptance": False,
        "gates": gates,
    }


def summarize_review_bundle(result: dict[str, Any]) -> dict[str, Any]:
    """Return a log-safe summary without source quotes or raw values."""

    gates = result.get("gates", {})
    summary: dict[str, Any] = {
        key: {
            field: gate.get(field)
            for field in (
                "status",
                "complete",
                "release_ready",
                "finding_count",
                "expected_records",
                "observed_records",
                "expected_items",
                "observed_items",
            )
            if field in gate
        }
        for key, gate in gates.items()
        if isinstance(gate, dict)
    }
    return {
        "version": result.get("version"),
        "status": result.get("status"),
        "complete": result.get("complete"),
        "release_ready": result.get("release_ready"),
        "automatic_acceptance": result.get("automatic_acceptance"),
        "gates": summary,
    }
