"""40+ deterministic exact-model/accessory cases без сети."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.exact_match import (
    ACCESSORY,
    COMPATIBLE_VARIANT,
    EXACT,
    GENERIC_MATCH,
    MODEL_MISMATCH,
    REQUIRED_SPEC_MISMATCH,
    UNKNOWN,
    match_candidate,
)
from app.request_parser import full_parse


CASES = (
    ("iphone_exact", "iPhone 17 Pro", "Apple iPhone 17 Pro 256 ГБ", COMPATIBLE_VARIANT),
    ("iphone_base", "iPhone 17 Pro", "Apple iPhone 17 256 ГБ", MODEL_MISMATCH),
    ("iphone_e", "iPhone 17 Pro", "Apple iPhone 17e 256 ГБ", MODEL_MISMATCH),
    ("iphone_max", "iPhone 17 Pro", "Apple iPhone 17 Pro Max 256 ГБ", MODEL_MISMATCH),
    ("iphone_old", "iPhone 17 Pro", "Apple iPhone 16 Pro 256 ГБ", MODEL_MISMATCH),
    ("iphone_storage_exact", "iPhone 15 Pro 256 ГБ", "Apple iPhone 15 Pro 256 ГБ", EXACT),
    ("iphone_storage_wrong", "iPhone 15 Pro 256 ГБ", "Apple iPhone 15 Pro 128 ГБ", REQUIRED_SPEC_MISMATCH),
    ("iphone_storage_missing", "iPhone 15 Pro 256 ГБ", "Apple iPhone 15 Pro", GENERIC_MATCH),
    ("iphone_variant", "iPhone 15 Pro", "Apple iPhone 15 Pro 512 ГБ", COMPATIBLE_VARIANT),
    ("iphone_accessory", "iPhone 15 Pro", "Чехол для Apple iPhone 15 Pro", ACCESSORY),
    ("iphone_glass", "iPhone 15 Pro", "Защитное стекло для iPhone 15 Pro", ACCESSORY),
    ("galaxy_a55", "Samsung Galaxy A55 256 ГБ", "Samsung Galaxy A55 256 ГБ 5G", EXACT),
    ("galaxy_a35", "Samsung Galaxy A55 256 ГБ", "Samsung Galaxy A35 256 ГБ", MODEL_MISMATCH),
    ("galaxy_storage", "Samsung Galaxy A55 256 ГБ", "Samsung Galaxy A55 128 ГБ", REQUIRED_SPEC_MISMATCH),
    ("s24_ultra", "Galaxy S24 Ultra", "Samsung Galaxy S24 Ultra 512 ГБ", COMPATIBLE_VARIANT),
    ("s24_fe", "Galaxy S24 Ultra", "Samsung Galaxy S24 FE 256 ГБ", MODEL_MISMATCH),
    ("s24_plus", "Galaxy S24", "Samsung Galaxy S24 Plus", MODEL_MISMATCH),
    ("sony_xm5", "Sony WH-1000XM5", "Наушники Sony WH-1000XM5", EXACT),
    ("sony_xm4", "Sony WH-1000XM5", "Наушники Sony WH-1000XM4", MODEL_MISMATCH),
    ("sony_case", "Sony WH-1000XM5", "Кейс для наушников Sony WH-1000XM5", ACCESSORY),
    ("monitor_27", "Монитор 27 дюймов 144 Гц", "Монитор LG 27GN800 27 дюймов 144 Гц IPS", GENERIC_MATCH),
    ("monitor_24", "Монитор 27 дюймов 144 Гц", "Монитор LG 24GN600 24 дюйма 144 Гц", REQUIRED_SPEC_MISMATCH),
    ("monitor_75hz", "Монитор 27 дюймов 144 Гц", "Монитор Samsung 27 дюймов 75 Гц", REQUIRED_SPEC_MISMATCH),
    ("monitor_arm", "Монитор 27 дюймов", "Кронштейн для монитора 27 дюймов", ACCESSORY),
    ("tv_55", "Телевизор 55 дюймов 4К", "Телевизор Hisense 55E7KQ 55 дюймов 4K", GENERIC_MATCH),
    ("tv_43", "Телевизор 55 дюймов 4К", "Телевизор Hisense 43E7KQ 43 дюйма 4K", REQUIRED_SPEC_MISMATCH),
    ("tv_remote", "Телевизор 55 дюймов", "Пульт для телевизора Samsung", ACCESSORY),
    ("mattress_exact", "Матрас 160x200", "Матрас Askona Balance 160х200", GENERIC_MATCH),
    ("mattress_wrong", "Матрас 160x200", "Матрас Askona Balance 140х200", REQUIRED_SPEC_MISMATCH),
    ("mattress_topper", "Матрас 160x200", "Наматрасник 160x200", ACCESSORY),
    ("bed_exact", "Кровать 160x200", "Кровать с основанием 160х200", GENERIC_MATCH),
    ("bed_wrong", "Кровать 160x200", "Кровать с основанием 180х200", REQUIRED_SPEC_MISMATCH),
    ("bed_headboard", "Кровать 160x200", "Изголовье отдельно для кровати 160x200", ACCESSORY),
    ("bed_mattress", "Кровать 160x200", "Матрас 160x200 средней жёсткости", ACCESSORY),
    ("robot_exact", "Робот-пылесос с лидаром", "Робот-пылесос Dreame D10s с лидаром", GENERIC_MATCH),
    ("robot_vertical", "Робот-пылесос с лидаром", "Вертикальный пылесос Dreame R10", MODEL_MISMATCH),
    ("robot_filter", "Робот-пылесос", "Фильтр для робота-пылесоса Xiaomi", ACCESSORY),
    ("vacuum_exact", "Вертикальный пылесос", "Вертикальный пылесос Dyson V12", GENERIC_MATCH),
    ("vacuum_brush", "Вертикальный пылесос", "Щётка для пылесоса Dyson V12", ACCESSORY),
    ("coffee_exact", "Автоматическая кофемашина с капучинатором", "Автоматическая кофемашина Philips с капучинатором", GENERIC_MATCH),
    ("coffee_missing_fact", "Кофемашина с капучинатором", "Кофемашина Philips EP1200", GENERIC_MATCH),
    ("coffee_grinder", "Кофемашина", "Кофемолка электрическая Kitfort", ACCESSORY),
    ("microwave_exact", "Микроволновка 20 литров", "Микроволновая печь Samsung 20 л", GENERIC_MATCH),
    ("microwave_plate", "Микроволновка", "Тарелка для микроволновки Samsung", ACCESSORY),
    ("chair_exact", "Офисное кресло", "Офисное кресло Chairman с поясничной поддержкой", GENERIC_MATCH),
    ("chair_gaslift", "Офисное кресло", "Газлифт для офисного кресла", ACCESSORY),
    ("used_conflict", "iPhone 15 Pro б/у", "Новый Apple iPhone 15 Pro", REQUIRED_SPEC_MISMATCH),
    ("used_unknown", "iPhone 15 Pro б/у", "Apple iPhone 15 Pro", EXACT),
    ("unknown", "Аккумуляторная дрель", "Дрель Bosch GSR 12V", UNKNOWN),
)


class ExactMatchCases(unittest.TestCase):
    def test_01_exact_match_matrix(self) -> None:
        correct = 0
        for case_id, query, title, expected in CASES:
            result = match_candidate(full_parse(query), {"title": title})
            with self.subTest(case_id=case_id, result=result):
                self.assertEqual(result.status, expected, result)
                if result.status in {MODEL_MISMATCH, REQUIRED_SPEC_MISMATCH, ACCESSORY}:
                    self.assertTrue(result.reason)
            correct += int(result.status == expected)
        self.assertGreaterEqual(correct / len(CASES), 0.95)

    def test_02_mismatch_reason_names_difference(self) -> None:
        result = match_candidate(full_parse("iPhone 15 Pro 256 ГБ"), {"title": "iPhone 15 Pro 128 ГБ"})
        self.assertEqual(result.status, REQUIRED_SPEC_MISMATCH)
        self.assertIn("256", result.reason)
        self.assertIn("128", result.reason)

    def test_03_accessory_rejection_rate(self) -> None:
        accessory_cases = [item for item in CASES if item[3] == ACCESSORY]
        rejected = sum(
            match_candidate(full_parse(query), {"title": title}).status == ACCESSORY
            for _, query, title, _ in accessory_cases
        )
        self.assertGreaterEqual(rejected / len(accessory_cases), 0.98)


if __name__ == "__main__":
    unittest.main(verbosity=2)
