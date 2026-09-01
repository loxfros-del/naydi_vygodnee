from __future__ import annotations

from collections import Counter
import json
from types import SimpleNamespace
import unittest

from app.search_v2.adapters import AvitoAdapter, DnsAdapter, YandexWebDiscoveryAdapter
from app.search_v2.models import ProductCondition, QueryTier, SearchRequestV2, SourceStatus
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
        self.assertIn("160x200", mattress.hard_tokens)

    def test_string_request_is_supported_and_has_no_transport_dependency(self) -> None:
        request = normalize_legacy_request("новый iPhone 16 Pro 256 ГБ до 80к в Ярославле")
        self.assertEqual(request.category, "phone")
        self.assertEqual(request.canonical_model, "iPhone 16")
        self.assertIn("Pro", request.hard_tokens)
        self.assertIn("256 ГБ", request.hard_tokens)
        self.assertEqual(request.budget, 80_000)
        self.assertEqual(request.city, "Ярославль")
        self.assertEqual(request.condition, ProductCondition.NEW)

    def test_macbook_compact_memory_becomes_a_required_configuration(self) -> None:
        request = normalize_legacy_request({
            "category": "manual",
            "product_name": "Ноутбук MacBook Air M4 16/256",
            "original_query": "Ноутбук MacBook Air M4 16/256",
            "model": "Ноутбук MacBook Air M4 16/256",
        })
        self.assertEqual(request.category, "laptop")
        self.assertEqual(request.brand, "Apple")
        self.assertEqual(request.canonical_model, "MacBook M4")
        self.assertEqual(request.model_modifiers, ["Air"])
        self.assertEqual(request.required_specs["ram_gb"], 16)
        self.assertEqual(request.required_specs["storage_gb"], 256)
        self.assertIn("RAM 16 ГБ", request.hard_tokens)
        self.assertIn("256 ГБ", request.hard_tokens)

    def test_full_macbook_phrase_keeps_chip_as_the_canonical_model(self) -> None:
        request = normalize_legacy_request({
            "category": "manual",
            "product_name": "Ноутбук Apple MacBook Air 13 M4",
            "original_query": "Ноутбук Apple MacBook Air 13 M4",
            "city": "Ярославль",
        })

        self.assertEqual(request.category, "laptop")
        self.assertEqual(request.brand, "Apple")
        self.assertEqual(request.canonical_model, "MacBook 13 M4")
        self.assertEqual(request.model_modifiers, ["Air"])
        self.assertEqual(request.hard_tokens, ["Apple", "MacBook 13 M4", "Air"])
        self.assertNotIn("Ноутбук", request.canonical_model)
        self.assertNotIn("Apple", request.canonical_model)

        missing_chip = make_source_query(
            request,
            "yandex_market",
            QueryTier.STRICT,
            priority=100,
            query="Apple MacBook 13 Air",
        )
        self.assertFalse(missing_chip.is_valid)
        self.assertIn("MacBook 13 M4", missing_chip.rejection_reason)

    def test_explicit_laptop_configuration_with_units_becomes_hard_specs(self) -> None:
        request = normalize_legacy_request(
            "Ноутбук Apple MacBook Air M4 16GB/512GB"
        )

        self.assertEqual(request.category, "laptop")
        self.assertEqual(request.canonical_model, "MacBook M4")
        self.assertEqual(request.model_modifiers, ["Air"])
        self.assertEqual(request.required_specs["ram_gb"], 16)
        self.assertEqual(request.required_specs["storage_gb"], 512)
        self.assertNotIn("diagonal", request.required_specs)
        self.assertIn("RAM 16 ГБ", request.hard_tokens)
        self.assertIn("512 ГБ", request.hard_tokens)

    def test_explicit_category_features_use_strict_offer_fact_names(self) -> None:
        cases = (
            (
                "Кофемашина DeLonghi автоматическая с капучинатором",
                "coffee_machine",
                {"machine_type": "automatic", "cappuccinator": True},
                {"автоматическая", "капучинатор"},
            ),
            (
                "Монитор 27 дюймов IPS 144 Гц",
                "monitor",
                {"diagonal": "27", "panel": "IPS", "refresh_rate": 144},
                {"27 дюймов", "IPS", "144 Гц"},
            ),
        )
        for query, category, expected_specs, expected_tokens in cases:
            with self.subTest(query=query):
                request = normalize_legacy_request(query)
                self.assertEqual(request.category, category)
                for key, value in expected_specs.items():
                    self.assertEqual(request.required_specs.get(key), value)
                self.assertTrue(expected_tokens.issubset(set(request.hard_tokens)))

    def test_vague_category_request_does_not_invent_a_configuration(self) -> None:
        request = normalize_legacy_request("Кофемашина DeLonghi Magnifica")

        self.assertNotIn("machine_type", request.required_specs)
        self.assertNotIn("cappuccinator", request.required_specs)

    def test_named_unknown_technical_device_is_safe_generic_tech(self) -> None:
        request = normalize_legacy_request({
            "category": "manual",
            "product_name": "Фотоаппарат Canon EOS R50 до 60к в Москве",
            "original_query": "Фотоаппарат Canon EOS R50 до 60к в Москве",
            "model": "Фотоаппарат Canon EOS R50",
        })
        self.assertEqual(request.category, "generic_tech")
        self.assertTrue(request.supported_category)
        self.assertEqual(request.brand, "Canon")
        self.assertEqual(request.canonical_model, "Canon EOS R50")
        self.assertIn("Canon EOS R50", request.hard_tokens)

    def test_vague_or_accessory_unknown_text_remains_unsupported(self) -> None:
        for product in (
            "хорошая дрель",
            "Кабель USB-C Canon R50",
            "ремонт Canon EOS R50",
            "Canon 2026 купить",
        ):
            request = normalize_legacy_request({"category": "manual", "product_name": product, "model": product})
            self.assertFalse(request.supported_category, product)

        explicit = normalize_legacy_request({
            "category": "generic_tech",
            "product_name": "хорошая камера",
            "model": "хорошая камера",
        })
        self.assertFalse(explicit.supported_category)


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
        self.assertIn("новый", strict.query)
        self.assertNotIn(" new ", f" {strict.query.casefold()} ")
        self.assertIn("до 80000", strict.query)
        self.assertNotIn("Ярославль", strict.query)
        self.assertIn("Ярославль", strict.dropped_soft_tokens)
        self.assertEqual(strict.hard_tokens_preserved, request.hard_tokens)
        self.assertEqual(strict.status, SourceStatus.SUCCESS)
        self.assertNotIn("Ярославль", source_exact.query)
        self.assertNotIn("до 80000", source_exact.query)
        self.assertIn("Ярославль", source_exact.dropped_soft_tokens)
        self.assertIn("до 80000", source_exact.dropped_soft_tokens)
        self.assertEqual(source_exact.hard_tokens_preserved, request.hard_tokens)

    def test_city_is_kept_only_for_avito_not_national_sources(self) -> None:
        request = iphone_request()
        sources = ["yandex_market", "ozon", "wildberries", "yandex_web", "generic_search", "dns", "avito"]
        plan = build_query_plan(request, sources=sources, max_queries_per_source=1)
        strict = {item.source: item for item in plan.source_queries}

        self.assertIn("Ярославль", strict["avito"].query)
        for source in ("yandex_market", "ozon", "wildberries", "yandex_web", "generic_search", "dns"):
            self.assertNotIn("Ярославль", strict[source].query, source)
            self.assertIn("Ярославль", strict[source].dropped_soft_tokens, source)

    def test_capabilities_separate_structured_region_from_text_and_unknown_scope(self) -> None:
        request = iphone_request()
        capabilities = {
            "yandex_web": YandexWebDiscoveryAdapter().capabilities,
            "avito": AvitoAdapter().capabilities,
            "dns": DnsAdapter().capabilities,
        }
        plan = build_query_plan(
            request,
            sources=capabilities,
            max_queries_per_source=1,
            source_capabilities=capabilities,
        )
        strict = {item.source: item for item in plan.source_queries}

        self.assertNotIn("Ярославль", strict["yandex_web"].query)
        self.assertEqual(strict["yandex_web"].structured_filters["city"], "Ярославль")
        self.assertIn("Ярославль", strict["avito"].query)
        self.assertEqual(strict["avito"].structured_filters, {})
        self.assertNotIn("Ярославль", strict["dns"].query)
        self.assertIn("Ярославль", strict["dns"].dropped_soft_tokens)

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

    def test_category_variant_is_additive_and_keeps_every_hard_token(self) -> None:
        request = normalize_legacy_request({
            "product_name": "Телевизор Samsung QN90D 55 дюймов 4K 120 Гц",
            "condition": "new",
            "budget": "150000",
        })
        plan = build_query_plan(request, sources=["ozon"])

        self.assertEqual([item.tier for item in plan.source_queries], [QueryTier.STRICT, QueryTier.SOURCE_EXACT])
        strict, variant = plan.source_queries
        self.assertEqual(variant.priority, 90)
        self.assertIn("телевизор", variant.query.casefold())
        self.assertEqual(variant.hard_tokens_preserved, request.hard_tokens)
        self.assertTrue(variant.is_valid)
        self.assertIn("до 150000", strict.query)
        self.assertNotIn("до 150000", variant.query)
        self.assertLessEqual(len(plan.source_queries), 2)

    def test_category_variant_never_replaces_configuration_or_condition(self) -> None:
        request = normalize_legacy_request({
            "product_name": "Ноутбук Apple MacBook Air 13 M4 16/512",
            "condition": "new",
        })
        plan = build_query_plan(request, sources=["yandex_market"])
        variant = next(item for item in plan.source_queries if item.tier is QueryTier.SOURCE_EXACT)

        self.assertIn("ноутбук", variant.query.casefold())
        self.assertEqual(variant.hard_tokens_preserved, request.hard_tokens)
        self.assertIn("MacBook 13 M4", variant.query)
        self.assertIn("RAM 16 ГБ", variant.query)
        self.assertIn("512 ГБ", variant.query)
        self.assertIn("новый", variant.query)
        self.assertNotIn("used", variant.query.casefold())

    def test_specialised_category_template_requires_confirmed_parameter(self) -> None:
        # A plain coffee-machine request must not silently become an
        # automatic/espresso/capsule request because such words happen to be
        # present in a registry template.
        request = normalize_legacy_request({
            "product_name": "Кофемашина DeLonghi Magnifica S ECAM21.117.SB",
            "condition": "new",
        })
        plan = build_query_plan(request, sources=["ozon"])
        strict, source_exact = plan.source_queries

        self.assertEqual(source_exact.query, "DeLonghi coffee machine новый купить")
        self.assertNotIn("автоматическ", source_exact.query.casefold())
        self.assertNotIn("рожков", source_exact.query.casefold())
        self.assertNotIn("капсуль", source_exact.query.casefold())
        self.assertEqual(strict.hard_tokens_preserved, request.hard_tokens)

    def test_unsupported_and_non_discovery_sources_keep_existing_second_query(self) -> None:
        request = normalize_legacy_request({
            "category": "manual",
            "product_name": "Фотоаппарат Canon EOS R50",
            "model": "Фотоаппарат Canon EOS R50",
        })
        discovery = build_query_plan(request, sources=["ozon"])
        anchor = build_query_plan(request, sources=["dns"])
        web = build_query_plan(request, sources=["yandex_web"])

        self.assertEqual(discovery.source_queries[1].query, "Canon Canon EOS R50 купить")
        # No city/budget is present, so the historical strict and relaxed
        # anchor texts are identical and dedupe leaves one query.
        self.assertEqual([item.tier for item in anchor.source_queries], [QueryTier.STRICT])
        self.assertEqual(web.source_queries[1].query, "Canon Canon EOS R50 купить")

        unknown = SearchRequestV2(
            category="unknown",
            canonical_model="Camera X1",
            supported_category=False,
            hard_tokens=["Camera X1"],
        )
        unknown_plan = build_query_plan(unknown, sources=["ozon"])
        self.assertEqual(
            [item.query for item in unknown_plan.source_queries],
            ["Camera X1", "Camera X1 купить"],
        )


if __name__ == "__main__":
    unittest.main()
