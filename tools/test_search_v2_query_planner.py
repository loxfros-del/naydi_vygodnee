from __future__ import annotations

from collections import Counter
import json
from types import SimpleNamespace
import unittest

from app.search_v2.models import ProductCondition, QueryTier, SourceStatus
from app.search_v2.query_planner import (
    QueryPlannerV2,
    build_query_plan,
    make_source_query,
    normalize_source_name,
)
from app.search_v2.request_normalizer import normalize_legacy_request


def iphone_request():
    return normalize_legacy_request({
        "product_name": "iPhone 16 Pro",
        "original_query": "iPhone 16 Pro 256 ГБ новый до 80 000 ₽ в Ярославле",
        "budget": "80000",
        "city": "Ярославль",
        "condition": "new",
        "important_criteria": "Какой объём памяти нужен: 256",
    })


class SearchV2RequestNormalizerTests(unittest.TestCase):
    def test_vertical_iphone_request_is_strictly_normalized(self) -> None:
        request = iphone_request()
        self.assertEqual(request.original_query, "iPhone 16 Pro 256 ГБ новый до 80 000 ₽ в Ярославле")
        self.assertEqual(request.category, "phone")
        self.assertEqual(request.brand, "Apple")
        self.assertEqual(request.canonical_model, "iPhone 16")
        self.assertEqual(request.model_modifiers, ["Pro"])
        self.assertEqual(request.required_specs, {"storage_gb": 256})
        self.assertEqual(request.condition, ProductCondition.NEW)
        self.assertEqual(request.budget, 80_000)
        self.assertEqual(request.city, "Ярославль")
        self.assertTrue(request.supported_category)
        self.assertEqual(request.hard_tokens, ["Apple", "iPhone 16", "Pro", "256 ГБ", "new"])
        self.assertIn("до 80000", request.soft_tokens)
        self.assertIn("Ярославль", request.soft_tokens)
        self.assertNotIn("Какой объём памяти нужен", " ".join(request.hard_tokens))

    def test_requirements_json_overrides_legacy_object_without_wizard_questions(self) -> None:
        legacy = SimpleNamespace(
            product_name="iPhone 16 Pro",
            original_query="iPhone 16 Pro",
            budget="70000",
            city="Москва",
            condition="any",
            category="smartphones",
            important_criteria="Какой объём памяти нужен: 128",
            criteria="",
            priority="price",
            requirements_json=json.dumps({
                "model": "iPhone 16 Pro",
                "storage_gb": 256,
                "condition": "new",
                "city": "Ярославль",
                "budget": "80к",
                "optional_features": ["цвет синий"],
            }, ensure_ascii=False),
        )
        request = normalize_legacy_request(legacy)
        self.assertEqual(request.canonical_model, "iPhone 16")
        self.assertEqual(request.model_modifiers, ["Pro"])
        self.assertEqual(request.required_specs["storage_gb"], 256)
        self.assertEqual(request.budget, 80_000)
        self.assertEqual(request.city, "Ярославль")
        self.assertEqual(request.condition, ProductCondition.NEW)
        self.assertEqual(request.priority, "price")
        self.assertEqual(request.optional_specs["features"], ["цвет синий"])
        self.assertNotIn("128 ГБ", request.hard_tokens)
        self.assertIn("256 ГБ", request.hard_tokens)

    def test_old_requirement_questions_become_structured_specs(self) -> None:
        tv = normalize_legacy_request({
            "product_name": "телевизор",
            "requirements": "Какая диагональ нужна: 55; Какая герцовка нужна: 120 Гц",
            "condition": "new",
        })
        self.assertEqual(tv.category, "tv")
        self.assertEqual(tv.required_specs["diagonal"], 55)
        self.assertEqual(tv.required_specs["refresh_rate"], 120)
        self.assertIn("55 дюймов", tv.hard_tokens)
        self.assertIn("120 Гц", tv.hard_tokens)
        mattress = normalize_legacy_request({
            "product_name": "матрас",
            "important_criteria": "Какой размер нужен: 160х200 см",
        })
        self.assertEqual(mattress.category, "mattress")
        self.assertEqual(mattress.required_specs["size"], "160x200")
        self.assertIn("160x200", " ".join(mattress.hard_tokens))

    def test_string_request_is_supported_and_has_no_transport_dependency(self) -> None:
        request = normalize_legacy_request("новый iPhone 16 Pro 256 ГБ до 80к в Ярославле")
        self.assertEqual(request.category, "phone")
        self.assertEqual(request.canonical_model, "iPhone 16")
        self.assertIn("Pro", request.hard_tokens)
        self.assertIn("256 ГБ", request.hard_tokens)
        self.assertEqual(request.budget, 80_000)
        self.assertEqual(request.city, "Ярославль")
        self.assertEqual(request.condition, ProductCondition.NEW)


