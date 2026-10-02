"""Offline comparable market engine tests."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.market_engine import SkuMarketSnapshotCache, build_market_snapshots, normalized_sku
from avito_service.models import NormalizedListing, SellerSummary


def item(identifier: str, price: int, *, seller: str, company: bool = True, description: str = "Новая консоль."):
    return NormalizedListing(
        listing_id=identifier,
        title="Sony PlayStation 5 Slim Disc 1TB новая",
        url=f"https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/sony_ps5_{identifier}",
        status="active",
        price=price,
        currency="RUB",
        description=description,
        images=("https://example.test/photo.jpg",),
        image_count_claimed=1,
        parameters={"Модель": "PlayStation 5 Slim Disc", "Встроенная память": "1 TB", "Состояние": "Новое"},
        parameter_ids={}, badges=(), stock="",
        seller=SellerSummary(name=seller, seller_type="company" if company else "private",
                             is_shop=company, identity_hash=seller),
        collected_at="2026-09-23T10:00:00+03:00", location="Ярославль",
    )


class MarketEngineTests(unittest.TestCase):
    def test_snapshot_is_shared_by_sku_and_seller_lanes_are_separate(self):
        listings = [
            item("1000000001", 60_000, seller="shop-a"),
            item("1000000002", 62_000, seller="shop-b"),
            item("1000000003", 58_000, seller="person-a", company=False),
        ]
        snapshots = build_market_snapshots(listings, city="Ярославль", observed_date="2026-09-23")
        sku = normalized_sku(listings[0])
        self.assertIsNotNone(sku)
        self.assertEqual(len(snapshots), 1)
        snapshot = snapshots[sku.key]
        self.assertEqual(snapshot.segment("company").median, 61_000)
        self.assertEqual(snapshot.segment("company").stable_seller_count, 2)
        self.assertEqual(snapshot.segment("private").median, 58_000)
        self.assertIs(snapshot, snapshots[normalized_sku(listings[1]).key])

    def test_ambiguous_repair_and_trade_in_are_excluded(self):
        ambiguous = replace(
            item("1000000004", 49_999, seller="shop-c"),
            description="PS5 Slim Digital — 66 999 руб.\nPS5 Slim Disc — 63 999 руб.",
        )
        listings = [
            item("1000000005", 60_000, seller="shop-a"),
            item("1000000006", 55_000, seller="shop-b", description="После ремонта, полностью исправна."),
            item("1000000007", 50_000, seller="shop-c", description="Цена при trade-in."),
            ambiguous,
        ]
        snapshot = next(iter(build_market_snapshots(listings, city="Ярославль").values()))
        self.assertEqual(snapshot.segment("company").prices, (60_000,))
        self.assertEqual(snapshot.excluded_count, 3)

    def test_quartiles_and_minimum_sane_price(self):
        prices = (10_000, 60_000, 61_000, 62_000, 63_000)
        listings = [item(str(2000000000 + index), price, seller=f"shop-{index}") for index, price in enumerate(prices)]
        snapshot = next(iter(build_market_snapshots(listings, city="Ярославль").values()))
        segment = snapshot.segment("company")
        self.assertEqual(segment.median, 61_000)
        self.assertEqual(segment.p25, 60_000)
        self.assertEqual(segment.p75, 62_000)
        self.assertEqual(segment.minimum_sane_price, 60_000)

    def test_date_city_sku_cache_round_trip(self):
        listing = item("3000000001", 60_000, seller="shop-a")
        snapshot = next(iter(build_market_snapshots([listing], city="Ярославль", observed_date="2026-09-23").values()))
        with tempfile.TemporaryDirectory() as directory:
            cache = SkuMarketSnapshotCache(Path(directory))
            cache.put(snapshot)
            loaded = cache.get(snapshot.sku, "Ярославль", "2026-09-23")
        self.assertEqual(loaded, snapshot)

    def test_unknown_seller_is_not_counted_as_private(self):
        listing = replace(
            item("3000000002", 61_000, seller="unknown"),
            seller=SellerSummary(),
        )
        snapshot = next(iter(build_market_snapshots([listing], city="Ярославль").values()))
        self.assertIsNone(snapshot.segment("private"))
        self.assertEqual(snapshot.segment("unknown").seller_count, 0)
        self.assertEqual(snapshot.segment("unknown").median, 61_000)

    def test_same_weak_seller_name_is_one_source(self):
        left = replace(item("3000000003", 60_000, seller="Same Shop"),
                       seller=SellerSummary(name="Same Shop", seller_type="company"))
        right = replace(item("3000000004", 62_000, seller="Same Shop"),
                        seller=SellerSummary(name=" same  shop ", seller_type="company"))
        segment = next(iter(build_market_snapshots([left, right], city="Ярославль").values())).segment("company")
        self.assertEqual(segment.seller_count, 1)
        self.assertEqual(segment.stable_seller_count, 0)
        self.assertEqual(segment.listing_count, 2)

    def test_generic_seller_label_is_not_independence_evidence(self):
        left = replace(item("3000000005", 60_000, seller="Пользователь", company=False),
                       seller=SellerSummary(name="Пользователь", seller_type="person"))
        right = replace(item("3000000006", 70_000, seller="Пользователь", company=False),
                        seller=SellerSummary(name="Пользователь", seller_type="person"))
        segment = next(iter(build_market_snapshots([left, right], city="Ярославль").values())).segment("private")
        self.assertEqual(segment.seller_count, 0)
        self.assertEqual(segment.prices, (60_000, 70_000))


if __name__ == "__main__":
    unittest.main()
