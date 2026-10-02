"""Offline regressions for owner-only source diagnostics and strict public output."""
from __future__ import annotations

from copy import deepcopy
import time
import unittest
from unittest.mock import Mock

from avito_service.errors import ExternalServiceError
from avito_service.jobs import SearchJob, SearchJobRegistry
from avito_service.service import AvitoAnalysisService
from tools.test_avito_collection_quality import EvidenceProvider, EvidenceReviewer, request, row, service


def preview(rows):
    reviewer = EvidenceReviewer()
    engine = AvitoAnalysisService(None, reviewer, ai_max_cost_rub=0)
    report = engine.analyze_dataset(rows, request()).public_dict(include_admin=True)
    return report, reviewer


class AvitoDiscoveryTests(unittest.TestCase):
    def test_zero_ai_preserves_real_listings_only_for_owner(self):
        rows = [row(10000000 + index, 28_000 + index * 1000) for index in range(3)]
        result, reviewer = preview(rows)
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["verifiedCount"], 0)
        self.assertEqual(result["visibleCount"], len(result["recommendations"]))
        self.assertEqual(result["discoveredListings"], [])
        self.assertEqual({item["listing"]["url"] for item in result["adminDiscoveredListings"]},
                         {item["url"] for item in rows})
        self.assertTrue(all(item["discoveryStatus"] == "needs_review" for item in result["adminDiscoveredListings"]))
        self.assertEqual(reviewer.text_calls, [])
        self.assertEqual(reviewer.photo_calls, [])

    def test_conditional_price_remains_in_owner_diagnostics_with_risk(self):
        candidate = row(10000000, 28_000)
        candidate["description"] = "Цена 28000 только при оформлении кредита. За наличные цена другая."
        result, _ = preview([candidate])
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["visibleCount"], 0)
        self.assertEqual(result["discoveredListings"], [])
        card = result["adminDiscoveredListings"][0]
        self.assertEqual(card["listing"]["url"], candidate["url"])
        self.assertEqual(card["discoveryStatus"], "has_risks")
        self.assertTrue(card.get("reasons") or card.get("risks"))
        self.assertFalse(card["belowMarket"])

    def test_verified_result_is_not_filled_with_owner_diagnostics(self):
        provider = EvidenceProvider()
        provider.candidate_rows[1]["description"] = "Цена только при оформлении кредита."
        provider.candidate_rows[2]["description"] = "Камера не работает."
        reviewer = EvidenceReviewer()
        result = service(provider, reviewer).search(request()).public_dict(include_admin=True)
        self.assertEqual([item["listing"]["id"] for item in result["recommendations"]], ["10000000"])
        self.assertEqual(result["verifiedCount"], 1)
        self.assertEqual(result["visibleCount"], len(result["recommendations"]))
        self.assertEqual(result["discoveredListings"], [])
        self.assertEqual({item["listing"]["id"] for item in result["adminDiscoveredListings"]}, {"10000001", "10000002"})
        self.assertTrue(all(item["discoveryStatus"] == "has_risks" for item in result["adminDiscoveredListings"]))
        self.assertEqual(reviewer.photo_calls, ["10000000"])

    def test_wrong_model_is_owner_only_with_mismatch_status(self):
        wrong = row(10000000, 25_000)
        wrong["title"] = "iPhone 12, 128 ГБ"
        wrong["parameters"]["Модель"] = "iPhone 12"
        matching = row(10000001, 30_000)
        result, _ = preview([wrong, matching])
        cards = result["adminDiscoveredListings"]
        self.assertEqual([item["listing"]["id"] for item in cards], ["10000001", "10000000"])
        self.assertEqual(cards[1]["discoveryStatus"], "mismatch")
        self.assertTrue(cards[1].get("reasons") or cards[1].get("risks"))
        self.assertEqual(result["recommendations"], [])

    def test_invalid_or_absent_direct_links_do_not_create_cards(self):
        for url in ("", "https://example.com/item_10000000", "https://www.avito.ru/moskva/telefony", "https://www.avito.ru/all?q=iphone"):
            with self.subTest(url=url):
                candidate = row(10000000)
                candidate["url"] = url
                result, _ = preview([candidate])
                self.assertEqual(result["recommendations"], [])
                self.assertEqual(result["adminDiscoveredListings"], [])
                self.assertEqual(result["visibleCount"], 0)
                self.assertEqual(result["verifiedCount"], 0)
        empty, _ = preview([])
        self.assertEqual(empty["adminDiscoveredListings"], [])
        self.assertEqual(empty["visibleCount"], 0)

    def test_duplicate_identifiers_and_direct_links_are_shown_once(self):
        first = row(10000000, 28_000)
        same_id = deepcopy(first)
        same_id["price"] = 29_000
        same_url = row(10000001, 30_000)
        same_url["url"] = first["url"]
        other = row(10000002, 31_000)
        result, _ = preview([first, same_id, same_url, other])
        cards = result["adminDiscoveredListings"]
        self.assertEqual(len(cards), 2)
        self.assertEqual(len({item["listing"]["id"] for item in cards}), 2)
        self.assertEqual({item["listing"]["url"] for item in cards}, {first["url"], other["url"]})

    def test_discovery_never_claims_verified_bargain_or_savings(self):
        result, _ = preview([row(10000000, 25_000)])
        card = result["adminDiscoveredListings"][0]
        self.assertIs(card["belowMarket"], False)
        for field in ("marketMedian", "savingAmount", "savingPercent"):
            self.assertIsNone(card.get(field))
        self.assertFalse(card.get("marketEvidence"))
        self.assertNotIn(card.get("role"), {"TOP", "TOP1", "BEST", "BACKUP", "BUDGET"})
        self.assertNotEqual(card["listing"].get("verificationStatus"), "verified")
        self.assertEqual(result["recommendations"], [])

    def test_inactive_page_remains_visible_last_with_inactive_status(self):
        inactive = row(10000000, 25_000)
        inactive["status"] = "removed"
        active = row(10000001, 30_000)
        result, _ = preview([inactive, active])
        cards = result["adminDiscoveredListings"]
        self.assertEqual([item["listing"]["id"] for item in cards], ["10000001", "10000000"])
        self.assertEqual(cards[1]["discoveryStatus"], "inactive")
        self.assertTrue(cards[1].get("reasons") or cards[1].get("risks"))
        self.assertEqual(result["recommendations"], [])

    def test_fresh_matching_candidates_come_first_then_lower_price(self):
        stale = row(10000000, 25_000, observed_at="2020-01-01T00:00:00+00:00")
        expensive = row(10000001, 30_000)
        cheaper = row(10000002, 28_000)
        result, _ = preview([stale, expensive, cheaper])
        self.assertEqual([item["listing"]["id"] for item in result["adminDiscoveredListings"]],
                         ["10000002", "10000001", "10000000"])

    def test_empty_candidate_collection_preserves_real_market_source_cards(self):
        provider = EvidenceProvider()
        provider.candidate_rows = []
        result = service(provider).search(request()).public_dict(include_admin=True)
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["verifiedCount"], 0)
        self.assertEqual(result["visibleCount"], len(result["recommendations"]))
        self.assertEqual(result["discoveredListings"], [])
        cards = result["adminDiscoveredListings"]
        self.assertEqual({item["listing"]["id"] for item in cards}, {"20000000", "20000001", "20000002"})
        self.assertTrue(all(item["discoveryStatus"] == "mismatch" for item in cards))
        self.assertTrue(all(item["listing"]["price"] > request().price_max for item in cards))
        self.assertEqual(len(provider.market_calls), 1)
        self.assertEqual(len(provider.candidate_calls), 1)
        self.assertEqual(provider.refresh_calls, [])

    def test_ai_provider_failure_preserves_sources_without_new_collection_calls(self):
        class FailingReviewer(EvidenceReviewer):
            def review_text(self, listing, search=None):
                self.text_calls.append((listing.listing_id, search))
                raise ExternalServiceError("Offline AI provider failure", code="AI_UNAVAILABLE")

        provider = Mock()
        reviewer = FailingReviewer()
        engine = AvitoAnalysisService(provider, reviewer)
        rows = [row(10000000 + index, 28_000 + index * 1000) for index in range(3)]
        result = engine.analyze_dataset(rows, request()).public_dict(include_admin=True)
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["verifiedCount"], 0)
        self.assertEqual(result["visibleCount"], len(result["recommendations"]))
        self.assertEqual(result["discoveredListings"], [])
        self.assertEqual({item["listing"]["id"] for item in result["adminDiscoveredListings"]},
                         {item["id"] for item in rows})
        self.assertEqual(len(reviewer.text_calls), 1)
        self.assertEqual(reviewer.photo_calls, [])
        provider.collect.assert_not_called()
        provider.collect_market.assert_not_called()
        provider.refresh.assert_not_called()

    def test_optional_credit_mention_does_not_create_conditional_price_risk(self):
        candidate = row(10000000, 28_000)
        candidate["description"] = "Цена окончательная. Можно оформить кредит."
        result, _ = preview([candidate])
        card = result["adminDiscoveredListings"][0]
        self.assertEqual(card["discoveryStatus"], "needs_review")
        self.assertNotIn("NON_FINAL_PRICE", {risk["code"] for risk in card["ruleFindings"]})

    def test_market_ai_failure_preserves_paid_sources_and_stops_further_calls(self):
        class MarketFailureReviewer(EvidenceReviewer):
            def review_text(self, listing, search=None):
                self.text_calls.append((listing.listing_id, search))
                raise ExternalServiceError("Offline market AI failure", code="AI_UNAVAILABLE")

        provider = EvidenceProvider()
        reviewer = MarketFailureReviewer()
        report = service(provider, reviewer).search(request())
        result = report.public_dict(include_admin=True)
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["verifiedCount"], 0)
        self.assertEqual(result["visibleCount"], len(result["recommendations"]))
        self.assertEqual(result["discoveredListings"], [])
        self.assertEqual({item["listing"]["id"] for item in result["adminDiscoveredListings"]},
                         {"20000000", "20000001", "20000002"})
        self.assertEqual(len(provider.market_calls), 1)
        self.assertEqual(provider.candidate_calls, [])
        self.assertEqual(provider.refresh_calls, [])
        self.assertEqual(len(reviewer.text_calls), 1)
        self.assertEqual(reviewer.photo_calls, [])
        self.assertAlmostEqual(report.costs.apify_cost_usd, provider.market_cost)
        self.assertTrue(report.costs.ai_cost_estimated)
        self.assertGreater(report.costs.ai_cost_rub, 0)
        self.assertTrue(any("AI" in warning for warning in result["adminWarnings"]))

    def test_candidate_collection_failure_keeps_market_without_retry_or_refresh(self):
        class CandidateFailureProvider(EvidenceProvider):
            def collect(self, request, *, deadline_at=None, max_charge_usd=None):
                self.candidate_calls.append((request, max_charge_usd))
                raise ExternalServiceError("Offline candidate collection failure", code="APIFY_UNAVAILABLE")

        provider = CandidateFailureProvider()
        reviewer = EvidenceReviewer()
        report = service(provider, reviewer).search(request())
        result = report.public_dict(include_admin=True)
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(result["verifiedCount"], 0)
        self.assertEqual(result["visibleCount"], len(result["recommendations"]))
        self.assertEqual(result["discoveredListings"], [])
        self.assertEqual({item["listing"]["id"] for item in result["adminDiscoveredListings"]},
                         {"20000000", "20000001", "20000002"})
        self.assertEqual(len(provider.market_calls), 1)
        self.assertEqual(len(provider.candidate_calls), 1)
        self.assertEqual(provider.refresh_calls, [])
        self.assertEqual(len(reviewer.text_calls), len(provider.market_rows))
        self.assertEqual(reviewer.photo_calls, [])
        self.assertTrue(report.costs.apify_cost_estimated)
        self.assertAlmostEqual(report.costs.apify_cost_usd, provider.market_cost + provider.candidate_calls[0][1])
        self.assertTrue(result["warnings"])

    def test_photo_failure_retains_completed_result_and_stops_later_photos(self):
        class PhotoFailureReviewer(EvidenceReviewer):
            def review_photos(self, listing, text_review):
                if self.photo_calls:
                    self.photo_calls.append(listing.listing_id)
                    raise ExternalServiceError("Offline photo AI failure", code="AI_UNAVAILABLE")
                return super().review_photos(listing, text_review)

        provider = EvidenceProvider()
        reviewer = PhotoFailureReviewer()
        engine = service(provider, reviewer)
        report = engine.search(request())
        result = report.public_dict(include_admin=True)
        self.assertEqual([item["listing"]["id"] for item in result["recommendations"]], ["10000000"])
        self.assertEqual(result["verifiedCount"], 1)
        self.assertEqual(result["visibleCount"], len(result["recommendations"]))
        self.assertEqual(result["discoveredListings"], [])
        self.assertEqual({item["listing"]["id"] for item in result["adminDiscoveredListings"]}, {"10000001", "10000002"})
        self.assertTrue(all(item["belowMarket"] is False for item in result["adminDiscoveredListings"]))
        self.assertEqual(reviewer.photo_calls, ["10000000", "10000001"])
        self.assertEqual(len(provider.market_calls), 1)
        self.assertEqual(len(provider.candidate_calls), 1)
        self.assertEqual(len(provider.refresh_calls), 1)
        self.assertEqual(provider.refresh_calls[0][0], ("10000000",))
        self.assertTrue(report.costs.ai_cost_estimated)
        self.assertLessEqual(report.costs.ai_cost_rub, engine.ai_max_cost_rub + 1e-9)

    def test_completed_job_reports_found_count_without_claiming_best_or_verified(self):
        engine = AvitoAnalysisService(None, EvidenceReviewer(), ai_max_cost_rub=0)
        registry = SearchJobRegistry(engine)
        now = time.monotonic()
        job = SearchJob("offline-discovery", started_at=now, deadline_at=now + 30)
        rows = [row(10000000 + index, 28_000 + index * 1000) for index in range(3)]
        registry._run(job, request(), rows)
        snapshot = job.snapshot()
        self.assertEqual(snapshot["state"], "complete")
        self.assertFalse(snapshot["workerAlive"])
        self.assertEqual(snapshot["result"]["recommendations"], [])
        self.assertEqual(snapshot["result"]["visibleCount"], 0)
        self.assertEqual(snapshot["result"]["discoveredListings"], [])
        self.assertEqual(len(job.snapshot(include_admin=True)["result"]["adminDiscoveredListings"]), 3)
        self.assertEqual(snapshot["result"]["verifiedCount"], 0)
        self.assertIn("найдено: 0", snapshot["message"].casefold())
        self.assertIn("проверенных рекомендаций: 0", snapshot["message"].casefold())
        self.assertNotIn("лучш", snapshot["message"].casefold())


if __name__ == "__main__":
    unittest.main()
