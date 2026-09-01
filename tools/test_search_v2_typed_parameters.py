#!/usr/bin/env python3
"""Strict unit/enum comparison coverage for Search Engine V2."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.exact_match import evaluate_exact_match  # noqa: E402
from app.search_v2.models import ExactMatchResult, Offer, ProductIdentity, SearchRequestV2  # noqa: E402


def offer(*, facts: dict[str, object] | None = None, title_confirmed: tuple[str, ...] = ()) -> Offer:
    normalized_facts = dict(facts or {})
    if title_confirmed:
        normalized_facts["fact_evidence"] = {
            key: {"confidence": "high", "evidence": "title"}
            for key in title_confirmed
        }
    return Offer(
        title="Товар Test X1",
        facts=normalized_facts,
        identity=ProductIdentity(
            category="generic_tech",
            canonical_model="Test X1",
            identity_confidence=0.95,
            key_configuration=dict(facts or {}),
        ),
    )


def request(required_specs: dict[str, object]) -> SearchRequestV2:
    return SearchRequestV2(
        category="generic_tech",
        canonical_model="Test X1",
        required_specs=required_specs,
        supported_category=True,
    )


class TypedParameterMatchTests(unittest.TestCase):
    def test_resolution_spellings_are_strict_but_equivalent(self) -> None:
        self.assertEqual(
            evaluate_exact_match(request({"resolution": "4K"}), offer(facts={"resolution": "3840 x 2160"})),
            ExactMatchResult.EXACT,
        )
        self.assertEqual(
            evaluate_exact_match(request({"resolution": "1920x1080"}), offer(facts={"resolution": "Full HD"})),
            ExactMatchResult.EXACT,
        )
        self.assertEqual(
            evaluate_exact_match(request({"resolution": "4K"}), offer(facts={"resolution": "Full HD"})),
            ExactMatchResult.REQUIRED_SPEC_MISMATCH,
        )

    def test_boolean_facts_require_explicit_confirmation(self) -> None:
        self.assertEqual(
            evaluate_exact_match(request({"cappuccinator": True}), offer(facts={"cappuccinator": "yes"})),
            ExactMatchResult.EXACT,
        )
        self.assertEqual(
            evaluate_exact_match(request({"cappuccinator": True}), offer(facts={"cappuccinator": "no"})),
            ExactMatchResult.REQUIRED_SPEC_MISMATCH,
        )
        self.assertEqual(
            evaluate_exact_match(request({"cappuccinator": False}), offer(facts={"cappuccinator": "нет"})),
            ExactMatchResult.EXACT,
        )
        self.assertEqual(
            evaluate_exact_match(request({"cappuccinator": False}), offer(facts={"cappuccinator": True})),
            ExactMatchResult.REQUIRED_SPEC_MISMATCH,
        )
        self.assertEqual(
            evaluate_exact_match(request({"cappuccinator": True}), offer(facts={"cappuccinator": "unknown"})),
            ExactMatchResult.GENERIC_MATCH,
        )

    def test_numeric_units_are_converted_without_relaxing_values(self) -> None:
        cases = (
            ({"ram_gb": 16}, {"ram": "16 GB"}),
            ({"ssd_gb": 1024}, {"ssd": "1 TB"}),
            ({"refresh_rate": "120 Hz"}, {"hz": "120 Гц"}),
            ({"diagonal": '13.3"'}, {"screen": "33.8 cm"}),
            ({"pressure": "15 bar"}, {"pressure_bar": "1.5 MPa"}),
            ({"volume_l": 1.5}, {"volume": "1500 ml"}),
            ({"power_w": 1450}, {"power": "1.45 kW"}),
        )
        for required_specs, facts in cases:
            with self.subTest(required_specs=required_specs, facts=facts):
                title_confirmed = tuple(key for key in facts if key in {"ram", "ssd"})
                self.assertEqual(
                    evaluate_exact_match(request(required_specs), offer(facts=facts, title_confirmed=title_confirmed)),
                    ExactMatchResult.EXACT,
                )

        self.assertEqual(
            evaluate_exact_match(
                request({"ram_gb": 16}),
                offer(facts={"ram": "8 GB"}, title_confirmed=("ram",)),
            ),
            ExactMatchResult.REQUIRED_SPEC_MISMATCH,
        )
        self.assertEqual(
            evaluate_exact_match(request({"power_w": 1450}), offer(facts={"power": "1200 W"})),
            ExactMatchResult.REQUIRED_SPEC_MISMATCH,
        )

    def test_enum_aliases_and_model_codes_are_unambiguous(self) -> None:
        self.assertEqual(
            evaluate_exact_match(request({"machine_type": "automatic"}), offer(facts={"machine_type": "Автоматическая"})),
            ExactMatchResult.EXACT,
        )
        self.assertEqual(
            evaluate_exact_match(request({"smart_platform": "Google TV"}), offer(facts={"smart_platform": "googletv"})),
            ExactMatchResult.EXACT,
        )
        self.assertEqual(
            evaluate_exact_match(request({"panel": "OLED"}), offer(facts={"panel": "QLED"})),
            ExactMatchResult.REQUIRED_SPEC_MISMATCH,
        )
        self.assertEqual(
            evaluate_exact_match(request({"mpn": "MW103RU/A"}), offer(facts={"model_code": "mw103ru-a"})),
            ExactMatchResult.EXACT,
        )
        self.assertEqual(
            evaluate_exact_match(request({"mpn": "MW103RU/A"}), offer(facts={"model_code": "MW102RU/A"})),
            ExactMatchResult.REQUIRED_SPEC_MISMATCH,
        )

    def test_missing_or_unparseable_required_fact_never_becomes_exact(self) -> None:
        self.assertEqual(
            evaluate_exact_match(request({"volume_l": 1.5}), offer(facts={"volume": "compact"})),
            ExactMatchResult.GENERIC_MATCH,
        )
        self.assertEqual(
            evaluate_exact_match(request({"ram_gb": 16}), offer(facts={})),
            ExactMatchResult.GENERIC_MATCH,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
