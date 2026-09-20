import copy
import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location(
    "render_preservation", HERE / "render_preservation.py"
)
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


class BindingTests(unittest.TestCase):
    def candidate(self):
        return {
            "claims": [{"id": "c1"}, {"id": "c2"}],
            "coverage": ["immutable"],
            "pages": [
                {
                    "title": "Policy",
                    "sections": [
                        {
                            "heading": "Rules",
                            "paragraphs": [
                                {
                                    "id": "p1",
                                    "text": "A",
                                    "claim_ids": ["c1"],
                                    "annotation_ids": [],
                                },
                                {
                                    "id": "p2",
                                    "text": "B",
                                    "claim_ids": ["c2"],
                                    "annotation_ids": [],
                                },
                            ],
                        }
                    ],
                }
            ],
        }

    def test_text_edit_is_allowed_without_changing_input(self):
        before = self.candidate()
        after = copy.deepcopy(before)
        renderer.paragraphs(after)[0]["text"] = "Edited prose"
        renderer.verify_bindings(before, after)
        self.assertEqual(before, self.candidate())

    def test_rotated_citations_rejected_even_with_identical_global_set(self):
        before = self.candidate()
        after = copy.deepcopy(before)
        a, b = renderer.paragraphs(after)
        a["claim_ids"], b["claim_ids"] = b["claim_ids"], a["claim_ids"]
        with self.assertRaisesRegex(ValueError, "immutable"):
            renderer.verify_bindings(before, after)

    def test_changed_claims_or_paragraph_structure_rejected(self):
        for mode in ["claim", "paragraph", "ledger"]:
            before = self.candidate()
            after = copy.deepcopy(before)
            if mode == "claim":
                after["claims"][0]["id"] = "wrong"
            elif mode == "paragraph":
                after["pages"][0]["sections"][0]["paragraphs"].reverse()
            else:
                after["coverage"] = []
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                renderer.verify_bindings(before, after)
