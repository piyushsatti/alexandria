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
SOURCE_STATUSES = {
    "accepted",
    "proposed",
    "open",
    "requirement",
    "limitation",
    "historical",
    "deferred",
}
AMBIGUITY_KINDS = {"ambiguity", "scope_tension"}
DISPOSITION_TYPES = {"accepted", "corrected", "held"}
REVIEW_RULE = re.compile(
    r"\bheld\b|neither\s+accepted\s+knowledge|not\s+a\s+failure|"
    r"requires?\s+explicit\s+review|cannot\s+distinguish",
    re.IGNORECASE,
)
SEMANTIC_RISK_RULES = {
    "negation": re.compile(
        r"\b(?:not|never|no|without|cannot|can't|won't|doesn't|isn't|aren't)\b",
        re.IGNORECASE,
    ),
    "condition": re.compile(
        r"\b(?:if|unless|when|provided|only if|except|requires?|must|should)\b",
        re.IGNORECASE,
    ),
    "time": re.compile(
        r"\b(?:current|now|historical|proposed|deferred|pending|open|superseded)\b"
        r"|\b20\d{2}[.-]\d{1,2}[.-]\d{1,2}\b",
        re.IGNORECASE,
    ),
    "authority": re.compile(
        r"\b(?:owner|approved?|approval|authorized?|decision|selected|canonical)\b",
        re.IGNORECASE,
    ),
    "uncertainty": re.compile(
        r"\b(?:may|might|could|possibly|uncertain|ambiguous|unknown|likely|probably)\b",
        re.IGNORECASE,
    ),
}


def _source(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": claim.get("path"),
        "quote": claim.get("quote"),
        "source_status": claim.get("source_status"),
    }


