"""30+ deterministic price-evidence cases без сети."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.price_extractor import (
    PRICE_CONFIDENCE_HIGH,
    PRICE_CONFIDENCE_LOW,
    PRICE_CONFIDENCE_MEDIUM,
    PRICE_EVIDENCE_CURRENCY,
    PRICE_EVIDENCE_DIRECT,
    PRICE_EVIDENCE_INFERRED,
    PRICE_EVIDENCE_LABELED,
    PRICE_EVIDENCE_REJECTED_SPEC,
    PRICE_EVIDENCE_STRUCTURED,
    extract_price,
    extract_price_evidence,
)


CASES = (
    ("rub_symbol", "Ноутбук 49 489 ₽", 49_489),
    ("rub_word", "Ноутбук 49489 руб.", 49_489),
    ("rubles", "Стоимость 45 000 рублей", 45_000),
    ("label", "цена: 45999", 45_999),
    ("cost", "стоимость — 8990", 8_990),
    ("short_k", "Кофемашина 45к", 45_000),
    ("latin_k", "Headphones 15k", 15_000),
    ("formatted", "Монитор 34 990", 34_990),
    ("commerce", "Купить ноутбук 49999", 49_999),
    ("currency_priority", "SSD 512 ГБ, цена 49 999 ₽", 49_999),
    ("battery_then_price", "10000 mAh, цена 1999 ₽", 1_999),
    ("load_then_price", "120 кг, стоимость 8990 ₽", 8_990),
    ("resolution_then_price", "1920x1080, 144 Гц, 45999 ₽", 45_999),
    ("year_with_money", "Модель 2026, цена 2025 ₽", 2_025),
    ("price_from", "Цена от 12 990 ₽", 12_990),
    ("currency_before", "₽ 54990 телевизор", 54_990),
    ("ssd", "SSD 1024 ГБ", None),
    ("ram", "RAM 16 ГБ SSD 512 ГБ", None),
    ("resolution", "Разрешение 1920x1080", None),
    ("resolution_4k", "3840 × 2160 пикселей", None),
    ("refresh", "Частота 144 Гц", None),
    ("diagonal", "Диагональ 55 дюймов", None),
    ("load", "Нагрузка 120 кг", None),
    ("power", "Мощность 2000 Вт", None),
    ("battery", "Аккумулятор 10000 mAh", None),
    ("article", "Артикул 123456", None),
    ("article_after", "123456 артикул", None),
    ("model", "Модель 89999", None),
    ("reviews", "12000 отзывов", None),
    ("ratings", "Оценок 45000", None),
    ("budget", "до 35000", None),
    ("budget_word", "бюджет 60000", None),
    ("year", "Модель 2026 года", None),
    ("size", "Матрас 160x200", None),
    ("volume", "Микроволновка 20-25 литров", None),
    ("games", "цена 45000 встроенных игр", None),
    ("bare", "49999", None),
    ("model_code", "Телевизор UE55AU7100", None),
    ("empty", "", None),
    ("words", "цена по запросу", None),
)


class PriceCases(unittest.TestCase):
    def test_01_price_matrix(self) -> None:
        false_positives = 0
        for case_id, text, expected in CASES:
            actual = extract_price(text)
            with self.subTest(case_id=case_id):
                self.assertEqual(actual, expected)
            if expected is None and actual is not None:
                false_positives += 1
        self.assertEqual(false_positives, 0)

    def test_02_evidence_levels(self) -> None:
        cases = (
            ("49 999 ₽", PRICE_CONFIDENCE_HIGH, PRICE_EVIDENCE_CURRENCY),
            ("цена 49999", PRICE_CONFIDENCE_MEDIUM, PRICE_EVIDENCE_LABELED),
            ("49 999", PRICE_CONFIDENCE_LOW, PRICE_EVIDENCE_INFERRED),
        )
        for text, confidence, evidence in cases:
            with self.subTest(text=text):
                result = extract_price_evidence(text)
                self.assertEqual(result.confidence, confidence)
                self.assertEqual(result.evidence, evidence)

    def test_03_structured_priority(self) -> None:
        direct = extract_price_evidence("SSD 512 ГБ", structured_price=49_990, price_source="direct_store")
        page = extract_price_evidence("нет цены", structured_price=51_000, price_source="json_ld")
        self.assertEqual((direct.price, direct.confidence, direct.evidence), (49_990, PRICE_CONFIDENCE_HIGH, PRICE_EVIDENCE_DIRECT))
        self.assertEqual((page.price, page.confidence, page.evidence), (51_000, PRICE_CONFIDENCE_HIGH, PRICE_EVIDENCE_STRUCTURED))

    def test_04_rejected_spec_evidence(self) -> None:
        result = extract_price_evidence("SSD 1024 ГБ")
        self.assertIsNone(result.price)
        self.assertEqual(result.evidence, PRICE_EVIDENCE_REJECTED_SPEC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
