from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.adapters.base import SourceAdapter, SourceCapabilities, SourceContext, SourceResult
from app.search_v2.market_history import MarketHistoryStore, MarketLane
from app.search_v2.models import (
    Offer,
    PlatformTrust,
    ProductCondition,
    RawOffer,
    SearchRequestV2,
    SourceAttempt,
    SourceStatus,
)
from app.search_v2.orchestrator import SearchSourceOrchestrator
from app.search_v2.service import (
    SearchServiceV2,
    _market_history_identity,
    _market_history_lane,
    _market_history_scope,
)
from app.search_v2.source_registry import SourceRegistry


class StaticAdapter(SourceAdapter):
    capabilities = SourceCapabilities(kind="test")
    version = "market-history-test"

    def __init__(self, name: str, platform: str, offer: RawOffer) -> None:
        self.name = name
        self.platform = platform
        self.offer = offer

    async def search(
        self,
        request: SearchRequestV2,
        source_query,
        context: SourceContext,
    ) -> SourceResult:
        return SourceResult(
            status=SourceStatus.SUCCESS,
            raw_offers=(self.offer,),
            attempts=(
                SourceAttempt(
                    source=self.name,
                    query=source_query.query,
                    tier=source_query.tier,
                    status=SourceStatus.SUCCESS,
                    raw_offer_count=1,
                ),
            ),
        )


def request() -> SearchRequestV2:
    return SearchRequestV2(
        original_query="iPhone 16 Pro 256 ГБ новый",
        category="phone",
        brand="Apple",
        canonical_model="iPhone 16",
        model_modifiers=["Pro"],
        required_specs={"storage_gb": 256},
        condition=ProductCondition.NEW,
        city="Ярославль",
        supported_category=True,
        hard_tokens=["iPhone", "16", "Pro", "256"],
    )


def raw(source: str, platform: str, product_id: str, price: int, url: str) -> RawOffer:
    return RawOffer(
        source=source,
        platform=platform,
        product_id=product_id,
        title="Apple iPhone 16 Pro 256 ГБ новый",
        url=url,
        seller_name=f"{source} shop",
        price=price,
        availability_text="в наличии",
        condition=ProductCondition.NEW,
        city="Ярославль",
        raw_metadata={"price_confidence": 0.95, "product_page_verified": True},
    )


def service(store: object) -> SearchServiceV2:
    adapters = (
        StaticAdapter(
            "yandex_market",
            "Yandex Market",
            raw("yandex_market", "Yandex Market", "ym-1", 79_990, "https://market.yandex.ru/product--iphone/123456"),
        ),
        StaticAdapter(
            "ozon",
            "Ozon",
            raw("ozon", "Ozon", "oz-1", 78_990, "https://ozon.ru/product/iphone-16-pro-123456"),
        ),
        StaticAdapter(
            "wildberries",
            "Wildberries",
            raw("wildberries", "Wildberries", "wb-1", 80_490, "https://www.wildberries.ru/catalog/123456/detail.aspx"),
        ),
    )
    orchestrator = SearchSourceOrchestrator(
        SourceRegistry(adapters),
        max_concurrency=3,
        per_source_timeout=1,
        case_timeout=3,
    )
    return SearchServiceV2(
        orchestrator=orchestrator,
        market_history_store=store,
        discovery_sources=("yandex_market", "ozon", "wildberries"),
        anchor_sources=(),
        generic_sources=(),
        overall_timeout=4,
    )


class ExplodingHistoryStore:
    def __init__(self) -> None:
        self.calls = 0

    def capture(self, **_kwargs: object) -> None:
        self.calls += 1
        raise RuntimeError("history storage is unavailable")


class SearchV2MarketHistoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_captures_one_anonymous_marketplace_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = MarketHistoryStore(Path(temp_dir) / "market_history.sqlite3")
            result = await service(store).search(request())

            self.assertEqual(len(result.product_groups), 1)
            identity = result.product_groups[0].identity
            self.assertIsNotNone(identity)
            assert identity is not None
            canonical_identity = _market_history_identity(identity)
            self.assertIsNotNone(canonical_identity)
            first_offer = result.product_groups[0].offers[0]
            rows = store.history(
                canonical_identity=canonical_identity,
                scope=_market_history_scope(result.normalized_request, first_offer),
                lane=MarketLane.NEW_MARKETPLACE,
            )

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].offer_count, 3)
            self.assertEqual(rows[0].source_count, 3)
            self.assertEqual(rows[0].lane, MarketLane.NEW_MARKETPLACE)
            contents = (Path(temp_dir) / "market_history.sqlite3").read_bytes()
            for forbidden in ("iPhone 16 Pro", "Ярославль", "yandex_market", "shop"):
                self.assertNotIn(forbidden.encode("utf-8"), contents)

    async def test_history_failure_never_changes_search_result(self) -> None:
        store = ExplodingHistoryStore()
        result = await service(store).search(request())

        self.assertGreater(store.calls, 0)
        self.assertEqual(len(result.normalized_offers), 3)
        self.assertEqual(len(result.product_groups), 1)
        self.assertFalse(result.errors)

    def test_lane_selection_never_mixes_conditions_or_platforms(self) -> None:
        def offer(condition: ProductCondition, trust: PlatformTrust) -> Offer:
            return Offer(condition=condition, platform_trust=trust)

        self.assertEqual(
            _market_history_lane(offer(ProductCondition.NEW, PlatformTrust.HIGH_RETAIL)),
            MarketLane.NEW_RETAIL,
        )
        self.assertEqual(
            _market_history_lane(offer(ProductCondition.NEW, PlatformTrust.HIGH_MARKETPLACE)),
            MarketLane.NEW_MARKETPLACE,
        )
        self.assertEqual(
            _market_history_lane(offer(ProductCondition.NEW, PlatformTrust.CLASSIFIED)),
            MarketLane.NEW_PRIVATE,
        )
        self.assertEqual(
            _market_history_lane(offer(ProductCondition.USED, PlatformTrust.HIGH_RETAIL)),
            MarketLane.USED,
        )
        self.assertEqual(
            _market_history_lane(offer(ProductCondition.REFURBISHED, PlatformTrust.HIGH_MARKETPLACE)),
            MarketLane.REFURBISHED,
        )
        self.assertIsNone(_market_history_lane(offer(ProductCondition.UNKNOWN, PlatformTrust.UNKNOWN)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
