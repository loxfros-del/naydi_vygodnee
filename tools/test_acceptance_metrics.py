"""Synthetic deterministic tests for acceptance metrics; no live sources."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.acceptance_metrics import evaluate_acceptance_results  # noqa: E402


class AcceptanceMetricsTests(unittest.TestCase):
    def test_synthetic_acceptance_rows(self) -> None:
        results = [
            {
                "case_id": "phone-1",
                "search_time_ms": 100,
                "market_stats": {"median": 100},
                "expected_roles": ["BEST_OVERALL", "CHEAP_WITH_RISK", "RELIABLE"],
                "roles": {1: "BEST", 2: "BUDGET", 3: "BACKUP"},
                "candidates": [
                    {
                        "title": "Phone Pro 256",
                        "source": "Ozon",
                        "seller": "Store",
                        "url": "https://ozon.test/product/1?utm_source=ad",
                        "exact_match": "EXACT",
                        "product_page_predicted": True,
                        "actual_product_page": True,
                        "structured_price": 100,
                        "persisted_price": 100,
                        "price_source": "structured",
                        "status": "VERIFIED_GOOD",
                    },
                    {
                        "title": "Phone Pro 256 duplicate",
                        "source": "Ozon",
                        "seller": "Store",
                        "url": "https://ozon.test/product/1?ref=search",
                        "exact_match": "EXACT",
                        "product_page_predicted": True,
                        "actual_product_page": True,
                        "status": "VERIFIED_GOOD",
                    },
                    {
                        "title": "Phone Pro 128",
                        "source": "Avito",
                        "seller": "Private",
                        "url": "https://avito.test/item/2",
                        "exact_match": "REQUIRED_SPEC_MISMATCH",
                        "product_page_predicted": True,
                        "actual_product_page": False,
                        "status": "WEAK_CANDIDATE",
                    },
                ],
                "source_attempts": [
                    {"source": "Ozon", "status": "SUCCESS", "latency_ms": 30},
                    {"source": "Avito", "status": "BLOCKED", "latency_ms": 60},
                ],
            },
            {
                "case_id": "phone-2",
                "duration_ms": 200,
                "market_median": 200,
                "expected_roles": ["BEST_OVERALL", "RELIABLE"],
                "recommendations": [{"role": "BEST_OVERALL"}],
                "candidates": [{
                    "title": "Phone 2",
                    "source": "DNS",
                    "url": "https://dns.test/product/3",
                    "exact_match_status": "COMPATIBLE_VARIANT",
                    "structured_price": 200,
                    "price": 210,
                    "price_evidence": "json_ld",
                    "status": "VERIFIED_OK",
                }],
                "attempts": [
                    {"source": "Ozon", "status": "EMPTY", "duration_ms": 50},
                    {"source": "DNS", "status": "TIMEOUT", "duration_ms": 100},
                ],
            },
            {
                "case_id": "phone-3",
                "elapsed_ms": 400,
                "expected_roles": ["BEST_OVERALL"],
                "roles": [],
                "candidates": [{
                    "title": "Unknown phone",
                    "source": "Avito",
                    "url": "https://avito.test/item/4",
                    "exact_match": "UNKNOWN",
                    "quality": "weak",
                }],
                "sources": [
                    {"source": "Avito", "status": "SUCCESS", "elapsed_ms": 90},
                    {"source": "DNS", "status": "INVALID_RESPONSE", "elapsed_ms": 120},
                ],
            },
        ]

        metrics = evaluate_acceptance_results(results)
        self.assertEqual(metrics.exact_match_recall, 0.6667)
        self.assertEqual(metrics.required_spec_mismatch_rate, 0.2)
        self.assertEqual(metrics.product_page_precision, 0.6667)
        self.assertEqual(metrics.structured_price_persistence, 0.5)
        self.assertEqual(metrics.source_failure_rate, 0.5)
        self.assertEqual(metrics.duplicate_rate, 0.2)
        self.assertEqual(metrics.market_median_coverage, 0.6667)
        self.assertEqual(metrics.role_completeness, 0.6667)
        self.assertEqual(metrics.weak_candidate_rate, 0.4)
        self.assertEqual(metrics.average_search_time_ms, 233.33)
        self.assertEqual(metrics.p95_search_time_ms, 400.0)
        self.assertEqual(metrics.per_source_latency_ms, {
            "Avito": 75.0,
            "DNS": 110.0,
            "Ozon": 40.0,
        })
        self.assertEqual(metrics.as_dict()["duplicate_rate"], 0.2)

    def test_empty_input_is_stable(self) -> None:
        metrics = evaluate_acceptance_results([])
        self.assertEqual(metrics.exact_match_recall, 0.0)
        self.assertEqual(metrics.average_search_time_ms, 0.0)
        self.assertEqual(metrics.p95_search_time_ms, 0.0)
        self.assertEqual(metrics.per_source_latency_ms, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
