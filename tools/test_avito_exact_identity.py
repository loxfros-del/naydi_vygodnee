#!/usr/bin/env python3
"""Offline regressions for exact product identity before and after AI review."""
from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone

from avito_service.matching import canonical_evidence, effective_model_identity, matches_listing_request
from avito_service.models import AIReview, AnalyzedListing, ReviewVerdict, SearchRequest, SellerSummary
from avito_service.normalization import normalize_listing
from avito_service.ranking import rank_listings


def console(*, title="Sony PlayStation 5", model="Sony PlayStation 5", description="Продаю исправную консоль.", **changes):
    raw = {
        "id": "12345678",
        "title": title,
        "url": "https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/console_12345678",
        "price": 40_000,
        "currency": "RUB",
        "status": "active",
        "location": "Ярославль",
        "description": description,
        "images": ["https://01.img.avito.st/console.jpg"],
        "parameters": {"Модель": model, "Состояние": "Отличное"} if model else {},
        "stock": "",
    }
    raw.update(changes)
    return normalize_listing(raw)


class ExactIdentityTests(unittest.TestCase):
    def assert_rejected_at_both_stages(self, candidate, query="PlayStation 5", ai_model="PlayStation 5"):
        request = SearchRequest(query, location="Ярославль")
        self.assertFalse(matches_listing_request(candidate, request))
        self.assertFalse(matches_listing_request(candidate, request, identified_model=ai_model, final=True))

    def test_screenshot_ps4_is_rejected_before_ai_and_after_incorrect_ai_approval(self):
        candidate = console(
            title="Sony Playstation 4 slim PS4 0,5-1Тб прошита + игры",
            model="PlayStation 4 Slim", price=23_500,
        )
        for query in ("PlayStation 5", "PS5", "пс 5", "плейстейшен 5"):
            with self.subTest(query=query):
                self.assert_rejected_at_both_stages(candidate, query)

    def test_ps5_aliases_are_equivalent_with_real_consistent_source_data(self):
        aliases = ("PlayStation 5", "Sony PlayStation5", "PS5", "PS 5", "пс 5", "плейстейшен 5", "Плей Стейшен 5", "PlayStation®5")
        for query in aliases:
            for actual in aliases:
                with self.subTest(query=query, actual=actual):
                    candidate = console(title=actual, model=actual)
                    self.assertTrue(matches_listing_request(candidate, SearchRequest(query)))
                    self.assertTrue(matches_listing_request(candidate, SearchRequest(query), identified_model=actual, final=True))

    def test_ps4_title_cannot_be_overridden_by_ps5_parameter_or_ai(self):
        self.assert_rejected_at_both_stages(console(title="Sony PlayStation 4 Slim", model="PlayStation 5"))

    def test_ps5_title_cannot_override_ps4_parameter(self):
        self.assert_rejected_at_both_stages(console(title="Sony PlayStation 5", model="PlayStation 4"))

    def test_bare_family_accepts_versions_but_explicit_variant_is_required(self):
        versions = ("PlayStation 5", "PlayStation 5 Slim", "PlayStation 5 Pro", "PlayStation 5 Digital", "PlayStation 5 Disc")
        for query in versions:
            for actual in versions:
                with self.subTest(query=query, actual=actual):
                    result = matches_listing_request(console(title=actual, model=actual), SearchRequest(query), identified_model=actual, final=True)
                    self.assertEqual(
                        result,
                        (query == "PlayStation 5" and actual != "PlayStation 5 Pro") or query == actual,
                    )

    def test_explicit_console_dimensions_are_independent(self):
        for query, actual, expected in (
            ("PS5 Slim", "PS5 Slim Digital", True),
            ("PS5 Slim", "PS5 Slim Disc", True),
            ("PS5 Digital", "PS5 Slim Digital", True),
            ("PS5 Slim Disc", "PS5 Slim Digital", False),
            ("PS5 Pro", "PS5 Slim", False),
            ("PS5 Slim Digital", "PS5 Digital", False),
        ):
            with self.subTest(query=query, actual=actual):
                self.assertEqual(matches_listing_request(
                    console(title=actual, model=actual), SearchRequest(query), identified_model=actual, final=True,
                ), expected)

    def test_edition_aliases_are_equivalent(self):
        for actual, query in (
            ("PlayStation 5 Slim", "пс 5 слим"),
            ("PLAY STATION5 SLIM", "ps5 slim"),
            ("PlayStation 5 Pro", "плейстейшен 5 про"),
            ("PlayStation 5 Digital Edition", "PS5 Digital"),
            ("PS-5 digital edition", "пс5 без привода"),
            ("PlayStation 5 цифровая версия", "PS5 без дисковода"),
            ("PlayStation 5 Disc Edition", "PS5 с дисководом"),
            ("PS5 disk", "PlayStation 5 версия с дисководом"),
            ("PlayStation 5 дисковая версия", "PS5 Disc"),
        ):
            with self.subTest(actual=actual, query=query):
                self.assertEqual(canonical_evidence("model", actual), canonical_evidence("model", query))
                self.assertTrue(matches_listing_request(console(title=actual, model=actual), SearchRequest(query), identified_model=actual, final=True))

    def test_title_variant_refines_generic_parameters_without_losing_identity(self):
        for variant in ("Slim", "Pro", "Digital Edition", "Disc Edition"):
            with self.subTest(variant=variant):
                candidate = console(title=f"PlayStation 5 {variant}", model="PlayStation 5")
                self.assertEqual(
                    matches_listing_request(candidate, SearchRequest("PS5"), identified_model="PS5", final=True),
                    variant != "Pro",
                )
                self.assertEqual(effective_model_identity(candidate, "PS5"), canonical_evidence("model", f"PS5 {variant}"))

    def test_explicit_title_and_structured_versions_cannot_conflict(self):
        for title, model in (("PS5 Slim", "PS5 Pro"), ("PS5 Pro", "PS5 Slim"), ("PS5 Digital", "PS5 Disc")):
            with self.subTest(title=title, model=model):
                self.assert_rejected_at_both_stages(console(title=title, model=model))
        for title in ("PS5 Slim Fat", "PS5 Slim Pro Fat огромный выбор"):
            with self.subTest(title=title):
                self.assert_rejected_at_both_stages(console(title=title, model="PS5 Slim"))

    def test_specific_model_numbers_are_not_lost_when_matching_family(self):
        candidate = console(title="PS5 Slim", model="PS5 Slim CFI-2016A")
        self.assertIn("cfi 2016 a", effective_model_identity(candidate, "PS5 Slim"))
        self.assertTrue(matches_listing_request(candidate, SearchRequest("PS5"), identified_model="PS5 Slim CFI-2016A", final=True))
        self.assertFalse(matches_listing_request(candidate, SearchRequest("PS5"), identified_model="PS5 Slim CFI-2016B", final=True))

    def test_generic_structure_and_specific_ai_agree_but_explicit_conflict_does_not(self):
        candidate = console(title="PS5 Slim", model="PS5")
        self.assertTrue(matches_listing_request(candidate, SearchRequest("PS5 Slim"), identified_model="PS5 Slim", final=True))
        self.assertFalse(matches_listing_request(candidate, SearchRequest("PS5"), identified_model="PS5 Pro", final=True))

    def test_unknown_version_and_three_known_slim_market_references_stay_separate(self):
        timestamp = datetime.now(timezone.utc).isoformat()
        def analyzed(number, title, price):
            listing = replace(console(title=title, model="PS5"),
                listing_id=str(number), url=f"https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/ps5_{number}",
                price=price, completeness="full", repair_status="never_repaired", parts_status="original",
                delivery="unavailable", seller=SellerSummary(identity_hash=f"offline-seller-{number}"),
                collected_at=timestamp, verified_at=timestamp, verification_status="verified",
            )
            return AnalyzedListing(listing, (), AIReview(
                listing_id=str(number), text_analyzed=True, photos_analyzed=True, photo_coverage=(1,),
                identified_model="PS5", condition="Отличное", matches_request=True,
                verdict=ReviewVerdict.APPROVE, confidence=0.95,
            ))
        generic = analyzed(11000001, "PS5", 40_000)
        slim = analyzed(11000002, "PS5 Slim", 40_000)
        references = tuple(analyzed(11000010 + index, "PS5 Slim", 50_000 + index * 1_000) for index in range(3))
        self.assertEqual(generic.comparable_key()[1], "playstation 5")
        self.assertEqual(slim.comparable_key()[1], "playstation 5 slim")
        self.assertNotEqual(generic.comparable_key(), references[0].comparable_key())
        request = SearchRequest("PS5", location="Ярославль")
        generic_result = rank_listings((generic,), request, market_analyzed=references)[0]
        self.assertEqual(generic_result.comparable_seller_count, 0)
        self.assertFalse(generic_result.below_market)
        slim_result = rank_listings((slim,), request, market_analyzed=references)[0]
        self.assertEqual(slim_result.comparable_seller_count, 3)

    def test_conflicting_disc_and_digital_evidence_is_rejected(self):
        self.assert_rejected_at_both_stages(console(title="PS5 с дисководом", model="PlayStation 5 Digital"), "PS5 Digital", "PS5 Digital")

    def test_accessory_game_service_or_empty_box_is_not_a_console_even_at_console_price(self):
        for title in (
            "Игра для PlayStation 5", "Игры PS5", "Battlefield 6 PS5", "Геймпад DualSense для PS5",
            "Контроллер PS5", "Чехол PS5", "Коробка от PlayStation 5", "PS5 коробка",
            "Пустая коробка PS5", "Дисковод для PS5", "Disc Drive для PS5 Digital Edition",
            "Подставка для PS5", "SSD для PS5", "Аренда PS5", "PS5 в аренду", "Ремонт PS5",
            "Аккаунт PlayStation 5", "Турецкий профиль PS5 PS Plus", "PlayStation 5 PS Plus",
            "Наушники PS5", "Крышки PS5", "Кабель для PlayStation 5", "Зарядная станция PS5",
        ):
            with self.subTest(title=title):
                self.assert_rejected_at_both_stages(console(title=title))

    def test_description_can_explicitly_reveal_only_accessory_or_rental(self):
        for description in (
            "Только коробка от консоли.", "Продаю коробку от PS5.", "Продаю только геймпад.",
            "Без консоли. Коробка оригинальная.", "Консоль не продаётся.", "Сдаю PS5 в аренду.",
        ):
            with self.subTest(description=description):
                self.assert_rejected_at_both_stages(console(description=description))

    def test_real_console_bundle_with_controller_box_or_games_is_allowed(self):
        for title in (
            "Sony PS5 + игры", "Sony PS5 + DualSense", "PS5 с геймпадом", "PS5 с коробкой",
            "PS5 с играми PS4", "PS5 комплект с геймпадом и коробкой", "PS5 + 2 геймпада",
            "Игровая консоль Sony PlayStation 5 без ремонтов",
        ):
            with self.subTest(title=title):
                candidate = console(title=title)
                self.assertTrue(matches_listing_request(candidate, SearchRequest("PS5"), identified_model="PS5", final=True))

    def test_ps4_compatibility_in_description_does_not_turn_real_ps5_into_ps4(self):
        candidate = console(description="Консоль PlayStation 5. Запускает совместимые игры PS4. Контроллер DualSense в комплекте.")
        self.assertTrue(matches_listing_request(candidate, SearchRequest("PS5"), identified_model="PS5", final=True))

    def test_ambiguous_multi_generation_title_is_rejected(self):
        self.assert_rejected_at_both_stages(console(title="Sony PS5 / PS4"))

    def test_unrelated_numeric_model_generations_are_not_fuzzy_matched(self):
        for query, actual in (("iPhone 15", "iPhone 14"), ("Galaxy S23", "Galaxy S24"), ("RTX 4070", "RTX 4060"), ("LG F2V3GS6W", "LG F2V3GS7W")):
            with self.subTest(query=query, actual=actual):
                self.assert_rejected_at_both_stages(console(title=actual, model=actual), query, query)

    def test_same_family_title_conflict_is_rejected_outside_consoles(self):
        for query, title in (("iPhone 15", "iPhone 14"), ("Galaxy S23", "Galaxy S24"), ("RTX 4070", "RTX 4060"), ("iPhone 15", "iPhone 15 Pro")):
            with self.subTest(query=query, title=title):
                self.assert_rejected_at_both_stages(console(title=title, model=query), query, query)

    def test_generic_category_is_not_rejected_for_specific_model_name(self):
        candidate = console()
        self.assertTrue(matches_listing_request(candidate, SearchRequest("Игровая консоль"), identified_model="PS5", final=True))

    def test_missing_model_can_be_reviewed_but_not_invented_at_final_stage(self):
        candidate = console(model="", title="Игровая приставка")
        self.assertFalse(matches_listing_request(candidate, SearchRequest("PS5")))
        self.assertFalse(matches_listing_request(candidate, SearchRequest("PS5"), final=True))

    def test_stock_is_ignored_for_identity(self):
        for stock in ("", "нет", "unknown", "out of stock", "В наличии"):
            with self.subTest(stock=stock):
                self.assertTrue(matches_listing_request(console(stock=stock), SearchRequest("PS5"), identified_model="PS5", final=True))


if __name__ == "__main__":
    unittest.main()
