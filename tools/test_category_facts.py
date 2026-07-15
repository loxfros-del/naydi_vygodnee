"""30+ category facts/brand boundary cases без сети."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.category_facts import extract_category_facts


CASES = (
    ("lunnen", "laptop", "LUNNEN Ground Ryzen 7", "", {"brand": "LUNNEN", "cpu": "RYZEN 7"}),
    ("expe", "laptop", "EXPEcomp EVO Ryzen 5", "", {"brand": "EXPEcomp", "cpu": "RYZEN 5"}),
    ("echips", "laptop", "ECHIPS Taganay N95", "", {"brand": "ECHIPS", "cpu": "N95"}),
    ("hp", "laptop", "HP 250 G10", "", {"brand": "HP"}),
    ("hp_boundary", "laptop", "SHPRO Laptop N100", "", {"brand": ""}),
    ("title_brand_priority", "laptop", "LUNNEN Ground Ryzen 5", "HP 250 G10 в рекомендациях", {"brand": "LUNNEN"}),
    ("laptop_full", "laptop", "Lenovo IdeaPad Ryzen 5 RAM 16 ГБ SSD 512 ГБ 15.6", "Windows 11 IPS", {"brand": "Lenovo", "ram": "16 ГБ", "ssd": "512 ГБ", "os": "Windows 11"}),
    ("laptop_gpu", "laptop", "ASUS TUF Core i5 RTX 4060", "RAM 16 ГБ SSD 512 ГБ", {"brand": "Asus", "gpu": "RTX 4060"}),
    ("phone_iphone", "phone", "Apple iPhone 15 Pro 256 ГБ черный", "eSIM, новый", {"brand": "Apple", "storage_gb": 256, "color": "черный", "sim_variant": "eSIM"}),
    ("phone_galaxy", "phone", "Samsung Galaxy A55 128 ГБ", "Dual SIM", {"brand": "Samsung", "storage_gb": 128, "sim_variant": "Dual SIM"}),
    ("phone_used", "phone", "Apple iPhone 13 128 ГБ б/у", "", {"condition": "б/у"}),
    ("tv", "tv", "Hisense 55E7KQ 55 дюймов 4K QLED 120 Гц", "HDMI 2.1 Google TV", {"brand": "Hisense", "diagonal": "55", "resolution": "4K", "refresh_rate": "120 Гц", "panel": "QLED", "hdmi": "HDMI 2.1"}),
    ("tv_platform", "tv", "LG OLED55C3 55 дюймов 4K OLED", "webOS HDMI", {"brand": "LG", "panel": "OLED", "smart_platform": "webOS"}),
    ("monitor", "monitor", "AOC Q27G2S 27 дюймов QHD IPS 165 Гц 1 мс", "FreeSync", {"brand": "AOC", "diagonal": "27", "resolution": "QHD", "panel": "IPS", "response_time": "1 мс", "adaptive_sync": "FreeSync"}),
    ("monitor_supplement", "monitor", "Samsung Odyssey G5 27", "QHD 144 Гц VA FreeSync", {"brand": "Samsung", "resolution": "QHD", "refresh_rate": "144 Гц", "panel": "VA"}),
    ("headphones", "headphones", "Sony WH-1000XM5 беспроводные полноразмерные ANC", "", {"brand": "Sony", "connection": "wireless", "anc": True, "form_factor": "over-ear"}),
    ("headphones_tws", "headphones", "QCY T13 TWS", "Bluetooth ANC", {"brand": "QCY", "connection": "TWS", "anc": True}),
    ("headphones_wired", "headphones", "JBL Tune 110 проводные", "3.5 мм", {"brand": "JBL", "connection": "wired"}),
    ("chair", "chair", "Кресло Chairman с поясничной поддержкой и подголовником", "регулировка высоты, сетка, нагрузка 150 кг", {"brand": "Chairman", "lumbar_support": True, "headrest": True, "material": "сетка", "load_capacity": "150 кг"}),
    ("chair_mechanism", "chair", "Офисное кресло Metta", "мультиблок, регулируемые подлокотники", {"brand": "Metta", "mechanism": "мультиблок"}),
    ("robot", "robot_vacuum", "Робот-пылесос Dreame D10s с лидаром и влажной уборкой", "5000 Па, построение карты", {"brand": "Dreame", "wet_cleaning": True, "lidar": True, "suction": "5000 Па", "mapping": True}),
    ("robot_station", "robot_vacuum", "Roborock Q Revo", "станция самоочистки, лидар", {"brand": "Roborock", "self_empty_station": True, "navigation": "lidar"}),
    ("vacuum", "vacuum", "Вертикальный пылесос Dyson V12 545 Вт", "60 мин, турбощетка для мебели", {"brand": "Dyson", "type": "вертикальный", "power": "545 Вт", "battery": "60 мин"}),
    ("vacuum_wet", "vacuum", "Моющий пылесос Tefal", "влажная уборка", {"brand": "Tefal", "wet_cleaning": True}),
    ("microwave", "microwave", "Микроволновка Samsung 23 л 800 Вт гриль", "сенсорное управление, инвертор", {"brand": "Samsung", "volume": "23 л", "power": "800 Вт", "grill": True, "inverter": True, "controls": "сенсорное"}),
    ("microwave_simple", "microwave", "Микроволновая печь Midea 20 л", "механическое управление 700 Вт", {"brand": "Midea", "volume": "20 л", "controls": "механическое"}),
    ("coffee", "coffee_machine", "Автоматическая кофемашина Philips LatteGo с капучинатором", "15 бар, встроенная кофемолка, молочная система", {"brand": "Philips", "machine_type": "automatic", "cappuccinator": True, "pressure": "15 бар", "grinder": True, "milk_system": True}),
    ("coffee_capsule", "coffee_machine", "Капсульная кофемашина Krups", "19 бар", {"brand": "Krups", "machine_type": "capsule", "pressure": "19 бар"}),
    ("mattress", "mattress", "Матрас Askona 160x200 средней жёсткости", "независимые пружины, высота 22 см, нагрузка 140 кг, кокос", {"brand": "Askona", "size": "160x200", "firmness": "средняя", "spring_type": "независимые пружины", "height": "22 см", "load_per_bed": "140 кг"}),
    ("mattress_material", "mattress", "Матрас Ormatek 140х200", "латекс memory foam", {"brand": "Ormatek", "size": "140x200"}),
    ("bed", "bed", "Кровать Askona 160x200 с подъёмным механизмом", "ортопедическое основание, ящик для белья, массив дерева", {"brand": "Askona", "size": "160x200", "lift_mechanism": True, "base": "ортопедическое", "storage": True, "material": "дерево"}),
    ("bed_mattress", "bed", "Кровать Hoff 180x200 с матрасом", "основание в комплекте", {"brand": "Hoff", "size": "180x200", "mattress_included": True, "base": "в комплекте"}),
    ("unknown", "unknown", "Аккумуляторная дрель Bosch GSR", "кофемашина Philips в рекомендациях", {"brand": ""}),
)


class CategoryFactCases(unittest.TestCase):
    def test_01_fact_matrix(self) -> None:
        for case_id, category, title, supplemental, expected in CASES:
            facts = extract_category_facts(category, title, supplemental)
            with self.subTest(case_id=case_id, facts=facts):
                for key, value in expected.items():
                    self.assertEqual(facts.get(key, ""), value)
                for key in expected:
                    if key != "brand" and expected[key] not in ("", None, False):
                        self.assertIn(key, facts["fact_evidence"])

    def test_02_no_foreign_category_fields(self) -> None:
        phone = extract_category_facts("phone", "Samsung Galaxy A55 256 ГБ", "Ryzen 5 SSD 512 ГБ")
        laptop = extract_category_facts("laptop", "Lenovo Ryzen 5 RAM 16 ГБ", "капучинатор 15 бар")
        mattress = extract_category_facts("mattress", "Матрас 160x200", "HDMI 2.1 144 Гц")
        self.assertNotIn("cpu", phone)
        self.assertNotIn("cappuccinator", laptop)
        self.assertNotIn("hdmi", mattress)
        self.assertNotIn("refresh_rate", mattress)

    def test_03_title_brand_beats_page_recommendations(self) -> None:
        facts = extract_category_facts("laptop", "LUNNEN Ground Ryzen 5", "HP и ASUS похожие товары")
        self.assertEqual(facts["brand"], "LUNNEN")
        self.assertEqual(facts["fact_evidence"]["brand"]["evidence"], "title")


if __name__ == "__main__":
    unittest.main(verbosity=2)
