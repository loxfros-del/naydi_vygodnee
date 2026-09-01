#!/usr/bin/env python3
"""Deterministic safety checks for the NOVA web-to-search boundary."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.models import (  # noqa: E402
    AvailabilityInfo, AvailabilityStatus, ExactMatchResult, Offer, ProductCondition,
    ProductIdentity, Recommendation, RecommendationRole, SearchRequestV2,
    SearchResultStatus, SearchResultV2, SellerInfo, SourceAttempt, SourceStatus,
)
from app.search_v2.request_normalizer import normalize_legacy_request  # noqa: E402
from app.search_v2.query_planner import build_query_plan  # noqa: E402
from app.search_v2.normalization import is_product_page_url  # noqa: E402
from app.search_v2.savings import SavingsEvidence  # noqa: E402
from app.web_api import (  # noqa: E402
    _offer_facts, _web_page_verifier, build_web_response, build_web_service,
    normalize_web_search_request, search_telegram_miniapp, search_web, web_search_health,
)


class FakeService:
    def __init__(self, result: SearchResultV2) -> None:
        self.result = result
        self.request = None

    async def search(self, request):
        self.request = request
        return self.result


class NovaWebApiTests(unittest.TestCase):
    def payload(self, **overrides):
        return {
            "category": "manual",
            "product": "Кофемашина DeLonghi Magnifica",
            "budget": "",
            "city": "Москва",
            "priority": "balance",
            **overrides,
        }

    def result(self) -> SearchResultV2:
        request = SearchRequestV2(
            category="phone", canonical_model="iPhone 16", model_modifiers=["Pro"],
            required_specs={"storage_gb": 256, "sim_variant": "eSIM"},
            condition=ProductCondition.NEW, supported_category=True,
        )
        offer = Offer(
            offer_id="nova-1",
            title="Apple iPhone 16 Pro 256 GB eSIM",
            url="https://shop.example/product/iphone-16-pro-123456",
            platform="NOVA Store",
            seller=SellerInfo(name="Проверенный продавец"),
            price=79_990,
            condition=ProductCondition.NEW,
            exact_match=ExactMatchResult.EXACT,
            availability=AvailabilityInfo(status=AvailabilityStatus.IN_STOCK, available=True),
            identity=ProductIdentity(
                category="phone", canonical_model="iPhone 16", modifiers=["pro"], storage=256,
                condition=ProductCondition.NEW, region_or_sim_variant="esim",
            ),
        )
        evidence = SavingsEvidence(
            selected_price=79_990,
            baseline_price=84_990,
            saving_rub=5_000,
            saving_percent=5.88,
            comparable_offer_count=3,
            source_count=3,
            reference_offer_ids=("a", "b", "c"),
            client_reason="экономия 5 000 ₽ относительно типичной цены 84 990 ₽",
        )
        recommendation = Recommendation(
            role=RecommendationRole.BEST_OVERALL,
            offer_id=offer.offer_id,
            offer=offer,
            score=999,
            reasons=[evidence.client_reason, "точная модель и обязательная конфигурация", "score 999"],
            checks=["adapter timeout", "Проверьте гарантию"],
            savings_evidence=evidence,
        )
        second_offer = replace(
            offer,
            offer_id="nova-2",
            source="dns",
            platform="DNS",
            url="https://www.dns-shop.ru/product/iphone-16-pro-654321",
        )
        return SearchResultV2(
            normalized_request=request,
            recommendations=[recommendation],
            normalized_offers=[offer, second_offer],
            status=SearchResultStatus.SUCCESS,
            errors=["token=secret"],
        )

    def test_blank_budget_keeps_market_wide_auto_detected_product_request(self) -> None:
        legacy = normalize_web_search_request(self.payload())
        request = normalize_legacy_request(legacy)

        self.assertEqual(legacy["budget"], "")
        self.assertIsNone(request.budget)
        self.assertEqual(legacy["category"], "manual")
        self.assertEqual(legacy["model"], "Кофемашина DeLonghi Magnifica")
        self.assertNotIn("storage_gb", legacy)
        self.assertEqual(request.category, "coffee_machine")
        self.assertTrue(request.supported_category)

    def test_old_phone_draft_remains_compatible(self) -> None:
        legacy = normalize_web_search_request({
            "category": "smartphones",
            "product": "Apple iPhone 16 Pro",
            "phoneMemory": "256",
            "phoneVariant": "esim",
            "phoneCondition": "new",
        })
        request = normalize_legacy_request(legacy)

        self.assertEqual(request.category, "phone")
        self.assertEqual(request.required_specs["storage_gb"], 256)
        self.assertEqual(request.required_specs["sim_variant"], "eSIM")

    def test_auto_detected_product_name_reaches_the_query_plan(self) -> None:
        request = normalize_legacy_request(normalize_web_search_request(self.payload()))
        plan = build_query_plan(request, sources=("yandex_web",))

        self.assertTrue(plan.source_queries)
        self.assertTrue(all("кофемашина delonghi magnifica" in item.query.casefold() for item in plan.source_queries))

    def test_response_has_only_safe_client_card_fields(self) -> None:
        response = build_web_response(self.result())
        card = response["recommendations"][0]
        packed = json.dumps(response, ensure_ascii=False).casefold()

        self.assertEqual(card["roleLabel"], "Лучший выбор")
        self.assertEqual(card["price"], "79 990 ₽")
        self.assertIn("экономия 5 000 ₽", card["saving"])
        self.assertEqual(card["checks"], ["Проверьте гарантию"])
        self.assertNotIn("token", packed)
        self.assertNotIn("score", packed)
        self.assertNotIn("балл", packed)
        self.assertNotIn("errors", response)

    def test_one_source_does_not_claim_the_best_market_price(self) -> None:
        result = self.result()
        result.normalized_offers = [result.recommendations[0].offer]

        response = build_web_response(result)

        self.assertEqual(response["recommendations"][0]["roleLabel"], "Точный вариант")
        self.assertFalse(response["coverage"]["marketCovered"])
        self.assertEqual(response["coverage"]["pricedExactSources"], 1)
        self.assertIn("один источник", response["message"])

    def test_unselected_macbook_configuration_is_labelled_honestly(self) -> None:
        result = self.result()
        request = replace(
            result.normalized_request,
            category="laptop",
            canonical_model="MacBook Air M4",
            required_specs={},
        )
        offer = replace(
            result.recommendations[0].offer,
            title="Apple MacBook Air 13 M4 16/512 GB",
            exact_match=ExactMatchResult.COMPATIBLE_VARIANT,
            identity=ProductIdentity(
                category="laptop", brand="Apple", canonical_model="MacBook Air M4",
                storage=512, diagonal=13,
                key_configuration={"ram_gb": 16}, condition=ProductCondition.NEW,
            ),
        )
        recommendation = replace(
            result.recommendations[0],
            offer=offer,
            reasons=["модель совпадает, но конфигурация не выбрана"],
        )
        result = replace(result, normalized_request=request, recommendations=[recommendation], normalized_offers=[offer])

        response = build_web_response(result)
        card = response["recommendations"][0]

        self.assertEqual(card["roleLabel"], "Вариант конфигурации")
        self.assertEqual(card["facts"], ["13″", "RAM 16 GB", "512 GB"])
        self.assertIn("разной конфигурацией", response["message"])

    def test_cards_show_only_concise_confirmed_category_facts(self) -> None:
        laptop = Offer(
            identity=ProductIdentity(
                category="laptop", diagonal=13.6, storage=512,
                key_configuration={"ram_gb": 16},
            ),
            facts={
                "cpu": "Apple M4",
                "ssd": "512 ГБ",
                "fact_evidence": {
                    "cpu": {"confidence": "high", "evidence": "title"},
                    "ssd": {"confidence": "high", "evidence": "title"},
                },
            },
        )
        tv = Offer(
            identity=ProductIdentity(
                category="tv", diagonal=55, refresh_rate=120,
                key_configuration={"resolution": "4K", "panel": "OLED"},
            ),
        )
        monitor = Offer(
            identity=ProductIdentity(
                category="monitor", diagonal=27, refresh_rate=165,
                key_configuration={"resolution": "QHD", "panel": "IPS"},
            ),
        )
        coffee = Offer(
            identity=ProductIdentity(
                category="coffee_machine",
                key_configuration={"machine_type": "automatic", "cappuccinator": True},
            ),
        )

        self.assertEqual(_offer_facts(laptop), ["13.6″", "RAM 16 GB", "SSD 512 GB", "Apple M4"])
        self.assertEqual(_offer_facts(tv), ["55″", "4K", "120 Гц", "OLED"])
        self.assertEqual(_offer_facts(monitor), ["27″", "QHD", "165 Гц", "IPS"])
        self.assertEqual(_offer_facts(coffee), ["Автоматическая", "Капучинатор"])

    def test_phone_card_uses_identity_and_never_leaks_unconfirmed_raw_facts(self) -> None:
        phone = Offer(
            condition=ProductCondition.REFURBISHED,
            identity=ProductIdentity(
                category="phone", storage=256, region_or_sim_variant="esim|ru",
            ),
            facts={
                "cpu": "adapter timeout secret https://example.test/hidden",
                "fact_evidence": {
                    "cpu": {"confidence": "medium", "evidence": "page_text"},
                },
            },
            raw_metadata={"warranty": "token=hidden", "url": "https://example.test/hidden"},
        )
        unconfirmed_laptop = Offer(
            identity=ProductIdentity(category="laptop"),
            facts={
                "cpu": "Intel Core Ultra 7",
                "ram": "32 ГБ",
                "fact_evidence": {
                    "cpu": {"confidence": "medium", "evidence": "page_text"},
                    "ram": {"confidence": "unknown", "evidence": "metadata"},
                },
            },
            raw_metadata={"cpu": "secret", "ram": "32 GB"},
        )

        self.assertEqual(_offer_facts(phone), ["Восстановленный", "256 GB", "eSIM · RU / EAC"])
        self.assertEqual(_offer_facts(unconfirmed_laptop), [])

    def test_unselected_laptop_shows_cheapest_direct_card_per_configuration(self) -> None:
        result = self.result()
        request = replace(
            result.normalized_request,
            category="laptop",
            canonical_model="MacBook 13 M4",
            required_specs={},
        )
        expensive = replace(
            result.recommendations[0].offer,
            offer_id="mac-512",
            title="Apple MacBook Air 13 M4 16 GB / 512 GB",
            url="https://shop.example/product/macbook-m4-512-123456",
            price=116_529,
            exact_match=ExactMatchResult.COMPATIBLE_VARIANT,
            identity=ProductIdentity(
                category="laptop", brand="Apple", canonical_model="MacBook 13 M4",
                storage=512, diagonal=13, key_configuration={"ram_gb": 16},
            ),
        )
        cheaper = replace(
            expensive,
            offer_id="mac-256",
            title="Apple MacBook Air 13 M4 16 GB / 256 GB",
            url="https://shop.example/product/macbook-m4-256-123456",
            price=89_990,
            identity=ProductIdentity(
                category="laptop", brand="Apple", canonical_model="MacBook 13 M4",
                storage=256, diagonal=13, key_configuration={"ram_gb": 16},
            ),
        )
        duplicate_512 = replace(expensive, offer_id="mac-512-high", price=127_285)
        recommendation = replace(result.recommendations[0], offer=expensive)
        result = replace(
            result,
            normalized_request=request,
            recommendations=[recommendation],
            normalized_offers=[expensive, cheaper, duplicate_512],
        )

        response = build_web_response(result)

        self.assertEqual([card["price"] for card in response["recommendations"]], ["89 990 ₽", "116 529 ₽"])
        self.assertTrue(all(card["roleLabel"] == "Вариант конфигурации" for card in response["recommendations"]))
        self.assertIn("разной конфигурацией", response["message"])

    def test_unknown_product_gets_an_honest_unsupported_message(self) -> None:
        request = normalize_legacy_request(normalize_web_search_request(self.payload(product="Кроссовки Nike Air")))
        response = build_web_response(SearchResultV2(
            normalized_request=request,
            status=SearchResultStatus.UNSUPPORTED_CATEGORY,
        ))

        self.assertFalse(request.supported_category)
        self.assertIn("не распознала этот тип товара", response["message"])

    def test_timeout_is_marked_as_an_incomplete_search_not_a_missing_product(self) -> None:
        result = self.result()
        result = replace(result, recommendations=[], normalized_offers=[], status=SearchResultStatus.TIMEOUT)

        response = build_web_response(result)

        self.assertEqual(response["status"], "TIMEOUT")
        self.assertEqual(response["searchState"], "sources_unavailable")
        self.assertIn("Поиск не завершился", response["message"])
        self.assertNotIn("Точных предложений", response["message"])
        self.assertEqual(response["recommendations"], [])

    def test_partial_timeout_without_cards_is_also_an_incomplete_search(self) -> None:
        result = self.result()
        result = replace(
            result,
            recommendations=[],
            normalized_offers=[],
            status=SearchResultStatus.PARTIAL_SUCCESS,
            source_attempts=[SourceAttempt(source="ozon", status=SourceStatus.TIMEOUT)],
        )

        response = build_web_response(result)

        self.assertEqual(response["searchState"], "sources_unavailable")
        self.assertIn("Поиск не завершился", response["message"])

    def test_default_web_service_has_yandex_and_wildberries_discovery_stages(self) -> None:
        service = build_web_service()
        health = web_search_health()

        self.assertEqual(service.web_discovery_sources, ("yandex_web", "wildberries"))
        self.assertIn("yandex_web", service.orchestrator.registry.names())
        self.assertIn("wildberries", service.orchestrator.registry.names())
        self.assertIn(health["yandexWeb"], {"disabled", "missing_credentials", "sdk_missing", "ready"})

    def test_wildberries_detail_url_is_a_product_page_and_requires_page_proof(self) -> None:
        url = "https://www.wildberries.ru/catalog/321732159/detail.aspx"
        offer = Offer(source="wildberries", platform="Wildberries", url=url, price=27_344)
        calls: list[Offer] = []

        def verifier(value: Offer) -> Offer:
            calls.append(value)
            return value

        self.assertTrue(is_product_page_url(url))
        self.assertFalse(is_product_page_url("https://www.wildberries.ru/catalog/321732159"))
        self.assertFalse(is_product_page_url(url + "?text=coffee"))
        self.assertIs(_web_page_verifier(offer, verifier), offer)
        self.assertEqual(calls, [offer])

    def test_search_web_validates_browser_input_before_injected_service(self) -> None:
        service = FakeService(self.result())
        response = asyncio.run(search_web(self.payload(), service=service))

        self.assertIsNotNone(service.request)
        self.assertEqual(service.request["category"], "manual")
        self.assertEqual(service.request["model"], "Кофемашина DeLonghi Magnifica")
        self.assertEqual(response["status"], "SUCCESS")
        with self.assertRaisesRegex(ValueError, "товар"):
            normalize_web_search_request(self.payload(product=""))

    def test_telegram_miniapp_search_requires_init_data_before_the_search_payload(self) -> None:
        class RejectingValidator:
            def validate(self, _value):
                raise ValueError("invalid init data")

        service = FakeService(self.result())
        with self.assertRaisesRegex(ValueError, "invalid init data"):
            asyncio.run(search_telegram_miniapp(
                {"initData": "not-valid", "search": self.payload()},
                validator=RejectingValidator(),
                service=service,
            ))
        self.assertIsNone(service.request)


if __name__ == "__main__":
    unittest.main(verbosity=2)
