from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.search_v2.adapters.yandex_web import YandexWebDiscoveryAdapter
from app.search_v2.query_planner import QueryPlannerV2
from tools.search_v2_yandex_live_check import LIVE_CASES


class LiveCheckFixtureTests(unittest.TestCase):
    def test_every_pilot_category_produces_a_valid_yandex_query(self) -> None:
        planner = QueryPlannerV2(max_queries_per_source=1)
        for category, request in LIVE_CASES.items():
            with self.subTest(category=category):
                plan = planner.plan(
                    request,
                    sources=("yandex_web",),
                    source_capabilities={"yandex_web": YandexWebDiscoveryAdapter.capabilities},
                )
                self.assertEqual(len(plan.source_queries), 1)
                self.assertFalse(plan.rejected_queries)


if __name__ == "__main__":
    unittest.main()
