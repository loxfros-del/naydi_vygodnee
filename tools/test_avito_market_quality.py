"""Offline evidence tests for independent Avito market estimates and freshness."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest

from avito_service.models import (
    AIReview, AnalysisReport, AnalyzedListing, RecommendationRole, ReviewVerdict, SearchRequest,
)
from avito_service.normalization import normalize_listing
from avito_service.matching import matches_listing_request
from avito_service.ranking import is_customer_safe, rank_listings
from avito_service.risk_rules import evaluate_rules


def raw_item(number: int = 1000001, *, price: int = 40_000, seller_id: str | None = None) -> dict:
    return {
        "id": number,
        "title": "Apple iPhone 15 Pro 256 ГБ",
        "url": f"https://www.avito.ru/moskva/telefony/iphone_{number}",
        "status": "active", "stock": "В наличии", "price": price, "currency": "₽",
        "description": "Аккумулятор 96%. Не ремонтировался. Все детали оригинальные. Полный комплект.",
        "images": [f"https://10.img.avito.st/image/{number}"],
        "parameters": {
            "Модель": "iPhone 15 Pro", "Встроенная память": "256 ГБ",
            "SIM-карты": "SIM + eSIM", "Состояние": "Отличное",
            "История смартфона": "Активирован",
        },
        "seller": {"id": seller_id or str(number), "name": "Александр", "sellerType": "private"},
        "location": "Москва", "deliveryAvailable": True,
        "scrapedAt": datetime.now(timezone.utc).isoformat(),
    }


def analyzed_item(number: int = 1000001, *, price: int = 40_000, seller_id: str | None = None) -> AnalyzedListing:
    listing = normalize_listing(raw_item(number, price=price, seller_id=seller_id))
    listing = replace(listing, verified_at=datetime.now(timezone.utc).isoformat(), verification_status="verified")
    review = AIReview(
        listing_id=listing.listing_id, text_analyzed=True, photos_analyzed=True,
        photo_coverage=(1,), identified_model=listing.model, storage=listing.storage,
        sim_variant=listing.sim_variant, condition=listing.condition,
        verdict=ReviewVerdict.APPROVE, confidence=0.9,
    )
    return AnalyzedListing(listing, evaluate_rules(listing), review)


REQUEST = SearchRequest(query="iPhone 15 Pro", required_storage="256 ГБ", required_condition="Отличное")


class AvitoMarketQualityTests(unittest.TestCase):
    def test_unsafe_candidate_never_gets_bargain_label(self):
        refs = tuple(analyzed_item(1000002 + index, price=55_000) for index in range(10))
        for changes in ({"verdict": ReviewVerdict.CAUTION, "defects": ("Поврежден экран",)},
                        {"verdict": ReviewVerdict.REJECT}, {"photos_analyzed": False}):
            with self.subTest(changes=changes):
                candidate = analyzed_item()
                candidate = replace(candidate, ai_review=replace(candidate.ai_review, **changes))
                result = rank_listings((candidate,), REQUEST, market_analyzed=refs)[0]
                self.assertFalse(result.below_market)

    def test_known_variants_do_not_share_market_evidence(self):
        for field, own, other in (("Цвет", "Синий", "Золотой"), ("Материал", "Кожа", "Ткань"),
                                  ("Год выпуска", "2024", "2025"), ("Мощность", "1000 Вт", "2000 Вт")):
            with self.subTest(field=field):
                candidate = analyzed_item()
                candidate = replace(candidate, listing=replace(candidate.listing,
                    parameters={**candidate.listing.parameters, field: own}))
                refs = tuple(analyzed_item(1000002 + index, price=55_000) for index in range(10))
                refs = tuple(replace(item, listing=replace(item.listing,
                    parameters={**item.listing.parameters, field: other})) for item in refs)
                result = rank_listings((candidate,), REQUEST, market_analyzed=refs)[0]
                self.assertFalse(result.below_market)
                self.assertEqual(result.comparable_seller_count, 0)

    def test_unknown_computer_configuration_cannot_establish_market(self):
        def laptop(number, price):
            item = analyzed_item(number, price=price)
            return replace(item, listing=replace(item.listing,
                title="Dell Latitude 5420", url=f"https://www.avito.ru/moskva/noutbuki/dell_{number}",
                parameters={"Модель": "Dell Latitude 5420", "Состояние": "Новое"}),
                ai_review=replace(item.ai_review, identified_model="Dell Latitude 5420",
                                  storage="", sim_variant="", condition="Новое"))
        candidate = laptop(1000001, 40_000)
        refs = tuple(laptop(1000002 + index, 55_000) for index in range(10))
        result = rank_listings((candidate,), SearchRequest("Dell Latitude 5420"), market_analyzed=refs)[0]
        self.assertEqual(result.role, RecommendationRole.TOP)
        self.assertFalse(result.below_market)
        self.assertIsNone(result.saving_amount)
        self.assertEqual(result.comparable_seller_count, 0)
        def configured(item, *, gpu=True):
            return replace(item, listing=replace(item.listing, parameters={**item.listing.parameters,
                "CPU": "Intel Core i5-1135G7", "RAM": "16 GB", "SSD": "512 GB",
                **({"GPU": "Intel Iris Xe"} if gpu else {})}))
        result = rank_listings((configured(candidate, gpu=False),), SearchRequest("Dell Latitude 5420"),
                              market_analyzed=tuple(configured(item, gpu=False) for item in refs))[0]
        self.assertFalse(result.below_market)
        result = rank_listings((configured(candidate),), SearchRequest("Dell Latitude 5420"),
                              market_analyzed=tuple(configured(item) for item in refs))[0]
        self.assertTrue(result.below_market)
        self.assertEqual(result.comparable_seller_count, 10)

    def test_equivalent_known_ram_aliases_share_configuration(self):
        item = analyzed_item()
        left = replace(item, listing=replace(item.listing, parameters={**item.listing.parameters, "RAM": "8 GB"}))
        right = replace(item, listing=replace(item.listing, parameters={**item.listing.parameters, "Оперативная память": "8 ГБ"}))
        self.assertEqual(left.comparable_key(), right.comparable_key())

    def test_same_capacity_ssd_and_hdd_are_different_configurations(self):
        item = analyzed_item()
        ssd = replace(item, listing=replace(item.listing, parameters={**item.listing.parameters, "SSD": "512 GB"}))
        hdd = replace(item, listing=replace(item.listing, parameters={**item.listing.parameters, "HDD": "512 GB"}))
        self.assertNotEqual(ssd.comparable_key(), hdd.comparable_key())

    def test_equivalent_model_and_condition_wording_share_market(self):
        item = analyzed_item()
        equivalent = replace(item, ai_review=replace(item.ai_review,
            identified_model="Apple iPhone15 Pro", storage="256 GB", sim_variant="eSIM + SIM",
            condition="Отличное состояние"))
        self.assertEqual(item.comparable_key(), equivalent.comparable_key())

    def test_details_need_literal_source_evidence_without_negation(self):
        request = replace(REQUEST, attributes=(("details", "зарядка и кейс"),))
        item = analyzed_item()
        for description, expected in (
            ("В комплекте зарядка и кейс.", True),
            ("В комплекте только устройство.", False),
            ("Зарядка и кейс не входят в комплект.", False),
            ("Нет в комплекте: зарядка и кейс.", False),
            ("Нет в комплекте:\nзарядка и кейс.", False),
            ("В комплекте зарядка и кейс. Зарядки нет.", False),
            ("Возможно, зарядка и кейс.", False),
            ("Может быть зарядка и кейс.", False),
            ("Полный комплект, кроме: зарядка и кейс.", False),
        ):
            with self.subTest(description=description):
                listing = replace(item.listing, description=description)
                self.assertEqual(matches_listing_request(listing, request, final=True), expected)

    def test_stable_seller_id_is_hashed_and_never_public(self):
        one = normalize_listing(raw_item(seller_id="secret-seller-id"))
        two = normalize_listing(raw_item(1000002, seller_id="secret-seller-id"))
        self.assertEqual(one.seller.identity_hash, two.seller.identity_hash)
        self.assertEqual(len(one.seller.identity_hash), 64)
        public = json.dumps(one.public_dict(), ensure_ascii=False)
        self.assertNotIn("secret-seller-id", public)
        self.assertNotIn(one.seller.identity_hash, public)

    def test_same_names_different_ids_remain_independent(self):
        candidate = analyzed_item()
        references = tuple(analyzed_item(1000002 + index, price=50_000 + index * 1000) for index in range(3))
        recommendation = rank_listings((candidate,), REQUEST, market_analyzed=references)[0]
        self.assertFalse(recommendation.below_market)
        self.assertTrue(recommendation.below_comparables)
        self.assertEqual(recommendation.comparable_seller_count, 3)
        self.assertEqual(recommendation.market_median, 51_000)
        self.assertEqual(recommendation.saving_amount, 11_000)
        self.assertEqual(recommendation.market_confidence, "limited")

    def test_find_budget_selects_cheapest_match_even_without_its_own_comparison_sample(self):
        cheapest = analyzed_item(1000001, price=40_000)
        cheapest = replace(cheapest, listing=replace(cheapest.listing,
            parameters={**cheapest.listing.parameters, "Цвет": "Синий"}))
        discounted = analyzed_item(1000002, price=42_000)
        references = tuple(analyzed_item(1000010 + index, price=50_000 + index * 1000) for index in range(3))
        for mode, top_id, visible_ids in (("find", "1000001", {"1000001", "1000002"}),
                                          ("bargain", "1000002", {"1000001", "1000002"})):
            with self.subTest(mode=mode):
                request = replace(REQUEST, mode=mode, priority="budget")
                ranked = rank_listings((cheapest, discounted), request, market_analyzed=references)
                self.assertEqual(ranked[0].role, RecommendationRole.TOP)
                self.assertEqual(ranked[0].analyzed.listing.listing_id, top_id)
                by_id = {item.analyzed.listing.listing_id: item for item in ranked}
                self.assertFalse(by_id["1000001"].below_comparables)
                self.assertTrue(by_id["1000002"].below_comparables)
                if mode == "bargain":
                    self.assertIsNone(by_id["1000001"].saving_amount)
                report = AnalysisReport(query=request.query, location=request.location,
                    collected_count=2, analyzed_count=2, mode=mode, priority="budget",
                    request=request, recommendations=ranked)
                self.assertEqual({item["listing"]["id"] for item in report.public_dict()["recommendations"]}, visible_ids)

    def test_absent_id_does_not_use_seller_name_for_market_evidence(self):
        candidate = analyzed_item()
        references = []
        for index in range(3):
            item = analyzed_item(1000002 + index, price=60_000)
            references.append(replace(item, listing=replace(item.listing, seller=replace(item.listing.seller, identity_hash=""))))
        result = rank_listings((candidate,), REQUEST, market_analyzed=tuple(references))[0]
        self.assertFalse(result.below_market)
        self.assertEqual(result.comparable_seller_count, 0)

    def test_budget_never_truncates_external_market_references(self):
        candidate = analyzed_item(price=40_000)
        refs = tuple(analyzed_item(1000002 + index, price=60_000) for index in range(3))
        result = rank_listings((candidate,), replace(REQUEST, price_max=45_000), market_analyzed=refs)[0]
        self.assertFalse(result.below_market)
        self.assertEqual(result.market_median, 60_000)

    def test_candidate_and_same_seller_cannot_support_own_discount(self):
        candidate = analyzed_item(seller_id="owner")
        refs = (
            candidate, analyzed_item(1000002, price=200_000, seller_id="owner"),
            analyzed_item(1000003, price=55_000), analyzed_item(1000004, price=65_000),
        )
        result = rank_listings((candidate,), REQUEST, market_analyzed=refs)[0]
        self.assertFalse(result.below_market)
        self.assertEqual(result.comparable_seller_count, 2)
        self.assertIsNone(result.market_median)
        self.assertNotIn(candidate.listing.url, [value["url"] for value in result.market_evidence])

    def test_duplicate_ads_do_not_weight_market(self):
        candidate = analyzed_item()
        refs = [analyzed_item(1000002 + index, price=100_000, seller_id="shop") for index in range(5)]
        refs.extend([analyzed_item(1000010, price=50_000), analyzed_item(1000011, price=60_000)])
        result = rank_listings((candidate,), REQUEST, market_analyzed=tuple(refs))[0]
        self.assertEqual(result.market_median, 60_000)
        self.assertEqual(len(result.market_evidence), 3)
        self.assertEqual(result.market_min, 50_000)
        self.assertEqual(result.market_max, 100_000)

    def test_ten_external_sellers_have_moderate_not_guaranteed_confidence(self):
        refs = tuple(analyzed_item(1000002 + index, price=55_000) for index in range(10))
        result = rank_listings((analyzed_item(),), REQUEST, market_analyzed=refs)[0]
        self.assertEqual(result.market_confidence, "moderate")
        self.assertTrue(result.below_market)

    def test_empty_market_pool_can_use_independently_reviewed_candidate_prices(self):
        candidates = tuple(analyzed_item(1000001 + index, price=40_000 + index * 10_000) for index in range(4))
        result = rank_listings(candidates, REQUEST, market_analyzed=())
        cheapest = next(item for item in result if item.analyzed.listing.listing_id == "1000001")
        self.assertTrue(cheapest.below_comparables)
        self.assertEqual(cheapest.market_median, 60_000)
        self.assertEqual(cheapest.comparable_seller_count, 3)
        self.assertTrue(all(not item.below_market for item in result))

    def test_stale_market_reference_is_excluded(self):
        refs = [analyzed_item(1000002 + index, price=55_000) for index in range(3)]
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        refs[0] = replace(refs[0], listing=replace(refs[0].listing, collected_at=old))
        result = rank_listings((analyzed_item(),), REQUEST, market_analyzed=tuple(refs))[0]
        self.assertFalse(result.below_market)
        self.assertEqual(result.comparable_seller_count, 2)

    def test_used_condition_key_separates_material_differences(self):
        original = analyzed_item()
        for updates in (
            {"battery_health_percent": 85}, {"repair_status": "repaired"},
            {"parts_status": "non_original"}, {"completeness": "device_only"},
            {"location": "Казань"}, {"delivery": "unavailable"},
        ):
            with self.subTest(updates=updates):
                changed = replace(original, listing=replace(original.listing, **updates))
                self.assertNotEqual(original.comparable_key(), changed.comparable_key())

    def test_unknown_material_used_evidence_never_customer_safe(self):
        original = analyzed_item()
        self.assertTrue(is_customer_safe(original, REQUEST))
        for updates in ({"battery_health_percent": None}, {"repair_status": "conflicting"}, {"parts_status": "conflicting"}, {"completeness": ""}):
            with self.subTest(updates=updates):
                changed = replace(original, listing=replace(original.listing, **updates))
                self.assertFalse(is_customer_safe(changed, REQUEST))
                self.assertEqual(rank_listings((changed,), REQUEST)[0].role, RecommendationRole.CAUTION)

    def test_new_used_and_refurbished_are_different_markets(self):
        used = analyzed_item()
        new = replace(used, ai_review=replace(used.ai_review, condition="Новое"))
        refurb = replace(used, ai_review=replace(used.ai_review, condition="Восстановленное"))
        self.assertEqual(used.market_lane(), "used")
        self.assertEqual(new.market_lane(), "new_private")
        self.assertEqual(refurb.market_lane(), "refurbished")
        self.assertEqual(len({used.comparable_key(), new.comparable_key(), refurb.comparable_key()}), 3)

    def test_like_new_is_used_and_does_not_share_new_market(self):
        item = analyzed_item()
        like_new = replace(item, ai_review=replace(item.ai_review, condition="Как новый"))
        self.assertEqual(like_new.market_lane(), "used")

    def test_nonphone_brand_does_not_require_phone_sim_evidence(self):
        item = analyzed_item()
        listing = replace(
            item.listing, title="Пылесос Xiaomi Robot Vacuum", url="https://www.avito.ru/moskva/bytovaya_tehnika/robot_1000001",
            parameters={"Модель": "Xiaomi Robot Vacuum", "Состояние": "Отличное"},
            battery_health_percent=None,
        )
        item = replace(item, listing=listing, ai_review=replace(item.ai_review, identified_model="Xiaomi Robot Vacuum", storage="", sim_variant=""))
        self.assertTrue(item.condition_evidence_complete())

    def test_imported_verification_flags_cannot_assert_live_checks(self):
        raw = raw_item()
        raw.update({"verifiedAt": datetime.now(timezone.utc).isoformat(), "verification_status": "verified"})
        listing = normalize_listing(raw)
        self.assertEqual(listing.verification_status, "unverified")
        self.assertFalse(listing.is_freshly_verified())

    def test_final_requires_refresh_but_photo_quota_can_use_analysis(self):
        original = analyzed_item()
        item = replace(original, listing=replace(original.listing, verification_status="unverified", verified_at=""))
        self.assertFalse(is_customer_safe(item, REQUEST))
        self.assertTrue(is_customer_safe(item, REQUEST, require_freshness=False))

    def test_stale_naive_and_future_verification_dates_fail_closed(self):
        item = analyzed_item()
        for stamp in (
            (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat(),
            datetime.now().isoformat(),
            (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(), "bad-date",
        ):
            with self.subTest(stamp=stamp):
                self.assertFalse(replace(item.listing, verified_at=stamp).is_freshly_verified())

    def test_public_report_rechecks_freshness_and_discloses_limits(self):
        item = analyzed_item()
        recommendation = rank_listings((item,), REQUEST)[0]
        payload = recommendation.public_dict()
        self.assertFalse(payload["analysis"]["functionalConditionVerified"])
        self.assertEqual(payload["listing"]["conditionEvidence"]["basis"], "seller_listing")
        stale_item = replace(item, listing=replace(item.listing, verified_at=""))
        report = AnalysisReport("iPhone", "Москва", 1, 1, recommendations=(replace(recommendation, analyzed=stale_item),))
        self.assertEqual(report.public_dict()["recommendations"], [])

    def test_description_omission_does_not_invent_condition_evidence(self):
        raw = raw_item()
        raw["description"] = "Телефон в отличном состоянии. Продаю."
        item = normalize_listing(raw)
        self.assertIsNone(item.battery_health_percent)
        self.assertEqual((item.repair_status, item.parts_status, item.completeness), ("", "", ""))

    def test_repair_conflict_is_not_treated_as_original_unrepaired(self):
        raw = raw_item()
        raw["description"] += " После ремонта. Неоригинальный экран."
        item = normalize_listing(raw)
        self.assertEqual(item.repair_status, "conflicting")
        self.assertEqual(item.parts_status, "conflicting")

    def test_mandatory_fee_reverses_apparent_bargain(self):
        candidate = analyzed_item(price=40_000)
        candidate = replace(candidate, listing=replace(candidate.listing, mandatory_fee_rub=20_000))
        refs = tuple(analyzed_item(1000002 + index, price=55_000) for index in range(10))
        result = rank_listings((candidate,), REQUEST, market_analyzed=refs)[0]
        self.assertEqual(candidate.listing.acquisition_price, 60_000)
        self.assertFalse(result.below_market)
        self.assertEqual(result.saving_amount, -5_000)

    def test_mandatory_fee_above_budget_prevents_customer_recommendation(self):
        item = analyzed_item(price=40_000)
        item = replace(item, listing=replace(item.listing, mandatory_fee_rub=10_000))
        self.assertFalse(is_customer_safe(item, replace(REQUEST, price_max=45_000)))
        self.assertEqual(rank_listings((item,), replace(REQUEST, price_max=45_000)), ())

    def test_optional_delivery_is_not_assumed_free_or_added_to_pickup(self):
        listing = normalize_listing(raw_item())
        self.assertIsNone(listing.delivery_cost_rub)
        self.assertEqual(listing.acquisition_price, 40_000)
        self.assertEqual(listing.public_dict()["priceBasis"], "pickup_without_optional_delivery")

    def test_required_delivery_needs_known_cost_and_is_included(self):
        raw = raw_item()
        raw["onlyShipping"] = True
        listing = normalize_listing(raw)
        self.assertIsNone(listing.acquisition_price)
        raw["deliveryCostRub"] = 2_500
        listing = normalize_listing(raw)
        self.assertEqual(listing.acquisition_price, 42_500)
        self.assertEqual(listing.price_basis, "required_delivery_included")

    def test_unknown_delivery_or_mandatory_fee_never_enters_market(self):
        for changes in ({"delivery_required": True, "delivery_cost_rub": None}, {"mandatory_fee_rub": None}):
            with self.subTest(changes=changes):
                candidate = analyzed_item()
                candidate = replace(candidate, listing=replace(candidate.listing, **changes))
                self.assertFalse(is_customer_safe(candidate, REQUEST))
                self.assertFalse(rank_listings((candidate,), REQUEST)[0].below_market)

    def test_fee_normalizer_does_not_guess_percent_or_invalid_cost(self):
        for fee in (None, "уточняйте", "5%", -100, True):
            with self.subTest(fee=fee):
                raw = raw_item()
                raw["mandatoryFeeRub"] = fee
                self.assertIsNone(normalize_listing(raw).acquisition_price)
        raw = raw_item()
        raw["mandatoryFeeRub"] = "1 999,50 ₽"
        self.assertEqual(normalize_listing(raw).acquisition_price, 42_000)

    def test_omitted_numeric_fee_with_declared_commission_is_unknown(self):
        raw = raw_item()
        raw["description"] += " Комиссия рассчитывается при оплате."
        self.assertIsNone(normalize_listing(raw).mandatory_fee_rub)
        raw["description"] = "Без комиссии. Полный комплект."
        self.assertEqual(normalize_listing(raw).mandatory_fee_rub, 0)

    def test_reference_fees_are_included_and_shipping_basis_is_separate(self):
        candidate = analyzed_item()
        refs = tuple(analyzed_item(1000002 + index, price=50_000) for index in range(10))
        refs = tuple(replace(item, listing=replace(item.listing, mandatory_fee_rub=5_000)) for item in refs)
        result = rank_listings((candidate,), REQUEST, market_analyzed=refs)[0]
        self.assertEqual(result.market_median, 55_000)
        self.assertEqual(result.market_evidence[0]["price"], 55_000)
        self.assertEqual(result.market_evidence[0]["listingPrice"], 50_000)
        shipping = replace(candidate, listing=replace(candidate.listing, delivery_required=True, delivery_cost_rub=0))
        self.assertNotEqual(candidate.comparable_key(), shipping.comparable_key())

    def test_more_expensive_remainder_is_not_labelled_budget(self):
        candidates = tuple(analyzed_item(1000001 + index, price=28_000 + index * 1000) for index in range(3))
        refs = tuple(analyzed_item(1000010 + index, price=36_000) for index in range(10))
        results = rank_listings(candidates, REQUEST, market_analyzed=refs)
        self.assertEqual(results[0].role, RecommendationRole.TOP)
        self.assertEqual(results[0].analyzed.listing.acquisition_price, 28_000)
        self.assertTrue(all(item.role is RecommendationRole.BACKUP for item in results[1:]))

    def test_budget_role_requires_cheaper_total_than_top_and_backup(self):
        candidates = []
        for number, price, confidence in ((1000001, 36_000, 0.99), (1000002, 32_000, 0.9), (1000003, 28_000, 0.8)):
            item = analyzed_item(number, price=price)
            candidates.append(replace(item, ai_review=replace(item.ai_review, confidence=confidence)))
        refs = tuple(analyzed_item(1000010 + index, price=50_000) for index in range(10))
        results = rank_listings(tuple(candidates), replace(REQUEST, priority="quality"), market_analyzed=refs)
        prices_by_role = {item.role: item.analyzed.listing.acquisition_price for item in results}
        self.assertEqual(prices_by_role[RecommendationRole.TOP], 36_000)
        self.assertEqual(prices_by_role[RecommendationRole.BACKUP], 32_000)
        self.assertEqual(prices_by_role[RecommendationRole.BUDGET], 28_000)


if __name__ == "__main__":
    unittest.main()
