"""Regression tests for deterministic SKU price truth."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.models import NormalizedListing, SellerSummary
from avito_service.risk_rules import evaluate_rules


def listing(listing_id: str, *, title: str, price: int, model: str, condition: str, description: str):
    return NormalizedListing(
        listing_id=listing_id,
        title=title,
        url=f"https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/{listing_id}",
        status="active",
        price=price,
        currency="RUB",
        description=description,
        images=("https://example.test/photo.jpg",),
        image_count_claimed=1,
        parameters={"Модель": model, "Встроенная память": "1000 ГБ", "Состояние": condition},
        parameter_ids={},
        badges=(),
        stock="",
        seller=SellerSummary(name="test"),
        collected_at="2026-09-23T10:00:00+03:00",
        location="Ярославль",
    )


class PriceTruthRegressionTests(unittest.TestCase):
    def test_8376263618_error_code_is_not_a_price(self):
        # Saved live description from 2026-10-03: the dash in CE-108255-1
        # used to turn the diagnostic code into an offer for 108,255 RUB.
        item = listing(
            "8376263618", title="Sony playstation 5,с дисководом", price=43_100,
            model="PlayStation 5", condition="Отличное",
            description=(
                "Полный комплект, состояние отличное.\n\n"
                "Вылезает ошибка на играх ps5 (ce-108255-1)\n"
                "На играх PS4 все работает.\n\nСамовывоз."
            ),
        )
        self.assertEqual(item.effective_price, 43_100)
        self.assertEqual(item.acquisition_price, 43_100)
        self.assertEqual(item.price_truth.offers, ())
        self.assertNotIn("PRICE_VARIANT_MISMATCH", item.price_truth.conflicts)
        self.assertIn("Вылезает ошибка", item.description)

    def test_identifiers_do_not_override_a_real_price_on_the_same_line(self):
        for code in ("CE-108255-1", "CE‑108255‑1", "NP-103111-7"):
            with self.subTest(code=code):
                item = listing(
                    "9000000002", title="Sony PlayStation 5", price=43_100,
                    model="PlayStation 5", condition="Отличное",
                    description=f"PS5 ({code}) — 43 100 ₽",
                )
                self.assertEqual(item.effective_price, 43_100)
                self.assertEqual([offer.amount for offer in item.price_truth.offers], [43_100])

    def test_long_identifiers_are_not_truncated_into_prices(self):
        item = listing(
            "9000000003", title="Sony PlayStation 5", price=43_100,
            model="PlayStation 5", condition="Отличное",
            description="PS5, номер объявления — 8376263618",
        )
        self.assertEqual(item.effective_price, 43_100)
        self.assertEqual(item.price_truth.offers, ())

    def test_compact_price_rows_still_use_the_description_price(self):
        for row in ("PS5-48000", "PS5 цена-48000", "PS5 — 48к", "PS5 — 48.000₽", "PS5 — 48000 руб."):
            with self.subTest(row=row):
                item = listing(
                    "9000000004", title="Sony PlayStation 5", price=43_100,
                    model="PlayStation 5", condition="Отличное", description=row,
                )
                self.assertEqual(item.effective_price, 48_000)

    def test_7704979247_new_price_is_not_card_price(self):
        item = listing(
            "7704979247",
            title="Новая Sony PlayStation 5 Slim 1TB Digital",
            price=58_990,
            model="PlayStation 5 Slim Digital Edition",
            condition="Новое",
            description="Sony PS5 Slim Digital 1TB новая.\nБУ — от 59к\nНовая — от 65к",
        )
        self.assertEqual(item.effective_price, 65_000)
        self.assertEqual(item.price_confidence, "likely")
        self.assertEqual(item.acquisition_price, 65_000)
        self.assertIn("PRICE_VARIANT_MISMATCH", item.price_truth.conflicts)

    def test_8057330597_multi_sku_price_is_ambiguous(self):
        item = listing(
            "8057330597",
            title="Sony PlayStation 5 slim 1tb новая PS5",
            price=49_999,
            model="PlayStation 5 Slim",
            condition="Новое",
            description=(
                "Новые и Б/У Sony PS5 в наличии\n"
                "PS5 fat — от 57.999₽\n"
                "PS5 Slim Digital Edition 2rev — от 66.999₽\n"
                "PS5 Slim Disk 1tb — от 63.999₽\n"
                "PS5 pro — от 108.999₽\nDualsense — 6.999₽"
            ),
        )
        self.assertIsNone(item.effective_price)
        self.assertEqual(item.price_confidence, "ambiguous")
        self.assertIsNone(item.acquisition_price)
        self.assertIn("PRICE_VARIANT_MISMATCH", item.price_truth.conflicts)

    def test_8351987913_disc_variant_uses_its_text_price(self):
        item = listing(
            "8351987913",
            title="Sony PlayStation 5 slim с дисководом новая",
            price=54_990,
            model="PlayStation 5 Slim",
            condition="Новое",
            description=(
                "Sony PS5 Slim Digital новая — «шестьдесят пять тысяч»\n"
                "Sony PS5 Slim 1TB Disk новая — «семьдесят пять тысяч»\n"
                "Цена в описании указана со скидкой — за наличный расчет"
            ),
        )
        self.assertEqual(item.effective_price, 75_000)
        self.assertEqual(item.price_confidence, "exact")
        self.assertIn("cash_price", item.price_truth.payment_conditions)
        self.assertIn("PRICE_VARIANT_MISMATCH", item.price_truth.conflicts)

    def test_4791552729_new_parameter_conflicts_with_used_description(self):
        item = listing(
            "4791552729",
            title="Sony PlayStation 5 Slim, 1000 гб идеал как новая",
            price=62_990,
            model="PlayStation 5 Slim",
            condition="Новое",
            description="PlayStation 5 Slim с дисководом БУ. Оплата наличными.",
        )
        self.assertEqual(item.price_truth.condition, "conflict:new_vs_used")
        self.assertIn("CONDITION_CONFLICT", {finding.code for finding in evaluate_rules(item)})

    def test_cash_and_cashless_payment_are_not_conflated(self):
        cashless = listing(
            "9000000001", title="Sony PlayStation 5 Slim 1TB новая", price=60_000,
            model="PlayStation 5 Slim", condition="Новое",
            description="Безналичный расчет и оплата по QR без переплаты.",
        )
        self.assertNotIn("cash_price", cashless.price_truth.payment_conditions)
        self.assertIn("card_available", cashless.price_truth.payment_conditions)


if __name__ == "__main__":
    unittest.main()
