import copy
import unittest

from pi.alexandria.graph.provenance_review import validate_provenance_review


def item(index=0, decision="provenance-only", reason="Source provenance only."):
    return {
        "file": "records/receipt.json",
        "pointer": f"/{index}/source",
        "key": "source",
        "classification": "local_outside_checkout",
        "value_sha256": "a" * 64,
        "value_length": 12,
        "proposed_category": "provenance-only-candidate",
        "reason": "scanner proposal",
        "owner_decision": decision,
        "owner_reason": reason,
    }


def review(items):
    return {
        "review_version": "2026.09.22",
        "reviewer": "owner",
        "reviewed_at": "2026-09-22",
        "automatic_rewrite": False,
        "raw_values_stored": False,
        "raw_scanner_report_retained": False,
        "classification_items_are_the_replayable_sample": True,
        "owner_decision_required": True,
        "scope": {"findings_included": len(items)},
        "items": items,
    }


class ProvenanceReviewTests(unittest.TestCase):
    def test_blank_review_is_held(self):
        result = validate_provenance_review(
            {
                "review_version": "2026.09.22",
                "automatic_rewrite": False,
                "raw_values_stored": False,
                "raw_scanner_report_retained": False,
                "classification_items_are_the_replayable_sample": True,
                "owner_decision_required": True,
                "scope": {"findings_included": 1},
                "items": [
                    {
                        **item(),
                        "owner_decision": None,
                        "owner_reason": "",
                    }
                ],
            }
        )
        self.assertFalse(result["complete"])
        self.assertFalse(result["release_ready"])
        kinds = {finding["kind"] for finding in result["findings"]}
        self.assertIn("review_metadata_missing", kinds)
        self.assertIn("owner_decision_invalid", kinds)
        self.assertIn("owner_reason_missing", kinds)

    def test_safe_classification_can_complete(self):
        result = validate_provenance_review(review([item()]))
        self.assertTrue(result["complete"])
        self.assertTrue(result["release_ready"])
        self.assertEqual(result["status"], "complete")
        self.assertFalse(result["automatic_acceptance"])

    def test_external_edge_is_complete_but_blocks_release(self):
        result = validate_provenance_review(
            review([item(decision="external-collection", reason="Owner permits it.")])
        )
        self.assertTrue(result["complete"])
        self.assertFalse(result["release_ready"])
        self.assertEqual(result["status"], "hold")
        self.assertEqual(
            result["release_blockers"][0]["owner_decision"], "external-collection"
        )

    def test_raw_value_and_identity_changes_are_rejected(self):
        candidate = item()
        candidate["value"] = "/Users/example/private.md"
        candidate["value_sha256"] = "not-a-hash"
        result = validate_provenance_review(review([candidate]))
        kinds = {finding["kind"] for finding in result["findings"]}
        self.assertIn("raw_value_present", kinds)
        self.assertIn("value_hash_invalid", kinds)

    def test_duplicate_identity_is_rejected_without_mutation(self):
        items = [item(0), item(0)]
        before = copy.deepcopy(items)
        result = validate_provenance_review(review(items))
        self.assertIn(
            "item_duplicate", {finding["kind"] for finding in result["findings"]}
        )
        self.assertEqual(items, before)


if __name__ == "__main__":
    unittest.main()
