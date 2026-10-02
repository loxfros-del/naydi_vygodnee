"""Real Zen request construction with fake HTTP: one shared pool, no paid calls."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig, expanded_review_config
from avito_service.models import SearchRequest
from avito_service.service import AvitoAnalysisService
from tools.test_avito_collection_quality import EvidenceProvider, EvidenceReviewer, service
from tools.test_avito_strict_results import console_row
from tools.test_avito_transport import Response


def pool(count=60):
    return [console_row(30000000 + index, 30_000 + index * 100 if index < 3 else 40_000 + index * 100)
            for index in range(count)]


def request(**changes):
    return replace(SearchRequest("PlayStation 5", location="Ярославль", category="gaming",
                                 desired_results=3, max_results=60), **changes)


class FakeZenHTTP:
    def __init__(self, rows):
        self.rows = rows
        self.starts = []
        self.reservations = []
        self.runs = {}
        self.refresh_changes = {}

    def receipt(self, identifier):
        count = len(self.runs[identifier])
        events = {"listing-scraped": 0.00499, "detail-enriched": 0.00299,
                  "apify-default-dataset-item": 0.00001, "apify-actor-start": 0.005}
        return {"data": {"id": identifier, "defaultDatasetId": identifier, "status": "SUCCEEDED",
                         "usageTotalUsd": count * 0.00799 + 0.005,
                         "pricingInfo": {"pricingPerEvent": {"actorChargeEvents": {
                             name: {"eventPriceUsd": price} for name, price in events.items()}}},
                         "chargedEventCounts": {name: 1 if name == "apify-actor-start" else count
                                                for name in events}}}

    def __call__(self, call, **kwargs):
        parsed = urlparse(call.full_url)
        if call.method == "POST":
            payload = json.loads(call.data)
            self.starts.append(payload)
            self.reservations.append(float(parse_qs(parsed.query)["maxTotalChargeUsd"][0]))
            if "listingUrls" in payload:
                values = [deepcopy(row) for row in self.rows if row["url"] in payload["listingUrls"]]
                for row in values:
                    row.update(self.refresh_changes.get(str(row["id"]), {}))
                    row["scrapedAt"] = datetime.now(timezone.utc).isoformat()
            else:
                values = [deepcopy(row) for row in self.rows
                          if row["price"] >= payload.get("priceMin", 0)
                          and row["price"] <= payload.get("priceMax", 10**12)]
            identifier = f"offline-run-{len(self.starts)}"
            self.runs[identifier] = values[:payload["maxResults"]]
            return Response(self.receipt(identifier))
        parts = parsed.path.strip("/").split("/")
        if "datasets" in parts:
            return Response(self.runs[parts[parts.index("datasets") + 1]])
        if "actor-runs" in parts:
            return Response(self.receipt(parts[parts.index("actor-runs") + 1]))
        raise AssertionError(f"Unexpected offline request: {call.method} {parsed.path}")

    @property
    def searches(self):
        return [value for value in self.starts if "query" in value]

    @property
    def refreshes(self):
        return [value for value in self.starts if "listingUrls" in value]


def engine(reviewer=None, config=None):
    # These cases assert a complete 60-row pool. Give the fake run enough
    # report room explicitly; the production default correctly caps a 50 RUB
    # report below 60 enriched rows and is covered by the tight-budget case.
    config = config or expanded_review_config(ServiceConfig(
        apify_token="offline-test", report_max_cost_rub=100,
    ))
    return AvitoAnalysisService(ZenStudioProvider(config), reviewer or EvidenceReviewer(),
                               ai_concurrency=1, ai_text_max_listings=60, ai_max_listings=20,
                               ai_max_cost_rub=config.ai_max_cost_rub,
                               report_max_cost_rub=config.report_max_cost_rub,
                               usd_rub_rate=config.usd_rub_rate)


def shared_key(current, search):
    return current.market_cache.key(search, ":strict-identity-kit-v6")


class AvitoSharedCollectionTests(unittest.TestCase):
    def test_one_wide_query_replaces_two_identical_zen_runs_and_refreshes_finalists(self):
        fake = FakeZenHTTP(pool())
        reviewer = EvidenceReviewer()
        current = engine(reviewer)
        with patch("avito_service.apify.urlopen", side_effect=fake):
            result = current.search(request()).public_dict()
        self.assertEqual(len(fake.searches), 1)
        self.assertEqual(fake.searches[0]["maxResults"], 60)
        self.assertEqual(fake.searches[0]["query"], "PlayStation 5")
        self.assertEqual(fake.searches[0]["location"], "Ярославль")
        self.assertEqual(fake.searches[0]["sort"], "relevance")
        self.assertNotIn("priceMin", fake.searches[0])
        self.assertNotIn("priceMax", fake.searches[0])
        self.assertEqual(len(fake.refreshes), 1)
        self.assertGreaterEqual(len(reviewer.photo_calls), 3)
        self.assertEqual(len(result["recommendations"]), 3)
        self.assertEqual(result["discoveredListings"], [])
        for card in result["recommendations"]:
            self.assertTrue(card["belowMarket"])
            self.assertTrue(card["analysis"]["complete"])
            self.assertEqual(card["listing"]["verificationStatus"], "verified")
            self.assertIn(card["listing"]["url"], fake.refreshes[0]["listingUrls"])
        self.assertLessEqual(sum(fake.reservations), 1.0)

    def test_price_bounds_keep_distinct_market_and_candidate_queries(self):
        fake = FakeZenHTTP(pool())
        with patch("avito_service.apify.urlopen", side_effect=fake):
            result = engine().search(request(price_max=35_000)).public_dict()
        self.assertEqual(len(fake.searches), 2)
        self.assertNotIn("priceMax", fake.searches[0])
        self.assertEqual(fake.searches[1]["priceMax"], 35_000)
        self.assertEqual(len(fake.refreshes), 1)
        self.assertEqual(len(result["recommendations"]), 3)
        self.assertLessEqual(sum(fake.reservations), 1.0 + 1e-8)

    def test_price_bound_request_reuses_prior_broad_market_snapshot(self):
        fake = FakeZenHTTP(pool())
        current = engine()
        with patch("avito_service.apify.urlopen", side_effect=fake):
            current.search(request())
            searches_after_broad = len(fake.searches)
            current.search(request(price_max=35_000))
        self.assertEqual(searches_after_broad, 1)
        self.assertEqual(len(fake.searches), 2)
        self.assertNotIn("priceMax", fake.searches[0])
        self.assertEqual(fake.searches[1]["priceMax"], 35_000)

    def test_tight_collection_allowance_reserves_two_starts_and_refresh(self):
        config = ServiceConfig(apify_token="offline-test", apify_max_charge_usd=0.10,
                               ai_max_cost_rub=15, report_max_cost_rub=25, usd_rub_rate=100)
        fake = FakeZenHTTP(pool())
        with patch("avito_service.apify.urlopen", side_effect=fake):
            engine(config=config).search(request(mode="find"))
        self.assertEqual(len(fake.searches), 1)
        self.assertEqual(fake.searches[0]["maxResults"], 9)
        self.assertEqual(len(fake.refreshes), 1)
        self.assertEqual(fake.refreshes[0]["maxResults"], 2)
        self.assertLessEqual(sum(fake.reservations), 0.10)

    def test_generic_provider_keeps_existing_separate_pipeline(self):
        source = EvidenceProvider()
        source.market_rows = pool()[3:]
        source.candidate_rows = pool()[:3]
        service(source).search(request())
        self.assertEqual(len(source.market_calls), 1)
        self.assertEqual(len(source.candidate_calls), 1)

    def test_complete_recent_source_cache_skips_search_but_not_refresh(self):
        fake = FakeZenHTTP(pool())
        reviewer = EvidenceReviewer()
        current = engine(reviewer)
        with patch("avito_service.apify.urlopen", side_effect=fake):
            current.search(request())
            ai_calls = (len(reviewer.text_calls), len(reviewer.photo_calls))
            cached = current.market_cache.get(shared_key(current, request()))
            self.assertEqual(len(cached), 60)
            result = current.search(request()).public_dict()
        self.assertEqual(len(fake.searches), 1)
        self.assertEqual(len(fake.refreshes), 2)
        self.assertEqual(len(result["recommendations"]), 3)
        self.assertLess(len(reviewer.text_calls) - ai_calls[0], ai_calls[0])
        self.assertEqual(len(reviewer.photo_calls), ai_calls[1])

    def test_cache_keeps_only_request_family_rows_before_market_ai(self):
        rows = [console_row(30000000 + index, 30_000 + index * 100,
                            model="PlayStation 5" if index < 2 else "PlayStation 4") for index in range(60)]
        fake = FakeZenHTTP(rows)
        current = engine()
        with patch("avito_service.apify.urlopen", side_effect=fake):
            current.search(request())
            cached = current.market_cache.get(shared_key(current, request()))
            self.assertEqual(len(cached), 2)
            self.assertEqual(sum(item.ai_review.text_analyzed for item in cached), 2)
            result = current.search(request()).public_dict()
        self.assertEqual(len(fake.searches), 1)
        recommendations = result["recommendations"]
        self.assertEqual(
            {item["listing"]["id"] for item in recommendations},
            {"30000000", "30000001"},
        )
        self.assertTrue(all(not item["belowComparables"] for item in recommendations))
        self.assertTrue(all(item["savingAmount"] is None for item in recommendations))

    def test_short_fill_snapshot_is_reused_without_automatic_paid_retry(self):
        fake = FakeZenHTTP(pool(20))
        current = engine()
        with patch("avito_service.apify.urlopen", side_effect=fake):
            current.search(request())
            self.assertEqual(len(current.market_cache.get(shared_key(current, request()))), 20)
            searches_after_bounded_fill = len(fake.searches)
            fake.rows = pool(60)
            result = current.search(request()).public_dict()
        self.assertGreater(searches_after_bounded_fill, 0)
        self.assertEqual(len(fake.searches), searches_after_bounded_fill)
        self.assertEqual(len(current.market_cache.get(shared_key(current, request()))), 20)
        self.assertEqual(len(result["recommendations"]), 3)

    def test_stale_source_cache_is_recollected(self):
        fake = FakeZenHTTP(pool())
        current = engine()
        with patch("avito_service.apify.urlopen", side_effect=fake):
            current.search(request())
            key = shared_key(current, request())
            old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            cached = current.market_cache.get(key)
            current.market_cache.put(key, tuple(replace(item, listing=replace(item.listing, collected_at=old_time))
                                               for item in cached))
            current.search(request())
        self.assertEqual(len(fake.searches), 2)

    def test_changed_model_on_refresh_never_becomes_a_shared_pool_recommendation(self):
        fake = FakeZenHTTP(pool())
        fake.refresh_changes = {str(row["id"]): {"title": "Sony PlayStation 4"} for row in fake.rows}
        with patch("avito_service.apify.urlopen", side_effect=fake):
            result = engine().search(request()).public_dict()
        self.assertEqual(len(fake.searches), 1)
        self.assertEqual(len(fake.refreshes), 1)
        self.assertEqual(result["recommendations"], [])


if __name__ == "__main__":
    unittest.main()
