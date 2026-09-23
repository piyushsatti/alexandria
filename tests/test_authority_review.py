import unittest

from pi.alexandria.graph.authority_review import validate_authority_review


def manifest(records=None, additions=None):
    return {
        "records": records or [],
        "final_context_additions": additions or [],
    }


def held_record(status="held_authority", authority="unresolved"):
    return {
        "file": "docs/held.md",
        "source": "sources/held.md",
        "source_sha256": "a" * 64,
        "release_status": status,
        "authority_status": authority,
    }


def review(decisions, **extra):
    return {
        "review_version": "2026.09.22",
        "reviewer": "owner",
        "reviewed_at": "2026-09-22",
        "automatic_acceptance": False,
        "decisions": decisions,
        **extra,
    }


class AuthorityReviewTests(unittest.TestCase):
    def test_blank_review_is_held(self):
        result = validate_authority_review(
            manifest([held_record()]),
            {
                "review_version": "2026.09.22",
                "reviewer": "",
                "reviewed_at": "",
                "automatic_acceptance": False,
                "decisions": [],
            },
        )
        self.assertFalse(result["complete"])
        self.assertFalse(result["release_ready"])
        self.assertIn(
            "record_without_decision", {x["kind"] for x in result["findings"]}
        )

    def test_held_decision_is_complete_but_not_release_ready(self):
        record = held_record()
        result = validate_authority_review(
            manifest([record]),
            review(
                [
                    {
                        "collection": "records",
                        "record_index": 0,
                        **record,
                        "decision": "held",
                        "canonical_variant": "",
                        "rendering_equivalence_evidence": [],
                        "reason": "Authority remains unresolved.",
                    }
                ]
            ),
        )
        self.assertTrue(result["complete"])
        self.assertFalse(result["release_ready"])
        self.assertEqual(result["status"], "hold")

    def test_accepted_record_requires_rendering_evidence(self):
        record = held_record()
        result = validate_authority_review(
            manifest([record]),
            review(
                [
                    {
                        "collection": "records",
                        "record_index": 0,
                        **record,
                        "decision": "accepted",
                        "canonical_variant": "sources/held.md",
                        "rendering_equivalence_evidence": [],
                        "reason": "Owner selected this source.",
                    }
                ]
            ),
        )
        self.assertIn(
            "rendering_evidence_missing", {x["kind"] for x in result["findings"]}
        )
        self.assertFalse(result["release_ready"])

    def test_accepted_record_can_release_with_evidence(self):
        record = held_record()
        result = validate_authority_review(
            manifest([record]),
            review(
                [
                    {
                        "collection": "records",
                        "record_index": 0,
                        **record,
                        "decision": "accepted",
                        "canonical_variant": "sources/held.md",
                        "rendering_equivalence_evidence": ["receipt.json"],
                        "reason": "Owner confirmed the source and rendering.",
                    }
                ]
            ),
        )
        self.assertTrue(result["complete"])
        self.assertTrue(result["release_ready"])
        self.assertEqual(result["status"], "complete")

    def test_duplicate_cannot_be_accepted(self):
        record = held_record("held_duplicate", "duplicate")
        result = validate_authority_review(
            manifest([record]),
            review(
                [
                    {
                        "collection": "records",
                        "record_index": 0,
                        **record,
                        "decision": "accepted",
                        "canonical_variant": "sources/held.md",
                        "rendering_equivalence_evidence": ["receipt.json"],
                        "reason": "Owner reviewed this record.",
                    }
                ]
            ),
        )
        self.assertIn(
            "duplicate_cannot_be_accepted", {x["kind"] for x in result["findings"]}
        )

    def test_final_context_additions_are_included(self):
        record = held_record()
        result = validate_authority_review(
            manifest(additions=[record]),
            review(
                [
                    {
                        "collection": "final_context_additions",
                        "record_index": 0,
                        **record,
                        "decision": "held",
                        "canonical_variant": "",
                        "rendering_equivalence_evidence": [],
                        "reason": "Supporting context needs owner review.",
                    }
                ]
            ),
        )
        self.assertEqual(result["expected_records"], 1)
        self.assertTrue(result["complete"])


if __name__ == "__main__":
    unittest.main()
