"""Offline regressions for cost/latency optimization without weaker checks."""
from dataclasses import replace
import unittest

from avito_service.errors import SearchCancelledError
from avito_service.models import AIReview, AnalyzedListing, ReviewVerdict, SearchRequest
from avito_service.normalization import normalize_listing
from avito_service.rejection import AmbiguityState, aggregate_rejections, route_text_ai
from avito_service.ranking import rank_listings
from avito_service.risk_rules import evaluate_rules
from avito_service.service import AvitoAnalysisService
from tools.test_avito_market_quality import analyzed_item as market_analyzed_item, REQUEST as MARKET_REQUEST
from tools.test_avito_service import CompleteReviewer, raw_listing


def analyzed(raw, request, **review_changes):
    listing = normalize_listing(raw)
    review = CompleteReviewer().review_text(listing, request)
    return AnalyzedListing(listing, evaluate_rules(listing), replace(review, **review_changes))


class AvitoOptimizationTests(unittest.TestCase):
    def test_rejection_reason_aggregation_keeps_primary_and_secondary_codes(self):
        request = SearchRequest("iPhone 15 Pro", category="phones", mode="find")
        removed = raw_listing(9901)
        removed["status"] = "removed"
        conflict = analyzed(
            raw_listing(9902), request, matches_request=False,
            conflicts=("source contradiction",), verdict=ReviewVerdict.REJECT,
        )
        incomplete_photo = analyzed(raw_listing(9903), request)
        values = (analyzed(removed, request), conflict, incomplete_photo)

        summary = aggregate_rejections(values, request, {"9902", "9903"}, ())

        by_stage = {item["stage"]: item for item in summary}
        self.assertEqual(by_stage["before_text_ai"]["primary"], {"LISTING_INACTIVE": 1})
        self.assertEqual(by_stage["after_text_ai"]["primary"], {"AI_REQUEST_MISMATCH": 1})
        self.assertEqual(by_stage["after_text_ai"]["secondary"]["AI_CONFLICT"], 1)
        self.assertEqual(by_stage["before_or_during_photo_ai"]["primary"], {"PHOTO_NOT_ANALYZED": 1})
        self.assertNotIn("source contradiction", repr(summary))

    def test_ambiguity_router_skips_deterministic_reject_but_keeps_known_safety_review(self):
        request = SearchRequest("iPhone 15 Pro", category="phones", mode="find")
        good = normalize_listing(raw_listing(9910))
        removed_raw = raw_listing(9911)
        removed_raw["status"] = "removed"
        removed = normalize_listing(removed_raw)

        known = route_text_ai(good, request, evaluate_rules(good))
        rejected = route_text_ai(removed, request, evaluate_rules(removed))

        self.assertEqual(known.state, AmbiguityState.KNOWN)
        self.assertTrue(known.needs_ai)  # full untrusted description remains mandatory
        self.assertFalse(rejected.needs_ai)
        self.assertIn("LISTING_INACTIVE", rejected.reason_codes)

    def test_conflict_found_by_text_ai_never_reaches_photo_ai(self):
        class ConflictReviewer(CompleteReviewer):
            def __init__(self):
                super().__init__()
                self.text_ids = []
                self.photo_ids = []

            def review_text(self, listing, search=None):
                self.text_ids.append(listing.listing_id)
                return replace(super().review_text(listing, search),
                               conflicts=("source contradiction",), verdict=ReviewVerdict.CAUTION)

            def review_photos(self, listing, text_review):
                self.photo_ids.append(listing.listing_id)
                return super().review_photos(listing, text_review)

        reviewer = ConflictReviewer()
        service = AvitoAnalysisService(None, reviewer, ai_concurrency=1)
        service.analyze_dataset([raw_listing(9920)], SearchRequest(
            "iPhone 15 Pro", category="phones", mode="find",
        ))
        self.assertEqual(reviewer.text_ids, ["9920"])
        self.assertEqual(reviewer.photo_ids, [])

    def test_comparables_control_savings_only_and_never_photo_admission(self):
        request = SearchRequest("iPhone 15 Pro", category="phones", mode="bargain")
        references = tuple(
            analyzed(raw_listing(9930 + index, price=40_000 + index * 100), request)
            for index in range(3)
        )
        service = AvitoAnalysisService(None, CompleteReviewer())

        def selected(price, reference_set=references, diagnostics=None):
            listing = normalize_listing(raw_listing(9940, price=price))
            review = CompleteReviewer().review_text(listing, request)
            return service._photo_candidate_ids(
                (listing,), {listing.listing_id: evaluate_rules(listing)},
                {listing.listing_id: review}, 5, request, reference_set,
                require_bargain_evidence=True,
                bargain_diagnostics=diagnostics,
            )

        self.assertEqual(selected(50_000), ("9940",))
        self.assertEqual(selected(30_000), ("9940",))

        diagnostics = []
        self.assertEqual(selected(30_000, diagnostics=diagnostics), ("9940",))
        self.assertEqual(diagnostics[0]["status"], "pass")
        self.assertEqual(diagnostics[0]["reason_code"], "POSITIVE_SAVING_WITH_MINIMUM_SAMPLE")
        self.assertEqual(diagnostics[0]["reference_price"], 40_100)
        self.assertEqual(diagnostics[0]["delta_rub"], 10_100)
        self.assertTrue(diagnostics[0]["photo_ai_eligible"])

        two_sellers = references[:2]
        diagnostics = []
        self.assertEqual(selected(30_000, two_sellers, diagnostics), ("9940",))
        self.assertEqual(diagnostics[0]["reason_code"], "INSUFFICIENT_COMPARABLE_SELLERS")
        self.assertEqual(diagnostics[0]["comparable_seller_count"], 2)
        self.assertIsNone(diagnostics[0]["reference_price"])
        self.assertIsNone(diagnostics[0]["delta_rub"])
        self.assertEqual(diagnostics[0]["photo_admission_status"], "pass")
        self.assertTrue(diagnostics[0]["photo_ai_eligible"])
        low_sample_rank = rank_listings(
            (analyzed(raw_listing(9940, price=30_000), request),), request,
            market_analyzed=two_sellers, require_freshness=False,
        )[0]
        self.assertFalse(low_sample_rank.below_comparables)
        self.assertIsNone(low_sample_rank.saving_amount)

        diagnostics = []
        unmatched_reference = analyzed(raw_listing(9939, model="iPhone 14"), request)
        self.assertEqual(selected(30_000, (unmatched_reference,), diagnostics), ("9940",))
        self.assertEqual(diagnostics[0]["reason_code"], "INSUFFICIENT_COMPARABLE_SELLERS")
        self.assertEqual(diagnostics[0]["comparable_seller_count"], 0)
        self.assertTrue(diagnostics[0]["photo_ai_eligible"])

        diagnostics = []
        self.assertEqual(selected(30_000, (), diagnostics), ("9940",))
        self.assertEqual(diagnostics[0]["reason_code"], "MARKET_REFERENCE_SNAPSHOT_UNAVAILABLE")
        self.assertEqual(diagnostics[0]["status"], "skipped")
        self.assertTrue(diagnostics[0]["photo_ai_eligible"])

        market_request = replace(MARKET_REQUEST, mode="bargain")
        proven_references = tuple(
            market_analyzed_item(9960 + index, price=50_000 + index * 100)
            for index in range(3)
        )
        bargain = rank_listings(
            (market_analyzed_item(9940, price=30_000),), market_request,
            market_analyzed=proven_references, require_freshness=False,
        )[0]
        self.assertTrue(bargain.below_comparables)
        self.assertEqual(bargain.comparable_seller_count, 3)
        self.assertGreater(bargain.saving_amount, 0)

    def test_bad_price_defect_or_sku_still_blocks_photo_independent_of_comparables(self):
        request = SearchRequest("iPhone 15 Pro", category="phones", mode="bargain", price_max=45_000)
        references = tuple(
            analyzed(raw_listing(9950 + index, price=40_000 + index * 100), request)
            for index in range(3)
        )
        service = AvitoAnalysisService(None, CompleteReviewer())

        def selected(raw, reference_set=references, *, review_changes=None, change_listing=None):
            listing = normalize_listing(raw)
            if change_listing:
                listing = change_listing(listing)
            review = CompleteReviewer().review_text(listing, request)
            if review_changes:
                review = replace(review, **review_changes)
            return service._photo_candidate_ids(
                (listing,), {listing.listing_id: evaluate_rules(listing)},
                {listing.listing_id: review}, 5, request, reference_set,
                require_bargain_evidence=True,
            )

        # Above-request price, unknown mandatory total, a stated defect, and an
        # explicit SKU mismatch remain hard blocks with no or ample references.
        for reference_set in ((), references):
            with self.subTest(comparable_count=len(reference_set)):
                self.assertEqual(selected(raw_listing(9960, price=50_000), reference_set), ())
                unknown_total = normalize_listing(raw_listing(9961, price=30_000))
                unknown_total = replace(unknown_total, mandatory_fee_rub=None)
                unknown_review = CompleteReviewer().review_text(unknown_total, request)
                self.assertEqual(service._photo_candidate_ids(
                    (unknown_total,), {unknown_total.listing_id: evaluate_rules(unknown_total)},
                    {unknown_total.listing_id: unknown_review}, 5, request, reference_set,
                    require_bargain_evidence=True,
                ), ())
                self.assertEqual(selected(
                    raw_listing(9962, price=30_000, description="Экран не работает. Полный комплект."),
                    reference_set,
                ), ())
                self.assertEqual(selected(
                    raw_listing(9963, price=30_000), reference_set,
                    review_changes={"matches_request": False, "identified_model": "iPhone 14"},
                ), ())

    def test_photo_gate_uses_the_same_real_per_seller_representative_as_ranking(self):
        request = SearchRequest("iPhone 15 Pro", category="phones", mode="bargain")
        references = tuple(analyzed(
            raw_listing(9960 + index, price=price, seller=seller), request,
        ) for index, (price, seller) in enumerate((
            (80_000, "duplicate-shop"),
            (120_000, "duplicate-shop"),
            (80_000, "seller-b"),
            (100_000, "seller-c"),
        )))
        listing = normalize_listing(raw_listing(9970, price=90_000))
        review = CompleteReviewer().review_text(listing, request)
        service = AvitoAnalysisService(None, CompleteReviewer())
        diagnostics = []

        selected = service._photo_candidate_ids(
            (listing,), {listing.listing_id: evaluate_rules(listing)},
            {listing.listing_id: review}, 5, request, references,
            require_bargain_evidence=True, bargain_diagnostics=diagnostics,
        )
        final = rank_listings(
            (AnalyzedListing(listing, evaluate_rules(listing), review),), request,
            market_analyzed=references, require_freshness=False,
        )[0]

        self.assertEqual(selected, ("9970",))
        self.assertEqual(diagnostics[0]["reference_price"], 80_000)
        self.assertEqual(diagnostics[0]["reason_code"], "NO_POSITIVE_SAVING")
        self.assertEqual(final.market_median, 80_000)
        self.assertEqual(final.saving_amount, -10_000)
        self.assertFalse(final.below_comparables)

    def test_cancel_after_text_prevents_photo_stage(self):
        state = {"cancelled": False}

        class CancellingReviewer(CompleteReviewer):
            def __init__(self):
                super().__init__()
                self.photo_ids = []

            def review_text(self, listing, search=None):
                review = super().review_text(listing, search)
                state["cancelled"] = True
                return review

            def review_photos(self, listing, text_review):
                self.photo_ids.append(listing.listing_id)
                return super().review_photos(listing, text_review)

        class Progress:
            def __call__(self, stage, message, percent):
                return None

            @staticmethod
            def cancel_requested():
                return state["cancelled"]

        reviewer = CancellingReviewer()
        service = AvitoAnalysisService(None, reviewer, ai_concurrency=1)
        with self.assertRaises(SearchCancelledError):
            service.analyze_dataset(
                [raw_listing(9950)],
                SearchRequest("iPhone 15 Pro", category="phones", mode="find"),
                progress=Progress(),
            )
        self.assertEqual(reviewer.photo_ids, [])


if __name__ == "__main__":
    unittest.main()
