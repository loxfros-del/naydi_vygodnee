"""Offline 200-listing planning with the existing source budget and city guard."""
from dataclasses import replace
from copy import deepcopy
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig, expanded_review_config, load_config
from avito_service.errors import ApifyPlanRequiredError
from avito_service.http_api import parse_search_request
from avito_service.models import CollectionBatch, SearchRequest
from avito_service.service import AvitoAnalysisService
from avito_service.spending import SpendingGuard
from tools.test_avito_collection_quality import EvidenceReviewer, row
from tools.test_avito_transport import rows, run_receipt


def config(**changes):
    return replace(ServiceConfig(apify_token="offline-test", apify_max_charge_usd=2,
                   ai_max_cost_rub=50, report_max_cost_rub=250, usd_rub_rate=100), **changes)


class RecordingProvider:
    supports_collection_budget = True

    def __init__(self, settings):
        self.config = settings
        self.calls = []

    def can_share_discovery(self, search):
        return search.price_min is None and search.price_max is None

    def collect_market(self, search, **kwargs):
        self.calls.append(("market", search, kwargs["max_charge_usd"]))
        return CollectionBatch((), 0, False, requested_count=search.max_results, capped_count=search.max_results)

    def collect(self, search, **kwargs):
        self.calls.append(("candidates", search, kwargs["max_charge_usd"]))
        return CollectionBatch((), 0, False, requested_count=search.max_results, capped_count=search.max_results)


def engine(provider):
    return AvitoAnalysisService(provider, EvidenceReviewer(), ai_text_max_listings=200,
                               ai_max_cost_rub=50, report_max_cost_rub=250, usd_rub_rate=100)


