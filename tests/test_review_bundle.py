import copy
import unittest

from pi.alexandria.graph.review_bundle import (
    summarize_review_bundle,
    validate_review_bundle,
)


def quality_gate_and_review(disposition="accepted"):
    gate = {
        "findings": [
            {
                "id": "risk-1",
                "source": {"path": "fixture.md", "quote": "A claim."},
            }
        ]
    }
    review = {
        "review_version": "2026.09.22",
        "reviewer": "owner",
        "reviewed_at": "2026-09-22",
        "decisions": [
            {
                "finding_id": "risk-1",
                "disposition": disposition,
                "reason": "Owner reviewed the bounded source.",
                "source": gate["findings"][0]["source"],
                **(
                    {"correction": "Use the bounded source wording."}
                    if disposition == "corrected"
                    else {}
                ),
            }
        ],
    }
    return gate, review


def authority_manifest_and_review(decision="accepted"):
    record = {
        "file": "docs/held.md",
        "source": "sources/held.md",
        "source_sha256": "a" * 64,
        "release_status": "held_authority",
        "authority_status": "unresolved",
    }
    manifest = {"records": [record], "final_context_additions": []}
    review = {
        "review_version": "2026.09.22",
        "reviewer": "owner",
        "reviewed_at": "2026-09-22",
        "automatic_acceptance": False,
        "decisions": [
            {
                "collection": "records",
                "record_index": 0,
                **record,
                "decision": decision,
                "canonical_variant": "sources/held.md"
                if decision == "accepted"
                else "",
                "rendering_equivalence_evidence": ["receipt.json"]
                if decision == "accepted"
                else [],
                "reason": "Owner reviewed the source.",
            }
        ],
    }
    return manifest, review


def provenance_review(decision="provenance-only"):
    return {
        "review_version": "2026.09.22",
        "reviewer": "owner",
        "reviewed_at": "2026-09-22",
        "automatic_rewrite": False,
        "raw_values_stored": False,
        "raw_scanner_report_retained": False,
        "classification_items_are_the_replayable_sample": True,
        "owner_decision_required": True,
        "scope": {"findings_included": 1},
        "items": [
            {
                "file": "records/receipt.json",
                "pointer": "/0/source",
                "key": "source",
                "classification": "local_outside_checkout",
                "value_sha256": "b" * 64,
                "value_length": 12,
                "proposed_category": "provenance-only-candidate",
                "owner_decision": decision,
                "owner_reason": "Owner confirmed the value is provenance only.",
            }
        ],
    }


class ReviewBundleTests(unittest.TestCase):
    def test_blank_bundle_is_hold_and_summary_is_log_safe(self):
        quality_gate, _ = quality_gate_and_review()
        manifest, _ = authority_manifest_and_review()
        blank_quality = {"review_version": "2026.09.22", "decisions": []}
        blank_authority = {"decisions": []}
        blank_provenance = provenance_review()
        blank_provenance["reviewer"] = ""
        blank_provenance["reviewed_at"] = ""
        blank_provenance["items"][0]["owner_decision"] = None
        blank_provenance["items"][0]["owner_reason"] = ""
        result = validate_review_bundle(
            quality_gate=quality_gate,
            quality_review=blank_quality,
            manifest=manifest,
            authority_review=blank_authority,
            provenance_review=blank_provenance,
        )
        self.assertFalse(result["complete"])
        self.assertFalse(result["release_ready"])
        summary = summarize_review_bundle(result)
        self.assertNotIn("findings", str(summary))
        self.assertFalse(summary["automatic_acceptance"])

    def test_all_safe_decisions_make_bundle_release_ready(self):
        quality_gate, quality_review = quality_gate_and_review()
        manifest, authority_review = authority_manifest_and_review()
        result = validate_review_bundle(
            quality_gate=quality_gate,
            quality_review=quality_review,
            manifest=manifest,
            authority_review=authority_review,
            provenance_review=provenance_review(),
        )
        self.assertTrue(result["complete"])
        self.assertTrue(result["release_ready"])
        self.assertEqual(result["status"], "complete")

    def test_any_held_gate_keeps_bundle_held(self):
        quality_gate, quality_review = quality_gate_and_review("held")
        manifest, authority_review = authority_manifest_and_review("held")
        held_provenance = provenance_review("held")
        before = copy.deepcopy(held_provenance)
        result = validate_review_bundle(
            quality_gate=quality_gate,
            quality_review=quality_review,
            manifest=manifest,
            authority_review=authority_review,
            provenance_review=held_provenance,
        )
        self.assertTrue(result["complete"])
        self.assertFalse(result["release_ready"])
        self.assertEqual(result["status"], "hold")
        self.assertEqual(held_provenance, before)


if __name__ == "__main__":
    unittest.main()
