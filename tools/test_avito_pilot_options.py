"""Offline pilot CLI regressions; no .env reads and no external requests."""
from contextlib import redirect_stderr
from dataclasses import asdict
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from avito_service.config import ServiceConfig
from avito_service.spending import SpendingGuard
from tools.run_avito_pilot import (
    account_bounded_spending_guard, apply_config_overrides, build_request, closest_failures, collection_only_allowances,
    funnel_from_audit, parse_options,
)


class PilotOptionTests(unittest.TestCase):
    def test_account_verified_override_preserves_journal_and_reserves_buffer(self):
        class Account:
            def _json_request(self, method, url, **_):
                self_url = url.endswith("/users/me/limits")
                assert method == "GET" and self_url
                snapshot = guard.snapshot()
                end = snapshot["monthlyPeriodEndExclusive"]
                from datetime import date, timedelta
                return {"data": {
                    "monthlyUsageCycle": {"startAt": snapshot["monthlyPeriodStart"] + "T00:00:00Z",
                                           "endAt": (date.fromisoformat(end) - timedelta(days=1)).isoformat() + "T23:59:59Z"},
                    "limits": {"maxMonthlyUsageUsd": 19},
                    "current": {"monthlyUsageUsd": 11.11},
                }}

        with TemporaryDirectory() as folder:
            guard = SpendingGuard(Path(folder) / "spend.json", daily_limit_usd=3,
                                  monthly_limit_usd=18, billing_cycle_day=9)
            before = guard.snapshot()
            elevated, details = account_bounded_spending_guard(
                ServiceConfig(), guard, 8, Account(),
            )
            self.assertEqual(details["new_spend_ceiling_usd"], 7.39)
            self.assertEqual(elevated.snapshot()["monthlyCommittedUsd"], before["monthlyCommittedUsd"])
            self.assertAlmostEqual(elevated.snapshot()["monthlyLimitUsd"],
                                   before["monthlyCommittedUsd"] + 7.39)
            self.assertEqual(guard.snapshot()["monthlyLimitUsd"], 18)

            # The account endpoint may not yet include a started/uncertain run.
            guard.reserve(1.0)
            pending_guard, pending_details = account_bounded_spending_guard(
                ServiceConfig(), guard, 8, Account(),
            )
            self.assertEqual(pending_details["pending_reservations_usd"], 1.0)
            self.assertEqual(pending_details["new_spend_ceiling_usd"], 6.39)
            self.assertEqual(pending_guard.snapshot()["activeReservationUsd"], 1.0)
            self.assertAlmostEqual(pending_guard.snapshot()["monthlyLimitUsd"], 7.39)

            # A caller's remaining authorization must also stay a hard ceiling.
            _, lower_details = account_bounded_spending_guard(
                ServiceConfig(), guard, 0.5, Account(),
            )
            self.assertEqual(lower_details["new_spend_ceiling_usd"], 0.5)

            guard.reserve(2.0)
            near_limit = Account()._json_request("GET", "/users/me/limits")
            near_limit["data"]["current"]["monthlyUsageUsd"] = 16
            with patch.object(Account, "_json_request", return_value=near_limit), self.assertRaises(ValueError):
                account_bounded_spending_guard(ServiceConfig(), guard, 8, Account())

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
