"""Deterministic status/ambiguity guards; no model or filesystem calls."""

import copy
import unittest

from pi.alexandria.graph.quality_gate import inspect_candidate


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
        candidate = {"claims": [claim("c1", "Owner selected Saturn.", "accepted")]}
        result = inspect_candidate(candidate)
        self.assertEqual(result["status"], "clear")
        self.assertEqual(result["blocking_findings"], 0)
        self.assertFalse(result["automatic_acceptance"])
        self.assertEqual(result["review_states"]["c1"]["review_state"], "not_checked")


if __name__ == "__main__":
    unittest.main()