def _semantic_risk_flags(quote: str) -> list[str]:
    return [
        name for name, pattern in SEMANTIC_RISK_RULES.items() if pattern.search(quote)
    ]


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
        source_status = claim.get("source_status")
        if source_status not in SOURCE_STATUSES:
            findings.append(
                {
                    "id": f"invalid-source-status-{claim_id}",
                    "kind": "invalid_source_status",
                    "severity": "blocking",
                    "claim_id": claim_id,
                    "source_status": source_status,
                    "source": _source(claim),
                    "reason": "A claim must use the bounded Alexandria source-status vocabulary.",
                }
            )
            source_status = "open"
        claim_annotations = [
            annotations[annotation_id]
            for annotation_id in claim.get("annotation_ids", [])
            if annotation_id in annotations
        ]
        has_ambiguity = any(
            annotation.get("kind") in AMBIGUITY_KINDS
            for annotation in claim_annotations
        )
        quote = str(claim.get("quote", ""))
        is_review_rule = bool(REVIEW_RULE.search(quote))
        semantic_flags = _semantic_risk_flags(quote)
        semantic_hold = bool(semantic_flags)
        derived_state = (
            "held" if has_ambiguity or is_review_rule or semantic_hold else state
        )
        review_states[claim_id] = {
            "review_state": derived_state,
            "derived": derived_state != state,
            "semantic_risk_flags": semantic_flags,
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
        if semantic_hold:
            findings.append(
                {
                    "id": f"semantic-risk-{claim_id}",
                    "kind": "semantic_qualification_requires_review",
                    "severity": "blocking",
                    "claim_id": claim_id,
                    "flags": semantic_flags,
                    "annotation_ids": list(claim.get("annotation_ids", [])),
                    "source": _source(claim),
                    "reason": "Negation, conditions, time, authority, or uncertainty require explicit source-linked review before promotion.",
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


def validate_dispositions(
    gate: dict[str, Any], review: dict[str, Any]
) -> dict[str, Any]:
    """Validate an owner review without changing the candidate or gate.

    A disposition is an auditable decision about one deterministic finding. It
    cannot make the candidate accepted automatically; it only proves that every
    finding was explicitly considered and records which ones remain held.
    """

    result: dict[str, Any] = {
        "status": "hold",
        "complete": False,
        "automatic_acceptance": False,
        "findings": [],
    }
    if not isinstance(gate, dict):
        result["findings"].append(
            {"kind": "gate_not_object", "reason": "Quality gate must be an object."}
        )
        result["finding_count"] = 1
        return result
    if not isinstance(review, dict):
        result["findings"].append(
            {
                "kind": "review_not_object",
                "reason": "Owner review must be an object.",
            }
        )
        result["finding_count"] = 1
        return result

    for field in ("review_version", "reviewer", "reviewed_at"):
        if not isinstance(review.get(field), str) or not review[field].strip():
            result["findings"].append(
                {
                    "kind": "review_metadata_missing",
                    "field": field,
                    "reason": "Owner review metadata must be explicit.",
                }
            )

    gate_findings = gate.get("findings")
    if not isinstance(gate_findings, list):
        result["findings"].append(
            {
                "kind": "gate_findings_missing",
                "reason": "Quality gate findings must be a list.",
            }
        )
        gate_findings = []
    expected: dict[str, dict[str, Any]] = {}
    for finding in gate_findings:
        if not isinstance(finding, dict) or not isinstance(finding.get("id"), str):
            result["findings"].append(
                {
                    "kind": "gate_finding_invalid",
                    "reason": "Every quality finding needs a string id.",
                }
            )
            continue
        finding_id = finding["id"]
        if finding_id in expected:
            result["findings"].append(
                {
                    "kind": "gate_finding_duplicate",
                    "finding_id": finding_id,
                    "reason": "Finding ids must be unique before review.",
                }
            )
        expected[finding_id] = finding

    decisions = review.get("decisions")
    if not isinstance(decisions, list):
        result["findings"].append(
            {
                "kind": "review_decisions_missing",
                "reason": "Owner review decisions must be a list.",
            }
        )
        decisions = []

    observed: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        if not isinstance(decision, dict) or not isinstance(
            decision.get("finding_id"), str
        ):
            result["findings"].append(
                {
                    "kind": "decision_invalid",
                    "reason": "Every disposition needs a string finding_id.",
                }
            )
            continue
        finding_id = decision["finding_id"]
        if finding_id not in expected:
            result["findings"].append(
                {
                    "kind": "decision_unknown_finding",
                    "finding_id": finding_id,
                    "reason": "A disposition may only address a finding emitted by the gate.",
                }
            )
            continue
        if finding_id in observed:
            result["findings"].append(
                {
                    "kind": "decision_duplicate",
                    "finding_id": finding_id,
                    "reason": "Each finding must have exactly one disposition.",
                }
            )
            continue
        observed[finding_id] = decision
        disposition = decision.get("disposition")
        if disposition not in DISPOSITION_TYPES:
            result["findings"].append(
                {
                    "kind": "disposition_invalid",
                    "finding_id": finding_id,
                    "disposition": disposition,
                    "reason": "Disposition must be accepted, corrected, or held.",
                }
            )
        reason = decision.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            result["findings"].append(
                {
                    "kind": "disposition_reason_missing",
                    "finding_id": finding_id,
                    "reason": "Every disposition needs a bounded owner reason.",
                }
            )
        source = decision.get("source")
        expected_source = expected[finding_id].get("source")
        if not isinstance(source, dict) or not isinstance(expected_source, dict):
            result["findings"].append(
                {
                    "kind": "disposition_source_missing",
                    "finding_id": finding_id,
                    "reason": "Every disposition must retain the finding's source binding.",
                }
            )
        elif any(
            source.get(key) != expected_source.get(key) for key in ("path", "quote")
        ):
            result["findings"].append(
                {
                    "kind": "disposition_source_mismatch",
                    "finding_id": finding_id,
                    "reason": "A disposition cannot move a finding to a different source quote.",
                }
            )
        if disposition == "corrected":
            correction = decision.get("correction")
            if not isinstance(correction, str) or not correction.strip():
                result["findings"].append(
                    {
                        "kind": "correction_missing",
                        "finding_id": finding_id,
                        "reason": "A corrected finding needs the bounded replacement or correction text.",
                    }
                )

    missing = sorted(set(expected) - set(observed))
    if missing:
        result["findings"].append(
            {
                "kind": "finding_without_disposition",
                "finding_ids": missing,
                "reason": "Every emitted finding must be explicitly considered.",
            }
        )
    held = sorted(
        finding_id
        for finding_id, decision in observed.items()
        if decision.get("disposition") == "held"
    )
    if not result["findings"]:
        result["complete"] = True
        result["status"] = "hold" if held else "complete"
        result["held_finding_ids"] = held
    result["finding_count"] = len(result["findings"])
    return result
