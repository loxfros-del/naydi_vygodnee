from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.fill_target import CollectionPage, CollectionSafetyLimits, fill_to_target
from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig
from avito_service.models import CollectionBatch, SearchRequest


def raw(identifier: int, *, pro: bool = False, city: str = "Ярославль") -> dict:
    model = "PlayStation 5 Pro" if pro else "PlayStation 5 Slim"
    title = f"Sony {model} 1TB"
    return {
        "id": str(identifier), "title": title,
        "url": f"https://www.avito.ru/yaroslavl/igry_pristavki_i_programmy/ps5_{identifier}",
        "status": "active", "price": 60_000, "description": "Новая консоль.",
        "images": ["https://10.img.avito.st/image/test"],
        "parameters": {"Модель": model, "Встроенная память, ГБ": "1000", "Состояние": "Новое"},
        "location": city,
    }


class FillToTargetTests(unittest.TestCase):
    def test_zen_adapter_pages_with_standard_only_query_variants(self):
        class OfflineZen(ZenStudioProvider):
            def __init__(self):
                super().__init__(ServiceConfig(apify_max_charge_usd=.1, report_max_cost_rub=50))
                self.payloads = []

            def _collect_payload(self, payload, requested_count, deadline_at, max_charge_usd, *args, **kwargs):
                self.payloads.append(payload)
                identifier = 9_500_000_000 + len(self.payloads)
                rows = (raw(identifier), raw(9_600_000_000, pro=True)) if len(self.payloads) == 1 else (raw(identifier),)
                return CollectionBatch(
                    tuple(rows), .01, False,
                    requested_count=requested_count, capped_count=len(rows),
                )

        provider = OfflineZen()
        batch = provider.collect_to_target(
            SearchRequest(query="PlayStation 5", location="Ярославль", max_results=8),
            max_charge_usd=.1,
        )
        self.assertEqual(len(batch.items), 8)
        self.assertTrue(batch.fill_metrics["target_filled"])
        self.assertEqual(len(provider.payloads), 8)
        self.assertTrue(all("pro" not in payload["query"].casefold() for payload in provider.payloads))
        self.assertTrue(all(payload["location"] == "Ярославль" for payload in provider.payloads))
        self.assertTrue(all("avito.ru/yaroslavl" in payload["searchUrl"] for payload in provider.payloads))
        self.assertTrue(all("p=1" in payload["searchUrl"] for payload in provider.payloads[:7]))
        self.assertIn("p=2", provider.payloads[7]["searchUrl"])

    def test_each_query_variant_starts_at_page_one(self):
        calls = []

        def fetch(request, page, query, remaining):
            calls.append((query, page))
            return CollectionPage((), exhausted=True)

        result = fill_to_target(
            SearchRequest(query="PS5 Pro", location="Ярославль", max_results=3), fetch,
            CollectionSafetyLimits(max_raw_pages=5),
        )
        self.assertEqual([page for _, page in calls], [1, 1, 1])
        self.assertEqual(len({query for query, _ in calls}), 3)
        self.assertEqual(result.metrics.stop_reason, "SEARCH_EXHAUSTED")

    def test_exhausted_variant_does_not_stop_other_variants(self):
        calls = []
        variants = []
        survivor = None

        def fetch(request, page, query, remaining):
            nonlocal survivor
            if query not in variants:
                variants.append(query)
                if len(variants) == 2:
                    survivor = query
            calls.append((query, page))
            if query == survivor:
                return CollectionPage((raw(9_700_000_000 + page, pro=True),), exhausted=False)
            return CollectionPage((), exhausted=True)

        result = fill_to_target(
            SearchRequest(query="PS5 Pro", location="Ярославль", max_results=2), fetch,
            CollectionSafetyLimits(max_raw_pages=6),
        )
        self.assertEqual(len(result.items), 2)
        self.assertEqual(calls[0][1:2], (1,))
        self.assertIn((variants[1], 2), calls)
        self.assertEqual(result.metrics.stop_reason, "TARGET_FILLED")

    def test_continues_until_200_valid_family_matches(self):
        pages = [
            [*(raw(1_000_000_000 + i) for i in range(70)),
             *(raw(2_000_000_000 + i, pro=True) for i in range(30))],
            [*(raw(3_000_000_000 + i) for i in range(80)),
             *(raw(4_000_000_000 + i, pro=True) for i in range(20))],
            [raw(5_000_000_000 + i) for i in range(50)],
        ]
        calls = []

        def fetch(request, page, query, remaining):
            calls.append((page, query, remaining))
            return CollectionPage(tuple(pages[len(calls) - 1]))

        result = fill_to_target(
            SearchRequest(query="PlayStation 5", location="Ярославль", max_results=200),
            fetch,
            CollectionSafetyLimits(max_raw_pages=5, max_raw_listings=500, page_size=100),
        )
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(result.items), 200)
        self.assertEqual(result.metrics.raw_collected, 250)
        self.assertEqual(result.metrics.request_family_matched, 200)
        self.assertEqual(result.metrics.valid_target_count, 200)
        self.assertTrue(result.metrics.target_filled)
        self.assertEqual(result.metrics.stop_reason, "TARGET_FILLED")

    def test_search_exhaustion_reports_shortfall(self):
        calls = 0

        def fetch(request, page, query, remaining):
            nonlocal calls
            values = ([raw(6_000_000_000 + i) for i in range(100)] if calls == 0 else
                      [raw(7_000_000_000 + i) for i in range(74)] if calls == 1 else [])
            calls += 1
            return CollectionPage(tuple(values), exhausted=not values)

        result = fill_to_target(
            SearchRequest(query="PS5", location="Ярославль", max_results=200), fetch,
            CollectionSafetyLimits(max_raw_pages=10, max_raw_listings=500),
        )
        self.assertEqual(len(result.items), 174)
        self.assertFalse(result.metrics.target_filled)
        self.assertEqual(result.metrics.shortfall, 26)
        self.assertEqual(result.metrics.stop_reason, "SEARCH_EXHAUSTED")

    def test_dedup_city_and_family_do_not_increment_target(self):
        valid = raw(8_000_000_001)
        pages = [[valid, valid, raw(8_000_000_002, pro=True), raw(8_000_000_003, city="Москва")], []]

        calls = 0

        def fetch(request, page, query, remaining):
            nonlocal calls
            values = pages[min(calls, 1)]
            calls += 1
            return CollectionPage(tuple(values), exhausted=calls >= 2)

        result = fill_to_target(
            SearchRequest(query="PS5", location="Ярославль", max_results=3), fetch,
            CollectionSafetyLimits(max_raw_pages=3, max_raw_listings=20),
        )
        self.assertEqual(result.metrics.raw_collected, 4)
        self.assertEqual(result.metrics.deduplicated, 3)
        self.assertEqual(result.metrics.correct_city, 2)
        self.assertEqual(result.metrics.request_family_matched, 1)
        self.assertEqual(result.metrics.valid_target_count, 1)
        self.assertEqual(result.metrics.shortfall, 2)

    def test_budget_and_duplicate_saturation_are_explicit_stops(self):
        duplicate = raw(9_000_000_001)

        def budget_fetch(request, page, query, remaining):
            return CollectionPage((raw(9_100_000_000 + page),), cost_usd=.06)

        budget = fill_to_target(
            SearchRequest(query="PS5", max_results=5), budget_fetch,
            CollectionSafetyLimits(max_raw_pages=5, max_collection_cost_usd=.1),
        )
        self.assertEqual(budget.metrics.stop_reason, "BUDGET_LIMIT")

        def duplicate_fetch(request, page, query, remaining):
            return CollectionPage((duplicate,))

        saturated = fill_to_target(
            SearchRequest(query="PS5 Pro", max_results=5), duplicate_fetch,
            CollectionSafetyLimits(max_raw_pages=7, duplicate_saturation_pages=2),
        )
        self.assertEqual(saturated.metrics.stop_reason, "DUPLICATE_SATURATION")


if __name__ == "__main__":
    unittest.main()
