"""Offline pickup constraints, comparable evidence and cache isolation."""
from dataclasses import replace
import unittest

from avito_service.http_api import parse_search_request
from avito_service.market_cache import MarketSnapshotCache
from avito_service.matching import matches_listing_request
from avito_service.models import SearchRequest
from avito_service.ranking import is_market_reference, rank_listings
from avito_service.service import AvitoAnalysisService
from tools.run_avito_pilot import build_request, parse_options
from tools.test_avito_collection_quality import service
from tools.test_avito_market_quality import REQUEST, analyzed_item
from tools.test_avito_kit_evidence import listing as console_listing


class AvitoPickupTests(unittest.TestCase):
    def test_http_and_dataclass_require_an_actual_boolean(self):
        self.assertFalse(parse_search_request({"query": "PS5"}).pickup_only)
        for value in (True, False):
            self.assertIs(parse_search_request({"query": "PS5", "pickupOnly": value}).pickup_only, value)
        for value in (None, "false", "true", 0, 1, [], {}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    SearchRequest("PS5", pickup_only=value)
                with self.assertRaises(ValueError):
                    parse_search_request({"query": "PS5", "pickupOnly": value})

    def test_cli_preserves_explicit_pickup_constraint(self):
        args = ["--query", "PS5", "--category", "gaming", "--location", "Ярославль"]
        self.assertFalse(build_request(parse_options(args)).pickup_only)
        result = build_request(parse_options(args + ["--pickup-only"]))
        self.assertTrue(result.pickup_only)
        self.assertEqual(result.location, "Ярославль")

    def test_mandatory_delivery_fails_before_ai_and_in_market(self):
        item = analyzed_item()
        item = replace(item, listing=replace(item.listing, delivery_required=True, delivery_cost_rub=500))
        pickup = replace(REQUEST, pickup_only=True)
        self.assertTrue(matches_listing_request(item.listing, REQUEST))
        self.assertFalse(matches_listing_request(item.listing, pickup))
        self.assertFalse(is_market_reference(item, pickup))
        self.assertEqual(rank_listings((item,), pickup), ())

    def test_shipping_only_wording_is_rejected_for_pickup_without_ai(self):
        request = SearchRequest("PS5", location="Ярославль", pickup_only=True)
        for text in (
            "Продаю только через Авито доставку.", "Только с Авито доставкой.",
            "Самовывоз не предусмотрен.", "Самовывоз: не доступен.",
            "Самовывоза нет.", "Без самовывоза.", "Только отправка.",
        ):
            with self.subTest(text=text):
                item = console_listing(text + " Полный комплект.")
                self.assertTrue(item.delivery_required)
                self.assertFalse(matches_listing_request(item, request))

    def test_negated_shipping_only_and_optional_shipping_keep_pickup(self):
        request = SearchRequest("PS5", location="Ярославль", pickup_only=True)
        for text in (
            "Не только доставка, но и самовывоз.",
            "Не только через Авито доставку, но и самовывоз.",
            "Можно через Авито доставку. Самовывоз тоже доступен.",
            "Только самовывоз.",
        ):
            with self.subTest(text=text):
                item = console_listing(text + " Полный комплект.")
                self.assertFalse(item.delivery_required)
                self.assertTrue(matches_listing_request(item, request))

    def test_optional_delivery_variants_compare_only_for_pickup(self):
        candidate = analyzed_item(price=40_000)
        candidate = replace(candidate, listing=replace(candidate.listing, delivery="Самовывоз"))
        refs = []
        for index, delivery in enumerate(("", "Авито Доставка", "Самовывоз и курьер")):
            item = analyzed_item(1000002 + index, price=40_300)
            refs.append(replace(item, listing=replace(item.listing, delivery=delivery,
                                                     delivery_cost_rub=500 + index)))
        refs = tuple(refs)
        ordinary = rank_listings((candidate,), REQUEST, market_analyzed=refs)[0]
        self.assertFalse(ordinary.below_comparables)
        self.assertEqual(ordinary.comparable_seller_count, 0)
        pickup = replace(REQUEST, pickup_only=True)
        result = rank_listings((candidate,), pickup, market_analyzed=refs)[0]
        self.assertTrue(result.below_comparables)
        self.assertFalse(result.below_market)
        self.assertEqual(result.comparable_seller_count, 3)
        self.assertEqual(result.saving_amount, 300)
        self.assertTrue(is_market_reference(refs[0], pickup))
        self.assertFalse(is_market_reference(refs[0], REQUEST))

    def test_pickup_does_not_hide_mandatory_fees_or_wrong_city(self):
        candidate = analyzed_item(price=40_000)
        pickup = replace(REQUEST, location="Москва", pickup_only=True)
        other_city = replace(candidate.listing, location="Ярославль")
        self.assertFalse(matches_listing_request(other_city, pickup))
        unknown_fee = replace(candidate.listing, mandatory_fee_rub=None)
        self.assertFalse(matches_listing_request(unknown_fee, pickup))
        candidate = replace(candidate, listing=replace(candidate.listing, mandatory_fee_rub=500))
        refs = tuple(analyzed_item(1000002 + index, price=40_300) for index in range(3))
        result = rank_listings((candidate,), pickup, market_analyzed=refs)[0]
        self.assertFalse(result.below_comparables)
        self.assertEqual(result.saving_amount, -200)

    def test_photo_selection_uses_pickup_comparable_evidence(self):
        def colored(number, price, color, delivery):
            item = analyzed_item(number, price=price)
            return replace(item, listing=replace(item.listing, delivery=delivery,
                           parameters={**item.listing.parameters, "Цвет": color}),
                           ai_review=replace(item.ai_review, photos_analyzed=False, photo_coverage=()))
        cheap = colored(1000001, 30_000, "Красный", "Самовывоз")
        bargain = colored(1000002, 31_000, "Чёрный", "Самовывоз")
        refs = tuple(colored(2000000 + index, 40_000, "Чёрный", "") for index in range(3))
        listings = (cheap.listing, bargain.listing)
        reviews = {item.listing.listing_id: item.ai_review for item in (cheap, bargain)}
        risks = {item.listing.listing_id: item.deterministic_risks for item in (cheap, bargain)}
        engine = service(None)
        self.assertEqual(engine._photo_candidate_ids(listings, risks, reviews, 1, REQUEST, refs),
                         (cheap.listing.listing_id,))
        self.assertEqual(engine._photo_candidate_ids(listings, risks, reviews, 1,
                         replace(REQUEST, pickup_only=True), refs), (bargain.listing.listing_id,))

    def test_pickup_and_delivery_requests_never_share_review_or_market_cache(self):
        listing = analyzed_item().listing
        pickup = replace(REQUEST, pickup_only=True)
        self.assertNotEqual(MarketSnapshotCache.key(REQUEST), MarketSnapshotCache.key(pickup))
        self.assertNotEqual(AvitoAnalysisService._cache_key(listing, REQUEST),
                            AvitoAnalysisService._cache_key(listing, pickup))


if __name__ == "__main__":
    unittest.main()
