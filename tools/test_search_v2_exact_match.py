#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.exact_match import evaluate_exact_match, is_hard_mismatch  # noqa: E402
from app.search_v2.models import (  # noqa: E402
    ExactMatchResult, Offer, ProductCondition, ProductIdentity, SearchRequestV2,
)


def offer(title: str, *, model="iPhone 16", modifiers=None, storage=256, condition=ProductCondition.NEW, facts=None) -> Offer:
    return Offer(
        title=title,
        condition=condition,
        facts=facts or {},
        identity=ProductIdentity(
            category="phone", canonical_model=model, modifiers=modifiers or [], storage=storage,
            condition=condition, identity_confidence=0.95,
        ),
    )


class SearchV2ExactMatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = SearchRequestV2(
            category="phone", canonical_model="iPhone 16", model_modifiers=["pro"],
            required_specs={"storage": 256}, condition=ProductCondition.NEW,
        )

    def test_exact_and_hard_variant_mismatches(self) -> None:
        self.assertEqual(evaluate_exact_match(self.request, offer("iPhone 16 Pro 256 ГБ", modifiers=["pro"])), ExactMatchResult.EXACT)
        self.assertEqual(evaluate_exact_match(self.request, offer("iPhone 16 256 ГБ")), ExactMatchResult.MODEL_MISMATCH)
        self.assertEqual(evaluate_exact_match(self.request, offer("iPhone 16 Pro Max 256 ГБ", modifiers=["pro", "max"])), ExactMatchResult.MODEL_MISMATCH)
        self.assertEqual(evaluate_exact_match(self.request, offer("iPhone 16 Pro 128 ГБ", modifiers=["pro"], storage=128)), ExactMatchResult.REQUIRED_SPEC_MISMATCH)
        self.assertTrue(is_hard_mismatch(ExactMatchResult.MODEL_MISMATCH))
        self.assertTrue(is_hard_mismatch(ExactMatchResult.REQUIRED_SPEC_MISMATCH))
        self.assertFalse(is_hard_mismatch(ExactMatchResult.EXACT))

    def test_accessory_condition_and_missing_required_fact(self) -> None:
        accessory = offer("Чехол для iPhone 16 Pro 256 ГБ", modifiers=["pro"])
        self.assertEqual(evaluate_exact_match(self.request, accessory), ExactMatchResult.ACCESSORY)
        used = offer("iPhone 16 Pro 256 ГБ б/у", modifiers=["pro"], condition=ProductCondition.USED)
        self.assertEqual(evaluate_exact_match(self.request, used), ExactMatchResult.REQUIRED_SPEC_MISMATCH)
        missing = offer("iPhone 16 Pro", modifiers=["pro"], storage=None)
        self.assertEqual(evaluate_exact_match(self.request, missing), ExactMatchResult.GENERIC_MATCH)

    def test_optional_color_does_not_create_hard_mismatch(self) -> None:
        request = SearchRequestV2(
            canonical_model="iPhone 16", model_modifiers=["pro"], required_specs={"storage": 256},
            optional_specs={"color": "black"}, condition=ProductCondition.NEW,
        )
        candidate = offer("iPhone 16 Pro 256 ГБ белый", modifiers=["pro"], facts={"color": "white"})
        self.assertEqual(evaluate_exact_match(request, candidate), ExactMatchResult.EXACT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
