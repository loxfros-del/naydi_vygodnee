"""End-to-end Search V2 proof with structured price-bearing offers."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("BOT_TOKEN", "123456:ci_test_token")
os.environ.setdefault("ADMIN_IDS", "1")

from app.search_v2.adapters.shopping_search import ShoppingSearchAdapter  # noqa: E402
from app.search_v2.cache import MemorySourceCache  # noqa: E402
from app.search_v2.models import (  # noqa: E402
    ProductCondition,
    RecommendationRole,
    SearchRequestV2,
    SearchResultStatus,
)
from app.search_v2.orchestrator import SearchSourceOrchestrator  # noqa: E402
from app.search_v2.query_planner import QueryPlannerV2  # noqa: E402
from app.search_v2.service import SearchServiceV2  # noqa: E402
from app.search_v2.source_registry import SourceRegistry  # noqa: E402


class SearchV2PricedEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_prices_produce_three_distinct_roles(self) -> None:
        rows = [
            {
                "id": "ozon-1",
                "title": "Apple iPhone 16 Pro 256 ГБ новый",
                "price": 74_990,
                "url": "https://www.ozon.ru/product/iphone-16-pro-111111111/",
                "store": "Ozon",
                "seller": "Ozon",
                "platform": "Ozon",
                "availability": "В наличии",
                "condition": "новый",
                "seller_rating": 4.8,
                "seller_reviews_count": 1200,
                "seller_verified": True,
                "link_verified": True,
                "availability_verified": True,
                "price_source": "structured_api",
            },
            {
                "id": "avito-1",
                "title": "Apple iPhone 16 Pro 256 ГБ новый",
                "price": 69_990,
                "url": "https://www.avito.ru/moskva/telefony/iphone_16_pro_256_2222222222",
                "store": "Avito Shop",
                "seller": "Avito Shop",
                "platform": "Avito",
                "availability": "В наличии",
                "condition": "новый",
                "seller_rating": 4.9,
                "seller_reviews_count": 350,
                "seller_verified": True,
                "link_verified": True,
                "availability_verified": True,
                "price_source": "structured_api",
            },
            {
                "id": "dns-1",
                "title": "Apple iPhone 16 Pro 256 ГБ новый",
                "price": 79_990,
                "url": "https://www.dns-shop.ru/product/iphone-16-pro-333333/",
                "store": "DNS",
                "seller": "DNS",
                "platform": "DNS",
                "availability": "В наличии",
                "condition": "новый",
                "seller_verified": True,
                "link_verified": True,
                "availability_verified": True,
                "price_source": "structured_api",
            },
        ]

        def structured_search(_query, _request, _context):
            return {"status": "success", "candidates": rows}

        registry = SourceRegistry([ShoppingSearchAdapter(structured_search)])
        orchestrator = SearchSourceOrchestrator(
            registry=registry,
            cache=MemorySourceCache(),
            max_concurrency=1,
            per_source_timeout=2,
            case_timeout=4,
            result_limit=10,
        )
        service = SearchServiceV2(
            planner=QueryPlannerV2(max_queries_per_source=1),
            orchestrator=orchestrator,
            discovery_sources=("shopping_search",),
            anchor_sources=(),
            generic_sources=(),
            overall_timeout=5,
            page_verifier=None,
        )
        request = SearchRequestV2(
            original_query="iPhone 16 Pro 256 ГБ новый до 80000",
            category="phone",
            brand="Apple",
            canonical_model="iPhone 16",
            model_modifiers=["Pro"],
            required_specs={"storage_gb": 256},
            budget=80_000,
            condition=ProductCondition.NEW,
            supported_category=True,
            hard_tokens=["Apple", "iPhone 16", "Pro", "256 ГБ", "new"],
        )

        result = await service.search(request)

        self.assertIn(result.status, {SearchResultStatus.SUCCESS, SearchResultStatus.MANUAL_REVIEW_REQUIRED})
        self.assertEqual(result.metrics.valid_price_rate, 1.0)
        self.assertEqual(result.metrics.top1_exact, True)
        self.assertEqual(len(result.product_groups), 1)
        self.assertEqual(len(result.recommendations), 3)
        self.assertEqual(len({item.offer_id for item in result.recommendations}), 3)
        self.assertEqual(
            {item.role for item in result.recommendations},
            {
                RecommendationRole.BEST_OVERALL,
                RecommendationRole.CHEAP_WITH_RISK,
                RecommendationRole.RELIABLE,
            },
        )
        self.assertTrue(all(item.offer and item.offer.price for item in result.recommendations))
        self.assertTrue(all(item.offer.exact_match.value == "EXACT" for item in result.recommendations))


if __name__ == "__main__":
    unittest.main(verbosity=2)
