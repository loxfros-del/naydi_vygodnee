"""Paid candidate evidence extends comparison without resurrecting rejected ads."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from avito_service.models import ReviewVerdict, RiskFinding, Severity
from avito_service.ranking import merge_market_evidence, rank_listings
from tools.test_avito_collection_quality import EvidenceProvider, EvidenceReviewer, request, row, service
from tools.test_avito_market_quality import REQUEST, analyzed_item


def pending_photos(item):
    return replace(item, ai_review=replace(item.ai_review, photos_analyzed=False, photo_coverage=()))


class AvitoEvidenceMergeTests(unittest.TestCase):
    def test_candidate_text_extends_empty_or_small_market_with_real_prices(self):
        candidate = analyzed_item(1000001, price=40_000)
        refs = tuple(pending_photos(analyzed_item(2000000 + index, price=40_300)) for index in range(3))
        for market_size in (0, 1):
            with self.subTest(market_size=market_size):
                ranked = rank_listings((candidate,) + refs[market_size:], REQUEST,
                                       market_analyzed=refs[:market_size])
                result = next(item for item in ranked if item.analyzed.listing.listing_id == "1000001")
                self.assertTrue(result.below_comparables)
                self.assertFalse(result.below_market)
                self.assertEqual(result.comparable_seller_count, 3)
                self.assertEqual(result.saving_amount, 300)
                self.assertEqual({link["url"] for link in result.market_evidence},
                                 {item.listing.url for item in refs})

    def test_candidate_itself_own_seller_and_duplicate_ids_do_not_expand_evidence(self):
        candidate = analyzed_item(1000001, price=40_000)
        own_other_ad = analyzed_item(1000002, price=80_000, seller_id="1000001")
        refs = tuple(analyzed_item(2000000 + index, price=40_300) for index in range(2))
        ranked = rank_listings((candidate, own_other_ad, *refs), REQUEST,
                               market_analyzed=(candidate, own_other_ad, *refs, *refs))
        result = next(item for item in ranked if item.analyzed.listing.listing_id == "1000001")
        self.assertEqual(result.comparable_seller_count, 2)
        self.assertFalse(result.below_comparables)

    def test_negative_candidate_photo_overrides_older_approved_market_copy(self):
        candidate = analyzed_item(1000001, price=40_000)
        refs = tuple(analyzed_item(2000000 + index, price=40_300) for index in range(3))
        negative = replace(refs[0], ai_review=replace(refs[0].ai_review,
                           verdict=ReviewVerdict.CAUTION, defects=("Повреждение корпуса на фото",)))
        combined = merge_market_evidence((candidate, negative), refs)
        self.assertIs(next(item for item in combined if item.listing.listing_id == negative.listing.listing_id), negative)
        result = next(item for item in rank_listings((candidate, negative), REQUEST, market_analyzed=refs)
                      if item.analyzed.listing.listing_id == "1000001")
        self.assertEqual(result.comparable_seller_count, 2)
        self.assertFalse(result.below_comparables)

    def test_changed_unreviewed_or_risky_candidate_cannot_reuse_previous_approval(self):
        old = analyzed_item(1000001)
        unknown = replace(old, ai_review=replace(old.ai_review, text_analyzed=False,
                          photos_analyzed=False, photo_coverage=()))
        candidates = (
            replace(unknown, listing=replace(unknown.listing, price=40_100)),
            replace(unknown, listing=replace(unknown.listing, description="Новое описание")),
            replace(unknown, listing=replace(unknown.listing, status="inactive")),
            replace(unknown, deterministic_risks=(RiskFinding("NEW_RISK", Severity.CRITICAL, "Обнаружен риск"),)),
        )
        for candidate in candidates:
            with self.subTest(listing=candidate.listing):
                self.assertIs(merge_market_evidence((candidate,), (old,))[0], candidate)

    def test_unchanged_unreviewed_candidate_reuses_fresh_review_without_refreshing_time(self):
        old = analyzed_item(1000001)
        candidate = replace(old, listing=replace(old.listing,
                            collected_at=datetime.now(timezone.utc).isoformat(), verified_at="",
                            verification_status="unverified", stock="Нет в наличии"),
                            ai_review=replace(old.ai_review, text_analyzed=False,
                                              photos_analyzed=False, photo_coverage=()))
        self.assertIs(merge_market_evidence((candidate,), (old,))[0], old)
        stale = replace(old, listing=replace(old.listing,
                        collected_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()))
        self.assertIs(merge_market_evidence((candidate,), (stale,))[0], candidate)

    def test_photo_selection_uses_other_completed_candidate_text_without_market_duplicate(self):
        def colored(number, price, color):
            item = analyzed_item(number, price=price)
            return replace(item, listing=replace(item.listing,
                           parameters={**item.listing.parameters, "Цвет": color}))
        cheap = pending_photos(colored(1000001, 30_000, "Красный"))
        bargain = pending_photos(colored(1000002, 31_000, "Чёрный"))
        refs = tuple(colored(2000000 + index, 40_000, "Чёрный") for index in range(3))
        sources = (cheap, bargain, *refs)
        listing_ids = service(None)._photo_candidate_ids(
            tuple(item.listing for item in sources),
            {item.listing.listing_id: item.deterministic_risks for item in sources},
            {item.listing.listing_id: item.ai_review for item in sources}, 1, REQUEST, (),
        )
        self.assertEqual(listing_ids, (bargain.listing.listing_id,))

    def test_completed_photo_quota_uses_all_paid_text_and_stops_at_three(self):
        provider = EvidenceProvider()
        provider.market_rows = []
        provider.candidate_rows = [row(1000000 + index, 40_000 + 100 * index) for index in range(7)]
        reviewer = EvidenceReviewer()
        search = replace(request(), price_min=None, price_max=None, desired_results=3)
        report = service(provider, reviewer).search(search)
        visible = report.public_dict()["recommendations"]
        self.assertEqual(len(reviewer.text_calls), 7)
        self.assertEqual(len(reviewer.photo_calls), 3)
        self.assertEqual(len(visible), 3)
        self.assertTrue(all(item["belowComparables"] and item["comparableSellerCount"] == 6 for item in visible))
        self.assertEqual({item["listing"]["id"] for item in visible}, {"1000000", "1000001", "1000002"})


if __name__ == "__main__":
    unittest.main()
