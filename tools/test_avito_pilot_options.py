"""Offline pilot CLI regressions; no .env reads and no external requests."""
from contextlib import redirect_stderr
from dataclasses import asdict
import io
import unittest
from unittest.mock import patch

from avito_service.config import ServiceConfig
from tools.run_avito_pilot import (
    apply_config_overrides, build_request, closest_failures, collection_only_allowances,
    funnel_from_audit, parse_options,
)


class PilotOptionTests(unittest.TestCase):
    def test_legacy_phone_defaults_keep_specs_without_an_implicit_price_ceiling(self):
        request = build_request(parse_options([]))
        self.assertEqual((request.query, request.location, request.category), ("iPhone 13", "Москва", "phones"))
        self.assertEqual((request.required_storage, request.required_sim), ("128 GB", "SIM + eSIM"))
        self.assertEqual(request.required_condition, "Отличное")
        self.assertEqual(request.max_results, 30)
        self.assertIsNone(request.price_max)

    def test_yaroslavl_ps5_has_no_silent_phone_specs(self):
        with patch("tools.run_avito_pilot.load_config", side_effect=AssertionError("No configuration reads")):
            request = build_request(parse_options([
                "--query", "PlayStation 5", "--location", "Ярославль", "--category", "gaming",
                "--condition", "", "--max-results", "20",
            ]))
        self.assertEqual((request.query, request.location, request.category), ("PlayStation 5", "Ярославль", "gaming"))
        self.assertEqual((request.required_storage, request.required_sim, request.required_condition), ("", "", ""))
        self.assertEqual(request.max_results, 20)
        self.assertIsNone(request.price_max)
        self.assertEqual(asdict(request)["location"], "Ярославль")

    def test_explicit_attributes_and_price_limits_are_preserved(self):
        request = build_request(parse_options([
            "--category", "gaming", "--query", "PS5 Slim", "--storage", "1 TB", "--sim", "custom",
            "--condition", "Новое", "--price-min", "30000", "--price-max", "65000",
        ]))
        self.assertEqual((request.required_storage, request.required_sim, request.required_condition), ("1 TB", "custom", "Новое"))
        self.assertEqual((request.price_min, request.price_max), (30000, 65000))

    def test_explicit_empty_phone_attributes_override_legacy_defaults(self):
        request = build_request(parse_options(["--storage", "", "--sim", "", "--condition", ""]))
        self.assertEqual((request.required_storage, request.required_sim, request.required_condition), ("", "", ""))

    def test_caps_and_model_are_local_configuration_overrides(self):
        original = ServiceConfig(apify_max_charge_usd=0.8, ai_max_cost_rub=50, ai_model="original")
        changed = apply_config_overrides(original, parse_options([
            "--model", "pilot-model", "--apify-cap-usd", "0.28", "--ai-budget-rub", "25",
        ]))
        self.assertEqual((changed.apify_max_charge_usd, changed.ai_max_cost_rub, changed.ai_model), (0.28, 25, "pilot-model"))
        self.assertEqual((original.apify_max_charge_usd, original.ai_max_cost_rub, original.ai_model), (0.8, 50, "original"))

    def test_arguments_cannot_raise_configured_spending_limits(self):
        original = ServiceConfig(apify_max_charge_usd=0.12, ai_max_cost_rub=10)
        changed = apply_config_overrides(original, parse_options(["--apify-cap-usd", "5", "--ai-budget-rub", "100"]))
        self.assertEqual((changed.apify_max_charge_usd, changed.ai_max_cost_rub), (0.12, 10))
        self.assertEqual(apply_config_overrides(ServiceConfig(apify_max_charge_usd=3), parse_options([])).apify_max_charge_usd, 2)

    def test_invalid_budget_arguments_are_rejected_before_configuration(self):
        for option in ("--apify-cap-usd", "--ai-budget-rub"):
            for value in ("0", "-1", "nan", "inf", "no"):
                with self.subTest(option=option, value=value), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    parse_options([option, value])

    def test_invalid_result_count_fails_before_any_live_service_is_built(self):
        for count in ("0", "201"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                build_request(parse_options(["--max-results", count]))

    def test_collection_only_two_calls_share_the_total_ceiling(self):
        for cap in (0.05, 0.1, 0.2, 0.28, 1):
            with self.subTest(cap=cap):
                market, candidates = collection_only_allowances(ServiceConfig(apify_max_charge_usd=cap))
                self.assertGreater(market, 0)
                self.assertGreater(candidates, 0)
                self.assertLessEqual(market + candidates, min(cap, 0.273))

    def test_owner_diagnostics_use_the_requested_funnel_names_and_exact_blockers(self):
        audit = {
            "collectedCount": 200,
            "adminAudit": {"collected": 200, "correctCity": 60, "basicFilters": 48,
                           "textCompleted": 40, "photoCompleted": 8, "highConfidence": 7,
                           "confirmed": 0},
            "adminRecommendations": [{
                "role": "CAUTION", "listing": {"id": "1", "title": "PS5", "acquisitionPrice": 50_000},
                "analysis": {"textAnalyzed": True, "matchesRequest": True, "verdict": "approve",
                             "defects": ["Повреждена коробка"], "conflicts": [], "priceConditions": []},
                "risks": ["Недостаточно сопоставимых свежих предложений для оценки рынка."],
            }],
        }
        self.assertEqual(list(funnel_from_audit(audit)), [
            "collected", "correct_city", "basic_filters", "text_check", "photo_check",
            "HIGH_CONFIDENCE", "CONFIRMED",
        ])
        self.assertEqual(list(funnel_from_audit(audit).values()), [200, 60, 48, 40, 8, 7, 0])
        reasons = closest_failures(audit)[0]["reasons"]
        self.assertIn("Повреждена коробка", reasons)
        self.assertIn("Недостаточно сопоставимых свежих предложений для оценки рынка.", reasons)


if __name__ == "__main__":
    unittest.main()
