#!/usr/bin/env python3
"""Offline integration tests for collection, broad market evidence and rechecks."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig
from avito_service.errors import ExternalServiceError
from avito_service.market_cache import MarketSnapshotCache
from avito_service.models import AIReview, CollectionBatch, ReviewVerdict, SearchRequest
from avito_service.service import AvitoAnalysisService


def row(identifier: int, price: int = 30_000, *, observed_at: str | None = None) -> dict:
    return {
        "id": str(identifier), "title": "iPhone 13, 128 ГБ",
        "url": f"https://www.avito.ru/moskva/telefony/iphone_13_{identifier}",
        "price": price, "currency": "RUB", "status": "active", "stock": "В наличии",
        "description": "Телефон исправен. Аккумулятор 95%. Не ремонтировался. Все детали оригинальные. Полный комплект. Цена окончательная.",
        "images": [f"https://01.img.avito.st/phone-{identifier}.jpg"], "imageCount": 1,
        "parameters": {
            "Модель": "iPhone 13", "Встроенная память": "128 ГБ", "SIM-карты": "SIM + eSIM",
            "Состояние": "Отличное", "История смартфона": "Активированный",
            "Состояние аккумулятора": "95%", "Ремонт": "Не ремонтировался",
            "Оригинальность деталей": "Оригинальные", "Комплектация": "Полный комплект",
        },
        "seller": {"id": f"seller-{identifier}", "name": f"Продавец {identifier}", "sellerType": "private", "ratingScore": 4.9, "reviewCount": 20},
        "location": "Москва", "deliveryAvailable": True,
        "scrapedAt": observed_at or datetime.now(timezone.utc).isoformat(),
    }


class EvidenceReviewer:
    text_cost = 0.1
    photo_cost = 0.2

    def __init__(self):
        self.text_calls = []
        self.photo_calls = []

    def review_text(self, listing, search=None):
        self.text_calls.append((listing.listing_id, search))
        return AIReview(
            listing_id=listing.listing_id, text_analyzed=True, photos_analyzed=False,
            identified_model=listing.model, storage=listing.storage, sim_variant=listing.sim_variant,
            condition=listing.condition, matches_request=True, verdict=ReviewVerdict.APPROVE,
            confidence=0.95, cost_rub=self.text_cost, request_count=1,
        )

    def review_photos(self, listing, text_review):
        self.photo_calls.append(listing.listing_id)
        return replace(text_review, photos_analyzed=True, photo_coverage=tuple(range(1, len(listing.images) + 1)),
                       cost_rub=text_review.cost_rub + self.photo_cost, request_count=text_review.request_count + 1)


class EvidenceProvider:
    supports_collection_budget = True
    supports_deadline = True

    def __init__(self):
        self.market_rows = [row(20000000 + index, 40_000 + index * 100) for index in range(10)]
        self.candidate_rows = [row(10000000 + index, 28_000 + index * 1000) for index in range(3)]
        self.market_calls = []
        self.candidate_calls = []
        self.refresh_calls = []
        self.refresh_changes = {}
        self.refresh_error = False
        self.return_list = False
        self.market_cost = 0.05
        self.candidate_cost = 0.03
        self.refresh_cost = 0.02

    def collect_market(self, request, *, deadline_at=None, max_charge_usd=None):
        self.market_calls.append((request, max_charge_usd))
        return CollectionBatch(tuple(deepcopy(self.market_rows)), self.market_cost, False)

    def collect(self, request, *, deadline_at=None, max_charge_usd=None):
        self.candidate_calls.append((request, max_charge_usd))
        if self.return_list:
            return deepcopy(self.candidate_rows)
        return CollectionBatch(tuple(deepcopy(self.candidate_rows)), self.candidate_cost, False)

    def refresh(self, listings, *, deadline_at=None, max_charge_usd=None):
        self.refresh_calls.append((tuple(item.listing_id for item in listings), max_charge_usd))
        if self.refresh_error:
            raise ExternalServiceError("Offline injected refresh error")
        current = {item["id"]: deepcopy(item) for item in self.candidate_rows}
        results = []
        for listing in listings:
            value = current.get(listing.listing_id)
            if value is None:
                continue
            value.update(self.refresh_changes.get(listing.listing_id, {}))
            value["scrapedAt"] = datetime.now(timezone.utc).isoformat()
            results.append(value)
        return CollectionBatch(tuple(results), self.refresh_cost, False)


def request(*, mode="bargain"):
    return SearchRequest("iPhone 13", location="Москва", category="phones", max_results=20,
                         mode=mode,
                         desired_results=3, price_min=25_000, price_max=31_000,
                         required_storage="128 GB", required_sim="SIM + eSIM", required_condition="Отличное")


def service(provider, reviewer=None, cache=None):
    return AvitoAnalysisService(provider, reviewer or EvidenceReviewer(), ai_text_batch_size=5,
                                ai_text_max_listings=20, ai_max_listings=6, ai_concurrency=1,
                                report_max_cost_rub=50, ai_max_cost_rub=15, usd_rub_rate=50,
                                market_cache=cache)


class AvitoCollectionQualityTests(unittest.TestCase):
    def test_completed_text_rejections_are_not_reported_as_unfinished_ai(self):
        class RejectingReviewer(EvidenceReviewer):
            def review_text(self, listing, search=None):
                return replace(super().review_text(listing, search),
                               verdict=ReviewVerdict.REJECT, matches_request=False,
                               conflicts=("Модель не совпадает с запросом",))

        reviewer = RejectingReviewer()
        report = service(None, reviewer).analyze_dataset([row(91000001)], request(mode="find"))
        self.assertEqual(len(reviewer.text_calls), 1)
        self.assertEqual(reviewer.photo_calls, [])
        self.assertFalse(any("незавершённым AI" in value for value in report.admin_warnings))
        self.assertEqual(report.public_dict()["recommendations"], [])

    def test_conditional_price_rejection_is_not_an_ai_failure(self):
        class ConditionalReviewer(EvidenceReviewer):
            def review_text(self, listing, search=None):
                return replace(super().review_text(listing, search),
                               price_conditions=("Цена при обмене старого телефона",))

        reviewer = ConditionalReviewer()
        report = service(None, reviewer).analyze_dataset([row(91000002)], request(mode="find"))
        self.assertEqual(reviewer.photo_calls, [])
        self.assertFalse(any("незавершённым AI" in value for value in report.admin_warnings))
        self.assertEqual(report.public_dict()["recommendations"], [])

    def test_real_text_failure_still_warns_about_unfinished_ai(self):
        class FailedReviewer(EvidenceReviewer):
            def review_text(self, listing, search=None):
                return replace(super().review_text(listing, search), text_analyzed=False,
                               verdict=ReviewVerdict.CAUTION, error="AI_TIMEOUT")

        report = service(None, FailedReviewer()).analyze_dataset([row(91000003)], request(mode="find"))
        public = report.public_dict()
        self.assertTrue(any("незавершённым AI" in value for value in report.public_dict(include_admin=True)["adminWarnings"]))
        self.assertIn("не завершена", public["emptyReason"])
        self.assertEqual(public["recommendations"], [])
        self.assertEqual(public["discoveredListings"], [])

    def test_stock_values_do_not_block_description_price_then_photo_flow(self):
        class RecordingReviewer(EvidenceReviewer):
            def __init__(self):
                super().__init__()
                self.events = []

            def review_text(self, listing, search=None):
                self.events.append(("text", listing.listing_id, listing.description, listing.price))
                return super().review_text(listing, search)

            def review_photos(self, listing, text_review):
                self.events.append(("photo", listing.listing_id, listing.description, listing.price))
                return super().review_photos(listing, text_review)

        for stock in ("__missing__", None, "", "Нет в наличии", "под заказ", "В наличии: 0"):
            with self.subTest(stock=stock):
                provider = EvidenceProvider()
                for value in provider.candidate_rows:
                    if stock == "__missing__":
                        value.pop("stock", None)
                    else:
                        value["stock"] = stock
                reviewer = RecordingReviewer()
                report = service(provider, reviewer).search(request(mode="find"))
                visible = report.public_dict()["recommendations"]
                self.assertEqual({item["listing"]["id"] for item in visible}, {"10000000", "10000001", "10000002"})
                candidate_events = [event for event in reviewer.events if event[1].startswith("100")]
                self.assertEqual([event[0] for event in candidate_events], ["text"] * 3 + ["photo"] * 3)
                by_id = {value["id"]: value for value in provider.candidate_rows}
                for _, identifier, description, price in candidate_events:
                    self.assertEqual(description, by_id[identifier]["description"])
                    self.assertEqual(price, by_id[identifier]["price"])

    def test_market_without_stock_still_provides_independent_price_references(self):
        provider = EvidenceProvider()
        for value in provider.market_rows:
            value.pop("stock", None)
        report = service(provider).search(request())
        best = report.public_dict()["recommendations"][0]
        self.assertTrue(best["belowMarket"])
        self.assertEqual(best["comparableSellerCount"], 12)
        self.assertEqual(len(best["marketEvidence"]), 12)

    def test_refresh_stock_change_alone_does_not_drop_finalists(self):
        provider = EvidenceProvider()
        provider.refresh_changes = {
            "10000000": {"stock": "Нет в наличии"},
            "10000001": {"stock": ""},
            "10000002": {"stock": "под заказ"},
        }
        visible = service(provider).search(request(mode="find")).public_dict()["recommendations"]
        self.assertEqual({item["listing"]["id"] for item in visible}, {"10000000", "10000001", "10000002"})
        self.assertTrue(all(item["listing"]["verificationStatus"] == "verified" for item in visible))

    def test_refresh_stock_parameter_changes_preserve_finalists_and_cached_reviews(self):
        provider = EvidenceProvider()
        for value in provider.candidate_rows:
            product_parameters = dict(value["parameters"])
            value["parameters"].update({"Наличие": "В наличии", "Остаток": "2"})
            provider.refresh_changes[value["id"]] = {
                "parameters": {**product_parameters, "Наличие": "Нет в наличии", "stock": "unknown", "availability": "под заказ"}
            }
        reviewer = EvidenceReviewer()
        engine = service(provider, reviewer)
        first = engine.search(request(mode="find")).public_dict()["recommendations"]
        expected_ids = {"10000000", "10000001", "10000002"}
        self.assertEqual({item["listing"]["id"] for item in first}, expected_ids)
        self.assertTrue(all(item["listing"]["verificationStatus"] == "verified" for item in first))
        call_counts = (len(reviewer.text_calls), len(reviewer.photo_calls))
        for value in provider.candidate_rows:
            value["parameters"] = deepcopy(provider.refresh_changes[value["id"]]["parameters"])
        second = engine.search(request(mode="find")).public_dict()["recommendations"]
        self.assertEqual({item["listing"]["id"] for item in second}, expected_ids)
        self.assertEqual((len(reviewer.text_calls), len(reviewer.photo_calls)), call_counts)
        self.assertTrue(all(item["listing"]["verificationStatus"] == "verified" for item in second))

    def test_removed_page_and_hardware_defect_still_stop_before_photos(self):
        provider = EvidenceProvider()
        provider.candidate_rows[0].update({"status": "removed", "stock": "В наличии"})
        provider.candidate_rows[1].update({"description": "Камера не работает.", "stock": ""})
        reviewer = EvidenceReviewer()
        visible = service(provider, reviewer).search(request(mode="find")).public_dict()["recommendations"]
        self.assertEqual([item["listing"]["id"] for item in visible], ["10000002"])
        self.assertEqual(reviewer.photo_calls, ["10000002"])
        self.assertNotIn("10000000", [identifier for identifier, _ in reviewer.text_calls])
        self.assertNotIn("10000001", [identifier for identifier, _ in reviewer.text_calls])

    def test_delayed_actor_billing_cannot_spend_same_reservation_twice(self):
        provider = EvidenceProvider()
        provider.market_cost = provider.candidate_cost = 0.005
        engine = service(provider)
        report = engine.search(request(mode="find"))
        self.assertTrue(report.public_dict()["recommendations"])
        allowances = [provider.market_calls[0][1], provider.candidate_calls[0][1], provider.refresh_calls[0][1]]
        cap = (engine.report_max_cost_rub - engine.ai_max_cost_rub) / engine.usd_rub_rate
        self.assertLessEqual(sum(allowances), cap + 1e-9)

    def test_separate_broad_market_and_budget_candidate_search(self):
        provider = EvidenceProvider()
        provider.candidate_rows.append(row(10000009, 90_000))
        reviewer = EvidenceReviewer()
        report = service(provider, reviewer).search(request())
        broad = provider.market_calls[0][0]
        self.assertIsNone(broad.price_min)
        self.assertIsNone(broad.price_max)
        candidate = provider.candidate_calls[0][0]
        self.assertEqual((candidate.price_min, candidate.price_max), (25_000, 31_000))
        self.assertNotIn("10000009", [identifier for identifier, _ in reviewer.text_calls])
        visible = report.public_dict()["recommendations"]
        self.assertEqual(len(visible), 3)
        self.assertTrue(visible[0]["belowMarket"])
        self.assertEqual(visible[0]["comparableSellerCount"], 12)
        self.assertGreater(visible[0]["marketMedian"], request().price_max)
        self.assertTrue(all(item["listing"]["verificationStatus"] == "verified" for item in visible))

    def test_sold_or_changed_product_evidence_is_removed_after_refresh(self):
        provider = EvidenceProvider()
        provider.refresh_changes = {
            "10000000": {"status": "removed", "stock": "Продано"},
            "10000001": {"description": "Вместо прежнего телефона предлагается другая комплектация."},
        }
        report = service(provider).search(request(mode="find"))
        self.assertEqual([item["listing"]["id"] for item in report.public_dict()["recommendations"]], ["10000002"])

    def test_refreshed_price_is_used_and_budget_is_checked_again(self):
        provider = EvidenceProvider()
        provider.refresh_changes = {"10000000": {"price": 35_000}, "10000001": {"price": 27_000}}
        visible = service(provider).search(request()).public_dict()["recommendations"]
        self.assertNotIn("10000000", [item["listing"]["id"] for item in visible])
        updated = next(item for item in visible if item["listing"]["id"] == "10000001")
        self.assertEqual(updated["listing"]["price"], 27_000)
        self.assertEqual(updated["savingAmount"], updated["marketMedian"] - 27_000)

    def test_stale_import_cannot_become_a_customer_recommendation(self):
        stale = row(12345678, observed_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
        stale.update({"verificationStatus": "verified", "verifiedAt": datetime.now(timezone.utc).isoformat()})
        report = service(None).analyze_dataset([stale], request(mode="find"))
        self.assertEqual(report.public_dict()["recommendations"], [])

    def test_old_reference_sample_is_not_analyzed_or_used_as_current_market(self):
        provider = EvidenceProvider()
        stale_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        for value in provider.market_rows:
            value["scrapedAt"] = stale_at
        reviewer = EvidenceReviewer()
        report = service(provider, reviewer).search(request())
        self.assertFalse(any(identifier.startswith("200") for identifier, _ in reviewer.text_calls))
        public = report.public_dict()
        self.assertEqual(public["visibleCount"], 3)
        self.assertEqual(public["discoveredListings"], [])
        self.assertEqual(public["resultPolicy"], "verified_exact_matches_with_optional_savings")
        diagnostics = report.public_dict(include_admin=True)["adminRecommendations"]
        self.assertEqual(len(diagnostics), 3)
        self.assertTrue(all(not item["belowMarket"] and not item["belowComparables"]
                            and item["comparableCount"] == 2 for item in diagnostics))
        self.assertTrue(all("/iphone_13_200" not in reference["url"]
                            for item in diagnostics for reference in item["marketEvidence"]))

    def test_cache_reuses_analysis_but_never_skips_collection_or_final_refresh(self):
        provider = EvidenceProvider()
        reviewer = EvidenceReviewer()
        pipeline = service(provider, reviewer, MarketSnapshotCache())
        first = pipeline.search(request(mode="find"))
        self.assertEqual(len(first.public_dict()["recommendations"]), 3)
        text_calls, photo_calls = len(reviewer.text_calls), len(reviewer.photo_calls)
        provider.refresh_changes = {"10000000": {"status": "removed", "stock": "Продано"}}
        second = pipeline.search(request(mode="find"))
        self.assertEqual(len(provider.market_calls), 1)
        self.assertEqual(len(provider.candidate_calls), 2)
        self.assertEqual(len(provider.refresh_calls), 2)
        self.assertEqual((len(reviewer.text_calls), len(reviewer.photo_calls)), (text_calls, photo_calls))
        self.assertEqual(second.costs.ai_cost_rub, 0)
        self.assertEqual(len(second.public_dict()["recommendations"]), 2)

    def test_total_cost_includes_broad_collection_text_photos_and_refresh(self):
        provider = EvidenceProvider()
        reviewer = EvidenceReviewer()
        report = service(provider, reviewer).search(request(mode="find"))
        expected_collection = provider.market_cost + provider.candidate_cost + provider.refresh_cost
        expected_ai = len(reviewer.text_calls) * reviewer.text_cost + len(reviewer.photo_calls) * reviewer.photo_cost
        self.assertAlmostEqual(report.costs.apify_cost_usd, expected_collection)
        self.assertAlmostEqual(report.costs.ai_cost_rub, expected_ai)
        self.assertAlmostEqual(report.costs.estimated_total_rub, expected_collection * 50 + expected_ai)
        self.assertTrue(report.costs.within_budget)

    def test_each_collection_allowance_fits_remaining_shared_collection_budget(self):
        provider = EvidenceProvider()
        service(provider).search(request(mode="find"))
        allowance = (50 - 15) / 50
        self.assertLessEqual(provider.market_calls[0][1], allowance)
        self.assertLessEqual(provider.market_cost + provider.candidate_calls[0][1], allowance)
        self.assertLessEqual(provider.market_cost + provider.candidate_cost + provider.refresh_calls[0][1], allowance)

    def test_failed_refresh_is_counted_and_cannot_issue_fresh_cards(self):
        provider = EvidenceProvider()
        provider.refresh_error = True
        report = service(provider).search(request(mode="find"))
        self.assertEqual(report.public_dict()["recommendations"], [])
        self.assertTrue(report.costs.apify_cost_estimated)
        self.assertAlmostEqual(report.costs.apify_cost_usd, provider.market_cost + provider.candidate_cost + provider.refresh_calls[0][1])

    def test_legacy_list_collector_still_accounts_for_paid_market_stage(self):
        provider = EvidenceProvider()
        provider.return_list = True
        report = service(provider).search(request(mode="find"))
        self.assertAlmostEqual(report.costs.apify_cost_usd, provider.market_cost + provider.refresh_cost)

    def test_apify_does_not_relabel_an_old_provider_snapshot_as_fresh(self):
        stale_at = "2020-01-01T00:00:00+00:00"

        class OfflineZen(ZenStudioProvider):
            def _json_request(self, method, url, *, payload=None, timeout=130):
                if method == "POST":
                    return {"data": {"id": "offline-run", "status": "SUCCEEDED", "defaultDatasetId": "offline-data", "usageTotalUsd": 0.01}}
                return [row(12345678, observed_at=stale_at)]

        batch = OfflineZen(ServiceConfig()).collect_market(request())
        self.assertEqual(batch.items[0]["scrapedAt"], stale_at)


if __name__ == "__main__":
    unittest.main()
