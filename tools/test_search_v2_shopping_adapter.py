"""Deterministic tests for the optional price-bearing shopping adapter."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "123456:ci_test_token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.config import settings  # noqa: E402
from app.search_v2.adapters.base import SourceContext  # noqa: E402
from app.search_v2.adapters.shopping_search import (  # noqa: E402
    ShoppingSearchAdapter,
    _normalize_candidates,
    selected_shopping_provider,
    shopping_google_legacy_search,
)
from app.search_v2.models import (  # noqa: E402
    ProductCondition,
    QueryTier,
    SearchRequestV2,
    SourceQuery,
    SourceStatus,
)


def request() -> SearchRequestV2:
    return SearchRequestV2(
        original_query="iPhone 16 Pro 256 ГБ до 80к",
        category="phone",
        brand="Apple",
        canonical_model="iPhone 16",
        model_modifiers=["Pro"],
        required_specs={"storage_gb": 256},
        condition=ProductCondition.NEW,
        budget=80_000,
        supported_category=True,
        hard_tokens=["Apple", "iPhone 16", "Pro", "256 ГБ", "new"],
    )


def query() -> SourceQuery:
    return SourceQuery(
        query="Apple iPhone 16 Pro 256 ГБ new",
        source="shopping_search",
        tier=QueryTier.STRICT,
        hard_tokens_required=["Apple", "iPhone 16", "Pro", "256 ГБ", "new"],
        hard_tokens_preserved=["Apple", "iPhone 16", "Pro", "256 ГБ", "new"],
    )


class ShoppingAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_price_becomes_raw_offer(self) -> None:
        def fake_search(_query, _request, _context):
            return {
                "status": "success",
                "candidates": [{
                    "title": "Apple iPhone 16 Pro 256 ГБ",
                    "price": 79_990,
                    "url": "https://www.ozon.ru/product/iphone-16-pro-123456789/",
                    "seller": "Ozon",
                    "platform": "Ozon",
                    "raw": {"price_source": "structured_api"},
                }],
            }

        result = await ShoppingSearchAdapter(fake_search).search(
            request(), query(), SourceContext(limit=5, timeout=3),
        )

        self.assertEqual(result.status, SourceStatus.SUCCESS)
        self.assertEqual(len(result.raw_offers), 1)
        offer = result.raw_offers[0]
        self.assertEqual(offer.price, 79_990)
        self.assertEqual(offer.platform, "Ozon")
        self.assertEqual(offer.seller_name, "Ozon")
        self.assertIn("structured_api", str(offer.raw_metadata))

    async def test_invalid_query_never_calls_provider(self) -> None:
        called = False

        def fake_search(*_args):
            nonlocal called
            called = True
            return []

        invalid = SourceQuery(
            query="Apple iPhone 16 256 ГБ new",
            source="shopping_search",
            tier=QueryTier.STRICT,
            hard_tokens_required=["Apple", "iPhone 16", "Pro", "256 ГБ", "new"],
            hard_tokens_preserved=["Apple", "iPhone 16", "256 ГБ", "new"],
        )
        result = await ShoppingSearchAdapter(fake_search).search(
            request(), invalid, SourceContext(limit=5, timeout=3),
        )

        self.assertEqual(result.status, SourceStatus.INVALID_QUERY_PLAN)
        self.assertFalse(called)

    def test_no_key_is_empty_without_network(self) -> None:
        with (
            patch.object(settings, "SEARCHAPI_ENABLED", False),
            patch.object(settings, "SEARCHAPI_API_KEY", ""),
            patch.object(settings, "SERPAPI_ENABLED", False),
            patch.object(settings, "SERPAPI_API_KEY", ""),
            patch.dict(os.environ, {"SEARCH_V2_SHOPPING_PROVIDER": "auto"}),
        ):
            payload = shopping_google_legacy_search(
                query().query, request(), SourceContext(limit=5, timeout=3),
            )
        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["candidates"], [])

    def test_provider_selection_honours_ready_provider(self) -> None:
        with (
            patch.object(settings, "SEARCHAPI_ENABLED", True),
            patch.object(settings, "SEARCHAPI_API_KEY", "search-key"),
            patch.object(settings, "SERPAPI_ENABLED", True),
            patch.object(settings, "SERPAPI_API_KEY", "serp-key"),
            patch.dict(os.environ, {"SEARCH_V2_SHOPPING_PROVIDER": "serpapi"}),
        ):
            self.assertEqual(selected_shopping_provider(), "serpapi")

    def test_direct_product_link_and_seller_platform_are_preserved(self) -> None:
        rows = _normalize_candidates([{
            "title": "Apple iPhone 16 Pro 256 ГБ",
            "price": 79_990,
            "url": "https://www.google.com/shopping/product/1",
            "seller": "DNS",
            "raw": {
                "product_link": "https://www.dns-shop.ru/product/iphone-16-pro-123456/",
                "link": "https://www.google.com/shopping/product/1",
            },
        }], provider="searchapi", limit=5)

        self.assertEqual(rows[0]["url"], "https://www.dns-shop.ru/product/iphone-16-pro-123456/")
        self.assertEqual(rows[0]["platform"], "DNS")
        self.assertEqual(rows[0]["raw"]["shopping_provider"], "searchapi")


if __name__ == "__main__":
    unittest.main(verbosity=2)
