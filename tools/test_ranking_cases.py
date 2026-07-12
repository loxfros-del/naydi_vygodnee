"""25+ ranking/dedupe invariants без сети."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.candidate_dedupe import dedupe_candidates, diversify_top_sources
from app.ranking import rank_candidate
from app.request_parser import full_parse


def candidate(
    title: str,
    *,
    price: int | None = 30_000,
    status: str = "VERIFIED_GOOD",
    source: str = "direct_retail",
    availability: str = "AVAILABLE",
    facts: dict | None = None,
    url: str = "https://shop.example/product/10001",
    price_source: str = "direct_store",
    product_quality_level: str = "good",
    risks: list[str] | None = None,
    low_price_suspect: bool = False,
) -> dict:
    return {
        "title": title,
        "url": url,
        "source": source,
        "price": price,
        "price_source": price_source,
        "verify_status": status,
        "status": status,
        "availability": availability,
        "product_facts": facts or {},
        "product_quality_level": product_quality_level,
        "risk_flags": risks or [],
        "low_price_suspect": low_price_suspect,
    }


def score(query: str, item: dict) -> float:
    return rank_candidate(full_parse(query), item).score


class RankingCases(unittest.TestCase):
    def test_01_exact_above_wrong_model(self) -> None:
        self.assertGreater(score("iPhone 15 Pro 256 ГБ", candidate("iPhone 15 Pro 256 ГБ")), score("iPhone 15 Pro 256 ГБ", candidate("iPhone 15 Pro Max 256 ГБ")))

    def test_02_good_above_manual(self) -> None:
        good = candidate("Samsung Galaxy A55 256 ГБ", status="VERIFIED_GOOD")
        manual = candidate("Samsung Galaxy A55 256 ГБ", status="NEED_MANUAL_CHECK")
        self.assertGreater(score("Samsung Galaxy A55 256 ГБ", good), score("Samsung Galaxy A55 256 ГБ", manual))

    def test_03_available_above_unavailable(self) -> None:
        available = candidate("Samsung Galaxy A55 256 ГБ", availability="AVAILABLE")
        unavailable = candidate("Samsung Galaxy A55 256 ГБ", availability="UNAVAILABLE", status="UNAVAILABLE")
        self.assertGreater(score("Samsung Galaxy A55 256 ГБ", available), score("Samsung Galaxy A55 256 ГБ", unavailable))

    def test_04_price_above_missing_price(self) -> None:
        priced = candidate("Sony WH-1000XM5", price=30_000)
        missing = candidate("Sony WH-1000XM5", price=None, status="PRICE_MISSING", price_source="")
        self.assertGreater(score("Sony WH-1000XM5", priced), score("Sony WH-1000XM5", missing))

    def test_05_in_budget_above_soft_overage(self) -> None:
        self.assertGreater(score("Наушники до 10000", candidate("Sony беспроводные наушники", price=9_000)), score("Наушники до 10000", candidate("Sony беспроводные наушники", price=11_000, status="OVER_BUDGET_SOFT")))

    def test_06_soft_overage_above_hard_overage(self) -> None:
        self.assertGreater(score("Наушники до 10000", candidate("Sony беспроводные наушники", price=11_000, status="OVER_BUDGET_SOFT")), score("Наушники до 10000", candidate("Sony беспроводные наушники", price=15_000, status="OVER_BUDGET_HARD")))

    def test_07_wireless_known_above_generic_wired(self) -> None:
        wireless = candidate("Sony WH-1000XM5 беспроводные ANC", facts={"category": "headphones", "brand": "Sony", "connection": "wireless", "anc": True})
        wired = candidate("Generic проводные наушники", facts={"category": "headphones", "connection": "wired"})
        self.assertGreater(score("Беспроводные наушники с ANC до 35000", wireless), score("Беспроводные наушники с ANC до 35000", wired))

    def test_08_ryzen_above_n95(self) -> None:
        ryzen = candidate("Lenovo ноутбук Ryzen 5 RAM 16 ГБ SSD 512 ГБ", facts={"category": "laptop", "brand": "Lenovo", "cpu": "RYZEN 5", "ram": "16 ГБ", "ssd": "512 ГБ"})
        n95 = candidate("ECHIPS ноутбук N95 RAM 16 ГБ SSD 512 ГБ", facts={"category": "laptop", "brand": "ECHIPS", "cpu": "N95", "ram": "16 ГБ", "ssd": "512 ГБ"})
        self.assertGreater(score("Ноутбук Ryzen 5 16 ГБ SSD 512 до 60000", ryzen), score("Ноутбук Ryzen 5 16 ГБ SSD 512 до 60000", n95))

    def test_09_high_product_card_above_generic(self) -> None:
        high = candidate("Sony WH-1000XM5", url="https://shop.example/product/10001")
        low = candidate("Sony WH-1000XM5", url="https://example.org/page", price_source="")
        self.assertGreater(score("Sony WH-1000XM5", high), score("Sony WH-1000XM5", low))

    def test_10_verified_above_blocked(self) -> None:
        verified = candidate("Sony WH-1000XM5", status="VERIFIED_GOOD")
        blocked = candidate("Sony WH-1000XM5", status="VERIFY_BLOCKED")
        self.assertGreater(score("Sony WH-1000XM5", verified), score("Sony WH-1000XM5", blocked))

    def test_11_complete_facts_above_sparse(self) -> None:
        complete = candidate("AOC монитор 27 QHD 144 Гц IPS", facts={"category": "monitor", "brand": "AOC", "model": "AOC Q27", "diagonal": "27", "resolution": "QHD", "refresh_rate": "144 Гц", "panel": "IPS", "adaptive_sync": "FreeSync"})
        sparse = candidate("Монитор 27 дюймов 144 Гц", facts={"category": "monitor", "diagonal": "27", "refresh_rate": "144 Гц"})
        self.assertGreater(score("Монитор 27 QHD 144 Гц", complete), score("Монитор 27 QHD 144 Гц", sparse))

    def test_12_exact_storage_above_wrong_storage(self) -> None:
        exact = candidate("iPhone 15 Pro 256 ГБ")
        wrong = candidate("iPhone 15 Pro 128 ГБ")
        self.assertGreater(score("iPhone 15 Pro 256 ГБ", exact), score("iPhone 15 Pro 256 ГБ", wrong))

    def test_13_monitor_144_above_75(self) -> None:
        good = candidate("Монитор LG 27 дюймов QHD 144 Гц", facts={"category": "monitor", "diagonal": "27", "resolution": "QHD", "refresh_rate": "144 Гц"})
        weak = candidate("Монитор LG 27 дюймов QHD 75 Гц", facts={"category": "monitor", "diagonal": "27", "resolution": "QHD", "refresh_rate": "75 Гц"})
        self.assertGreater(score("Монитор 27 QHD 144 Гц", good), score("Монитор 27 QHD 144 Гц", weak))

    def test_14_4k_tv_above_full_hd(self) -> None:
        good = candidate("Телевизор Hisense 55 дюймов 4K 120 Гц", facts={"category": "tv", "diagonal": "55", "resolution": "4K", "refresh_rate": "120 Гц", "hdmi": "HDMI 2.1"})
        weak = candidate("Телевизор Hisense 55 дюймов Full HD 60 Гц", facts={"category": "tv", "diagonal": "55", "resolution": "Full HD", "refresh_rate": "60 Гц"})
        self.assertGreater(score("Телевизор 55 4K 120 Гц для PS5", good), score("Телевизор 55 4K 120 Гц для PS5", weak))

    def test_15_exact_mattress_size_above_wrong(self) -> None:
        exact = candidate("Матрас 160x200 средней жёсткости", facts={"category": "mattress", "size": "160x200", "firmness": "средняя"})
        wrong = candidate("Матрас 140x200 средней жёсткости", facts={"category": "mattress", "size": "140x200", "firmness": "средняя"})
        self.assertGreater(score("Матрас 160x200 средней жёсткости", exact), score("Матрас 160x200 средней жёсткости", wrong))

    def test_16_accessory_has_low_cap(self) -> None:
        result = rank_candidate(full_parse("iPhone 15 Pro"), candidate("Чехол для iPhone 15 Pro"))
        self.assertLessEqual(result.score, 15)

    def test_17_category_mismatch_has_low_score(self) -> None:
        self.assertLess(score("Робот-пылесос", candidate("Вертикальный пылесос Dyson")), 30)

    def test_18_low_price_suspect_is_penalized(self) -> None:
        normal = candidate("Sony WH-1000XM5", price=25_000)
        suspect = candidate("Sony WH-1000XM5", price=500, low_price_suspect=True, risks=["подозрительно низкая цена"])
        self.assertGreater(score("Sony WH-1000XM5 до 30000", normal), score("Sony WH-1000XM5 до 30000", suspect))

    def test_19_manual_cap(self) -> None:
        self.assertLessEqual(score("Sony WH-1000XM5", candidate("Sony WH-1000XM5", status="NEED_MANUAL_CHECK")), 69)

    def test_20_blocked_cap(self) -> None:
        self.assertLessEqual(score("Sony WH-1000XM5", candidate("Sony WH-1000XM5", status="VERIFY_BLOCKED")), 60)

    def test_21_missing_price_cap(self) -> None:
        self.assertLessEqual(score("Sony WH-1000XM5", candidate("Sony WH-1000XM5", price=None, status="PRICE_MISSING")), 50)

    def test_22_unknown_category_cap(self) -> None:
        self.assertLessEqual(score("Аккумуляторная дрель", candidate("Дрель Bosch GSR")), 60)

    def test_23_score_never_exceeds_100(self) -> None:
        rich = candidate("Hisense телевизор 55 4K 120 Гц HDMI 2.1", facts={"category": "tv", "brand": "Hisense", "model": "Hisense 55E7", "diagonal": "55", "resolution": "4K", "refresh_rate": "120 Гц", "hdmi": "HDMI 2.1", "panel": "QLED", "smart_platform": "Google TV"})
        self.assertLessEqual(score("Телевизор 55 4K 120 Гц", rich), 100)

    def test_24_breakdown_has_required_components(self) -> None:
        result = rank_candidate(full_parse("Sony WH-1000XM5"), candidate("Sony WH-1000XM5"))
        self.assertEqual(set(result.breakdown), {"relevance", "model_match", "required_facts", "quality", "budget", "availability", "source", "verification", "completeness", "penalties"})

    def test_25_cap_is_applied_last(self) -> None:
        result = rank_candidate(full_parse("Sony WH-1000XM5"), candidate("Sony WH-1000XM5", status="VERIFY_BLOCKED"))
        self.assertLessEqual(result.score, result.raw_score)
        self.assertEqual(result.score_cap, 60)

    def test_26_dedupe_keeps_best_offer(self) -> None:
        weaker = candidate("iPhone 15 Pro 256 ГБ", source="generic_web", price=90_000, price_source="", url="https://a.example/product/1")
        better = candidate("iPhone 15 Pro 256 ГБ", source="direct_retail", price=85_000, url="https://b.example/product/2")
        rows = dedupe_candidates([weaker, better], sort_key=lambda item: (0 if item["source"] == "direct_retail" else 1, item["price"]))
        self.assertEqual(rows, [better])

    def test_27_dedupe_preserves_storage_variants(self) -> None:
        rows = dedupe_candidates([candidate("iPhone 15 Pro 128 ГБ", url="https://a.example/product/1"), candidate("iPhone 15 Pro 256 ГБ", url="https://b.example/product/2")], sort_key=lambda item: item["title"])
        self.assertEqual(len(rows), 2)

    def test_28_dedupe_preserves_pro_max(self) -> None:
        rows = dedupe_candidates([candidate("iPhone 15 Pro 256 ГБ", url="https://a.example/product/1"), candidate("iPhone 15 Pro Max 256 ГБ", url="https://b.example/product/2")], sort_key=lambda item: item["title"])
        self.assertEqual(len(rows), 2)

    def test_29_dedupe_preserves_condition(self) -> None:
        new = candidate("Новый iPhone 15 Pro 256 ГБ", url="https://a.example/product/1", facts={"category": "phone", "model": "iPhone 15 Pro", "storage_gb": 256, "condition": "новый"})
        used = candidate("iPhone 15 Pro 256 ГБ б/у", url="https://b.example/product/2", facts={"category": "phone", "model": "iPhone 15 Pro", "storage_gb": 256, "condition": "б/у"})
        self.assertEqual(len(dedupe_candidates([new, used], sort_key=lambda item: item["title"])), 2)

    def test_30_source_diversity_top3(self) -> None:
        rows = [candidate("A", source="one"), candidate("B", source="one"), candidate("C", source="two")]
        diversified = diversify_top_sources(rows, tier=lambda _: 0, limit=3)
        self.assertEqual([item["source"] for item in diversified[:2]], ["one", "two"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
