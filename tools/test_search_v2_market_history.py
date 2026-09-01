from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.market_history import (  # noqa: E402
    ComparableMarketOffer,
    MARKET_LANES,
    MarketHistoryStore,
    MarketLane,
    NullMarketHistoryStore,
    build_daily_aggregate,
)


def offer(price: float, source: str, **kwargs: object) -> ComparableMarketOffer:
    return ComparableMarketOffer(price=price, source=source, comparable=True, **kwargs)


class SearchV2MarketHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "market_history.sqlite3"
        self.store = MarketHistoryStore(self.path)
        self.identity = {"category": "laptop", "model": "MacBook Air M4", "ram": 16}
        self.scope = {"city": "Москва", "condition": "new", "currency": "RUB"}
        self.today = date(2026, 8, 13)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_all_lanes_are_explicit(self) -> None:
        self.assertEqual(
            MARKET_LANES,
            ("new_retail", "new_marketplace", "new_private", "used", "refurbished"),
        )

    def test_requires_three_current_comparable_offers_from_two_sources(self) -> None:
        self.assertIsNone(
            build_daily_aggregate(
                canonical_identity=self.identity,
                scope=self.scope,
                lane=MarketLane.NEW_RETAIL,
                offers=[offer(100, "one"), offer(110, "two")],
                observed_on=self.today,
            )
        )
        self.assertIsNone(
            build_daily_aggregate(
                canonical_identity=self.identity,
                scope=self.scope,
                lane=MarketLane.NEW_RETAIL,
                offers=[offer(100, "one"), offer(110, "one"), offer(120, "one")],
                observed_on=self.today,
            )
        )
        self.assertIsNone(
            build_daily_aggregate(
                canonical_identity=self.identity,
                scope=self.scope,
                lane=MarketLane.NEW_RETAIL,
                offers=[
                    offer(100, "one"),
                    offer(110, "two", current=False),
                    offer(120, "three", observed_at=self.today - timedelta(days=1)),
                ],
                observed_on=self.today,
            )
        )

    def test_stores_anonymous_daily_aggregate_only(self) -> None:
        aggregate = self.store.capture(
            canonical_identity=self.identity,
            scope=self.scope,
            lane="new_retail",
            offers=[offer(100_000, "shop one"), offer(110_000, "shop two"), offer(120_000, "shop one")],
            observed_on=self.today,
        )
        self.assertIsNotNone(aggregate)
        assert aggregate is not None
        self.assertEqual(aggregate.offer_count, 3)
        self.assertEqual(aggregate.source_count, 2)
        self.assertEqual(aggregate.median_price, 110_000)
        self.assertEqual(aggregate.lane, MarketLane.NEW_RETAIL)
        self.assertEqual(len(aggregate.identity_hash), 64)
        self.assertEqual(len(aggregate.scope_hash), 64)

        stored = self.store.history(
            canonical_identity=self.identity,
            scope=self.scope,
            lane=MarketLane.NEW_RETAIL,
        )
        self.assertEqual(stored, [aggregate])
        contents = self.path.read_bytes()
        for forbidden in ("MacBook Air M4", "Москва", "shop one", "new_retail"):
            # The lane is intentionally stored, but raw product, city, and source never are.
            if forbidden == "new_retail":
                continue
            self.assertNotIn(forbidden.encode("utf-8"), contents)

    def test_same_day_capture_is_an_idempotent_upsert(self) -> None:
        first = self.store.capture(
            canonical_identity=self.identity,
            scope=self.scope,
            lane=MarketLane.NEW_MARKETPLACE,
            offers=[offer(100, "a"), offer(200, "b"), offer(300, "a")],
            observed_on=self.today,
        )
        second = self.store.capture(
            canonical_identity=self.identity,
            scope=self.scope,
            lane=MarketLane.NEW_MARKETPLACE,
            offers=[offer(200, "a"), offer(300, "b"), offer(400, "a")],
            observed_on=self.today,
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        rows = self.store.history(
            canonical_identity=self.identity,
            scope=self.scope,
            lane=MarketLane.NEW_MARKETPLACE,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].median_price, 300)
        self.assertEqual(rows[0].offer_count, 3)

    def test_retention_prunes_aggregates_older_than_ninety_days(self) -> None:
        current = self.store.capture(
            canonical_identity=self.identity,
            scope=self.scope,
            lane=MarketLane.USED,
            offers=[offer(10, "a"), offer(20, "b"), offer(30, "a")],
            observed_on=self.today,
        )
        self.assertIsNotNone(current)
        assert current is not None
        old_day = self.today - timedelta(days=90)
        connection = sqlite3.connect(self.path)
        try:
            connection.execute(
                """
                INSERT INTO daily_market_aggregates (
                    identity_hash, scope_hash, lane, observed_on, offer_count, source_count,
                    minimum_price, median_price, maximum_price, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    current.identity_hash,
                    current.scope_hash,
                    MarketLane.USED.value,
                    old_day.isoformat(),
                    3,
                    2,
                    1.0,
                    2.0,
                    3.0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        self.assertEqual(self.store.prune(reference_day=self.today), 1)
        rows = self.store.history(canonical_identity=self.identity, scope=self.scope)
        self.assertEqual(rows, [current])

    def test_null_and_storage_errors_are_harmless(self) -> None:
        null_store = NullMarketHistoryStore()
        self.assertIsNone(null_store.capture())
        self.assertEqual(null_store.history(), [])
        self.assertEqual(null_store.prune(), 0)

        broken = MarketHistoryStore(Path(self.temp.name))
        self.assertIsNone(
            broken.capture(
                canonical_identity=self.identity,
                scope=self.scope,
                lane=MarketLane.REFURBISHED,
                offers=[offer(1, "a"), offer(2, "b"), offer(3, "a")],
            )
        )
        self.assertEqual(broken.history(canonical_identity=self.identity, scope=self.scope), [])
        self.assertEqual(broken.prune(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
