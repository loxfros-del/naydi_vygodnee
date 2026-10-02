#!/usr/bin/env python3
"""Offline regression checks for Avito listing evidence and review agreement."""
from __future__ import annotations

from dataclasses import replace
import json
import unittest

from avito_service.ai import OpenAICompatibleReviewer
from avito_service.matching import matches_listing_request
from avito_service.models import AIReview, ReviewVerdict, SearchRequest
from avito_service.normalization import normalize_listing
from avito_service.risk_rules import evaluate_rules


def listing(**changes):
    raw = {
        "id": "12345678",
        "title": "Apple iPhone 13, 128 ГБ",
        "url": "https://www.avito.ru/moskva/telefony/iphone_13_12345678",
        "status": "active",
        "price": 30_000,
        "currency": "RUB",
        "description": "Телефон исправен. Цена окончательная.",
        "images": ["https://01.img.avito.st/phone.jpg"],
        "parameters": {"Модель": "iPhone 13", "Встроенная память": "128 ГБ", "SIM-карты": "SIM + eSIM", "Состояние": "Отличное"},
        "stock": "В наличии",
        "seller": {"name": "Продавец"},
    }
    raw.update(changes)
    return normalize_listing(raw)


def review(**changes):
    values = dict(
        listing_id="12345678", text_analyzed=True, photos_analyzed=True,
        photo_coverage=(1,), identified_model="iPhone 13", storage="128 ГБ",
        sim_variant="SIM + eSIM", condition="Отличное", matches_request=True,
        verdict=ReviewVerdict.APPROVE, confidence=0.9,
    )
    values.update(changes)
    return AIReview(**values)


