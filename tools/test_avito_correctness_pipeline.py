from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import unittest

from avito_service.market_cache import MarketSnapshotCache
from avito_service.models import (
    AIReview, AnalysisReport, AnalyzedListing, CollectionBatch,
    ReviewVerdict, SearchRequest,
)
from avito_service.normalization import normalize_listing
from avito_service.ranking import is_customer_safe, rank_listings
from avito_service.request_intent import check_request_compatibility
from avito_service.risk_rules import evaluate_rules
from avito_service.service import AvitoAnalysisService
from avito_service.verification import VerificationState, evaluate_verification
from tools.diagnose_avito_decisions import DEFAULT_SNAPSHOT, load_snapshot, replay
from tools.test_avito_service import CompleteReviewer, raw_listing


def ps5_listing(*, model: str = "PlayStation 5 Slim", completeness: bool = False):
    row = raw_listing(
        91000001, model=model, storage="1 ТБ", sim="", condition="Отличное",
        description="Игровая консоль Sony. Цена окончательная. Можно проверить до оплаты.",
    )
    row["title"] = model
    row["url"] = f"https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/ps5_{row['id']}"
    row["location"] = "Ярославль"
    row["deliveryAvailable"] = False
    row["parameters"] = [
        value for value in row["parameters"]
        if value["name"] not in {"SIM-карты", "История смартфона"}
        and (completeness or value["name"] != "Комплектация")
    ]
    return normalize_listing(row)


class AvitoCorrectnessPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = SearchRequest(
            "ps5", location="Ярославль", category="gaming", mode="find",
            max_results=20, desired_results=3, pickup_only=True,
        )

    def test_unknown_kit_routes_to_photo_and_positive_photo_can_resolve_it(self) -> None:
        raw = raw_listing(
            91000001, model="PlayStation 5 Slim", storage="1 ТБ", sim="",
            condition="Отличное",
            description="Игровая консоль Sony. Цена окончательная. Можно проверить до оплаты.",
        )
        raw.update(
            title="PlayStation 5 Slim", location="Ярославль", deliveryAvailable=False,
            url="https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/ps5_91000001",
        )
        raw["parameters"] = [item for item in raw["parameters"] if item["name"] != "Комплектация"]
        listing = normalize_listing(raw)
        text = CompleteReviewer().review_text(listing, self.request)
        risks = evaluate_rules(listing)
        decision = evaluate_verification(listing, risks, text, self.request, stage="text")
        self.assertEqual(decision.state, VerificationState.NEEDS_EVIDENCE)
        self.assertEqual(decision.next_stage, "photo_ai")

        service = AvitoAnalysisService(None, CompleteReviewer(), ai_max_listings=10)
        ids = service._photo_candidate_ids(
            (listing,), {listing.listing_id: risks}, {listing.listing_id: text},
            6, self.request,
        )
        self.assertEqual(ids, (listing.listing_id,))

        photo = replace(
            text, photos_analyzed=True, photo_coverage=(1, 2),
            photo_condition_evidence="pass", photo_completeness_evidence="pass",
        )
        final = evaluate_verification(listing, risks, photo, self.request, stage="final")
        self.assertEqual(final.state, VerificationState.PASS)

    def test_unknown_after_photo_and_confirmed_blockers_never_pass(self) -> None:
        listing = ps5_listing(completeness=False)
        text = CompleteReviewer().review_text(listing, self.request)
        risks = evaluate_rules(listing)
        unresolved = replace(text, photos_analyzed=True, photo_coverage=(1, 2))
        self.assertEqual(
            evaluate_verification(listing, risks, unresolved, self.request, stage="final").state,
            VerificationState.FAIL,
        )
        for blocked in (
            replace(text, defects=("Трещина корпуса",)),
            replace(text, price_conditions=("Цена только при обмене",)),
            replace(text, matches_request=False, mismatch_reason="Другая модель"),
        ):
            with self.subTest(blocked=blocked):
                self.assertEqual(
                    evaluate_verification(listing, risks, blocked, self.request, stage="text").state,
                    VerificationState.FAIL,
                )

    def test_unresolved_photo_evidence_blocks_final_refresh(self) -> None:
        class RefreshProbe:
            def __init__(self):
                self.calls = []

            def refresh(self, listings, **kwargs):
                self.calls.append(tuple(item.listing_id for item in listings))
                return None

        listing = ps5_listing(completeness=True)
        listing = replace(
            listing, verification_status="verified", verified_at=listing.collected_at,
        )
        text = replace(
            CompleteReviewer().review_text(listing, self.request),
            photos_analyzed=True, photo_coverage=(1,),
        )
        analyzed = AnalyzedListing(listing, evaluate_rules(listing), text)
        provider = RefreshProbe()
        service = AvitoAnalysisService(provider, CompleteReviewer(), ai_max_listings=5, ai_concurrency=1)
        result, _, _ = service._verify_finalists(
            (analyzed,), self.request, deadline_at=None, allowance_usd=1.0,
            progress=None,
        )

        self.assertEqual(provider.calls, [])
        self.assertEqual(result, (analyzed,))

    def _fully_reviewed_phone(self):
        request = SearchRequest("iPhone 15 Pro", category="phones", mode="find", desired_results=3)
        raw = raw_listing(91000002, model="iPhone 15 Pro", condition="Новое")
        listing = normalize_listing(raw)
        review = CompleteReviewer().review_text(listing, request)
        review = replace(
            review,
            photos_analyzed=True,
            photo_coverage=tuple(range(1, len(listing.images) + 1)),
            photo_condition_evidence="pass",
            photo_completeness_evidence="pass",
        )
        return request, raw, AnalyzedListing(listing, evaluate_rules(listing), review)

    def test_finalist_without_direct_refresh_loses_any_prior_verification(self) -> None:
        request, _, analyzed = self._fully_reviewed_phone()
        listing = replace(
            analyzed.listing,
            verification_status="verified",
            verified_at=datetime.now(timezone.utc).isoformat(),
        )
        analyzed = replace(analyzed, listing=listing)
        service = AvitoAnalysisService(None, CompleteReviewer())

        result, _, _ = service._verify_finalists(
            (analyzed,), request, deadline_at=None, allowance_usd=1.0, progress=None,
        )

        self.assertEqual(result[0].listing.verification_status, "unverified")
        self.assertEqual(result[0].listing.verified_at, "")
        self.assertFalse(result[0].listing.is_freshly_verified())
        self.assertFalse(is_customer_safe(result[0], request))

    def test_final_refresh_uses_exact_url_and_updated_active_price(self) -> None:
        request, raw, analyzed = self._fully_reviewed_phone()

        class RefreshProbe:
            def __init__(self):
                self.urls = []

            def refresh(self, listings, **kwargs):
                self.urls.extend(item.url for item in listings)
                fresh = dict(raw)
                fresh["price"] = 47_000
                fresh["status"] = "active"
                fresh["scrapedAt"] = datetime.now(timezone.utc).isoformat()
                return CollectionBatch(items=(fresh,))

        provider = RefreshProbe()
        service = AvitoAnalysisService(provider, CompleteReviewer())

        result, _, _ = service._verify_finalists(
            (analyzed,), request, deadline_at=None, allowance_usd=1.0, progress=None,
        )

        self.assertEqual(provider.urls, [analyzed.listing.url])
        self.assertEqual(result[0].listing.price, 47_000)
        self.assertEqual(result[0].listing.status, "active")
        self.assertTrue(result[0].listing.is_freshly_verified())
        self.assertTrue(is_customer_safe(result[0], request))

    def test_public_result_uses_price_from_required_direct_refresh(self) -> None:
        request, raw, _ = self._fully_reviewed_phone()

        class RefreshProbe:
            def __init__(self):
                self.urls = []

            def refresh(self, listings, **kwargs):
                self.urls.extend(item.url for item in listings)
                fresh = dict(raw)
                fresh["price"] = 47_000
                fresh["status"] = "active"
                fresh["scrapedAt"] = datetime.now(timezone.utc).isoformat()
                return CollectionBatch(items=(fresh,))

        provider = RefreshProbe()
        service = AvitoAnalysisService(provider, CompleteReviewer(), ai_max_listings=3)
        report = service.analyze_dataset(
            (raw,), request, refresh_finalists=True, collection_budget_remaining_usd=0.01,
        )

        recommendations = report.public_dict()["recommendations"]
        self.assertEqual(provider.urls, [raw["url"]])
        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["listing"]["price"], 47_000)

    def test_public_result_stays_empty_when_final_refresh_is_unavailable(self) -> None:
        request, raw, _ = self._fully_reviewed_phone()
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_max_listings=3)

        report = service.analyze_dataset(
            (raw,), request, refresh_finalists=True, collection_budget_remaining_usd=0.01,
        )

        self.assertEqual(report.public_dict()["recommendations"], [])
        decisions = report.public_dict(include_admin=True)["adminAudit"]["bargainDecisions"]
        self.assertEqual(decisions[0]["final_status"], "pending")
        self.assertEqual(decisions[0]["final_reason_code"], "FINAL_REVALIDATION_NOT_RUN")

    def test_inactive_or_missing_final_refresh_cannot_become_customer_safe(self) -> None:
        request, raw, analyzed = self._fully_reviewed_phone()

        class RefreshProbe:
            def refresh(self, listings, **kwargs):
                fresh = dict(raw)
                fresh["status"] = "inactive"
                fresh["scrapedAt"] = datetime.now(timezone.utc).isoformat()
                return CollectionBatch(items=(fresh,))

        service = AvitoAnalysisService(RefreshProbe(), CompleteReviewer())
        inactive, _, _ = service._verify_finalists(
            (analyzed,), request, deadline_at=None, allowance_usd=1.0, progress=None,
        )
        self.assertEqual(inactive[0].listing.verification_status, "failed")
        self.assertFalse(is_customer_safe(inactive[0], request))

        class EmptyRefresh:
            def refresh(self, listings, **kwargs):
                return CollectionBatch(items=())

        missing_service = AvitoAnalysisService(EmptyRefresh(), CompleteReviewer())
        missing, _, _ = missing_service._verify_finalists(
            (analyzed,), request, deadline_at=None, allowance_usd=1.0, progress=None,
        )
        self.assertEqual(missing[0].listing.verification_status, "failed")
        self.assertFalse(is_customer_safe(missing[0], request))

    def test_generic_ps5_accepts_slim_but_explicit_slim_rejects_regular(self) -> None:
        slim = ps5_listing(model="PlayStation 5 Slim", completeness=True)
        regular = ps5_listing(model="PlayStation 5", completeness=True)
        self.assertTrue(check_request_compatibility(slim, self.request).matches)
        exact = replace(self.request, query="ps5 slim")
        mismatch = check_request_compatibility(regular, exact)
        self.assertFalse(mismatch.matches)
        self.assertEqual(mismatch.field, "variant")

    def test_ai_cache_key_is_request_specific_and_market_key_ignores_price_bounds(self) -> None:
        listing = ps5_listing(completeness=True)
        service = AvitoAnalysisService(None, CompleteReviewer())
        self.assertNotEqual(
            service._cache_key(listing, self.request),
            service._cache_key(listing, replace(self.request, query="ps5 slim")),
        )
        self.assertNotEqual(
            service._cache_key(listing, self.request),
            service._cache_key(listing, replace(self.request, required_condition="Новое")),
        )
        self.assertEqual(
            MarketSnapshotCache.key(self.request, "review-v1"),
            MarketSnapshotCache.key(replace(self.request, price_max=45_000), "review-v1"),
        )

    def test_bargain_mode_keeps_exact_match_without_savings_evidence(self) -> None:
        base_listing = ps5_listing(completeness=True)
        listing = replace(base_listing, verification_status="verified",
                          verified_at=base_listing.collected_at)
        review = replace(
            CompleteReviewer().review_text(listing, self.request),
            photos_analyzed=True, photo_coverage=(1, 2),
        )
        analyzed = AnalyzedListing(listing, evaluate_rules(listing), review)
        self.assertTrue(is_customer_safe(analyzed, self.request))
        find_recommendations = rank_listings((analyzed,), self.request)
        bargain_request = replace(self.request, mode="bargain")
        bargain_recommendations = rank_listings((analyzed,), bargain_request)
        find_report = AnalysisReport(
            query="ps5", location="Ярославль", collected_count=1, analyzed_count=1,
            mode="find", request=self.request, recommendations=find_recommendations,
        )
        bargain_report = replace(find_report, mode="bargain", request=bargain_request,
                                  recommendations=bargain_recommendations)
        self.assertEqual(len(find_report.public_dict()["recommendations"]), 1)
        card = bargain_report.public_dict()["recommendations"][0]
        self.assertFalse(card["belowComparables"])
        self.assertIsNone(card["savingAmount"])
        self.assertIn("Точное совпадение", card["reasons"][0])

    def test_terminal_outcomes_are_distinct(self) -> None:
        base = AnalysisReport(query="ps5", location="Ярославль", collected_count=1, analyzed_count=0)
        self.assertEqual(base.public_dict()["outcome"], "EMPTY_VERIFIED")
        self.assertEqual(replace(base, outcome="SEARCH_INCOMPLETE").public_dict()["outcome"], "SEARCH_INCOMPLETE")
        self.assertEqual(replace(base, outcome="SPEND_LIMIT").public_dict()["outcome"], "SPEND_LIMIT")
        self.assertEqual(replace(base, outcome="ERROR").public_dict()["outcome"], "ERROR")

    def test_incomplete_collection_is_not_verified_empty(self) -> None:
        service = AvitoAnalysisService(None, CompleteReviewer())
        report = service.analyze_dataset([], self.request, collection_metrics={
            "raw_collected": 0, "deduplicated": 0, "correct_city": 0,
            "request_family_matched": 0, "requested_target": 20,
            "target_filled": False, "stop_reason": "BUDGET_LIMIT",
        })
        self.assertEqual(report.public_dict()["outcome"], "SEARCH_INCOMPLETE")
        exhausted = service.analyze_dataset([], self.request, collection_metrics={
            "raw_collected": 0, "deduplicated": 0, "correct_city": 0,
            "request_family_matched": 0, "requested_target": 20,
            "target_filled": False, "stop_reason": "SEARCH_EXHAUSTED",
        })
        self.assertEqual(exhausted.public_dict()["outcome"], "EMPTY_VERIFIED")

    def test_saved_ps5_snapshot_replays_eighteen_survivors_offline(self) -> None:
        if not DEFAULT_SNAPSHOT.exists():
            self.skipTest("saved pilot snapshot is unavailable")
        result = replay(load_snapshot(DEFAULT_SNAPSHOT), self.request)
        self.assertEqual(result["hard_filter_survivors"], 18)
        self.assertEqual(result["valid_text"], 8)
        self.assertEqual(result["photo_eligible"], 8)
        self.assertEqual(result["definitely_rejected"], 5)
        self.assertEqual(result["awaiting_text_ai"], 5)


if __name__ == "__main__":
    unittest.main()
