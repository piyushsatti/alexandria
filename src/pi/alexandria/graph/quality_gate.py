"""Deterministic review guards for status and ambiguity preservation.

This module never rewrites a claim or grants acceptance. It emits source-linked
findings and derived hold states so an unresolved candidate stays visibly held.
"""

from __future__ import annotations

import re
from collections import defaultdict
from hashlib import sha256
from typing import Any

VERSION = "2026.09.21"
REVIEW_STATES = {"not_checked", "held"}
AMBIGUITY_KINDS = {"ambiguity", "scope_tension"}
REVIEW_RULE = re.compile(
    r"\bheld\b|neither\s+accepted\s+knowledge|not\s+a\s+failure|"
    r"requires?\s+explicit\s+review|cannot\s+distinguish",
    re.IGNORECASE,
)


def _source(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": claim.get("path"),
        "quote": claim.get("quote"),
        "source_status": claim.get("source_status"),
    }


def inspect_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic findings without changing candidate data."""

    claims = candidate.get("claims")
    annotations = {
        row.get("id"): row
        for row in candidate.get("annotations", [])
        if isinstance(row, dict)
    }
    if not isinstance(claims, list):
        raise ValueError("Candidate claims must be a list")

    findings: list[dict[str, Any]] = []
    review_states: dict[str, dict[str, Any]] = {}
    by_quote: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("id"), str):
            findings.append(
                {
                    "id": "invalid-claim",
                    "kind": "invalid_claim",
                    "severity": "blocking",
                    "reason": "A claim has no bounded string identifier.",
                }
            )
            continue
        claim_id = claim["id"]
        state = claim.get("review_state", "not_checked")
        if state not in REVIEW_STATES:
            findings.append(
                {
                    "id": f"invalid-review-state-{claim_id}",
                    "kind": "invalid_review_state",
                    "severity": "blocking",
                    "claim_id": claim_id,
                    "review_state": state,
                    "source": _source(claim),
                    "reason": "Only not_checked and held are valid pre-owner-review states.",
                }
            )
            state = "held"
        claim_annotations = [
            annotations[annotation_id]
            for annotation_id in claim.get("annotation_ids", [])
            if annotation_id in annotations
        ]
        has_ambiguity = any(
            annotation.get("kind") in AMBIGUITY_KINDS
            for annotation in claim_annotations
        )
        is_review_rule = bool(REVIEW_RULE.search(str(claim.get("quote", ""))))
        derived_state = "held" if has_ambiguity or is_review_rule else state
        review_states[claim_id] = {
            "review_state": derived_state,
            "derived": derived_state != state,
            "source": _source(claim),
        }
        by_quote[(str(claim.get("path")), str(claim.get("quote")))].append(claim)

        if has_ambiguity and state != "held":
            findings.append(
                {
                    "id": f"ambiguity-held-{claim_id}",
                    "kind": "ambiguity_requires_hold",
                    "severity": "blocking",
                    "claim_id": claim_id,
                    "expected_review_state": "held",
                    "observed_review_state": state,
                    "source": _source(claim),
                    "reason": "An ambiguity or scope-tension annotation cannot be presented as an ordinary claim.",
                }
            )
        if is_review_rule and claim.get("source_status") == "accepted":
            findings.append(
                {
                    "id": f"review-rule-status-{claim_id}",
                    "kind": "review_rule_status_inflation",
                    "severity": "blocking",
                    "claim_id": claim_id,
                    "expected_status": "requirement-or-held",
                    "observed_status": "accepted",
                    "source": _source(claim),
                    "reason": "A sentence defining a held/review state is a review rule, not an accepted decision.",
                }
            )

    for (path, quote), grouped in by_quote.items():
        statuses = sorted({claim.get("source_status") for claim in grouped})
        if len(statuses) <= 1:
            continue
        findings.append(
            {
                "id": "status-conflict-"
                + sha256(f"{path}\0{quote}".encode()).hexdigest()[:16],
                "kind": "source_quote_status_conflict",
                "severity": "blocking",
                "claim_ids": [claim["id"] for claim in grouped],
                "observed_statuses": statuses,
                "source": {"path": path, "quote": quote},
                "reason": "The same source quote received multiple statuses; retain it for review instead of silently choosing one.",
            }
        )
        for claim in grouped:
            review_states.setdefault(claim["id"], {})["review_state"] = "held"

    return {
        "version": VERSION,
        "status": "held" if findings else "clear",
        "automatic_acceptance": False,
        "blocking_findings": len(findings),
        "findings": findings,
        "review_states": review_states,
        "review_required": "Derived holds are review signals, not owner approval; source evidence remains authoritative.",
    }
