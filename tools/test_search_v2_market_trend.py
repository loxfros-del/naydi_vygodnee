from __future__ import annotations

from datetime import date, timedelta
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.market_history import (  # noqa: E402
    ComparableMarketOffer,
    MarketHistoryStore,
    MarketLane,
    MarketTrend,
    build_market_trend,
)
from app.search_v2.models import (  # noqa: E402
    AvailabilityInfo,
    AvailabilityStatus,
    ExactMatchResult,
    Offer,
    PlatformTrust,
    ProductCondition,
    ProductIdentity,
    Recommendation,
    RecommendationRole,
    SearchRequestV2,
    SearchResultStatus,
    SearchResultV2,
)
from app.web_api import build_web_response  # noqa: E402


class _TrendStore:
    def __init__(self, trend: MarketTrend | None = None, *, raises: bool = False) -> None:
        self.trend_value = trend
        self.raises = raises
        self.calls: list[dict[str, object]] = []

    def trend(self, **kwargs: object) -> MarketTrend | None:
        self.calls.append(kwargs)
        if self.raises:
            raise RuntimeError("history storage unavailable")
        return self.trend_value


def _offers(prices: tuple[float, float, float]) -> list[ComparableMarketOffer]:
    return [
        ComparableMarketOffer(price=prices[0], source="one", comparable=True),
        ComparableMarketOffer(price=prices[1], source="two", comparable=True),
        ComparableMarketOffer(price=prices[2], source="one", comparable=True),
    ]


class SearchV2MarketTrendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = MarketHistoryStore(Path(self.temp.name) / "market_history.sqlite3")
        self.identity = {"category": "laptop", "model": "MacBook Air M4", "ram": 16}
        self.scope = {"city_hash": "a" * 64, "coverage": "local", "currency": "RUB"}
        self.today = date.today()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_trend_requires_separate_days_and_returns_only_movement(self) -> None:
        for offset, prices in ((2, (100_000, 101_000, 102_000)), (1, (97_000, 98_000, 99_000)), (0, (94_000, 95_000, 96_000))):
            self.assertIsNotNone(self.store.capture(
                canonical_identity=self.identity,
                scope=self.scope,
                lane=MarketLane.NEW_MARKETPLACE,
                offers=_offers(prices),
                observed_on=self.today - timedelta(days=offset),
            ))

        trend = self.store.trend(
            canonical_identity=self.identity,
            scope=self.scope,
            lane=MarketLane.NEW_MARKETPLACE,
            as_of=self.today,
        )

        self.assertEqual(trend, MarketTrend(direction="down", percent_change=5.9, observed_days=3))
        rows = self.store.history(
            canonical_identity=self.identity,
            scope=self.scope,
            lane=MarketLane.NEW_MARKETPLACE,
        )
        self.assertEqual(build_market_trend(rows[:2]), None)

    def test_trend_never_mixes_lanes_or_shows_small_noise(self) -> None:
        for offset, prices in ((2, (100_000, 100_500, 101_000)), (1, (100_500, 101_000, 101_500)), (0, (100_700, 101_200, 101_700))):
            self.store.capture(
                canonical_identity=self.identity,
                scope=self.scope,
                lane=MarketLane.NEW_RETAIL,
                offers=_offers(prices),
                observed_on=self.today - timedelta(days=offset),
            )
        self.store.capture(
            canonical_identity=self.identity,
            scope=self.scope,
            lane=MarketLane.USED,
            offers=_offers((50_000, 51_000, 52_000)),
            observed_on=self.today,
        )

        self.assertIsNone(self.store.trend(
            canonical_identity=self.identity,
            scope=self.scope,
            lane=MarketLane.NEW_RETAIL,
            as_of=self.today,
        ))
        self.assertIsNone(self.store.trend(
            canonical_identity=self.identity,
            scope=self.scope,
            lane=MarketLane.USED,
            as_of=self.today,
        ))

    def test_web_response_exposes_only_a_compact_trend_label(self) -> None:
        offer = Offer(
            offer_id="private-offer-id",
            title="Apple MacBook Air M4 16 GB / 512 GB",
            url="https://shop.example/product/macbook-m4-512-123456",
            source="secret-source",
            platform="Safe Shop",
            price=99_990,
            condition=ProductCondition.NEW,
            platform_trust=PlatformTrust.HIGH_MARKETPLACE,
            exact_match=ExactMatchResult.EXACT,
            availability=AvailabilityInfo(status=AvailabilityStatus.IN_STOCK, available=True),
            identity=ProductIdentity(
                category="laptop",
                brand="Apple",
                canonical_model="MacBook Air M4",
                storage=512,
                key_configuration={"ram_gb": 16},
                condition=ProductCondition.NEW,
            ),
        )
        result = SearchResultV2(
            normalized_request=SearchRequestV2(category="laptop", canonical_model="MacBook Air M4", city="Москва"),
            recommendations=[Recommendation(role=RecommendationRole.BEST_OVERALL, offer_id=offer.offer_id, offer=offer)],
            normalized_offers=[offer],
            status=SearchResultStatus.SUCCESS,
        )
        store = _TrendStore(MarketTrend(direction="down", percent_change=4.2, observed_days=7))

        response = build_web_response(result, market_history_store=store)
        trend = response["recommendations"][0]["marketTrend"]

        self.assertEqual(trend, {"direction": "down", "label": "За 7 дней медиана снизилась на 4.2%"})
        self.assertEqual(set(trend), {"direction", "label"})
        packed_trend = json.dumps(trend, ensure_ascii=False).casefold()
        for forbidden in ("private-offer-id", "secret-source", "shop.example", "москва", "city_hash", "scope_hash", "metric"):
            self.assertNotIn(forbidden, packed_trend)
        self.assertEqual(len(store.calls), 1)
        self.assertNotIn("Москва", json.dumps(store.calls[0]["scope"], ensure_ascii=False))

    def test_web_response_omits_unavailable_or_broken_trend(self) -> None:
        offer = Offer(
            offer_id="nova-1",
            title="Apple iPhone 16 256 GB",
            url="https://shop.example/product/iphone-16-123456",
            price=79_990,
            condition=ProductCondition.NEW,
            platform_trust=PlatformTrust.HIGH_MARKETPLACE,
            exact_match=ExactMatchResult.EXACT,
            availability=AvailabilityInfo(status=AvailabilityStatus.IN_STOCK, available=True),
            identity=ProductIdentity(category="phone", canonical_model="iPhone 16", storage=256, condition=ProductCondition.NEW),
        )
        result = SearchResultV2(
            normalized_request=SearchRequestV2(category="phone", canonical_model="iPhone 16"),
            recommendations=[Recommendation(role=RecommendationRole.BEST_OVERALL, offer_id=offer.offer_id, offer=offer)],
            normalized_offers=[offer],
            status=SearchResultStatus.SUCCESS,
        )

        self.assertNotIn("marketTrend", build_web_response(result, market_history_store=_TrendStore()) ["recommendations"][0])
        self.assertNotIn("marketTrend", build_web_response(result, market_history_store=_TrendStore(raises=True))["recommendations"][0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
