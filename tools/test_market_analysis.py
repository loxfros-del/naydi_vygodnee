"""Deterministic tests for grouping, market statistics and role planning."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.market_analysis import (  # noqa: E402
    OfferIdentity,
    OfferPriceClass,
    ProductGroupKey,
    RecommendationRole,
    classify_offer_price,
    compute_market_price_stats,
    deduplicate_offers,
    group_offers,
    plan_recommendation_roles,
)


def offer(
    *,
    source: str = "Ozon",
    seller: str = "Seller",
    url: str = "https://example.test/product/1",
    price: int = 80_000,
    score: float = 80,
    storage: int = 256,
    modifiers: tuple[str, ...] = ("Pro",),
    size: str = "",
    condition: str = "new",
    region: str = "RU",
    sim: str = "nano+esim",
    exact: str = "EXACT",
    availability: str = "AVAILABLE",
    price_confidence: str = "high",
    platform_type: str = "MARKETPLACE",
    seller_trust: str = "UNKNOWN",
    seller_verified: bool = False,
    risks: list[str] | None = None,
) -> dict:
    return {
        "source": source,
        "platform": source,
        "seller": seller,
        "title": "Apple iPhone 16 Pro 256 ГБ",
        "url": url,
        "price": price,
        "score": score,
        "risk_flags": list(risks or []),
        "product_facts": {
            "category": "phone",
            "brand": "Apple",
            "model": "iPhone 16",
            "model_modifiers": list(modifiers),
            "storage_gb": storage,
            "size": size,
            "condition": condition,
            "region": region,
            "sim_variant": sim,
            "exact_match": exact,
            "availability": availability,
            "price_confidence": price_confidence,
            "platform_type": platform_type,
            "seller_trust": seller_trust,
            "seller_verified": seller_verified,
        },
    }


class MarketAnalysisTests(unittest.TestCase):
    def test_grouping_keeps_different_sellers(self) -> None:
        first = offer(seller="Store A", url="https://ozon.ru/product/1")
        second = offer(seller="Store B", url="https://ozon.ru/product/2")
        groups = group_offers([first, second])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(next(iter(groups.values()))), 2)
        self.assertNotEqual(OfferIdentity.from_offer(first), OfferIdentity.from_offer(second))

    def test_grouping_does_not_mix_variants(self) -> None:
        rows = [
            offer(),
            offer(storage=128, url="https://example.test/128"),
            offer(modifiers=("Pro Max",), url="https://example.test/max"),
            offer(size="6.9", url="https://example.test/size"),
            offer(condition="used", url="https://example.test/used"),
            offer(region="EU", url="https://example.test/eu"),
            offer(sim="esim-only", url="https://example.test/esim"),
        ]
        self.assertEqual(len(group_offers(rows)), 7)

    def test_offer_dedupe_uses_listing_not_product_group(self) -> None:
        duplicate = offer(url="https://ozon.ru/product/1?utm_source=x")
        same_listing = offer(url="https://ozon.ru/product/1?ref=ad")
        other_seller = offer(seller="Other", url="https://ozon.ru/product/1?ref=ad")
        self.assertEqual(len(deduplicate_offers([duplicate, same_listing, other_seller])), 2)

    def test_market_stats_filter_bad_evidence(self) -> None:
        prices = [50, 80, 90, 100, 100, 100, 110, 120, 130, 1000]
        rows = [offer(price=value, url=f"https://example.test/{index}") for index, value in enumerate(prices)]
        rows.extend([
            offer(price=1, exact="MODEL_MISMATCH", url="https://example.test/wrong"),
            offer(price=2, availability="UNAVAILABLE", url="https://example.test/unavailable"),
            offer(price=3, price_confidence="low", url="https://example.test/weak"),
        ])
        stats = compute_market_price_stats(rows)
        self.assertIsNotNone(stats)
        assert stats is not None
        self.assertEqual((stats.minimum, stats.median, stats.maximum, stats.count), (50.0, 100.0, 1000.0, 10))
        self.assertEqual(stats.trimmed_mean, 103.75)
        self.assertEqual(stats.market_range, (50.0, 1000.0))
        self.assertEqual(stats.verified_count, 10)

    def test_market_stats_refuse_mixed_configuration(self) -> None:
        with self.assertRaises(ValueError):
            compute_market_price_stats([offer(storage=256), offer(storage=128)])

    def test_price_classes(self) -> None:
        self.assertEqual(classify_offer_price(70, 100), OfferPriceClass.VERY_CHEAP)
        self.assertEqual(classify_offer_price(90, 100), OfferPriceClass.BELOW_MARKET)
        self.assertEqual(classify_offer_price(100, 100), OfferPriceClass.FAIR)
        self.assertEqual(classify_offer_price(120, 100), OfferPriceClass.ABOVE_MARKET)
        self.assertEqual(classify_offer_price(125, 100), OfferPriceClass.VERY_EXPENSIVE)
        self.assertEqual(classify_offer_price(None, 100), OfferPriceClass.UNKNOWN)

    def test_roles_allow_marketplace_avito_and_retail(self) -> None:
        marketplace = offer(
            source="Яндекс Маркет", seller="Good Shop", price=79_000, score=97,
            seller_trust="HIGH", seller_verified=True, url="https://market.yandex.ru/product/1",
        )
        avito = offer(
            source="Avito", seller="Private", price=60_000, score=75,
            platform_type="CLASSIFIED", risks=["частный продавец", "цена сильно ниже рынка"],
            url="https://avito.ru/items/2",
        )
        dns = offer(
            source="DNS", seller="DNS", price=83_000, score=88, platform_type="RETAIL",
            seller_trust="HIGH", seller_verified=True, url="https://dns-shop.ru/product/3",
        )
        assignments = plan_recommendation_roles([marketplace, avito, dns])
        self.assertEqual(
            [item.role for item in assignments],
            [RecommendationRole.BEST_OVERALL, RecommendationRole.CHEAP_WITH_RISK, RecommendationRole.RELIABLE],
        )
        self.assertIs(assignments[0].offer, marketplace)
        self.assertIs(assignments[1].offer, avito)
        self.assertIs(assignments[2].offer, dns)
        self.assertEqual(len({item.identity for item in assignments}), 3)

    def test_roles_do_not_invent_reliable_or_third_offer(self) -> None:
        marketplace = offer(score=95, price=80_000, url="https://ozon.ru/product/1")
        avito = offer(
            source="Avito", seller="Private", price=65_000, score=70,
            platform_type="CLASSIFIED", risks=["частный продавец"], url="https://avito.ru/items/2",
        )
        assignments = plan_recommendation_roles([marketplace, avito])
        self.assertEqual(
            [item.role for item in assignments],
            [RecommendationRole.BEST_OVERALL, RecommendationRole.CHEAP_WITH_RISK],
        )

    def test_duplicate_db_rows_cannot_consume_roles(self) -> None:
        first = offer(score=90, url="https://ozon.ru/product/1?utm_source=x")
        duplicate = offer(score=99, url="https://ozon.ru/product/1?ref=ad")
        dns = offer(
            source="DNS", seller="DNS", score=85, price=82_000, platform_type="RETAIL",
            seller_trust="HIGH", seller_verified=True, url="https://dns-shop.ru/product/2",
        )
        assignments = plan_recommendation_roles([first, duplicate, dns])
        self.assertEqual(len(assignments), 2)
        self.assertEqual({item.role for item in assignments}, {
            RecommendationRole.BEST_OVERALL, RecommendationRole.RELIABLE,
        })
        self.assertIs(assignments[0].offer, duplicate)


if __name__ == "__main__":
    unittest.main(verbosity=2)
