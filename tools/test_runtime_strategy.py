"""Offline regressions for bounded concurrency and partial source failover."""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_IDS", "1")

from app import candidate_verifier, product_search  # noqa: E402
from app.db import Request  # noqa: E402


class RuntimeStrategyTests(unittest.TestCase):
    def test_market_median_uses_offer_group_not_listing_identity(self) -> None:
        def offer(idx: int, price: int, storage: int):
            return product_search.ProductCandidate(
                title=f"Apple iPhone 16 Pro {storage} ГБ",
                url=f"https://seller{idx}.example/product/{idx}",
                source=f"seller_{idx}",
                seller=f"Seller {idx}",
                price=price,
                exact_match_status="EXACT",
                price_confidence="high",
                availability="AVAILABLE",
                product_facts={
                    "category": "phone", "brand": "Apple", "model": "iPhone 16 Pro",
                    "model_modifiers": ["Pro"], "storage_gb": storage,
                    "condition": "new", "exact_match": "EXACT", "available": True,
                    "price_confidence": "high",
                },
            )

        same_group = [offer(1, 50_000, 256), offer(2, 100_000, 256), offer(3, 110_000, 256)]
        other_configuration = offer(4, 70_000, 128)
        product_search._apply_market_analysis([*same_group, other_configuration])

        self.assertEqual(same_group[0].product_facts["market_price"]["median"], 100_000)
        self.assertEqual(same_group[0].product_facts["market_price"]["verified_count"], 3)
        self.assertEqual(same_group[0].product_facts["market_price"]["offer_class"], "VERY_CHEAP")
        self.assertEqual(other_configuration.product_facts["market_price"]["median"], 70_000)
        self.assertEqual(other_configuration.product_facts["market_price"]["verified_count"], 1)

    def test_verification_concurrency_is_two_and_order_is_stable(self) -> None:
        lock = threading.Lock()
        active = 0
        maximum = 0

        def fake_verify(candidate, _request):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.015)
            with lock:
                active -= 1
            return candidate

        values = list(range(7))
        request = Request(id=1, user_id=1, product_name="monitor")
        with patch.object(candidate_verifier, "verify_candidate", side_effect=fake_verify):
            result = candidate_verifier.verify_candidates(values, request, limit=7)

        self.assertEqual(result, values)
        self.assertEqual(maximum, 2)

    def test_source_failure_keeps_successful_partial_results(self) -> None:
        request = Request(
            id=1,
            user_id=1,
            product_name="Samsung Galaxy A55 256 ГБ",
            budget="35000",
            city="Москва",
        )

        def successful_source(_request, query, collection, _seen):
            candidate = product_search.ProductCandidate(
                title="Samsung Galaxy A55 256 ГБ",
                url="https://shop.example/product/a55",
                source=product_search.SEARCHAPI_SOURCE,
                price=34_990,
            )
            collection.raw_candidates.append(candidate)
            collection.candidates.append(candidate)
            collection.attempts.append(product_search.SearchAttemptData(
                product_search.SEARCHAPI_SOURCE, query, "SUCCESS", 1, 1,
            ))

        def failed_source(*_args, **_kwargs):
            raise RuntimeError("synthetic source failure")

        def keep_raw(collection, _request, **_kwargs):
            collection.candidates = list(collection.raw_candidates)
            collection.quality_stats.update({
                "raw": len(collection.raw_candidates),
                "total_found": len(collection.raw_candidates),
                "good": len(collection.raw_candidates),
                "normal": len(collection.raw_candidates),
                "saved": len(collection.raw_candidates),
            })

        with (
            patch.object(product_search, "_is_generic_enabled", return_value=False),
            patch.object(product_search, "_enabled_site_sources", return_value=[]),
            patch.object(product_search, "_is_source_enabled", return_value=False),
            patch.object(product_search.settings, "DIRECT_RETAIL_ENABLED", False),
            patch.object(product_search.settings, "SEARCHAPI_ENABLED", True),
            patch.object(product_search.settings, "SERPAPI_ENABLED", True),
            patch.object(product_search.settings, "YANDEX_SEARCH_API_ENABLED", False),
            patch.object(product_search, "_collect_from_searchapi", side_effect=successful_source),
            patch.object(product_search, "_collect_from_serpapi", side_effect=failed_source),
            patch.object(product_search, "_apply_verification", side_effect=keep_raw),
        ):
            collection = product_search.collect_product_candidates(
                request,
                verification_mode="none",
            )

        self.assertEqual([item.title for item in collection.candidates], ["Samsung Galaxy A55 256 ГБ"])
        failures = [
            attempt for attempt in collection.attempts
            if attempt.source == product_search.SERPAPI_SOURCE
        ]
        self.assertTrue(failures)
        self.assertEqual(failures[0].status, "ERROR")
        self.assertIn("synthetic source failure", failures[0].error_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
