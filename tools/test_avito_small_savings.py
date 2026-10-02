"""Small proven savings and useful-photo quota, with no external requests."""
from dataclasses import replace
import unittest

from avito_service.models import SearchRequest
from avito_service.ranking import rank_listings
from avito_service.service import AvitoAnalysisService
from tools.test_avito_market_quality import analyzed_item, REQUEST
from tools.test_avito_strict_results import console_row
from tools.test_avito_collection_quality import EvidenceProvider, EvidenceReviewer
from tools.test_avito_collection_quality import service
from tools.test_avito_strict_results import provider, search_request


class SmallSavingsTests(unittest.TestCase):
    def test_three_to_nine_comparables_have_a_distinct_honest_public_tier(self):
        for count in (2, 3, 9, 10):
            with self.subTest(count=count):
                source = provider(price=39_700)
                source.market_rows = [console_row(20000000+i, 40_000) for i in range(count)]
                result = service(source).search(replace(search_request(), price_max=None)).public_dict()
                if count < 3:
                    self.assertEqual(result['visibleCount'], 1)
                    card = result['recommendations'][0]
                    self.assertFalse(card['belowComparables'])
                    self.assertIsNone(card['savingAmount'])
                    self.assertEqual(card['marketConfidence'], 'insufficient')
                    continue
                self.assertEqual(result['visibleCount'], 1)
                card = result['recommendations'][0]
                self.assertTrue(card['belowComparables'])
                self.assertEqual(card['belowMarket'], count >= 10)
                self.assertEqual(card['marketConfidence'], 'moderate' if count >= 10 else 'limited')
                self.assertEqual(card['comparableSellerCount'], count)
                self.assertEqual(card['savingAmount'], 300)
                self.assertEqual(result['resultPolicy'], 'verified_exact_matches_with_optional_savings')

    def test_small_positive_savings_keep_full_market_proof(self):
        refs = tuple(analyzed_item(2000000+i, price=40_000) for i in range(10))
        for difference in (1, 100, 300, 1_000, 2_000, 0, -100):
            with self.subTest(difference=difference):
                candidate = analyzed_item(price=40_000-difference)
                card = rank_listings((candidate,), REQUEST, market_analyzed=refs)[0]
                self.assertEqual(card.below_market, difference > 0)
                self.assertEqual(card.saving_amount, difference)
        candidate = analyzed_item(price=39_700)
        self.assertFalse(rank_listings((candidate,), REQUEST, market_analyzed=refs[:9])[0].below_market)

    def test_bargain_quota_and_refresh_skip_safe_but_expensive_candidates(self):
        class OrderedService(AvitoAnalysisService):
            def _photo_candidate_ids(self, *args, **kwargs):
                return tuple(sorted(super()._photo_candidate_ids(*args, **kwargs)))

        source = EvidenceProvider()
        source.market_rows = [console_row(20000000+i, 40_000) for i in range(10)]
        source.candidate_rows = [console_row(10000000+i, 50_000 if i < 3 else 39_700) for i in range(6)]
        reviewer = EvidenceReviewer()
        engine = OrderedService(source, reviewer, ai_text_batch_size=10, ai_text_max_listings=20,
            ai_max_listings=20, ai_concurrency=1, report_max_cost_rub=50, ai_max_cost_rub=15, usd_rub_rate=50)
        request = SearchRequest('PlayStation 5', location='Ярославль', category='gaming', desired_results=3)
        report = engine.search(request).public_dict()
        # Bargain mode spends photo AI only on candidates with already proven
        # positive savings; the three more expensive rows are ineligible.
        self.assertEqual(len(reviewer.photo_calls), 3)
        self.assertEqual(report['visibleCount'], 3)
        self.assertTrue(all(card['savingAmount'] == 300 for card in report['recommendations']))
        self.assertEqual(set(source.refresh_calls[0][0]), {'10000003', '10000004', '10000005'})
        self.assertTrue(all(card['listing']['verificationStatus'] == 'verified' for card in report['recommendations']))

    def test_internal_refresh_planning_does_not_make_an_old_card_fresh(self):
        candidate = analyzed_item(price=39_700)
        candidate = replace(candidate, listing=replace(candidate.listing, verified_at='', verification_status='unverified'))
        refs = tuple(analyzed_item(2000000+i, price=40_000) for i in range(10))
        self.assertTrue(rank_listings((candidate,), REQUEST, market_analyzed=refs, require_freshness=False)[0].below_market)
        self.assertFalse(rank_listings((candidate,), REQUEST, market_analyzed=refs)[0].below_market)
        self.assertFalse(candidate.listing.is_freshly_verified())


if __name__ == '__main__':
    unittest.main()
