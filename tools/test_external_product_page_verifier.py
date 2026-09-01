#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.net_client import FetchResult  # noqa: E402
from app.search_v2.external_page_verifier import (  # noqa: E402
    ExternalProductPageVerifier, is_direct_product_candidate_url,
    needs_external_page_verification, verify_external_offer_if_needed,
)
from app.search_v2.models import AvailabilityStatus, Offer, ProductIdentity, VerificationAccess  # noqa: E402


class ExternalProductPageVerifierTests(unittest.IsolatedAsyncioTestCase):
    def offer(self, url: str = "https://store.example/products/iphone-17-pro-256") -> Offer:
        return Offer(
            offer_id="web:iphone",
            source="yandex_web",
            platform="Веб-поиск",
            title="Сниппет: iPhone за 150 217 ₽",
            url=url,
            price=150_217,
            price_confidence=0.2,
        )

    async def test_json_ld_page_replaces_unverified_snippet_price(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_fetch(url: str, **kwargs: object) -> FetchResult:
            calls.append((url, kwargs))
            return FetchResult(
                ok=True,
                status_code=200,
                final_url=url,
                html="""
                    <html><head><script type="application/ld+json">
                    {"@context":"https://schema.org","@type":"Product",
                     "name":"Apple iPhone 17 Pro 256 GB eSIM",
                     "offers":{"@type":"Offer","price":"75 399 ₽",
                     "priceCurrency":"RUB","availability":"https://schema.org/InStock"}}
                    </script></head><body>В наличии</body></html>
                """,
            )

        verified = await ExternalProductPageVerifier(fake_fetch)(self.offer())

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]["retries"], 0)
        self.assertEqual(verified.price, 75_399)
        self.assertEqual(verified.title, "Apple iPhone 17 Pro 256 GB eSIM")
        self.assertEqual(verified.availability.status, AvailabilityStatus.IN_STOCK)
        self.assertTrue(verified.availability.available)
        self.assertEqual(verified.verification_access, VerificationAccess.FULL)
        self.assertTrue(verified.raw_metadata["product_page_verified"])
        self.assertTrue(verified.raw_metadata["external_page_verified"])
        self.assertTrue(verified.raw_metadata["price_verified"])
        self.assertEqual(verified.raw_metadata["availability"], "available")
        self.assertTrue(verified.automatic_verification.price_verified)

    async def test_meta_product_page_is_accepted_and_preorder_is_preserved(self) -> None:
        def fake_fetch(url: str, **_kwargs: object) -> FetchResult:
            return FetchResult(
                ok=True,
                status_code=200,
                final_url=url,
                html="""
                    <meta property="og:type" content="product">
                    <meta property="og:title" content="Phone Max 512 GB">
                    <meta property="product:price:amount" content="89990">
                    <meta property="product:availability" content="PreOrder">
                """,
            )

        verified = await ExternalProductPageVerifier(fake_fetch)(
            self.offer("https://store.example/product/phone-max-512")
        )

        self.assertEqual(verified.price, 89_990)
        self.assertEqual(verified.title, "Phone Max 512 GB")
        self.assertEqual(verified.availability.status, AvailabilityStatus.PREORDER)
        self.assertEqual(verified.raw_metadata["availability"], "preorder")

    async def test_search_category_article_and_unpriced_page_fail_closed_without_snippet_price(self) -> None:
        calls: list[str] = []

        def fake_fetch(url: str, **_kwargs: object) -> FetchResult:
            calls.append(url)
            return FetchResult(
                ok=True,
                status_code=200,
                final_url=url,
                html='<script type="application/ld+json">{"@type":"Product","name":"Phone"}</script>',
            )

        verifier = ExternalProductPageVerifier(fake_fetch)
        for url in (
            "https://yandex.ru/search/?text=iphone+17+pro",
            "https://store.example/catalog/phones",
            "https://store.example/blog/iphone-review",
        ):
            failed = await verifier(self.offer(url))
            self.assertIsNone(failed.price)
            self.assertTrue(failed.raw_metadata["not_product_page"])
            self.assertFalse(failed.raw_metadata["external_page_verified"])
            self.assertFalse(failed.raw_metadata["price_verified"])
        self.assertEqual(calls, [])

        stale_verified = replace(
            self.offer("https://yandex.ru/search/?text=iphone+17+pro"),
            raw_metadata={"product_page_verified": True, "external_page_verified": True, "price_verified": True},
        )
        failed = await verifier(stale_verified)
        self.assertTrue(failed.raw_metadata["not_product_page"])
        self.assertFalse(failed.raw_metadata["product_page_verified"])
        self.assertFalse(failed.raw_metadata["external_page_verified"])
        self.assertFalse(failed.raw_metadata["price_verified"])

        failed = await verifier(self.offer())
        self.assertEqual(calls, ["https://store.example/products/iphone-17-pro-256"])
        self.assertIsNone(failed.price)
        self.assertTrue(failed.raw_metadata["not_product_page"])
        self.assertEqual(failed.raw_metadata["external_page_verification"]["reason"], "missing_structured_price")

    async def test_accessory_and_wrong_form_factor_pages_fail_closed(self) -> None:
        def fake_fetch(url: str, **_kwargs: object) -> FetchResult:
            return FetchResult(
                ok=True, status_code=200, final_url=url,
                html="""
                    <script type=\"application/ld+json\">{"@type":"Product","name":"Sony WH-1000XM5" ,"offers":{"price":"324"}}</script>
                    <body>Аксессуары для наушников и гарнитур. Чехлы для наушников. Материал: силикон.</body>
                """,
            )

        failed = await ExternalProductPageVerifier(fake_fetch)(self.offer())
        self.assertIsNone(failed.price)
        self.assertEqual(failed.raw_metadata["external_page_verification"]["reason"], "accessory_product_page")

        def neckband_fetch(url: str, **_kwargs: object) -> FetchResult:
            return FetchResult(
                ok=True, status_code=200, final_url=url,
                html="""
                    <script type=\"application/ld+json\">{"@type":"Product","name":"Sony WH-1000XM5","offers":{"price":"2176"}}</script>
                    <body>Конструкция: Наушники с шейным ободом.</body>
                """,
            )

        model_offer = replace(self.offer(), identity=ProductIdentity(category="headphones", canonical_model="WH-1000XM5"))
        failed = await ExternalProductPageVerifier(neckband_fetch)(model_offer)
        self.assertIsNone(failed.price)
        self.assertEqual(failed.raw_metadata["external_page_verification"]["reason"], "wrong_product_form_factor")

    def test_direct_url_gate_rejects_private_and_yandex_search_urls(self) -> None:
        self.assertTrue(is_direct_product_candidate_url("https://shop.example/item/iphone-17-pro"))
        self.assertFalse(is_direct_product_candidate_url("https://127.0.0.1/product/1"))
        self.assertFalse(is_direct_product_candidate_url("file:///tmp/product.html"))
        self.assertFalse(is_direct_product_candidate_url("https://market.yandex.ru/catalog--smartfony/123"))
        self.assertTrue(is_direct_product_candidate_url("https://market.yandex.ru/product--iphone/123"))

    def test_wildberries_only_allows_its_numeric_detail_route(self) -> None:
        self.assertTrue(is_direct_product_candidate_url("https://www.wildberries.ru/catalog/321732159/detail.aspx"))
        self.assertFalse(is_direct_product_candidate_url("https://www.wildberries.ru/catalog/321732159"))
        self.assertFalse(is_direct_product_candidate_url("https://www.wildberries.ru/catalog/321732159/detail.aspx?text=coffee"))

    def test_search_cards_require_page_proof_but_verified_pages_do_not_repeat_it(self) -> None:
        for source in ("yandex_web", "wildberries", "yandex_market", "dns", "citilink", "mvideo"):
            with self.subTest(source=source):
                self.assertTrue(needs_external_page_verification(replace(self.offer(), source=source)))

        verified = replace(
            self.offer(),
            source="yandex_web",
            raw_metadata={"external_page_verified": True},
        )
        verifier = ExternalProductPageVerifier(lambda *_args, **_kwargs: None)
        self.assertFalse(needs_external_page_verification(verified))
        self.assertIsNone(verify_external_offer_if_needed(verified, verifier))


if __name__ == "__main__":
    unittest.main(verbosity=2)