class AvitoEvidenceTests(unittest.TestCase):
    def test_explicit_defect_denials_do_not_reject_clean_listing(self):
        for description in (
            "Без трещин и сколов, всё работает.",
            "Без трещин и неисправностей, всё работает.",
            "Никаких неисправностей нет.",
            "Телефон не разбитый.",
            "Трещины отсутствуют. Неисправностей нет.",
            "Трещин, сколов и неисправностей нет.",
            "NAND исправен, ошибок нет.",
            "NAND без ошибок.",
        ):
            with self.subTest(description=description):
                codes = {risk.code for risk in evaluate_rules(listing(description=description))}
                self.assertNotIn("HARDWARE_DEFECT", codes)
                self.assertNotIn("NAND_DEFECT", codes)

    def test_negation_does_not_hide_separate_real_defect(self):
        for description in (
            "Без трещин, камера не работает.",
            "Без сколов и трещин, но экран разбит.",
            "Неисправностей нет. Камера не работает.",
            "Трещин нет, однако телефон не включается.",
            "Аппарат не без трещин.",
            "Телефон разбитый.",
        ):
            with self.subTest(description=description):
                self.assertIn("HARDWARE_DEFECT", {risk.code for risk in evaluate_rules(listing(description=description))})

    def test_nand_failure_before_and_after_chip_name_is_rejected(self):
        for description in (
            "Зафиксирован выход из строя чипа памяти NAND.",
            "NAND неисправен, аппарат не запускается.",
            "Появляются ошибки NAND при запуске.",
            "NAND выдаёт ошибки при включении.",
        ):
            with self.subTest(description=description):
                self.assertIn("NAND_DEFECT", {risk.code for risk in evaluate_rules(listing(description=description))})

    def test_active_status_must_be_explicit(self):
        for status in ("", "unknown", "неизвестно", "inactive", "removed"):
            with self.subTest(status=status):
                codes = {risk.code for risk in evaluate_rules(listing(status=status))}
                self.assertTrue(codes & {"LISTING_INACTIVE", "LISTING_STATUS_UNCONFIRMED"})
        self.assertFalse({risk.code for risk in evaluate_rules(listing())} & {"LISTING_INACTIVE", "LISTING_STATUS_UNCONFIRMED"})

    def test_unknown_or_conditional_stock_does_not_affect_risk_rules(self):
        for stock in ("", "уточняйте", "под заказ", "неизвестно", "В наличии уточняйте", "В наличии завтра"):
            with self.subTest(stock=stock):
                self.assertEqual(evaluate_rules(listing(stock=stock)), evaluate_rules(listing()))

    def test_negative_stock_does_not_affect_risk_rules(self):
        for stock in ("нет", "Нет в наличии", "out of stock", "sold", "unavailable", "В наличии нет"):
            with self.subTest(stock=stock):
                self.assertEqual(evaluate_rules(listing(stock=stock)), evaluate_rules(listing()))

    def test_unambiguous_stock_values_are_accepted(self):
        for stock in ("В наличии", "В наличии: несколько", "В наличии: 2 шт.", "in_stock", "available"):
            with self.subTest(stock=stock):
                self.assertFalse({risk.code for risk in evaluate_rules(listing(stock=stock))} & {"OUT_OF_STOCK", "STOCK_UNCONFIRMED"})

    def test_final_matching_rejects_wrong_model_even_after_ai_approval(self):
        self.assertFalse(matches_listing_request(listing(), SearchRequest("iPhone 15")))
        self.assertFalse(matches_listing_request(listing(), SearchRequest("iPhone 15"), identified_model="iPhone 13", final=True))

    def test_final_matching_rejects_wrong_model_variant(self):
        for query, actual in (("iPhone 13", "iPhone 13 Pro"), ("iPhone 13 Pro", "iPhone 13"), ("PS5 Slim", "PlayStation 5 Pro"), ("RTX 4070", "RTX 4070 Ti")):
            with self.subTest(query=query, actual=actual):
                candidate = listing(title=actual, parameters={"Модель": actual})
                self.assertFalse(matches_listing_request(candidate, SearchRequest(query), identified_model=actual, final=True))

    def test_final_matching_accepts_model_aliases_and_memory_in_query(self):
        for query, actual in (("Айфон 13 128 ГБ", "Apple iPhone 13"), ("PS5", "Sony PlayStation 5"), ("iPhone15 Pro", "Apple iPhone 15 Pro")):
            with self.subTest(query=query, actual=actual):
                candidate = listing(title=actual, parameters={"Модель": actual, "Встроенная память": "128 ГБ"})
                self.assertTrue(matches_listing_request(candidate, SearchRequest(query), identified_model=actual, final=True))

    def test_memory_in_query_is_a_constraint_even_without_separate_field(self):
        self.assertFalse(matches_listing_request(listing(), SearchRequest("iPhone 13 256 GB"), final=True))

    def test_galaxy_brand_prefix_is_optional_but_generation_is_exact(self):
        candidate = listing(title="Galaxy S23", parameters={"Модель": "Galaxy S23", "Встроенная память": "256 ГБ"})
        self.assertTrue(matches_listing_request(candidate, SearchRequest("Samsung Galaxy S23 256 ГБ"), identified_model="Samsung Galaxy S23", final=True))
        self.assertFalse(matches_listing_request(candidate, SearchRequest("Samsung Galaxy S24 256 ГБ"), identified_model="Samsung Galaxy S23", final=True))
        self.assertFalse(matches_listing_request(candidate, SearchRequest("Samsung Galaxy S23 Ultra"), identified_model="Samsung Galaxy S23", final=True))

    def test_macbook_legacy_query_separates_specs_without_losing_constraints(self):
        parameters = {"Модель": "Apple MacBook Air M1", "Встроенная память": "256 ГБ", "Оперативная память": "8 ГБ", "Диагональ экрана": "13.3 дюйма"}
        candidate = listing(title="Apple MacBook Air M1", parameters=parameters)
        request = SearchRequest("Apple MacBook Air M1 8/256 ГБ 13 дюймов")
        self.assertTrue(matches_listing_request(candidate, request, identified_model="MacBook Air M1", final=True))
        for query in ("MacBook Air M2 8/256 ГБ 13 дюймов", "MacBook Pro M1 8/256 ГБ 13 дюймов", "MacBook Air M1 16/256 ГБ 13 дюймов", "MacBook Air M1 8/512 ГБ 13 дюймов", "MacBook Air M1 8/256 ГБ 15 дюймов"):
            with self.subTest(query=query):
                self.assertFalse(matches_listing_request(candidate, SearchRequest(query), identified_model="MacBook Air M1", final=True))

    def test_every_ai_stage_receives_identical_purchase_and_condition_evidence(self):
        candidate = replace(listing(), mandatory_fee_rub=500, delivery_cost_rub=1000, delivery_required=True,
                            location="Москва", delivery="available", battery_health_percent=95,
                            repair_status="never_repaired", parts_status="original", completeness="full")
        request = SearchRequest("iPhone 13")
        single = OpenAICompatibleReviewer.build_text_payload(candidate, "offline", request)["messages"][1]["content"]
        batch = OpenAICompatibleReviewer.build_text_batch_payload((candidate,), "offline", request)["messages"][1]["content"]
        photo = OpenAICompatibleReviewer.build_photo_payload(candidate, "offline", review())["messages"][1]["content"][0]["text"]
        def extract(text):
            return json.loads(text.split("seller_evidence=", 1)[1].split("\n", 1)[0])
        values = (extract(single), extract(batch)[0], extract(photo))
        expected_cost = {"totalRub": 31500, "mandatoryFeeRub": 500, "deliveryCostRub": 1000,
                         "deliveryRequired": True, "basis": "required_delivery_included"}
        for value in values:
            self.assertNotIn("stock", value)
            self.assertEqual(value["acquisitionCost"], expected_cost)
            self.assertEqual(value["conditionEvidence"]["batteryHealthPercent"], 95)
            self.assertEqual(value["conditionEvidence"]["basis"], "seller_listing")
            self.assertEqual(value["location"], "Москва")
            self.assertEqual(value["delivery"], "available")
        self.assertEqual(values[0]["description"], candidate.description)
        self.assertEqual(values[1]["description"], candidate.description)
        self.assertNotIn("description", values[2])

    def test_stock_parameters_are_omitted_from_every_ai_stage(self):
        product_parameters = dict(listing().parameters)
        stock_parameters = {
            "Наличие товара": "stock-marker-unavailable",
            "Остаток": "stock-marker-zero",
            "Доступность": "stock-marker-conditional",
            "Stock": "stock-marker-none",
            "availability": "stock-marker-unknown",
        }
        candidate = listing(parameters={**product_parameters, **stock_parameters})
        request = SearchRequest("iPhone 13")
        single = OpenAICompatibleReviewer.build_text_payload(candidate, "offline", request)["messages"][1]["content"]
        batch = OpenAICompatibleReviewer.build_text_batch_payload((candidate,), "offline", request)["messages"][1]["content"]
        photo = OpenAICompatibleReviewer.build_photo_payload(candidate, "offline", review())["messages"][1]["content"][0]["text"]
        for stage, payload in (("text", single), ("batch", batch), ("photo", photo)):
            with self.subTest(stage=stage):
                evidence = json.loads(payload.split("seller_evidence=", 1)[1].split("\n", 1)[0])
                if isinstance(evidence, list):
                    evidence = evidence[0]
                self.assertEqual(evidence["parameters"], product_parameters)
                self.assertNotIn("stock-marker-", payload)

    def test_unknown_purchase_cost_is_explicit_null_in_ai_evidence(self):
        candidate = replace(listing(), mandatory_fee_rub=None, delivery_required=True, delivery_cost_rub=None)
        evidence = OpenAICompatibleReviewer._listing_evidence(candidate)
        self.assertIsNone(evidence["acquisitionCost"]["totalRub"])
        self.assertIsNone(evidence["acquisitionCost"]["mandatoryFeeRub"])
        self.assertIsNone(evidence["acquisitionCost"]["deliveryCostRub"])

    def test_ai_cannot_overwrite_a_conflicting_structured_fact(self):
        self.assertFalse(matches_listing_request(listing(), SearchRequest("iPhone 15"), identified_model="iPhone 15", final=True))
        self.assertFalse(matches_listing_request(listing(), SearchRequest("iPhone 13", required_storage="256 GB"), storage="256 GB", final=True))

    def test_required_unknown_fields_pass_preliminary_but_never_final(self):
        incomplete = listing(parameters={"Модель": "iPhone 13"})
        for requirements in (
            {"required_storage": "256 ГБ"}, {"required_sim": "SIM + eSIM"}, {"required_condition": "Отличное"},
            {"attributes": (("ram", "8 ГБ"),)}, {"attributes": (("details", "Оригинальный аккумулятор"),)},
        ):
            with self.subTest(requirements=requirements):
                request = SearchRequest("iPhone 13", **requirements)
                self.assertTrue(matches_listing_request(incomplete, request))
                self.assertFalse(matches_listing_request(incomplete, request, final=True))

    def test_unknown_ai_fact_does_not_confirm_required_value(self):
        self.assertFalse(matches_listing_request(listing(), SearchRequest("iPhone 13", required_storage="128 ГБ"), storage="Вероятно 128 ГБ", final=True))

    def test_numeric_substring_is_not_capacity_confirmation(self):
        self.assertFalse(matches_listing_request(listing(), SearchRequest("iPhone 13", required_storage="8 ГБ"), final=True))

    def test_final_matching_accepts_equivalent_units(self):
        self.assertTrue(matches_listing_request(listing(), SearchRequest("iPhone 13", required_storage="128 GB", required_condition="Отличное состояние"), final=True))

    def test_title_without_identified_model_is_not_final_evidence(self):
        self.assertFalse(matches_listing_request(listing(parameters={}), SearchRequest("iPhone 13"), final=True))

    def test_photo_text_fact_conflicts_automatically_downgrade_approval(self):
        text_review = review()
        for changes in ({"identified_model": "iPhone 15"}, {"storage": "256 ГБ"}, {"sim_variant": "eSIM"}, {"condition": "Новое"}):
            with self.subTest(changes=changes):
                merged = OpenAICompatibleReviewer._merge_reviews(text_review, review(**changes))
                self.assertEqual(merged.verdict, ReviewVerdict.CAUTION)
                self.assertEqual(len(merged.conflicts), 1)

    def test_equivalent_or_unobservable_photo_fact_is_not_a_conflict(self):
        for changes in (
            {"identified_model": "Apple iPhone 13", "storage": "128 GB", "sim_variant": "eSIM + SIM", "condition": "Отличное состояние"},
            {"storage": "Не определено по фото", "sim_variant": "", "condition": ""},
        ):
            with self.subTest(changes=changes):
                merged = OpenAICompatibleReviewer._merge_reviews(review(), review(**changes))
                self.assertEqual(merged.verdict, ReviewVerdict.APPROVE)
                self.assertEqual(merged.conflicts, ())

    def test_zero_photo_confidence_cannot_be_hidden_by_text_confidence(self):
        merged = OpenAICompatibleReviewer._merge_reviews(review(), review(confidence=0))
        self.assertEqual(merged.confidence, 0)

    def test_vision_keeps_every_image_at_high_detail(self):
        candidate = listing(images=["https://01.img.avito.st/front.jpg", "https://01.img.avito.st/back.jpg"])
        payload = OpenAICompatibleReviewer.build_photo_payload(candidate, "offline-test-model", review())
        photos = [part["image_url"] for part in payload["messages"][1]["content"] if part["type"] == "image_url"]
        self.assertEqual([photo["url"] for photo in photos], list(candidate.images))
        self.assertTrue(all(photo["detail"] == "high" for photo in photos))


if __name__ == "__main__":
    unittest.main()
