"""Deterministic parser и query-planner cases без сети."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Request
from app.query_planner import plan_search_queries
from app.request_parser import full_parse, parse_budget, parse_request_details


CASES = (
    ("a55", "Нужен Samsung Galaxy A55 256 ГБ до 35к в Москве", {"category": "phone", "budget": "35000", "city": "Москва", "storage_gb": 256, "model": "Samsung Galaxy A55"}),
    ("monitor", "Нужен монитор 27 дюймов QHD 144 Гц до 35к", {"category": "monitor", "budget": "35000", "diagonal": "27", "resolution": "QHD", "refresh_rate": 144}),
    ("robot", "Нужен робот-пылесос с лидаром и влажной уборкой до 30к", {"category": "robot_vacuum", "budget": "30000", "lidar": True, "wet_cleaning": True}),
    ("coffee", "Нужна автоматическая кофемашина с капучинатором до 45к", {"category": "coffee_machine", "budget": "45000", "automatic": True, "cappuccinator": True}),
    ("microwave", "Нужна микроволновка 20–25 литров до 15к", {"category": "microwave", "budget": "15000", "volume_l": [20, 25]}),
    ("mattress", "Нужен матрас 160x200 средней жёсткости до 20к", {"category": "mattress", "budget": "20000", "size": "160x200", "medium_firmness": True}),
    ("bed", "Нужна кровать 160х200 с подъёмным механизмом до 30к", {"category": "bed", "budget": "30000", "size": "160x200", "lift_mechanism": True}),
    ("headphones", "Нужны полноразмерные беспроводные наушники с ANC до 15к", {"category": "headphones", "budget": "15000", "wireless": True, "anc": True}),
    ("laptop", "Нужен ноутбук Ryzen 5, 16 ГБ ОЗУ, SSD 512 ГБ до 60к", {"category": "laptop", "budget": "60000", "ram_gb": 16, "ssd_gb": 512}),
    ("tv", "Нужен телевизор 55 дюймов 4К 120 Гц для PS5 до 70к", {"category": "tv", "budget": "70000", "diagonal": "55", "resolution": "4K", "refresh_rate": 120}),
    ("vacuum", "Нужен вертикальный пылесос для шерсти животных до 25к", {"category": "vacuum", "budget": "25000", "pet_hair": True}),
    ("iphone_used", "Нужен iPhone 15 Pro 256 ГБ б/у до 90к", {"category": "phone", "budget": "90000", "storage_gb": 256, "condition": "used", "model": "iPhone 15 Pro"}),
    ("size_not_money", "Матрас 160x200", {"category": "mattress", "budget": "", "size": "160x200"}),
    ("inch_not_money", "Монитор 27 дюймов", {"category": "monitor", "budget": "", "diagonal": "27"}),
    ("battery_not_money", "Смартфон с аккумулятором 10000 mAh", {"category": "phone", "budget": "", "battery_mah": 10000}),
    ("load_not_money", "Офисное кресло нагрузка 120 кг до 15к", {"category": "chair", "budget": "15000", "load_kg": 120}),
    ("pro_max", "Ищу iPhone 17 Pro Max", {"category": "phone", "budget": "", "model": "iPhone 17 Pro Max"}),
    ("iphone_e", "Ищу iPhone 17e до 80к", {"category": "phone", "budget": "80000", "model": "iPhone 17e"}),
    ("s24_ultra", "Samsung Galaxy S24 Ultra 512 ГБ", {"category": "phone", "budget": "", "storage_gb": 512, "model": "Samsung Galaxy S24 Ultra"}),
    ("sony", "Sony WH-1000XM5 до 30к", {"category": "headphones", "budget": "30000", "model": "Sony WH-1000XM5"}),
    ("rubles", "Новый смартфон до 50 000 рублей в Москве", {"category": "phone", "budget": "50000", "city": "Москва", "condition": "new"}),
    ("kazan", "Ноутбук до 55к в Казани", {"category": "laptop", "budget": "55000", "city": "Казань"}),
    ("hz_not_money", "Монитор 144 Гц", {"category": "monitor", "budget": "", "refresh_rate": 144}),
    ("storage_not_money", "iPhone 15 256 ГБ", {"category": "phone", "budget": "", "storage_gb": 256}),
    ("liters_not_money", "Микроволновка 25 литров", {"category": "microwave", "budget": "", "volume_l": 25}),
    ("bed_no_budget", "Кровать 180×200", {"category": "bed", "budget": "", "size": "180x200"}),
    ("unknown", "Нужна аккумуляторная дрель до 10к в Перми", {"category": "unknown", "budget": "10000", "city": "Пермь"}),
    ("capsule", "Капсульная кофемашина до 12к", {"category": "coffee_machine", "budget": "12000"}),
    ("chair", "Эргономичное офисное кресло с подголовником до 25к", {"category": "chair", "budget": "25000"}),
    ("fhd_tv", "Телевизор 43 дюйма Full HD до 25к", {"category": "tv", "budget": "25000", "diagonal": "43", "resolution": "Full HD"}),
    ("n100", "Ноутбук Intel N100 16 ГБ ОЗУ до 35к", {"category": "laptop", "budget": "35000", "ram_gb": 16, "cpu": "N100"}),
    ("currency", "Наушники Sony за 5 000 ₽", {"category": "headphones", "budget": "5000", "brand": "Sony"}),
    ("spaced_budget", "Матрас 140х200 до 20 000", {"category": "mattress", "budget": "20000", "size": "140x200"}),
    ("thousands", "Телефон бюджет 35 тыс. Москва", {"category": "phone", "budget": "35000"}),
    ("year", "Телевизор модель 2026 года", {"category": "tv", "budget": ""}),
    ("bare_number", "Ноутбук модель 89999", {"category": "laptop", "budget": ""}),
)


class RequestParserCases(unittest.TestCase):
    def test_01_all_parser_cases(self) -> None:
        exact = 0
        for case_id, query, expected in CASES:
            parsed = full_parse(query)
            details = parse_request_details(query)
            actual = {**parsed, **details}
            passed = all(actual.get(key) == value for key, value in expected.items())
            with self.subTest(case_id=case_id, actual=actual):
                self.assertTrue(passed, f"expected={expected}; actual={actual}")
            exact += int(passed)
        self.assertGreaterEqual(exact / len(CASES), 0.90)

    def test_02_budget_does_not_use_specs(self) -> None:
        for text in ("160x200", "27 дюймов", "144 Гц", "256 ГБ", "120 кг", "10000 mAh", "модель 2026"):
            with self.subTest(text=text):
                self.assertEqual(parse_budget(text), "")

    def test_03_model_modifiers(self) -> None:
        expected = {
            "iPhone 15 Pro": "pro",
            "iPhone 15 Pro Max": "pro max",
            "Galaxy S24 FE": "fe",
            "MacBook Air 13": "air",
        }
        for query, modifier in expected.items():
            with self.subTest(query=query):
                self.assertIn(modifier, parse_request_details(query)["model_modifiers"])

    def test_04_query_plan_is_bounded_and_explained(self) -> None:
        parsed = full_parse("Нужен монитор 27 дюймов QHD 144 Гц до 35к")
        request = Request(
            id=0, user_id=0, product=parsed["product_name"], product_name=parsed["product_name"],
            original_query=parsed["original_query"], budget=parsed["budget"], city=parsed["city"],
            use_case=parsed["use_case"], important_criteria=parsed["important_criteria"],
            clean_search_query=parsed["clean_search_query"],
        )
        plan = plan_search_queries(request, max_queries=10, site_domains=("dns-shop.ru", "mvideo.ru"))
        self.assertLessEqual(len(plan), 10)
        self.assertEqual(sum(item.kind == "main" for item in plan), 1)
        self.assertLessEqual(sum(item.kind == "category" for item in plan), 3)
        self.assertLessEqual(sum(item.kind == "feature" for item in plan), 3)
        self.assertTrue(all(item.reason for item in plan))
        self.assertEqual(len({item.text.casefold() for item in plan}), len(plan))

    def test_05_requested_brand_prevents_brand_expansion(self) -> None:
        parsed = full_parse("Samsung Galaxy A55 256 ГБ до 35к")
        request = Request(
            id=0, user_id=0, product=parsed["product_name"], product_name=parsed["product_name"],
            original_query=parsed["original_query"], budget=parsed["budget"],
            important_criteria=parsed["important_criteria"], clean_search_query=parsed["clean_search_query"],
        )
        combined = " | ".join(item.text.lower() for item in plan_search_queries(request))
        self.assertNotIn("xiaomi dreame roborock", combined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