class AvitoLargeCollectionTests(unittest.TestCase):
    def test_production_profile_runs_200_source_and_separate_final_refresh_within_two_dollars(self):
        local_stub = {"AVITO_AI_BASE_URL": "https://api.aitunnel.ru/v1", "AVITO_AI_MODEL": "old-model",
                      "AVITO_APIFY_MAX_CHARGE_USD": "1", "APIFY_TOKEN": "offline-test"}
        with patch.dict(os.environ, {}, clear=True), patch("avito_service.config._read_local_env", return_value=local_stub):
            settings = expanded_review_config(load_config())
        self.assertEqual(settings.apify_max_charge_usd, 2)
        self.assertEqual(settings.effective_ai_budget_rub, 50)
        self.assertEqual(settings.report_max_cost_rub, 250)

        class FixtureWireProvider(ZenStudioProvider):
            def __init__(self, settings, guard):
                super().__init__(settings, guard)
                self.posts = []
                self.current_rows = []
                self.source_rows = []
                for index in range(200):
                    item = row(10000000 + index, 40_000 + 100 * index)
                    item["location"] = "Ярославль"
                    item["url"] = item["url"].replace("/moskva/", "/yaroslavl/")
                    if index >= 20:
                        item["title"] = "iPhone 12, 128 ГБ"
                        item["parameters"]["Модель"] = "iPhone 12"
                    self.source_rows.append(item)

            def _json_request(self, method, url, *, payload=None, **kwargs):
                if method == "POST":
                    ceiling = float(parse_qs(urlparse(url).query)["maxTotalChargeUsd"][0])
                    self.posts.append((deepcopy(payload), ceiling))
                    if "listingUrls" in payload:
                        self.current_rows = [item for item in self.source_rows if item["url"] in payload["listingUrls"]]
                    else:
                        self.current_rows = self.source_rows[:payload["maxResults"]]
                    return run_receipt(count=len(self.current_rows))
                if "/items?" in url:
                    return deepcopy(self.current_rows)
                return run_receipt(count=len(self.current_rows))

        for quota in (3, 5):
            with self.subTest(quota=quota), TemporaryDirectory() as directory:
                guard = SpendingGuard(Path(directory) / "spend.json", daily_limit_usd=3)
                provider = FixtureWireProvider(settings, guard)
                current = AvitoAnalysisService(provider, EvidenceReviewer(), ai_text_max_listings=200,
                                               ai_max_listings=20, ai_text_batch_size=5,
                                               ai_max_cost_rub=settings.effective_ai_budget_rub,
                                               report_max_cost_rub=settings.report_max_cost_rub,
                                               usd_rub_rate=settings.usd_rub_rate)
                report = current.search(SearchRequest("iPhone 13", location="Ярославль", pickup_only=True,
                                         max_results=200, desired_results=quota))
                self.assertEqual(len(provider.posts), 2, "One shared source run and one exact final refresh")
                self.assertEqual(provider.posts[0][0]["maxResults"], 200)
                self.assertEqual(len(provider.posts[1][0]["listingUrls"]), quota)
                self.assertEqual(len(report.public_dict()["recommendations"]), quota)
                self.assertLessEqual(sum(ceiling for _, ceiling in provider.posts), 2)
                self.assertLessEqual(guard.snapshot()["dailyCommittedUsd"], 2)
                self.assertTrue(all(item["listing"]["location"] == "yaroslavl"
                                    for item in report.public_dict()["recommendations"]))

    def test_schema_accepts_200_and_keeps_output_quota_separate(self):
        for quota in (3, 5):
            request = parse_search_request({"query": "PS5", "location": "Ярославль", "pickupOnly": True,
                                            "maxResults": 200, "desiredResults": quota})
            self.assertEqual(request.max_results, 200)
            self.assertEqual(request.desired_results, quota)
            self.assertTrue(request.pickup_only)
        for count in (0, 201):
            with self.assertRaises(ValueError):
                SearchRequest("PS5", max_results=count)

    def test_safe_limit_still_obeys_configured_and_report_money_caps(self):
        settings = config()
        self.assertEqual(settings.safe_apify_listing_limit, 200)
        for limited in (replace(settings, apify_max_charge_usd=1),
                        replace(settings, report_max_cost_rub=100)):
            room = min(limited.apify_max_charge_usd,
                       (limited.report_max_cost_rub - limited.effective_ai_budget_rub) / limited.usd_rub_rate)
            expected = math.floor((room - 3 * limited.apify_start_cost_usd) / limited.apify_full_listing_cost_usd)
            self.assertEqual(limited.safe_apify_listing_limit, expected)
            self.assertLess(limited.safe_apify_listing_limit, 200)
            self.assertLessEqual(limited.effective_apify_max_charge_usd, room)

    def test_shared_discovery_200_reserves_separate_final_refresh(self):
        for quota in (3, 5):
            provider = RecordingProvider(config())
            current = engine(provider)
            current.search(SearchRequest("PS5", location="Ярославль", pickup_only=True,
                                         max_results=200, desired_results=quota))
            self.assertEqual(len(provider.calls), 1)
            stage, request, allowance = provider.calls[0]
            self.assertEqual(stage, "market")
            self.assertEqual(request.max_results, 200)
            self.assertEqual(request.location, "Ярославль")
            self.assertTrue(request.pickup_only)
            final_reserve = quota * provider.config.apify_full_listing_cost_usd + provider.config.apify_start_cost_usd
            self.assertLessEqual(allowance + final_reserve, 2)
            self.assertEqual(current.ai_text_max_listings, 200)

    def test_price_bounded_search_splits_at_most_200_and_preserves_broad_market(self):
        provider = RecordingProvider(config())
        engine(provider).search(SearchRequest("PS5", location="Ярославль", pickup_only=True,
                                price_min=20_000, price_max=50_000, max_results=200, desired_results=3))
        self.assertEqual([stage for stage, _, _ in provider.calls], ["market", "candidates"])
        market, candidate = [request for _, request, _ in provider.calls]
        self.assertEqual(market.max_results + candidate.max_results, 200)
        self.assertIsNone(market.price_max)
        self.assertIsNone(market.price_min)
        self.assertEqual((candidate.price_min, candidate.price_max), (20_000, 50_000))
        self.assertTrue(all(request.pickup_only and request.location == "Ярославль" for _, request, _ in provider.calls))
        reserve = 3 * provider.config.apify_full_listing_cost_usd + provider.config.apify_start_cost_usd
        self.assertLessEqual(sum(allowance for _, _, allowance in provider.calls) + reserve, 2)

    def test_small_configured_source_cap_reduces_pool_without_expanding_money(self):
        provider = RecordingProvider(config(apify_max_charge_usd=0.3))
        engine(provider).search(SearchRequest("PS5", location="Ярославль", pickup_only=True,
                                max_results=200, desired_results=3))
        _, request, allowance = provider.calls[0]
        self.assertLess(request.max_results, 100)
        reserve = 3 * provider.config.apify_full_listing_cost_usd + provider.config.apify_start_cost_usd
        self.assertLessEqual(allowance + reserve, 0.3)

    def test_actor_receives_200_and_exact_city_with_server_charge_ceiling(self):
        provider = ZenStudioProvider(config())
        request = SearchRequest("PS5", location="Ярославль", pickup_only=True, max_results=200)
        with patch.object(provider, "_json_request", side_effect=[run_receipt(count=200), rows(200), run_receipt(count=200)]) as wire:
            result = provider.collect(request, max_charge_usd=1.61)
        start = wire.call_args_list[0]
        payload = start.kwargs["payload"]
        self.assertEqual(payload["maxResults"], 200)
        self.assertEqual(payload["location"], "Ярославль")
        self.assertEqual(payload["query"], "PS5")
        self.assertEqual(payload["sort"], "relevance")
        self.assertLessEqual(float(parse_qs(urlparse(start.args[1]).query)["maxTotalChargeUsd"][0]), 1.61)
        self.assertEqual(len(result.items), 200)
        self.assertEqual(sum(call.args[0] == "POST" for call in wire.call_args_list), 1)

    def test_explicit_free_plan_failure_is_clear_and_does_not_repeat_paid_start(self):
        provider = ZenStudioProvider(config())
        request = SearchRequest("PS5", max_results=200)
        failed = {"data": {"id": "offline-run", "status": "FAILED",
                           "statusMessage": "Free plan limit is 100 listings. Upgrade to paid plan."}}
        with patch.object(provider, "_json_request", return_value=failed) as wire:
            for _ in range(2):
                with self.assertRaises(ApifyPlanRequiredError) as caught:
                    provider.collect(request)
                self.assertIn("100 объявлений", str(caught.exception))
                self.assertFalse(caught.exception.retryable)
        self.assertEqual(wire.call_count, 1)


if __name__ == "__main__":
    unittest.main()
