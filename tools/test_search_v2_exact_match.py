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


def offer(title: str, *, model="iPhone 16", modifiers=None, storage=256, condition=ProductCondition.NEW, facts=None, sim_variant="") -> Offer:
    return Offer(
        title=title,
        condition=condition,
        facts=facts or {},
        identity=ProductIdentity(
            category="phone", canonical_model=model, modifiers=modifiers or [], storage=storage,
            condition=condition, region_or_sim_variant=sim_variant, identity_confidence=0.95,
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

    def test_product_family_does_not_mark_different_model_codes_exact(self) -> None:
        family_request = SearchRequestV2(
            category="coffee_machine",
            brand="DeLonghi",
            canonical_model="Кофемашина DeLonghi Magnifica",
            supported_category=True,
        )
        start = offer(
            "Автоматическая кофемашина DeLonghi Magnifica Start ECAM220.22.GB",
            model="DeLonghi Magnifica Start ECAM220.22.GB",
        )
        magnifica_s = offer(
            "Кофемашина DeLonghi Magnifica S ECAM21.117.SB",
            model="DeLonghi Magnifica S ECAM21.117.SB",
        )
        specific_request = SearchRequestV2(
            category="coffee_machine",
            brand="DeLonghi",
            canonical_model="DeLonghi Magnifica S ECAM21.117.SB",
            supported_category=True,
        )

        self.assertEqual(evaluate_exact_match(family_request, start), ExactMatchResult.GENERIC_MATCH)
        self.assertEqual(evaluate_exact_match(family_request, magnifica_s), ExactMatchResult.GENERIC_MATCH)
        self.assertEqual(evaluate_exact_match(specific_request, magnifica_s), ExactMatchResult.EXACT)

    def test_headphone_request_rejects_hyperx_keyboard(self) -> None:
        request = SearchRequestV2(
            category="headphones",
            brand="HyperX",
            canonical_model="",
            condition=ProductCondition.NEW,
            supported_category=True,
        )
        keyboard = Offer(
            title="Клавиатура проводная HyperX Alloy Core RGB",
            condition=ProductCondition.NEW,
            identity=ProductIdentity(
                category="headphones",
                brand="HyperX",
                condition=ProductCondition.NEW,
                identity_confidence=0.8,
            ),
        )
        self.assertEqual(
            evaluate_exact_match(request, keyboard),
            ExactMatchResult.REQUIRED_SPEC_MISMATCH,
        )

    def test_phone_sim_or_region_is_a_hard_configuration(self) -> None:
        request = SearchRequestV2(
            category="phone", canonical_model="iPhone 16", model_modifiers=["pro"],
            required_specs={"storage_gb": 256, "sim_variant": "eSIM"},
            condition=ProductCondition.NEW,
        )
        exact = offer("iPhone 16 Pro 256 ГБ eSIM", modifiers=["pro"], sim_variant="global|esim")
        wrong = offer("iPhone 16 Pro 256 ГБ Dual SIM", modifiers=["pro"], sim_variant="dual_sim")
        unknown = offer("iPhone 16 Pro 256 ГБ", modifiers=["pro"])
        self.assertEqual(evaluate_exact_match(request, exact), ExactMatchResult.EXACT)
        self.assertEqual(evaluate_exact_match(request, wrong), ExactMatchResult.REQUIRED_SPEC_MISMATCH)
        self.assertEqual(evaluate_exact_match(request, unknown), ExactMatchResult.GENERIC_MATCH)

    def test_generic_tech_requires_the_named_model_code(self) -> None:
        request = SearchRequestV2(
            category="generic_tech", brand="Canon", canonical_model="Canon EOS R50",
            supported_category=True,
        )
        same = Offer(
            title="Фотоаппарат Canon EOS R50", identity=ProductIdentity(
                category="generic_tech", canonical_model="Canon EOS R50", identity_confidence=0.95,
            ),
        )
        other = Offer(
            title="Фотоаппарат Canon EOS R10", identity=ProductIdentity(
                category="generic_tech", canonical_model="Canon EOS R10", identity_confidence=0.95,
            ),
        )
        self.assertEqual(evaluate_exact_match(request, same), ExactMatchResult.EXACT)
        self.assertEqual(evaluate_exact_match(request, other), ExactMatchResult.MODEL_MISMATCH)


if __name__ == "__main__":
    unittest.main(verbosity=2)
