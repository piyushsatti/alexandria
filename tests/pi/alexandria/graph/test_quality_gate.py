"""Deterministic status/ambiguity guards; no model or filesystem calls."""

import copy
import unittest

from pi.alexandria.graph.quality_gate import inspect_candidate, validate_dispositions


def claim(identifier, quote, status, annotations=(), review_state="not_checked"):
    return {
        "id": identifier,
        "subject": "candidate",
        "predicate": "has rule",
        "object": identifier,
        "path": "fixture.md",
        "quote": quote,
        "source_status": status,
        "review_state": review_state,
        "scope": {"text": "Synthetic fixture", "path": "fixture.md", "quote": quote},
        "annotation_ids": list(annotations),
    }


class QualityGateTests(unittest.TestCase):
    def test_ambiguity_and_status_conflict_remain_held(self):
        quote = "Ready has no defined criterion."
        rule = (
            "A candidate output is held when a reviewer cannot distinguish "
            "historical from current state."
        )
        candidate = {
            "claims": [
                claim("c1", quote, "accepted", ("a1",)),
                claim("c2", quote, "open", ("a1",)),
                claim("c3", rule, "accepted"),
                claim("c4", rule, "requirement"),
            ],
            "annotations": [
                {
                    "id": "a1",
                    "kind": "ambiguity",
                    "path": "fixture.md",
                    "quote": quote,
                    "reason": "Criterion is missing",
                    "resolution_question": "What is ready?",
                }
            ],
        }
        original = copy.deepcopy(candidate)
        result = inspect_candidate(candidate)
        kinds = {finding["kind"] for finding in result["findings"]}
        self.assertEqual(result["status"], "held")
        self.assertIn("ambiguity_requires_hold", kinds)
        self.assertIn("review_rule_status_inflation", kinds)
        self.assertIn("source_quote_status_conflict", kinds)
        self.assertEqual(result["review_states"]["c1"]["review_state"], "held")
        self.assertEqual(result["review_states"]["c3"]["review_state"], "held")
        self.assertEqual(candidate, original)
        self.assertEqual(result, inspect_candidate(candidate))

    def test_clear_candidate_is_not_accepted_automatically(self):
        candidate = {"claims": [claim("c1", "Saturn has a blue label.", "accepted")]}
        result = inspect_candidate(candidate)
        self.assertEqual(result["status"], "clear")
        self.assertEqual(result["blocking_findings"], 0)
        self.assertFalse(result["automatic_acceptance"])
        self.assertEqual(result["review_states"]["c1"]["review_state"], "not_checked")

    def test_semantic_qualifications_require_a_hold(self):
        candidate = {
            "claims": [
                claim(
                    "c1",
                    "The owner may approve this only if the 2026-09-22 review is complete.",
                    "accepted",
                )
            ]
        }
        result = inspect_candidate(candidate)
        self.assertEqual(result["status"], "held")
        self.assertIn(
            "semantic_qualification_requires_review",
            {finding["kind"] for finding in result["findings"]},
        )
        self.assertEqual(result["review_states"]["c1"]["review_state"], "held")
        self.assertEqual(
            result["review_states"]["c1"]["semantic_risk_flags"],
            ["condition", "time", "authority", "uncertainty"],
        )

    def test_invalid_source_status_is_blocking(self):
        candidate = {"claims": [claim("c1", "A plain fact.", "unknown")]}
        result = inspect_candidate(candidate)
        self.assertEqual(result["status"], "held")
        self.assertIn(
            "invalid_source_status",
            {finding["kind"] for finding in result["findings"]},
        )

    def test_owner_review_requires_every_finding_and_source_binding(self):
        gate = {
            "findings": [
                {
                    "id": "risk-1",
                    "source": {"path": "fixture.md", "quote": "A claim."},
                },
                {
                    "id": "risk-2",
                    "source": {"path": "fixture.md", "quote": "Another claim."},
                },
            ]
        }
        incomplete = validate_dispositions(
            gate,
            {
                "review_version": "2026.09.22",
                "reviewer": "owner",
                "reviewed_at": "2026-09-22",
                "decisions": [
                    {
                        "finding_id": "risk-1",
                        "disposition": "held",
                        "reason": "Meaning still needs confirmation.",
                        "source": gate["findings"][0]["source"],
                    }
                ],
            },
        )
        self.assertEqual(incomplete["status"], "hold")
        self.assertFalse(incomplete["complete"])
        self.assertIn(
            "finding_without_disposition",
            {finding["kind"] for finding in incomplete["findings"]},
        )

    def test_owner_review_can_complete_without_auto_acceptance(self):
        gate = {
            "findings": [
                {
                    "id": "risk-1",
                    "source": {"path": "fixture.md", "quote": "A claim."},
                },
                {
                    "id": "risk-2",
                    "source": {"path": "fixture.md", "quote": "Another claim."},
                },
            ]
        }
        result = validate_dispositions(
            gate,
            {
                "review_version": "2026.09.22",
                "reviewer": "owner",
                "reviewed_at": "2026-09-22",
                "decisions": [
                    {
                        "finding_id": "risk-1",
                        "disposition": "accepted",
                        "reason": "The source explicitly supports the claim.",
                        "source": gate["findings"][0]["source"],
                    },
                    {
                        "finding_id": "risk-2",
                        "disposition": "corrected",
                        "reason": "The original wording overstates the source.",
                        "correction": "Use the bounded source wording.",
                        "source": gate["findings"][1]["source"],
                    },
                ],
            },
        )
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["complete"])
        self.assertFalse(result["automatic_acceptance"])

    def test_owner_review_rejects_source_rebinding_and_missing_correction(self):
        source = {"path": "fixture.md", "quote": "A claim."}
        result = validate_dispositions(
            {"findings": [{"id": "risk-1", "source": source}]},
            {
                "review_version": "2026.09.22",
                "reviewer": "owner",
                "reviewed_at": "2026-09-22",
                "decisions": [
                    {
                        "finding_id": "risk-1",
                        "disposition": "corrected",
                        "reason": "Needs correction.",
                        "source": {"path": "other.md", "quote": "A claim."},
                    }
                ],
            },
        )
        kinds = {finding["kind"] for finding in result["findings"]}
        self.assertIn("disposition_source_mismatch", kinds)
        self.assertIn("correction_missing", kinds)


if __name__ == "__main__":
    unittest.main()
