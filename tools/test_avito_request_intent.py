from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.models import NormalizedListing, SearchRequest, SellerSummary
from avito_service.request_intent import (
    check_request_compatibility, normalize_listing_sku, parse_request_signature,
    search_query_variants,
)


def listing(title: str, *, model: str = "PlayStation 5", storage: str = "", condition: str = "Новое",
            description: str = "Консоль в продаже.") -> NormalizedListing:
    parameters = {"Модель": model, "Состояние": condition}
    if storage:
        parameters["Встроенная память, ГБ"] = storage
    return NormalizedListing(
        listing_id="1234567890", title=title,
        url="https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/console_1234567890",
        status="active", price=60_000, currency="RUB", description=description,
        images=("https://10.img.avito.st/image/test",), image_count_claimed=1,
        parameters=parameters, parameter_ids={}, badges=(), stock="",
        seller=SellerSummary(), collected_at="2026-09-24T00:00:00+03:00", location="yaroslavl",
    )


class RequestIntentTests(unittest.TestCase):
    def compatible(self, query: str, title: str, **changes) -> bool:
        return check_request_compatibility(listing(title, **changes), SearchRequest(query=query, location="Ярославль")).matches

    def test_standard_ps5_excludes_pro_and_allows_standard_forms(self):
        self.assertFalse(self.compatible("PlayStation 5", "Sony PS5 Pro 2TB", model="PlayStation 5 Pro"))
        for title in ("PS5 Slim Disc Edition", "PS5 Slim Digital Edition", "PS5 Fat", "PlayStation5"):
            with self.subTest(title=title):
                self.assertTrue(self.compatible("PlayStation 5", title))

    def test_pro_is_pro_only(self):
        self.assertFalse(self.compatible("PS5 Pro", "PS5 Slim"))
        self.assertTrue(self.compatible("PS 5 Pro", "PlayStation 5 Pro", model="PlayStation 5 Pro"))

    def test_slim_excludes_pro_and_fat_but_allows_both_editions(self):
        self.assertFalse(self.compatible("PS5 Slim", "PS5 Pro", model="PlayStation 5 Pro"))
        self.assertFalse(self.compatible("PS 5 Slim", "PS5 Fat"))
        self.assertTrue(self.compatible("PS5 Slim", "PS5 Slim Digital Edition"))
        self.assertTrue(self.compatible("PlayStation5 Slim", "PS 5 Slim с дисководом"))

    def test_exact_slim_edition_is_required(self):
        self.assertFalse(self.compatible("PS5 Slim Disc", "PS5 Slim Digital"))
        self.assertTrue(self.compatible("PS 5 Slim Disc Edition", "PS5 Slim с дисководом"))
        self.assertTrue(self.compatible("PS5 Slim Digital Edition", "PS5 Slim без дисковода"))

    def test_explicit_storage_is_required(self):
        request = SearchRequest(query="PS5 Slim 1TB", location="Ярославль")
        self.assertTrue(check_request_compatibility(listing("PS5 Slim 1TB", storage="1000"), request).matches)
        mismatch = check_request_compatibility(listing("PS5 Slim 825GB", storage="825"), request)
        self.assertFalse(mismatch.matches)
        self.assertEqual(mismatch.reason, "REQUEST_SKU_MISMATCH")

    def test_accessory_and_previous_generation_are_rejected(self):
        self.assertFalse(self.compatible("PS5", "Геймпад для PS5"))
        self.assertFalse(self.compatible("PS5", "PlayStation 4 Pro", model="PlayStation 4 Pro"))

    def test_structured_and_text_conflicts_are_explicit(self):
        sku = normalize_listing_sku(listing("PS5 Slim", model="PlayStation 5 Pro"))
        self.assertIn("SKU_CONFLICT", sku.conflicts)
        condition = normalize_listing_sku(listing(
            "PS5 Slim", condition="Новое", description="PS5 Slim БУ, полностью исправна.",
        ))
        self.assertIn("CONDITION_CONFLICT", condition.conflicts)

    def test_description_refines_generic_model_and_edition(self):
        standard = SearchRequest("PS5", location="Ярославль")
        pro = SearchRequest("PS5 Pro", location="Ярославль")
        described_pro = listing("Sony PlayStation 5 2TB", description="PlayStation 5 Pro 2TB")
        self.assertFalse(check_request_compatibility(described_pro, standard).matches)
        self.assertTrue(check_request_compatibility(
            listing("Sony PlayStation 5", description="Pro, 2TB"), pro,
        ).matches)

        digital = normalize_listing_sku(listing("PS5 Slim", description="Digital Edition"))
        disc = normalize_listing_sku(listing("PS5 Slim", description="Версия с дисководом"))
        self.assertEqual((digital.form_factor, digital.edition), ("slim", "digital"))
        self.assertEqual((disc.form_factor, disc.edition), ("slim", "disc"))

    def test_description_specificity_conflicts_are_rejected(self):
        slim_pro = normalize_listing_sku(listing("PS5 Slim", description="PlayStation 5 Pro"))
        digital_disc = normalize_listing_sku(listing(
            "PS5 Digital", description="Консоль с дисководом",
        ))
        self.assertIn("SKU_CONFLICT", slim_pro.conflicts)
        self.assertIn("SKU_CONFLICT", digital_disc.conflicts)
        self.assertFalse(check_request_compatibility(listing(
            "PS5 Slim", description="PlayStation 5 Pro",
        ), SearchRequest("PS5")).matches)

    def test_like_new_and_ideal_are_excellent_not_new(self):
        for description in ("Как новая", "Идеал", "Идеальное состояние"):
            with self.subTest(description=description):
                sku = normalize_listing_sku(listing(
                    "PS5 Slim", condition="", description=description,
                ))
                self.assertEqual(sku.condition, "excellent")

    def test_storage_query_variants_include_tb_gb_and_russian_aliases(self):
        variants = search_query_variants(parse_request_signature("PS5 Slim 1TB"))
        rendered = "\n".join(variants)
        for alias in ("1TB", "1 TB", "1000GB", "1000 GB", "1ТБ", "1000 ГБ"):
            with self.subTest(alias=alias):
                self.assertIn(alias, rendered)
        self.assertEqual(len(variants), len(set(variants)))

    def test_request_signature_and_query_variants_keep_pro_separate(self):
        standard = parse_request_signature("PlayStation 5")
        pro = parse_request_signature("PS5 PRO")
        self.assertEqual(standard.requested_family, "ps5_standard")
        self.assertEqual(pro.requested_family, "ps5_pro")
        self.assertFalse(any("pro" in value.casefold() for value in search_query_variants(standard)))
        self.assertTrue(all("pro" in value.casefold() for value in search_query_variants(pro)))


if __name__ == "__main__":
    unittest.main()