class SearchV2QueryPlannerTests(unittest.TestCase):
    def test_strict_and_source_exact_keep_every_hard_token(self) -> None:
        request = iphone_request()
        plan = build_query_plan(request, sources=["ozon"])
        self.assertEqual(len(plan.source_queries), 2)
        self.assertEqual(plan.rejected_queries, [])
        strict, source_exact = plan.source_queries
        self.assertEqual(strict.tier, QueryTier.STRICT)
        self.assertEqual(source_exact.tier, QueryTier.SOURCE_EXACT)
        self.assertEqual(strict.source, "ozon")
        self.assertIn("Apple iPhone 16 Pro", strict.query)
        self.assertIn("256 ГБ", strict.query)
        self.assertIn("new", strict.query)
        self.assertIn("до 80000", strict.query)
        self.assertIn("Ярославль", strict.query)
        self.assertEqual(strict.hard_tokens_preserved, request.hard_tokens)
        self.assertEqual(strict.status, SourceStatus.SUCCESS)
        self.assertNotIn("Ярославль", source_exact.query)
        self.assertNotIn("до 80000", source_exact.query)
        self.assertIn("Ярославль", source_exact.dropped_soft_tokens)
        self.assertIn("до 80000", source_exact.dropped_soft_tokens)
        self.assertEqual(source_exact.hard_tokens_preserved, request.hard_tokens)

    def test_broad_query_is_rejected_before_network_call(self) -> None:
        request = iphone_request()
        candidate = make_source_query(
            request,
            "ozon",
            QueryTier.STRICT,
            priority=100,
            query="iPhone 16 смартфон до 80000",
        )
        self.assertFalse(candidate.is_valid)
        self.assertEqual(candidate.status, SourceStatus.INVALID_QUERY_PLAN)
        self.assertIn("Pro", candidate.rejection_reason)
        self.assertIn("256 ГБ", candidate.rejection_reason)
        self.assertNotIn("Pro", candidate.hard_tokens_preserved)
        self.assertIn("iPhone 16", candidate.hard_tokens_preserved)
        network_calls = 0
        if candidate.is_valid:  # adapter/orchestrator contract
            network_calls += 1
        self.assertEqual(network_calls, 0)

    def test_equivalent_units_and_russian_condition_pass_validation(self) -> None:
        request = iphone_request()
        candidate = make_source_query(
            request,
            "Ozon",
            QueryTier.SOURCE_EXACT,
            priority=90,
            query="Apple iPhone 16 Pro 256 GB новый купить",
        )
        self.assertTrue(candidate.is_valid)
        self.assertEqual(candidate.status, SourceStatus.SUCCESS)
        self.assertEqual(candidate.source, "ozon")
        self.assertIn("256 ГБ", candidate.hard_tokens_preserved)
        self.assertIn("new", candidate.hard_tokens_preserved)

    def test_pro_max_does_not_satisfy_required_pro_variant(self) -> None:
        request = iphone_request()
        candidate = make_source_query(
            request,
            "avito",
            QueryTier.SOURCE_EXACT,
            priority=90,
            query="Apple iPhone 16 Pro Max 256 ГБ new",
        )
        self.assertFalse(candidate.is_valid)
        self.assertEqual(candidate.status, SourceStatus.INVALID_QUERY_PLAN)
        self.assertIn("Pro Max replaces required Pro", candidate.rejection_reason)
        self.assertEqual(candidate.source, "avito")

    def test_query_budget_is_hard_capped_at_two_per_source(self) -> None:
        request = iphone_request()
        sources = [
            "yandex_market", "ozon", "avito", "wildberries",
            "dns", "citilink", "mvideo", "official_store", "generic_search",
        ]
        plan = build_query_plan(request, sources=sources, max_queries_per_source=99)
        counts = Counter(item.source for item in plan.source_queries)
        self.assertEqual(set(counts), set(sources))
        self.assertLessEqual(max(counts.values()), 2)
        self.assertEqual(counts["ozon"], 2)
        self.assertEqual(counts["avito"], 2)
        self.assertEqual(counts["dns"], 2)
        self.assertEqual(counts["generic_search"], 2)
        one_each = QueryPlannerV2(max_queries_per_source=1).plan(request, sources=sources)
        one_counts = Counter(item.source for item in one_each.source_queries)
        self.assertTrue(all(count == 1 for count in one_counts.values()))
        self.assertEqual(len(one_counts), len(sources))

    def test_anchor_uses_relaxed_tier_and_over_budget_is_explicit(self) -> None:
        request = iphone_request()
        anchor = build_query_plan(request, sources=["dns"])
        self.assertEqual([item.tier for item in anchor.source_queries], [QueryTier.STRICT, QueryTier.EXACT_RELAXED])
        self.assertNotIn("Ярославль", anchor.source_queries[1].query)
        self.assertIn("Pro", anchor.source_queries[1].query)
        over_budget = build_query_plan(request, sources=["ozon"], include_over_budget=True)
        self.assertEqual(over_budget.source_queries[1].tier, QueryTier.OVER_BUDGET_EXACT)
        self.assertNotIn("до 80000", over_budget.source_queries[1].query)
        self.assertNotIn("Ярославль", over_budget.source_queries[1].query)
        self.assertEqual(over_budget.source_queries[1].hard_tokens_preserved, request.hard_tokens)

    def test_source_names_are_canonical_and_duplicates_removed(self) -> None:
        request = iphone_request()
        plan = build_query_plan(request, sources=["Ozon", "озон", "Яндекс Маркет", "market"])
        sources = {item.source for item in plan.source_queries}
        self.assertEqual(sources, {"ozon", "yandex_market"})
        self.assertEqual(normalize_source_name("М.Видео"), "mvideo")
        self.assertEqual(normalize_source_name("ДНС"), "dns")
        self.assertEqual(normalize_source_name("generic"), "generic_search")
        self.assertEqual(len(plan.source_queries), 4)


if __name__ == "__main__":
    unittest.main()
