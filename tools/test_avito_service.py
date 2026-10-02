#!/usr/bin/env python3
"""Safety and behavior tests for the isolated Avito multimodal service."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from avito_service.ai import OpenAICompatibleReviewer, UnavailableReviewer
from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig, _clean_url, _read_local_env, load_config
from avito_service.errors import AvitoServiceError, ExternalServiceError
from avito_service.http_api import make_server, parse_search_request
from avito_service.jobs import SearchJob, SearchJobRegistry
from avito_service.models import AIReview, AnalyzedListing, CollectionBatch, ReviewVerdict, SearchRequest
from avito_service.normalization import normalize_dataset, normalize_listing
from avito_service.risk_rules import evaluate_rules
from avito_service.service import AvitoAnalysisService
from avito_service.support import SupportInbox


def raw_listing(
    listing_id: int,
    *,
    model: str = "iPhone 15 Pro",
    storage: str = "256 ГБ",
    sim: str = "SIM + eSIM",
    condition: str = "Новое",
    activation: str = "Неактивированный",
    price: int = 55_000,
    description: str = "Новый оригинальный телефон. Цена окончательная, можно проверить до оплаты.",
    images: int = 2,
    seller: str | None = None,
    seller_type: str = "company",
    is_shop: bool | None = None,
) -> dict:
    image_urls = [f"https://{index}0.img.avito.st/image/{listing_id}-{index}" for index in range(1, images + 1)]
    return {
        "id": listing_id,
        "title": f"{model}, {storage}, {sim}",
        "url": f"https://www.avito.ru/moskva/telefony/test_{listing_id}",
        "status": "active",
        "price": price,
        "currency": "₽",
        "description": description,
        "descriptionHtml": f"<p>{description}</p>",
        "images": image_urls,
        "imageCount": len(image_urls),
        "parameters": [
            {"name": "Состояние", "value": condition, "attributeId": 1},
            {"name": "Модель", "value": model, "attributeId": 2},
            {"name": "Встроенная память", "value": storage, "attributeId": 3},
            {"name": "SIM-карты", "value": sim, "attributeId": 4},
            {"name": "История смартфона", "value": activation, "attributeId": 5},
            {"name": "Комплектация", "value": "Полный комплект", "attributeId": 6},
        ],
        "badges": [{"title": "Рыночная цена"}],
        "stock": "В наличии: несколько",
        "seller": {
            "name": seller or f"Seller {listing_id}",
            "sellerType": seller_type,
            "isShop": seller_type == "company" if is_shop is None else is_shop,
            "ratingScore": 4.9,
            "reviewCount": 250,
            "memberSince": "На Авито с 2020",
            "userKey": f"fixture-profile-{seller or listing_id}",
        },
        "productSpecs": {"specs": [{"key": "large repeated catalog", "value": "unused"}]},
        "scrapedAt": datetime.now(timezone.utc).isoformat(),
        "location": "Москва",
        "deliveryAvailable": True,
    }


class CompleteReviewer:
    def __init__(self) -> None:
        self.seen: list[tuple[str, tuple[str, ...]]] = []

    def review_text(self, listing, search=None):
        return AIReview(
            listing_id=listing.listing_id,
            text_analyzed=True,
            photos_analyzed=False,
            identified_model=listing.model,
            storage=listing.storage,
            sim_variant=listing.sim_variant,
            condition=listing.condition,
            description_findings=("Описание прочитано полностью.",),
            verdict=ReviewVerdict.APPROVE,
            confidence=0.92,
        )

    def review_photos(self, listing, text_review):
        self.seen.append((listing.description, listing.images))
        return replace(
            text_review,
            photos_analyzed=True,
            photo_coverage=tuple(range(1, len(listing.images) + 1)),
            photo_findings=tuple(f"Фото {index} проверено." for index in range(1, len(listing.images) + 1)),
        )

    def review(self, listing):
        return self.review_photos(listing, self.review_text(listing))


class FixtureRefreshProvider:
    """Explicit fake live source for stage tests; imported JSON alone is unverified."""
    def __init__(self, rows):
        self.rows = {str(row["id"]): row for row in rows}

    def refresh(self, listings, **kwargs):
        return CollectionBatch(items=tuple(dict(self.rows[item.listing_id],
            scrapedAt=datetime.now(timezone.utc).isoformat()) for item in listings
            if item.listing_id in self.rows))


def analyze_fixture(service, raw_items, request, *, market_rows=None, **kwargs):
    """Inject a separate reference pool and exercise actual finalist refresh."""
    reference_reviewer = CompleteReviewer()
    references = tuple(AnalyzedListing(item, evaluate_rules(item),
        reference_reviewer.review_text(item, replace(request, price_min=None, price_max=None)))
        for item in normalize_dataset(raw_items if market_rows is None else market_rows))
    service.provider = FixtureRefreshProvider(raw_items)
    return service.analyze_dataset(raw_items, request, market_analyzed=references,
        refresh_finalists=True, collection_budget_remaining_usd=0.01, **kwargs)


class IncompleteReviewer:
    def review_text(self, listing, search=None):
        return AIReview(
            listing_id=listing.listing_id,
            text_analyzed=True,
            photos_analyzed=False,
            verdict=ReviewVerdict.APPROVE,
            confidence=0.99,
        )

    def review_photos(self, listing, text_review):
        return replace(text_review, photos_analyzed=True, photo_coverage=(1,))

    def review(self, listing):
        return self.review_photos(listing, self.review_text(listing))


class CostlyReviewer(CompleteReviewer):
    def __init__(self, cost_rub: float) -> None:
        super().__init__()
        self.cost_rub = cost_rub

    def review_photos(self, listing, text_review):
        return replace(
            super().review_photos(listing, text_review),
            cost_rub=text_review.cost_rub + self.cost_rub,
            request_count=text_review.request_count + 1,
        )


class RetryingOpenAIReviewer(OpenAICompatibleReviewer):
    def __init__(self, responses: list[dict]) -> None:
        super().__init__(ServiceConfig(
            ai_api_key="test-key",
            ai_base_url="https://ai.example/v1",
            ai_model="vision-model",
        ))
        self.responses = list(responses)
        self.request_count = 0
        self.payloads = []

    def _request(self, payload):
        self.request_count += 1
        self.payloads.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        return self.responses.pop(0)


class AvitoNormalizationAndRulesTests(unittest.TestCase):
    def test_client_search_mode_and_result_count_are_validated(self) -> None:
        request = parse_search_request({
            "query": "изогнутая палка для декора",
            "mode": "find",
            "priority": "quality",
            "desiredResults": 7,
            "priceMin": 500,
            "priceMax": 5_000,
            "attributes": {"material": "дерево", "dimensions": "80–120 см"},
        })

        self.assertEqual(request.mode, "find")
        self.assertEqual(request.priority, "quality")
        self.assertEqual(request.desired_results, 7)
        self.assertEqual(request.price_min, 500)
        self.assertEqual(request.price_max, 5_000)
        self.assertEqual(request.attribute_map()["material"], "дерево")
        with self.assertRaisesRegex(ValueError, "от 3 до 10"):
            parse_search_request({"query": "стул", "desiredResults": 2})

    def test_local_env_reader_loads_only_allowed_names_without_exposing_values(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "APIFY_TOKEN=test-apify\n"
                "AVITO_AI_API_KEY='test-ai'\n"
                "AVITO_AI_BASE_URL=[https://api.aitunnel.ru/v1](https://api.aitunnel.ru/v1)\n"
                "UNRELATED_SECRET=must-not-load\n",
                encoding="utf-8",
            )

            values = _read_local_env(path)

        self.assertEqual(values["APIFY_TOKEN"], "test-apify")
        self.assertEqual(values["AVITO_AI_API_KEY"], "test-ai")
        self.assertNotIn("UNRELATED_SECRET", values)
        self.assertEqual(
            _clean_url(values["AVITO_AI_BASE_URL"]),
            "https://api.aitunnel.ru/v1",
        )

    def test_existing_local_env_is_recognized_without_returning_secret_values(self) -> None:
        readiness = ServiceConfig(apify_token="fixture-only").readiness()

        self.assertEqual(set(readiness), {"apify", "ai"})

    def test_listing_id_is_recovered_from_url_when_actor_omits_id(self) -> None:
        raw = raw_listing(8086764780)
        raw.pop("id")

        listing = normalize_listing(raw)

        self.assertEqual(listing.listing_id, "8086764780")

    def test_listing_without_numeric_url_id_is_not_treated_as_direct_listing(self) -> None:
        raw = raw_listing(8086764782)
        raw.pop("id")
        raw["url"] = "https://www.avito.ru/moskva/telefony/iphone-pro-purple"

        self.assertEqual(normalize_dataset([raw]), ())

    def test_actor_metadata_row_does_not_abort_valid_listings(self) -> None:
        listings = normalize_dataset([
            {"type": "search-metadata", "message": "No exact matches on first page"},
            raw_listing(8086764781),
        ])

        self.assertEqual([item.listing_id for item in listings], ["8086764781"])

    def test_zen_input_forces_details_and_disables_personal_data(self) -> None:
        provider = ZenStudioProvider(ServiceConfig(apify_token="test-only"))
        payload = provider._actor_input(SearchRequest("iPhone 15 Pro", category="phones", max_results=20))

        self.assertTrue(payload["includeDetails"])
        self.assertFalse(payload["includePhone"])
        self.assertFalse(payload["includeReviews"])
        self.assertEqual(payload["maxResults"], 20)

    def test_zen_input_caps_collection_to_report_budget(self) -> None:
        config = ServiceConfig(apify_token="test-only")
        provider = ZenStudioProvider(config)

        payload = provider._actor_input(SearchRequest("iPhone 15 Pro", category="phones", max_results=100))

        self.assertEqual(config.safe_apify_listing_limit, 41)
        self.assertEqual(payload["maxResults"], 41)

    def test_zen_upgrade_record_reports_plan_limit_instead_of_empty_results(self) -> None:
        class UpgradeRequiredProvider(ZenStudioProvider):
            def __init__(self) -> None:
                super().__init__(ServiceConfig(apify_token="test-only"))
                self.responses = [
                    {"data": {
                        "id": "run-id",
                        "status": "SUCCEEDED",
                        "defaultDatasetId": "dataset-id",
                        "usageTotalUsd": 0.005,
                    }},
                    [{
                        "_upgradeRequired": True,
                        "_message": "Untrusted actor message",
                        "_upgradeUrl": "https://console.apify.com/billing",
                    }],
                ]

            def _json_request(self, method, url, **kwargs):
                return self.responses.pop(0)

        provider = UpgradeRequiredProvider()
        request = SearchRequest("PS5", location="Ярославль")
        with self.assertRaisesRegex(ExternalServiceError, "повысить тариф"):
            provider.collect(request)
        with self.assertRaisesRegex(ExternalServiceError, "повысить тариф"):
            provider.collect(request)
        self.assertEqual(provider.responses, [])

    def test_normalizer_keeps_complete_description_and_every_safe_photo(self) -> None:
        raw = raw_listing(101, images=3, description="Первая строка\nВторая строка — важное условие.")
        listing = normalize_listing(raw)

        self.assertEqual(listing.description, raw["description"])
        self.assertEqual(listing.images, tuple(raw["images"]))
        self.assertEqual(listing.parameter("Модель"), "iPhone 15 Pro")
        packed = json.dumps(listing.public_dict(), ensure_ascii=False)
        self.assertNotIn("private-key", packed)
        self.assertNotIn("productSpecs", packed)
        self.assertNotIn("descriptionHtml", packed)

    def test_normalizer_accepts_immutable_collection_batch(self) -> None:
        listings = normalize_dataset((raw_listing(106),))

        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].listing_id, "106")

    def test_two_stage_payload_separates_full_text_from_all_images(self) -> None:
        listing = normalize_listing(raw_listing(102, images=3, description="Полное описание без обрезания " * 80))
        search = SearchRequest(
            "точный iPhone 15 Pro",
            category="phones",
            priority="quality",
            attributes=(("storage", "256 ГБ"), ("color", "чёрный")),
        )
        text_payload = OpenAICompatibleReviewer.build_text_payload(listing, "vision-model", search)
        text = text_payload["messages"][1]["content"]
        text_review = CompleteReviewer().review_text(listing)
        photo_payload = OpenAICompatibleReviewer.build_photo_payload(listing, "vision-model", text_review)
        content = photo_payload["messages"][1]["content"]
        image_urls = [item["image_url"]["url"] for item in content if item["type"] == "image_url"]
        image_details = [item["image_url"]["detail"] for item in content if item["type"] == "image_url"]

        self.assertIn(listing.description, text)
        self.assertIn(search.query, text)
        self.assertIn('"priority": "quality"', text)
        self.assertIn('"required_attributes": {"storage": "256 ГБ", "color": "чёрный"}', text)
        self.assertNotIn("image_url", json.dumps(text_payload, ensure_ascii=False))
        self.assertEqual(image_urls, list(listing.images))
        self.assertEqual(image_details, ["high", "high", "high"])
        self.assertNotIn(listing.description, json.dumps(photo_payload, ensure_ascii=False))
        self.assertIn("недоверенное доказательство", text_payload["messages"][0]["content"])

    def test_text_batch_keeps_every_full_description_and_uses_json_object(self) -> None:
        listings = tuple(normalize_listing(raw_listing(
            110 + index,
            description=f"Полное уникальное описание {index} " * 100,
        )) for index in range(3))

        payload = OpenAICompatibleReviewer.build_text_batch_payload(listings, "vision-model")
        packed = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["response_format"], {"type": "json_object"})
        for listing in listings:
            self.assertIn(listing.description, packed)
        self.assertNotIn("image_url", packed)

    def test_ai_content_accepts_reasoning_json_when_content_is_null(self) -> None:
        content = OpenAICompatibleReviewer._content({
            "choices": [{
                "message": {
                    "content": None,
                    "reasoning_content": 'Проверка завершена. {"text_analyzed": true}',
                },
                "finish_reason": "stop",
            }]
        })

        self.assertIn('"text_analyzed": true', content)

    def test_real_reviewer_parses_one_batch_request_and_allocates_cost(self) -> None:
        listings = tuple(normalize_listing(raw_listing(120 + index)) for index in range(2))
        reviews = [{
            "listing_id": listing.listing_id,
            "text_analyzed": True,
            "matches_request": True,
            "identified_model": listing.model,
            "storage": listing.storage,
            "sim_variant": listing.sim_variant,
            "condition": listing.condition,
            "description_findings": ["Описание прочитано."],
            "defects": [],
            "price_conditions": [],
            "conflicts": [],
            "verdict": "approve",
            "confidence": 0.9,
        } for listing in listings]
        response = {
            "usage": {"cost_rub": 1.2},
            "choices": [{"message": {"content": json.dumps({"reviews": reviews})}}],
        }
        reviewer = RetryingOpenAIReviewer([response])

        result = reviewer.review_text_batch(listings)

        self.assertEqual(reviewer.request_count, 1)
        self.assertTrue(all(review.text_analyzed for review in result))
        self.assertAlmostEqual(sum(review.cost_rub for review in result), 1.2)

    def test_http_200_generation_error_is_surfaced_as_provider_failure(self) -> None:
        listing = normalize_listing(raw_listing(125))
        responses = [
            {"usage": {"cost_rub": 0.4}, "error": {"code": 502, "message": "provider"}},
            {"usage": {"cost_rub": 0.6}, "error": {"code": 502, "message": "provider"}},
        ]
        reviewer = RetryingOpenAIReviewer(responses)

        with self.assertRaisesRegex(ExternalServiceError, "AI Tunnel") as caught:
            reviewer.review_text(listing)

        self.assertEqual(caught.exception.code, "AI_INVALID_RESPONSE")
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(reviewer.request_count, 2)

    def test_generation_error_retries_without_response_format(self) -> None:
        listing = normalize_listing(raw_listing(126))
        success = {
            "usage": {"cost_rub": 0.2},
            "choices": [{"message": {"content": json.dumps({
                "text_analyzed": True,
                "matches_request": True,
                "identified_model": listing.model,
                "storage": listing.storage,
                "sim_variant": listing.sim_variant,
                "condition": listing.condition,
                "verdict": "approve",
                "confidence": 0.9,
            })}}],
        }
        reviewer = RetryingOpenAIReviewer([
            {"usage": {"cost_rub": 0.1}, "error": {"code": 400, "message": "unsupported"}},
            success,
        ])

        review = reviewer.review_text(listing)

        self.assertTrue(review.text_analyzed)
        self.assertIn("response_format", reviewer.payloads[0])
        self.assertNotIn("response_format", reviewer.payloads[1])
        self.assertAlmostEqual(review.cost_rub, 0.3)

    def test_nand_failure_is_a_critical_rejection_rule(self) -> None:
        listing = normalize_listing(raw_listing(
            103,
            condition="Отличное",
            description="Итоговая цена индивидуальна. У аппарата зафиксирован выход из строя чипа памяти NAND.",
        ))
        findings = evaluate_rules(listing)
        codes = {item.code for item in findings}

        self.assertIn("NAND_DEFECT", codes)
        self.assertIn("NON_FINAL_PRICE", codes)

    def test_activation_conflict_is_exposed(self) -> None:
        listing = normalize_listing(raw_listing(
            104,
            activation="Неактивированный",
            description="Телефон полностью новый. Был активирован сотрудниками таможенной службы.",
        ))

        self.assertIn("ACTIVATION_CONFLICT", {item.code for item in evaluate_rules(listing)})

    def test_word_not_activated_does_not_create_false_activation_conflict(self) -> None:
        listing = normalize_listing(raw_listing(
            105,
            activation="Неактивированный",
            description="Новый не активированный телефон в заводской упаковке.",
        ))

        codes = {item.code for item in evaluate_rules(listing)}
        self.assertNotIn("ACTIVATION_CONFLICT", codes)
        self.assertNotIn("ACTIVATED_AS_NEW", codes)


class AvitoPipelineTests(unittest.TestCase):
    def test_pipeline_passes_complete_evidence_to_reviewer(self) -> None:
        reviewer = CompleteReviewer()
        raw = raw_listing(201, images=4, description="Описание целиком " * 100)
        service = AvitoAnalysisService(None, reviewer, ai_concurrency=1)

        report = analyze_fixture(service, [raw], SearchRequest("iPhone 15 Pro", category="phones", mode="find"))

        self.assertEqual(reviewer.seen, [(raw["description"].strip(), tuple(raw["images"]))])
        self.assertEqual(report.recommendations[0].role.value, "TOP")
        self.assertFalse(report.recommendations[0].below_market)

    def test_incomplete_photo_coverage_can_never_be_recommended(self) -> None:
        service = AvitoAnalysisService(None, IncompleteReviewer(), ai_concurrency=1)
        report = analyze_fixture(service,
            [raw_listing(202, images=3)],
            SearchRequest("iPhone 15 Pro", category="phones"),
        )

        self.assertEqual([item.role.value for item in report.recommendations], ["CAUTION"])
        self.assertFalse(report.recommendations[0].analyzed.ai_review.is_complete_for(
            report.recommendations[0].analyzed.listing
        ))

    def test_only_same_configuration_builds_below_market_evidence(self) -> None:
        rows = [raw_listing(301, price=45_000, seller="A")]
        market_rows = [raw_listing(3010 + index, price=55_000, seller=f"Reference {index}") for index in range(10)]
        market_rows.extend([
            raw_listing(304, storage="512 ГБ", price=10_000, seller="D"),
            raw_listing(305, model="iPhone 15 Pro Max", price=5_000, seller="E"),
        ])
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)
        report = analyze_fixture(service, rows, SearchRequest("iPhone 15 Pro", category="phones"), market_rows=market_rows)
        top = next(item for item in report.recommendations if item.role.value == "TOP")

        self.assertEqual(top.analyzed.listing.listing_id, "301")
        self.assertEqual(top.market_median, 55_000)
        self.assertEqual(top.comparable_count, 10)
        self.assertEqual(top.comparable_seller_count, 10)
        self.assertNotIn("304", {entry["url"].rsplit("_", 1)[-1] for entry in top.market_evidence})

    def test_market_median_gives_each_seller_one_vote(self) -> None:
        rows = [raw_listing(3060, price=40_000, seller="Candidate Shop")]
        market_rows = [raw_listing(30601 + index, price=100_000, seller="Duplicate Shop") for index in range(12)]
        market_rows.extend(raw_listing(30620 + index, price=50_000, seller=f"Independent {index}") for index in range(9))
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1, ai_max_listings=6)

        report = analyze_fixture(service,
            rows,
            SearchRequest("iPhone 15 Pro", category="phones", desired_results=3),
            market_rows=market_rows,
        )
        bargain = next(
            item for item in report.recommendations
            if item.analyzed.listing.listing_id == "3060"
        )

        self.assertEqual(bargain.market_median, 50_000)
        self.assertEqual(bargain.comparable_count, 10)
        self.assertEqual(bargain.comparable_seller_count, 10)
        self.assertEqual(len(bargain.market_evidence), 10)
        self.assertTrue(bargain.below_market)

    def test_missing_seller_names_do_not_fake_independent_market_sources(self) -> None:
        rows = [
            raw_listing(30601, price=48_000),
            raw_listing(30602, price=50_000),
            raw_listing(30603, price=50_000),
        ]
        for row in rows:
            row["seller"]["name"] = ""
            row["seller"].pop("userKey", None)
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)

        report = analyze_fixture(service,
            rows,
            SearchRequest("iPhone 15 Pro", category="phones", desired_results=3),
        )
        recommendations = report.recommendations

        self.assertTrue(recommendations)
        self.assertTrue(all(item.comparable_seller_count == 0 for item in recommendations))
        self.assertFalse(any(item.below_market for item in recommendations))
        self.assertTrue(any(item.role.value == "TOP" for item in recommendations))
        self.assertTrue(all(item.saving_amount is None for item in recommendations))

    def test_unknown_seller_does_not_complete_independent_market_pair(self) -> None:
        rows = [
            raw_listing(30610, price=40_000, seller="Candidate Shop"),
            raw_listing(30611, price=50_000, seller="Known Shop"),
            raw_listing(30612, price=50_000),
        ]
        rows[2]["seller"]["name"] = ""
        rows[2]["seller"].pop("userKey", None)
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)

        report = analyze_fixture(service,
            rows,
            SearchRequest("iPhone 15 Pro", category="phones", desired_results=3),
        )

        self.assertTrue(report.recommendations)
        by_id = {item.analyzed.listing.listing_id: item for item in report.recommendations}
        self.assertEqual(by_id["30610"].comparable_seller_count, 1)
        self.assertEqual(by_id["30612"].comparable_seller_count, 0)
        self.assertTrue(all(item.market_median is None for item in report.recommendations))
        self.assertFalse(any(item.below_market for item in report.recommendations))

    def test_comparable_market_normalizes_gb_unit_spelling(self) -> None:
        rows = [raw_listing(3066, storage="256GB", price=48_000, seller="A")]
        market_rows = [raw_listing(30670 + index, storage=("256 ГБ", "256 gb", "256GB")[index % 3], price=50_000, seller=f"Reference {index}") for index in range(10)]
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)

        report = analyze_fixture(service,
            rows,
            SearchRequest(
                "iPhone 15 Pro",
                category="phones",
                desired_results=3,
                required_storage="256 ГБ",
            ),
            market_rows=market_rows,
        )
        top = next(item for item in report.recommendations if item.role.value == "TOP")

        self.assertEqual(top.analyzed.listing.listing_id, "3066")
        self.assertEqual(top.market_median, 50_000)
        self.assertEqual(top.comparable_count, 10)

    def test_price_range_is_enforced_before_any_ai_request(self) -> None:
        reviewer = CompleteReviewer()
        rows = [
            raw_listing(3069, price=39_999),
            raw_listing(3070, price=50_000),
            raw_listing(3073, price=60_001),
        ]
        service = AvitoAnalysisService(None, reviewer, ai_concurrency=1)

        report = analyze_fixture(service,
            rows,
            SearchRequest(
                "iPhone 15 Pro",
                category="phones",
                mode="find",
                price_min=40_000,
                price_max=60_000,
            ),
        )

        self.assertEqual(report.costs.ai_text_reviewed_count, 1)
        self.assertEqual(len(reviewer.seen), 1)
        self.assertEqual(
            [item["listing"]["id"] for item in report.public_dict()["recommendations"]],
            ["3070"],
        )

    def test_two_thousand_ruble_saving_is_enough_even_below_five_percent(self) -> None:
        rows = [raw_listing(311, price=48_000, seller="A")]
        market_rows = [raw_listing(3120 + index, price=50_000, seller=f"Reference {index}") for index in range(10)]
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)

        report = analyze_fixture(service, rows, SearchRequest("iPhone 15 Pro", category="phones"), market_rows=market_rows)
        top = next(item for item in report.recommendations if item.role.value == "TOP")

        self.assertEqual(top.analyzed.listing.listing_id, "311")
        self.assertEqual(top.saving_amount, 2_000)
        self.assertEqual(top.saving_percent, 4.0)
        self.assertEqual(top.public_dict()["savingAmount"], 2_000)

    def test_two_sellers_are_not_enough_for_below_market_claim(self) -> None:
        rows = [
            raw_listing(3131, price=40_000, seller="A"),
            raw_listing(3132, price=50_000, seller="A"),
            raw_listing(3133, price=50_000, seller="B"),
        ]
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)

        report = analyze_fixture(service, rows, SearchRequest("iPhone 15 Pro", category="phones"))

        self.assertTrue(report.recommendations)
        self.assertTrue(all(item.comparable_seller_count == 1 for item in report.recommendations))
        self.assertTrue(all(item.market_median is None for item in report.recommendations))
        self.assertFalse(any(item.below_market for item in report.recommendations))
        self.assertTrue(any(item.role.value == "TOP" for item in report.recommendations))
        self.assertTrue(all(item.saving_amount is None for item in report.recommendations))

    def test_small_saving_is_allowed_with_ten_independent_comparable_sellers(self) -> None:
        rows = [raw_listing(314, price=48_001, seller="A")]
        market_rows = [raw_listing(3150 + index, price=50_000, seller=f"Reference {index}") for index in range(10)]
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)

        report = analyze_fixture(service, rows, SearchRequest("iPhone 15 Pro", category="phones"), market_rows=market_rows)

        self.assertTrue(any(item.role.value == "TOP" for item in report.recommendations))
        self.assertEqual(report.recommendations[0].comparable_seller_count, 10)
        self.assertEqual(report.recommendations[0].saving_amount, 1_999)
        self.assertTrue(report.recommendations[0].below_market)

    def test_negative_stock_does_not_block_ai_or_customer_results(self) -> None:
        unavailable = raw_listing(317, price=40_000)
        unavailable["stock"] = "Нет в наличии"
        reviewer = CompleteReviewer()
        service = AvitoAnalysisService(None, reviewer, ai_concurrency=1)

        report = analyze_fixture(service,
            [unavailable, raw_listing(318, price=45_000)],
            SearchRequest("iPhone 15 Pro", category="phones", mode="find"),
        )

        self.assertEqual(report.costs.ai_text_reviewed_count, 2)
        self.assertEqual(len(reviewer.seen), 2)
        self.assertEqual({item["listing"]["id"] for item in report.public_dict()["recommendations"]}, {"317", "318"})

    def test_empty_stock_does_not_block_ai_or_customer_results(self) -> None:
        unconfirmed = raw_listing(3181, price=40_000)
        unconfirmed["stock"] = ""
        reviewer = CompleteReviewer()
        service = AvitoAnalysisService(None, reviewer, ai_concurrency=1)

        report = analyze_fixture(service,
            [unconfirmed, raw_listing(3182, price=45_000)],
            SearchRequest("iPhone 15 Pro", category="phones", mode="find"),
        )

        self.assertEqual(report.costs.ai_text_reviewed_count, 2)
        self.assertEqual(len(reviewer.seen), 2)
        self.assertEqual({item["listing"]["id"] for item in report.public_dict()["recommendations"]}, {"3181", "3182"})

    def test_search_page_url_never_becomes_a_customer_card(self) -> None:
        search_page = raw_listing(319, price=40_000)
        search_page["url"] = "https://www.avito.ru/moskva/telefony/apple-ASgBAgICAUTgtg3m"
        reviewer = CompleteReviewer()
        service = AvitoAnalysisService(None, reviewer, ai_concurrency=1)

        report = analyze_fixture(service,
            [search_page, raw_listing(3200, price=45_000)],
            SearchRequest("iPhone 15 Pro", category="phones", mode="find"),
        )

        self.assertEqual(report.costs.ai_text_reviewed_count, 1)
        self.assertEqual([item["listing"]["id"] for item in report.public_dict()["recommendations"]], ["3200"])

    def test_deterministic_critical_defect_overrides_approving_ai(self) -> None:
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)
        report = analyze_fixture(service, [
            raw_listing(306, condition="Отличное", description="Не работает чип памяти NAND, есть ошибки памяти.")
        ], SearchRequest("iPhone 15 Pro", category="phones"))

        self.assertEqual(report.recommendations[0].role.value, "REJECTED")

    def test_ai_parser_fails_closed_when_one_photo_was_skipped(self) -> None:
        listing = normalize_listing(raw_listing(307, images=2))
        response = json.dumps({
            "text_analyzed": True,
            "photos_analyzed": True,
            "photo_coverage": [1],
            "verdict": "approve",
            "confidence": 1,
        })

        review = OpenAICompatibleReviewer.parse_review(listing, response)

        self.assertEqual(review.verdict, ReviewVerdict.CAUTION)
        self.assertFalse(review.is_complete_for(listing))

    def test_ai_parser_accepts_zero_based_coverage_and_string_booleans(self) -> None:
        listing = normalize_listing(raw_listing(3071, images=3))
        response = "Ответ модели:\n```json\n" + json.dumps({
            "text_analyzed": "true",
            "photos_analyzed": "да",
            "photo_coverage": [0, 1, 2],
            "matches_request": "да",
            "verdict": "одобрено",
            "confidence": 0.9,
        }, ensure_ascii=False) + "\n```"

        review = OpenAICompatibleReviewer.parse_review(listing, response)

        self.assertTrue(review.is_complete_for(listing))
        self.assertEqual(review.verdict, ReviewVerdict.APPROVE)

    def test_ai_parser_requires_explicit_request_match_confirmation(self) -> None:
        listing = normalize_listing(raw_listing(3072))
        response = json.dumps({
            "text_analyzed": True,
            "identified_model": listing.model,
            "storage": listing.storage,
            "sim_variant": listing.sim_variant,
            "condition": listing.condition,
            "verdict": "approve",
            "confidence": 0.99,
        })

        review = OpenAICompatibleReviewer.parse_text_review(listing, response)

        self.assertFalse(review.matches_request)
        self.assertEqual(review.verdict, ReviewVerdict.CAUTION)
        self.assertIn("совпадение", review.error.casefold())

    def test_ai_parser_does_not_turn_neutral_payment_methods_into_price_conditions(self) -> None:
        listing = normalize_listing(raw_listing(3073))
        base = {
            "text_analyzed": True, "identified_model": listing.model,
            "storage": listing.storage, "sim_variant": listing.sim_variant,
            "condition": listing.condition, "matches_request": True,
            "mismatch_reason": "", "description_findings": [], "defects": [],
            "conflicts": [], "verdict": "approve", "confidence": 0.9,
        }
        neutral = OpenAICompatibleReviewer.parse_text_review(listing, json.dumps({
            **base, "price_conditions": [
                "Оплата наличными, терминалом, QR кодом, кредитом, рассрочкой",
                "Кредит/рассрочка оформляется только при посещении магазина и наличии паспорта",
            ],
        }, ensure_ascii=False))
        self.assertEqual(neutral.price_conditions, ())
        conditional = OpenAICompatibleReviewer.parse_text_review(listing, json.dumps({
            **base, "price_conditions": [
                "Цена действует только при оплате наличными",
                "При оплате картой комиссия 15%",
                "Цена указана при условии trade-in",
            ],
        }, ensure_ascii=False))
        self.assertEqual(len(conditional.price_conditions), 3)

    def test_ai_retries_incomplete_coverage_and_accepts_explicit_range(self) -> None:
        listing = normalize_listing(raw_listing(308, images=3))
        first = {"usage": {"cost_rub": 1.25}, "choices": [{"message": {"content": json.dumps({
            "text_analyzed": True,
            "photos_analyzed": True,
            "photo_coverage": [1],
            "verdict": "approve",
            "confidence": 0.8,
        })}}]}
        second = {"usage": {"cost_rub": 2.5}, "choices": [{"message": {"content": json.dumps({
            "text_analyzed": True,
            "photos_analyzed": True,
            "photo_coverage": "1-3",
            "verdict": "approve",
            "confidence": 0.9,
        })}}]}
        reviewer = RetryingOpenAIReviewer([first, second])

        review = reviewer.review_photos(listing, CompleteReviewer().review_text(listing))

        self.assertEqual(reviewer.request_count, 2)
        self.assertTrue(review.is_complete_for(listing))
        self.assertEqual(review.cost_rub, 3.75)

    def test_cost_controller_stops_before_predicted_ai_budget_overrun(self) -> None:
        reviewer = CostlyReviewer(8.0)
        service = AvitoAnalysisService(
            None,
            reviewer,
            ai_concurrency=1,
            ai_max_listings=5,
            ai_max_cost_rub=15.0,
        )
        rows = [raw_listing(360 + index, price=50_000 + index * 1_000) for index in range(5)]

        report = analyze_fixture(service, rows, SearchRequest("iPhone 15 Pro", category="phones"))

        self.assertEqual(report.costs.ai_text_reviewed_count, 5)
        self.assertEqual(report.costs.ai_photo_reviewed_count, 1)
        self.assertEqual(report.costs.ai_skipped_count, 0)
        self.assertEqual(report.costs.ai_cost_rub, 8.0)
        self.assertLessEqual(report.costs.ai_cost_rub, 15.0)

    def test_text_stage_finishes_before_photo_stage_for_shortlist(self) -> None:
        class TrackingReviewer(CompleteReviewer):
            def __init__(self):
                super().__init__()
                self.events = []

            def review_text(self, listing, search=None):
                self.events.append(("text", listing.listing_id))
                return super().review_text(listing, search)

            def review_photos(self, listing, text_review):
                self.events.append(("photo", listing.listing_id))
                return super().review_photos(listing, text_review)

        reviewer = TrackingReviewer()
        rows = [raw_listing(380 + index, price=40_000 + index * 1_000) for index in range(4)]
        service = AvitoAnalysisService(
            None,
            reviewer,
            ai_text_max_listings=4,
            ai_max_listings=2,
            ai_concurrency=1,
        )

        report = analyze_fixture(service, rows, SearchRequest("iPhone 15 Pro", category="phones"))

        self.assertEqual([stage for stage, _ in reviewer.events[:4]], ["text"] * 4)
        self.assertEqual([stage for stage, _ in reviewer.events[4:]], ["photo"] * 2)
        self.assertEqual(report.costs.ai_text_reviewed_count, 4)
        self.assertEqual(report.costs.ai_photo_reviewed_count, 2)

    def test_text_stage_uses_batches_instead_of_one_request_per_listing(self) -> None:
        class BatchReviewer(CompleteReviewer):
            def __init__(self):
                super().__init__()
                self.batch_calls = 0
                self.single_calls = 0

            def review_text(self, listing, search=None):
                self.single_calls += 1
                return super().review_text(listing, search)

            def review_text_batch(self, listings, search=None):
                self.batch_calls += 1
                return tuple(CompleteReviewer.review_text(self, listing, search) for listing in listings)

        reviewer = BatchReviewer()
        rows = [raw_listing(700 + index, price=40_000 + index * 100) for index in range(18)]
        service = AvitoAnalysisService(
            None,
            reviewer,
            ai_text_batch_size=6,
            ai_text_max_listings=18,
            ai_max_listings=1,
        )

        report = analyze_fixture(service, rows, SearchRequest("iPhone 15 Pro", category="phones"))

        self.assertEqual(reviewer.batch_calls, 3)
        self.assertEqual(reviewer.single_calls, 0)
        self.assertEqual(report.costs.ai_text_reviewed_count, 18)

    def test_repeated_empty_batch_opens_circuit_and_skips_remaining_calls(self) -> None:
        class FailingBatchReviewer(CompleteReviewer):
            def __init__(self):
                super().__init__()
                self.batch_calls = 0

            def review_text_batch(self, listings, search=None):
                self.batch_calls += 1
                return tuple(
                    AIReview(
                        listing_id=listing.listing_id,
                        verdict=ReviewVerdict.CAUTION,
                        error="Нейросеть не вернула текст JSON.",
                    )
                    for listing in listings
                )

        reviewer = FailingBatchReviewer()
        rows = [raw_listing(730 + index, price=40_000 + index * 100) for index in range(20)]
        service = AvitoAnalysisService(
            None,
            reviewer,
            ai_text_batch_size=5,
            ai_text_max_listings=20,
            ai_max_listings=10,
        )

        report = analyze_fixture(service, rows, SearchRequest("iPhone 15 Pro", category="phones"))

        self.assertEqual(reviewer.batch_calls, 1)
        self.assertEqual(report.costs.ai_text_reviewed_count, 5)
        self.assertEqual(report.costs.ai_skipped_count, 15)

    def test_text_rejection_never_reaches_photo_stage(self) -> None:
        class RejectingTextReviewer(CompleteReviewer):
            def __init__(self):
                super().__init__()
                self.photo_ids = []

            def review_text(self, listing, search=None):
                review = super().review_text(listing, search)
                if listing.listing_id == "390":
                    return replace(review, verdict=ReviewVerdict.REJECT, defects=("Сломан каркас.",))
                return review

            def review_photos(self, listing, text_review):
                self.photo_ids.append(listing.listing_id)
                return super().review_photos(listing, text_review)

        reviewer = RejectingTextReviewer()
        rows = [
            raw_listing(390, model="игровой стул", price=1_000),
            raw_listing(391, model="игровой стул", price=2_000),
            raw_listing(392, model="игровой стул", price=3_000),
        ]
        service = AvitoAnalysisService(None, reviewer, ai_max_listings=2, ai_concurrency=1)

        analyze_fixture(service, rows, SearchRequest("игровой стул", category="all"))

        self.assertNotIn("390", reviewer.photo_ids)
        self.assertEqual(len(reviewer.photo_ids), 2)

    def test_photo_stage_uses_reserves_until_requested_results_are_complete(self) -> None:
        class ReplacingReviewer(CompleteReviewer):
            def __init__(self):
                super().__init__()
                self.photo_ids = []

            def review_photos(self, listing, text_review):
                self.photo_ids.append(listing.listing_id)
                review = super().review_photos(listing, text_review)
                if len(self.photo_ids) <= 2:
                    return replace(review, verdict=ReviewVerdict.REJECT, defects=("Фото не соответствует товару.",))
                return review

        reviewer = ReplacingReviewer()
        rows = [raw_listing(800 + index, price=40_000 + index * 100) for index in range(7)]
        service = AvitoAnalysisService(None, reviewer, ai_max_listings=6, ai_concurrency=1)

        report = analyze_fixture(service,
            rows,
            SearchRequest("iPhone 15 Pro", category="phones", mode="find", desired_results=3),
        )

        self.assertEqual(len(reviewer.photo_ids), 5)
        self.assertEqual(len(report.public_dict()["recommendations"]), 3)

    def test_complete_caution_reviews_do_not_consume_customer_quota(self) -> None:
        class CautionThenApproveReviewer(CompleteReviewer):
            def __init__(self):
                super().__init__()
                self.photo_ids = []

            def review_photos(self, listing, text_review):
                self.photo_ids.append(listing.listing_id)
                review = super().review_photos(listing, text_review)
                if len(self.photo_ids) <= 3:
                    return replace(
                        review,
                        verdict=ReviewVerdict.CAUTION,
                        conflicts=("На фото не виден серийный номер.",),
                    )
                return review

        reviewer = CautionThenApproveReviewer()
        rows = [raw_listing(805 + index, price=40_000 + index * 100) for index in range(8)]
        service = AvitoAnalysisService(None, reviewer, ai_max_listings=8, ai_concurrency=1)

        report = analyze_fixture(service,
            rows,
            SearchRequest("iPhone 15 Pro", category="phones", mode="find", desired_results=3),
        )

        self.assertEqual(len(reviewer.photo_ids), 6)
        self.assertEqual(len(report.public_dict()["recommendations"]), 3)
        self.assertTrue(all(
            item["analysis"]["verdict"] == "approve"
            for item in report.public_dict()["recommendations"]
        ))

    def test_ai_request_mismatch_is_removed_before_photo_stage(self) -> None:
        class RelevanceReviewer(CompleteReviewer):
            def __init__(self):
                super().__init__()
                self.photo_ids = []

            def review_text(self, listing, search=None):
                review = super().review_text(listing, search)
                if listing.listing_id == "820":
                    return replace(
                        review,
                        matches_request=False,
                        mismatch_reason="Это аксессуар, а не запрошенный товар.",
                    )
                return review

            def review_photos(self, listing, text_review):
                self.photo_ids.append(listing.listing_id)
                return super().review_photos(listing, text_review)

        reviewer = RelevanceReviewer()
        service = AvitoAnalysisService(None, reviewer, ai_max_listings=3, ai_concurrency=1)
        report = analyze_fixture(service,
            [raw_listing(820, price=1_000), raw_listing(821, price=45_000)],
            SearchRequest("iPhone 15 Pro", category="phones", mode="find", desired_results=3),
        )

        self.assertEqual(reviewer.photo_ids, ["821"])
        self.assertEqual(report.public_dict()["recommendations"][0]["listing"]["id"], "821")

    def test_ps5_console_query_excludes_games_accounts_and_topups(self) -> None:
        game = raw_listing(750, model="PS5", price=130)
        game["title"] = "Battlefield 5 PS4/PS5 на русском"
        account = raw_listing(751, model="PS5", price=200)
        account["title"] = "Турецкий профиль PS5 PS Plus"
        console = raw_listing(752, model="Sony PlayStation 5 Slim", price=45_000)
        console["title"] = "Игровая консоль Sony PlayStation 5 Slim"
        reviewer = CompleteReviewer()
        service = AvitoAnalysisService(None, reviewer, ai_concurrency=1)

        report = analyze_fixture(service,
            [game, account, console],
            SearchRequest("PS5 Slim", category="all"),
        )

        self.assertEqual(report.costs.ai_text_reviewed_count, 1)
        self.assertEqual(
            [item.analyzed.listing.listing_id for item in report.recommendations],
            ["752"],
        )

    def test_complete_ai_review_is_reused_from_memory_cache(self) -> None:
        reviewer = CostlyReviewer(2.0)
        service = AvitoAnalysisService(None, reviewer, ai_concurrency=1)
        rows = [raw_listing(370)]
        request = SearchRequest("iPhone 15 Pro", category="phones")

        first = analyze_fixture(service, rows, request)
        second = analyze_fixture(service, rows, request)

        self.assertEqual(first.costs.ai_cost_rub, 2.0)
        self.assertEqual(second.costs.ai_cost_rub, 0.0)
        self.assertEqual(second.costs.ai_cached_count, 1)
        self.assertEqual(len(reviewer.seen), 1)

    def test_complete_ai_cache_is_scoped_to_the_search_request(self) -> None:
        class RequestAwareReviewer(CompleteReviewer):
            def __init__(self):
                super().__init__()
                self.queries = []

            def review_text(self, listing, search=None):
                self.queries.append(search.query)
                return super().review_text(listing, search)

        reviewer = RequestAwareReviewer()
        service = AvitoAnalysisService(None, reviewer, ai_concurrency=1)
        rows = [raw_listing(3701)]

        first = analyze_fixture(service,
            rows,
            SearchRequest("iPhone 15 Pro", category="phones"),
        )
        second = analyze_fixture(service,
            rows,
            SearchRequest("Apple iPhone 15 Pro", category="phones"),
        )

        self.assertEqual(first.costs.ai_cached_count, 0)
        self.assertEqual(second.costs.ai_cached_count, 0)
        self.assertEqual(reviewer.queries, ["iPhone 15 Pro", "Apple iPhone 15 Pro"])
        self.assertEqual(len(reviewer.seen), 2)

    def test_costs_are_admin_only_and_price_floor_covers_cost_multiplier(self) -> None:
        service = AvitoAnalysisService(
            None,
            CostlyReviewer(10.0),
            ai_concurrency=1,
            minimum_report_price_rub=199,
            target_cost_multiplier=4.0,
        )
        report = analyze_fixture(service,
            [raw_listing(371)],
            SearchRequest("iPhone 15 Pro", category="phones"),
            apify_cost_usd=0.3,
            collection_was_capped=True,
        )

        self.assertNotIn("adminCosts", report.public_dict())
        self.assertNotIn("бюджет", " ".join(report.public_dict()["warnings"]).casefold())
        admin = report.public_dict(include_admin=True)["adminCosts"]
        self.assertIn("бюджет", " ".join(report.public_dict(include_admin=True)["adminWarnings"]).casefold())
        self.assertEqual(admin["estimatedTotalRub"], 40.0)
        self.assertEqual(admin["suggestedMinPriceRub"], 199)

    def test_incomplete_discovery_is_preserved_only_in_admin_diagnostics(self) -> None:
        service = AvitoAnalysisService(None, IncompleteReviewer(), ai_concurrency=1)
        report = analyze_fixture(service,
            [raw_listing(309, images=3)],
            SearchRequest("iPhone 15 Pro", category="phones", mode="find"),
        )

        public = report.public_dict()
        self.assertEqual(public["recommendations"], [])
        self.assertEqual(public["verifiedCount"], 0)
        self.assertEqual(public["visibleCount"], 0)
        self.assertEqual(public["discoveredListings"], [])
        self.assertEqual(public["resultPolicy"], "verified_matches_only")
        self.assertNotIn("adminDiscoveredListings", public)
        admin = report.public_dict(include_admin=True)
        self.assertEqual(admin["recommendations"], public["recommendations"])
        self.assertEqual(admin["discoveredListings"], [])
        self.assertEqual(len(admin["adminDiscoveredListings"]), 1)
        discovery = admin["adminDiscoveredListings"][0]
        self.assertEqual(discovery["listing"]["id"], "309")
        self.assertEqual(discovery["discoveryStatus"], "needs_review")
        self.assertFalse(discovery["analysis"]["complete"])
        self.assertFalse(discovery["belowMarket"])
        self.assertNotEqual(discovery["listing"]["verificationStatus"], "verified")

    def test_admin_output_shows_ten_incomplete_candidates_as_caution(self) -> None:
        rows = [raw_listing(600 + index, model="игровой стул", price=30_000 + index * 100) for index in range(12)]
        service = AvitoAnalysisService(None, IncompleteReviewer(), ai_concurrency=1, ai_max_listings=12)

        report = analyze_fixture(service, rows, SearchRequest("игровой стул", category="all"))
        public = report.public_dict()
        admin = report.public_dict(include_admin=True)

        self.assertEqual(public["recommendations"], [])
        self.assertEqual(admin["recommendations"], public["recommendations"])
        self.assertEqual(admin["analyzedCount"], 0)
        self.assertEqual(len(admin["adminRecommendations"]), 10)
        self.assertTrue(all(item["role"] == "CAUTION" for item in admin["adminRecommendations"]))
        self.assertTrue(all(item["analysis"]["complete"] is False for item in admin["adminRecommendations"]))
        self.assertNotIn("adminRecommendations", public)
        self.assertNotIn("вручную", json.dumps(admin, ensure_ascii=False).casefold())

    def test_public_output_preserves_best_then_interleaves_seller_kinds(self) -> None:
        rows = [
            raw_listing(320 + index, price=50_000 + index * 1_000, seller=f"Company {index}")
            for index in range(6)
        ]
        rows.extend([
            raw_listing(340, price=49_000, seller="Private A", seller_type="person", is_shop=False),
            raw_listing(341, price=59_000, seller="Private B", seller_type="private", is_shop=False),
        ])
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)
        report = analyze_fixture(service,
            rows,
            SearchRequest("iPhone 15 Pro", category="phones", desired_results=10, mode="find", priority="budget"),
        )

        recommendations = report.public_dict()["recommendations"]
        kinds = [item["listing"]["seller"]["kind"] for item in recommendations]

        self.assertEqual(recommendations[0]["listing"]["id"], "340")
        self.assertEqual(kinds, [
            "private", "company", "company", "company",
            "private", "company", "company", "company",
        ])

    def test_public_output_respects_client_result_limit(self) -> None:
        rows = [raw_listing(700 + index, price=50_000 + index * 100) for index in range(8)]
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1, ai_max_listings=8)

        report = analyze_fixture(service,
            rows,
            SearchRequest(
                "iPhone 15 Pro",
                category="phones",
                mode="find",
                desired_results=3,
            ),
        )
        public = report.public_dict()

        self.assertEqual(public["mode"], "find")
        self.assertEqual(public["resultPolicy"], "verified_matches_only")
        self.assertEqual(public["requestedResults"], 3)
        self.assertEqual(len(public["recommendations"]), 3)

    def test_find_mode_prefers_a_more_reliable_seller_for_photo_review(self) -> None:
        cheap = raw_listing(720, price=40_000, seller="Новый продавец")
        cheap["seller"]["ratingScore"] = 3.5
        cheap["seller"]["reviewCount"] = 1
        reliable = raw_listing(721, price=45_000, seller="Проверенный магазин")
        reliable["seller"]["ratingScore"] = 5.0
        reliable["seller"]["reviewCount"] = 5_000
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1, ai_max_listings=1)

        report = analyze_fixture(service,
            [cheap, reliable],
            SearchRequest("iPhone 15 Pro", category="phones", mode="find", desired_results=3),
        )
        public = report.public_dict()

        self.assertEqual(len(public["recommendations"]), 1)
        self.assertEqual(public["recommendations"][0]["listing"]["id"], "721")


class AvitoHTTPTests(unittest.TestCase):
    def test_job_snapshot_enforces_customer_deadline(self) -> None:
        now = time.monotonic()
        job = SearchJob(job_id="late", started_at=now - 46, deadline_at=now - 1)

        snapshot = job.snapshot()

        self.assertEqual(snapshot["state"], "timeout")
        self.assertEqual(snapshot["remainingSeconds"], 0)
        self.assertIn("результат сохранится", snapshot["error"])

    def test_client_ui_offers_three_or_five_and_shows_absolute_saving(self) -> None:
        root = Path(__file__).resolve().parents[1]
        page = (root / "avito_web" / "index.html").read_text(encoding="utf-8")
        script = (root / "avito_web" / "app.js").read_text(encoding="utf-8")
        styles = (root / "avito_web" / "styles.css").read_text(encoding="utf-8")

        self.assertIn('name="desiredResults" value="3"', page)
        self.assertIn('name="desiredResults" value="5" checked', page)
        self.assertNotIn('<select name="desiredResults"', page)
        self.assertNotIn('name="desiredResults" value="7"', page)
        self.assertNotIn('name="desiredResults" value="10"', page)
        self.assertIn("savingAmount", script)
        self.assertIn("Экономия", script)
        self.assertIn('id="theme-toggle"', page)
        self.assertIn('id="dynamic-fields"', page)
        self.assertIn('id="support-dialog"', page)
        self.assertIn('/api/avito/jobs', script)
        self.assertIn('id="examples-list"', page)
        self.assertIn("EXAMPLE_ROTATION_MS", script)
        self.assertIn("VERIFIED_EXAMPLES_KEY", script)
        self.assertIn("Коряга для аквариума", script)
        self.assertIn('font-family: "Onest"', styles)
        self.assertIn('/fonts/Onest-Variable.ttf', styles)
        self.assertNotIn("Память / размер", page)
        self.assertIn('class="site-nav"', page)
        self.assertIn('id="menu-toggle"', page)
        self.assertIn('id="examples-pause"', page)
        self.assertIn('class="proof-section"', page)
        self.assertIn('class="use-section"', page)
        self.assertIn('class="faq-section"', page)
        self.assertIn('id="operator-panel" hidden', page)
        self.assertIn("const SOFT_TARGET_SECONDS = 45", script)
        self.assertNotIn("const SEARCH_LIMIT_MS = 52000", script)
        self.assertNotIn("наличие подтверждалось при сборе", script)

    def test_duplicate_inflight_search_reuses_the_same_worker(self) -> None:
        class FixtureReport:
            admin_warnings = ()

            def public_dict(self, *, include_admin=False):
                if include_admin:
                    return {"adminCosts": {}, "adminAudit": {}}
                return {"recommendations": []}

        class BlockingService:
            def __init__(self):
                self.calls = 0
                self.started = Event()
                self.release = Event()

            def search(self, search, *, deadline_seconds, progress):
                self.calls += 1
                self.started.set()
                self.release.wait(timeout=2)
                return FixtureReport()

        service = BlockingService()
        registry = SearchJobRegistry(service, max_active=2)
        request = SearchRequest("iPhone 15 Pro", location="Москва")

        first = registry.start(request)
        self.assertTrue(service.started.wait(timeout=1))
        second = registry.start(request)
        service.release.set()
        for _ in range(100):
            if first.worker_finished:
                break
            time.sleep(0.01)

        self.assertIs(first, second)
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(service.calls, 1)
        self.assertTrue(first.worker_finished)

    def test_invalid_numeric_search_field_returns_http_400(self) -> None:
        config = ServiceConfig(
            apify_token="fixture-token",
            ai_api_key="fixture-ai",
            ai_base_url="https://ai.example/v1",
            ai_model="vision-model",
        )
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)
        server = make_server("127.0.0.1", 0, service=service, config=config)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        request = Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/avito/jobs",
            data=json.dumps({
                "query": "iPhone 15 Pro",
                "maxResults": {"unexpected": True},
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=3)
            payload = json.loads(caught.exception.read().decode("utf-8"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(payload["code"], "INVALID_REQUEST")
        self.assertFalse(payload["retryable"])

    def test_find_mode_budget_priority_prefers_cheapest_safe_listing(self) -> None:
        cheap = raw_listing(930, price=39_000, seller="Частник")
        cheap["seller"]["ratingScore"] = 4.0
        cheap["seller"]["reviewCount"] = 20
        premium = raw_listing(931, price=55_000, seller="Магазин")
        premium["seller"]["ratingScore"] = 5.0
        premium["seller"]["reviewCount"] = 5_000
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)

        report = analyze_fixture(service,
            [premium, cheap],
            SearchRequest(
                "iPhone 15 Pro",
                category="phones",
                mode="find",
                priority="budget",
                desired_results=3,
            ),
        )

        self.assertEqual(report.public_dict()["recommendations"][0]["listing"]["id"], "930")

    def test_async_job_reports_real_stages_and_returns_result(self) -> None:
        class FixtureProvider:
            def collect(self, search):
                return [
                    raw_listing(940, price=39_000, seller="A"),
                    raw_listing(941, price=45_000, seller="B"),
                    raw_listing(942, price=48_000, seller="C"),
                ]

            def refresh(self, listings, **kwargs):
                return FixtureRefreshProvider(self.collect(None)).refresh(listings, **kwargs)

        config = ServiceConfig(
            apify_token="fixture-token",
            ai_api_key="fixture-ai",
            ai_base_url="https://ai.example/v1",
            ai_model="vision-model",
        )
        service = AvitoAnalysisService(FixtureProvider(), CompleteReviewer(), ai_concurrency=1)
        server = make_server("127.0.0.1", 0, service=service, config=config)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            request = Request(
                f"{base}/api/avito/jobs",
                data=json.dumps({"query": "iPhone 15 Pro", "mode": "find", "desiredResults": 3}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=3) as response:
                job = json.loads(response.read().decode("utf-8"))
            for _ in range(30):
                with urlopen(f"{base}/api/avito/jobs/{job['jobId']}", timeout=3) as response:
                    job = json.loads(response.read().decode("utf-8"))
                if job["state"] != "running":
                    break
                time.sleep(0.02)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.assertEqual(job["state"], "complete")
        self.assertEqual(job["percent"], 100)
        self.assertEqual(len(job["result"]["recommendations"]), 3)
        self.assertNotIn("adminAudit", job["result"])
        self.assertEqual(job["result"]["verification"]["photoChecked"], 3)

    def test_support_request_is_persisted_without_bot_database(self) -> None:
        config = ServiceConfig()
        service = AvitoAnalysisService(None, UnavailableReviewer(), ai_concurrency=1)
        with TemporaryDirectory() as directory:
            inbox = Path(directory) / "support.jsonl"
            server = make_server(
                "127.0.0.1",
                0,
                service=service,
                config=config,
                support_path=inbox,
            )
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            request = Request(
                f"http://127.0.0.1:{server.server_address[1]}/api/support",
                data=json.dumps({
                    "name": "Анна",
                    "contact": "@anna",
                    "topic": "Ошибка сайта",
                    "message": "Не удалось завершить подбор товара.",
                }, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=3) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
            saved = inbox.read_text(encoding="utf-8")

        self.assertTrue(payload["ticket"].startswith("NV-"))
        self.assertIn("@anna", saved)
        self.assertIn("Ошибка сайта", saved)

    def test_support_duplicate_is_idempotent_and_not_written_twice(self) -> None:
        payload = {
            "name": "Анна",
            "contact": "@anna",
            "topic": "Ошибка сайта",
            "message": "Один и тот же запрос отправлен дважды.",
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "support.jsonl"
            inbox = SupportInbox(path)

            first = inbox.create(payload)
            second = inbox.create(payload)
            lines = path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(first["ticket"], second["ticket"])
        self.assertEqual(len(lines), 1)
        self.assertIn("уже принята", second["message"].casefold())

    def test_support_limits_repeated_requests_from_one_contact(self) -> None:
        with TemporaryDirectory() as directory:
            inbox = SupportInbox(Path(directory) / "support.jsonl")
            for index in range(5):
                inbox.create({
                    "name": "Анна",
                    "contact": "@anna",
                    "topic": "Вопрос",
                    "message": f"Разное описание вопроса номер {index}.",
                })

            with self.assertRaisesRegex(AvitoServiceError, "Слишком много") as caught:
                inbox.create({
                    "name": "Анна",
                    "contact": "@anna",
                    "topic": "Вопрос",
                    "message": "Шестое отдельное описание вопроса.",
                })

        self.assertEqual(caught.exception.code, "SUPPORT_RATE_LIMIT")
        self.assertTrue(caught.exception.retryable)

    def test_health_and_dataset_api_never_expose_credentials(self) -> None:
        config = ServiceConfig(
            apify_token="apify-secret-value",
            ai_api_key="ai-secret-value",
            ai_base_url="https://ai.example/v1",
            ai_model="vision-model",
        )
        service = AvitoAnalysisService(None, CompleteReviewer(), ai_concurrency=1)
        owner_token = "fixture-owner-access-token-32-characters"
        server = make_server("127.0.0.1", 0, service=service, config=config, owner_token=owner_token)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(f"{base}/health", timeout=3) as response:
                health = response.read().decode("utf-8")
            with urlopen(f"{base}/", timeout=3) as response:
                page = response.read().decode("utf-8")
            with urlopen(f"{base}/fonts/Onest-Variable.ttf", timeout=3) as response:
                font_content_type = response.headers.get_content_type()
                font_size = len(response.read())
            payload = json.dumps({
                "search": {"query": "iPhone 15 Pro", "category": "phones"},
                "listings": [raw_listing(401)],
            }, ensure_ascii=False).encode("utf-8")
            request = Request(
                f"{base}/api/avito/analyze-dataset",
                data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {owner_token}"},
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                self.assertEqual(response.status, 202)
                job = json.loads(response.read().decode("utf-8"))
            for _ in range(100):
                status_request = Request(f"{base}/api/avito/jobs/{job['jobId']}",
                    headers={"Authorization": f"Bearer {owner_token}"})
                with urlopen(status_request, timeout=3) as response:
                    job = json.loads(response.read().decode("utf-8"))
                if job["state"] != "running":
                    break
                time.sleep(0.01)
            body = json.dumps(job["result"], ensure_ascii=False)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.assertIn('"ok": true', health)
        self.assertIn("Найди выгоднее", page)
        self.assertEqual(font_content_type, "font/ttf")
        self.assertGreater(font_size, 100_000)
        self.assertIn('"apify": true', health)
        self.assertIn('"ai": true', health)
        self.assertIn('"role": "CAUTION"', body)
        self.assertTrue(job["result"]["preview"])
        self.assertIn('"adminCosts"', body)
        self.assertNotIn("apify-secret-value", health + body)
        self.assertNotIn("ai-secret-value", health + body)

    def test_dataset_analysis_requires_configured_ai(self) -> None:
        config = ServiceConfig(apify_token="test-apify-token")
        service = AvitoAnalysisService(None, UnavailableReviewer(), ai_concurrency=1)
        owner_token = "fixture-owner-access-token-32-characters"
        server = make_server("127.0.0.1", 0, service=service, config=config, owner_token=owner_token)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        payload = json.dumps({
            "search": {"query": "iPhone 15 Pro", "category": "phones"},
            "listings": [raw_listing(402)],
        }, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{server.server_address[1]}/api/avito/analyze-dataset",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {owner_token}"},
            method="POST",
        )
        try:
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=5)
            body = caught.exception.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.assertEqual(caught.exception.code, 503)
        self.assertIn("AI не подключена", body)


if __name__ == "__main__":
    unittest.main()
