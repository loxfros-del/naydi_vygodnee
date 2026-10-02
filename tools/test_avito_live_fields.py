#!/usr/bin/env python3
"""Regression fixtures for field shapes observed in the September Avito pilot."""
from __future__ import annotations

import unittest
from dataclasses import replace

from avito_service.matching import matches_listing_request
from avito_service.models import AIReview, AnalyzedListing, ReviewVerdict, SearchRequest
from avito_service.normalization import normalize_listing
from avito_service.risk_rules import evaluate_rules


def raw(address="Москва, Электродная ул., 2с34", locality="moskva", **fields):
    value = {
        "id": "12345678", "title": "iPhone 13 128 ГБ",
        "url": f"https://www.avito.ru/{locality}/telefony/iphone_13_12345678",
        "status": "active", "stock": "В наличии: последний товар", "price": 30_000,
        "description": "Исправный телефон. Полный комплект.",
        "images": ["https://01.img.avito.st/photo.jpg"],
        "address": address, "deliveryAvailable": True,
        "parameters": {"Модель": "iPhone 13", "Встроенная память": "128 ГБ", "SIM-карты": "SIM + eSIM", "Состояние": "Отличное"},
    }
    value.update(fields)
    return value


def analyzed(value):
    listing = normalize_listing(value)
    review = AIReview(listing.listing_id, True, False, identified_model=listing.model, storage=listing.storage,
                      sim_variant=listing.sim_variant, condition=listing.condition, verdict=ReviewVerdict.APPROVE)
    return AnalyzedListing(listing, evaluate_rules(listing), review)


class AvitoLiveFieldsTests(unittest.TestCase):
    def test_live_charging_circuit_defect_overrides_excellent_condition(self):
        description = (
            "Цена бывшего в эксплуатации смартфона зависит от внешнего состояния полноты комплекта "
            "технических параметров и работоспособности данного экземпляра и потому считается индивидуально "
            "У представленного аппарата зафиксированы отклонения в работе цепи заряда."
        )
        codes = {risk.code for risk in evaluate_rules(normalize_listing(raw(description=description)))}
        self.assertIn("HARDWARE_DEFECT", codes)
        self.assertIn("NON_FINAL_PRICE", codes)

    def test_explicitly_denied_charging_circuit_defect_is_not_a_failure(self):
        for description in ("Отклонений в работе цепи заряда нет.",
                            "Телефон без отклонений в работе цепи заряда."):
            with self.subTest(description=description):
                codes = {risk.code for risk in evaluate_rules(normalize_listing(raw(description=description)))}
                self.assertNotIn("HARDWARE_DEFECT", codes)

    def test_last_item_stock_does_not_add_risk(self):
        codes = {risk.code for risk in evaluate_rules(normalize_listing(raw()))}
        self.assertNotIn("STOCK_UNCONFIRMED", codes)
        self.assertNotIn("OUT_OF_STOCK", codes)

    def test_remaining_two_items_stock_does_not_add_risk(self):
        codes = {risk.code for risk in evaluate_rules(normalize_listing(raw(stock="В наличии: осталось 2 штуки")))}
        self.assertNotIn("STOCK_UNCONFIRMED", codes)
        self.assertNotIn("OUT_OF_STOCK", codes)

    def test_zero_items_stock_is_ignored(self):
        for stock in ("В наличии: 0", "В наличии: 0 шт.", "В наличии: осталось 0 штук"):
            with self.subTest(stock=stock):
                codes = {risk.code for risk in evaluate_rules(normalize_listing(raw(stock=stock)))}
                self.assertEqual(codes, {risk.code for risk in evaluate_rules(normalize_listing(raw()))})

    def test_missing_stock_is_ignored_for_active_listing(self):
        for stock in (None, "", "уточняйте"):
            with self.subTest(stock=stock):
                self.assertEqual(evaluate_rules(normalize_listing(raw(stock=stock))), evaluate_rules(normalize_listing(raw())))

    def test_conflicting_stock_words_do_not_affect_risk_rules(self):
        self.assertEqual(evaluate_rules(normalize_listing(raw(stock="Последний товар, нет в наличии"))), evaluate_rules(normalize_listing(raw())))

    def test_same_city_street_addresses_share_comparable_locality(self):
        first = analyzed(raw("Москва, Электродная ул., 2с34"))
        second = analyzed(raw("Москва, Большая Черёмушкинская ул., 1"))
        self.assertEqual(first.listing.location, "moskva")
        self.assertEqual(first.comparable_key(), second.comparable_key())
        self.assertNotEqual(first.listing.address, second.listing.address)
        self.assertEqual(first.listing.public_dict()["address"], "Москва, Электродная ул., 2с34")

    def test_different_cities_never_share_comparable_locality(self):
        moscow = analyzed(raw())
        for address, locality in (("Ставрополь", "stavropol"), ("Ростовская обл., Ростов-на-Дону, Ворошиловский пр-т, 12/85", "rostov-na-donu")):
            with self.subTest(locality=locality):
                other = analyzed(raw(address, locality))
                self.assertNotEqual(moscow.comparable_key(), other.comparable_key())
                self.assertNotIn("обл", other.listing.location)

    def test_structured_city_and_url_locality_normalize_together(self):
        baseline = analyzed(raw())
        for fields in ({"city": "Москва"}, {"location": {"city": {"name": "Москва"}}}, {"location": "Москва"}):
            with self.subTest(fields=fields):
                value = analyzed(raw(**fields))
                self.assertEqual(baseline.comparable_key(), value.comparable_key())
                self.assertEqual(value.listing.address, "Москва, Электродная ул., 2с34")

    def test_explicit_different_city_is_not_silently_replaced_by_url(self):
        self.assertNotEqual(analyzed(raw()).comparable_key(), analyzed(raw(city="Казань")).comparable_key())

    def test_moscow_request_rejects_other_cities_even_with_delivery_and_ai_model(self):
        request = SearchRequest("iPhone 13", location="Москва")
        for locality in ("rostov-na-donu", "stavropol"):
            with self.subTest(locality=locality):
                candidate = normalize_listing(raw(locality=locality))
                self.assertFalse(matches_listing_request(candidate, request))
                self.assertFalse(matches_listing_request(candidate, request, identified_model="iPhone 13", final=True))
        self.assertTrue(matches_listing_request(normalize_listing(raw()), request, identified_model="iPhone 13", final=True))

    def test_nationwide_search_allows_the_observed_cities(self):
        for area in ("Россия", "rossiya", "all"):
            for locality in ("moskva", "rostov-na-donu", "stavropol"):
                with self.subTest(area=area, locality=locality):
                    self.assertTrue(matches_listing_request(normalize_listing(raw(locality=locality)), SearchRequest("iPhone 13", location=area), identified_model="iPhone 13", final=True))

    def test_missing_locality_is_allowed_only_before_final_city_confirmation(self):
        candidate = replace(normalize_listing(raw()), location="")
        request = SearchRequest("iPhone 13", location="Москва")
        self.assertTrue(matches_listing_request(candidate, request))
        self.assertFalse(matches_listing_request(candidate, request, identified_model="iPhone 13", final=True))

    def test_no_inferred_repair_parts_or_kit_without_evidence(self):
        listing = normalize_listing(raw(description="Телефон исправен."))
        self.assertEqual(listing.repair_status, "")
        self.assertEqual(listing.parts_status, "")
        self.assertEqual(listing.completeness, "")


if __name__ == "__main__":
    unittest.main()
