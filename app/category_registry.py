"""Декларативные правила товарных категорий без сетевой логики."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True)
class CategorySpec:
    key: str
    names: tuple[str, ...]
    required_product_markers: tuple[str, ...]
    accessory_markers: tuple[str, ...]
    useful_facts: tuple[str, ...]
    known_brands: tuple[str, ...]
    weak_indicators: tuple[str, ...]
    quality_requirements: tuple[str, ...]
    minimum_plausible_price: int
    model_modifiers: tuple[str, ...]
    query_variants: tuple[str, ...]
    ranking_preferences: tuple[str, ...]


COMMON_MODEL_MODIFIERS = (
    "pro max", "pro", "max", "plus", "mini", "se", "fe", "ultra",
    "lite", "air", "e", "поколение",
)


def _spec(
    key: str,
    *,
    names: Iterable[str],
    required_product_markers: Iterable[str],
    accessory_markers: Iterable[str],
    useful_facts: Iterable[str],
    known_brands: Iterable[str] = (),
    weak_indicators: Iterable[str] = (),
    quality_requirements: Iterable[str] = (),
    minimum_plausible_price: int = 1_000,
    model_modifiers: Iterable[str] = COMMON_MODEL_MODIFIERS,
    query_variants: Iterable[str] = (),
    ranking_preferences: Iterable[str] = (),
) -> CategorySpec:
    return CategorySpec(
        key=key,
        names=tuple(names),
        required_product_markers=tuple(required_product_markers),
        accessory_markers=tuple(accessory_markers),
        useful_facts=tuple(useful_facts),
        known_brands=tuple(known_brands),
        weak_indicators=tuple(weak_indicators),
        quality_requirements=tuple(quality_requirements),
        minimum_plausible_price=minimum_plausible_price,
        model_modifiers=tuple(model_modifiers),
        query_variants=tuple(query_variants),
        ranking_preferences=tuple(ranking_preferences),
    )


CATEGORY_SPECS: dict[str, CategorySpec] = {
    "phone": _spec(
        "phone",
        names=("смартфон", "телефон", "iphone", "айфон", "galaxy", "pixel phone", "google pixel"),
        required_product_markers=("смартфон", "телефон", "iphone", "galaxy", "pixel"),
        accessory_markers=("чехол", "стекло", "пленка", "плёнка", "кабель", "зарядка", "держатель", "муляж"),
        useful_facts=("brand", "model", "storage", "ram", "color", "condition", "sim_variant", "availability"),
        known_brands=("Apple", "Samsung", "Xiaomi", "Honor", "Huawei", "Google", "Realme", "OnePlus", "Nothing", "Tecno", "Infinix"),
        weak_indicators=("копия", "реплика", "восстановленный", "refurbished"),
        quality_requirements=("model", "price", "availability"),
        minimum_plausible_price=3_000,
        query_variants=("{identity} смартфон", "{identity} {storage}", "{identity} купить"),
        ranking_preferences=("exact_model", "required_storage", "availability", "price_confidence"),
    ),
    "laptop": _spec(
        "laptop",
        names=("ноутбук", "ноут", "laptop", "macbook", "ультрабук"),
        required_product_markers=("ноутбук", "laptop", "macbook", "ультрабук"),
        accessory_markers=("сумка", "чехол", "зарядка", "блок питания", "клавиатура", "подставка", "аккумулятор"),
        useful_facts=("brand", "model", "cpu", "ram", "ssd", "screen", "resolution", "os", "gpu"),
        known_brands=("LUNNEN", "EXPEcomp", "ECHIPS", "Acer", "Asus", "Apple", "Dell", "HP", "Huawei", "Honor", "Lenovo", "MSI", "Samsung", "Xiaomi", "Thunderobot", "Maibenben", "Digma"),
        weak_indicators=("N95", "N100", "N150", "N5095", "Celeron", "Pentium Silver"),
        quality_requirements=("cpu", "ram", "ssd"),
        minimum_plausible_price=10_000,
        query_variants=("ноутбук {criteria}", "{identity} {criteria}", "laptop {criteria}"),
        ranking_preferences=("ryzen_or_core", "ram_16", "ssd_512", "known_brand"),
    ),
    "tv": _spec(
        "tv",
        names=("телевизор", "smart tv", "смарт тв", "qled tv", "oled tv"),
        required_product_markers=("телевизор", "smart tv", "qled", "oled", "tv"),
        accessory_markers=("кронштейн", "пульт", "кабель", "подставка", "матрица", "плата", "подсветка"),
        useful_facts=("brand", "model", "diagonal", "resolution", "refresh_rate", "hdmi", "panel", "smart_platform"),
        known_brands=("Samsung", "LG", "Sony", "Xiaomi", "TCL", "Hisense", "Haier", "Philips", "Sber", "Tuvio"),
        weak_indicators=("Full HD", "HD Ready", "без HDMI"),
        quality_requirements=("diagonal", "resolution", "availability"),
        minimum_plausible_price=5_000,
        query_variants=("телевизор {diagonal} {resolution}", "smart tv {diagonal} {resolution}", "телевизор {criteria}"),
        ranking_preferences=("exact_diagonal", "4k", "120hz", "hdmi"),
    ),
    "headphones": _spec(
        "headphones",
        names=("наушники", "наушник", "гарнитура", "headphones", "earbuds", "airpods", "wh-1000xm"),
        required_product_markers=("наушники", "гарнитура", "headphones", "earbuds", "airpods"),
        accessory_markers=("амбушюры", "кейс", "чехол", "кабель", "переходник", "зарядка", "оголовье"),
        useful_facts=("brand", "model", "connection", "anc", "form_factor"),
        known_brands=("Sony", "JBL", "HyperX", "Anker", "Soundcore", "Xiaomi", "QCY", "Baseus", "Samsung", "Huawei", "Honor", "Marshall", "Sennheiser", "Edifier", "Apple", "Nothing"),
        weak_indicators=("копия", "реплика", "без бренда", "проводные дешевые"),
        quality_requirements=("model", "connection"),
        minimum_plausible_price=300,
        query_variants=("{identity} наушники {criteria}", "беспроводные наушники {criteria}", "headphones {criteria}"),
        ranking_preferences=("known_brand", "wireless", "anc", "exact_model"),
    ),
    "chair": _spec(
        "chair",
        names=("офисное кресло", "компьютерное кресло", "кресло", "рабочий стул", "office chair"),
        required_product_markers=("кресло", "стул", "chair"),
        accessory_markers=("газлифт", "колеса", "колёса", "подлокотник отдельно", "чехол", "крестовина", "механизм для кресла"),
        useful_facts=("brand", "model", "lumbar_support", "headrest", "adjustments", "material", "mechanism", "load_capacity"),
        known_brands=("Metta", "Chairman", "Everprof", "Norden", "TopChairs", "Buro", "TetChair"),
        weak_indicators=("без регулировок", "табурет", "ротанг"),
        quality_requirements=("lumbar_support", "adjustments"),
        minimum_plausible_price=1_000,
        query_variants=("офисное кресло {criteria}", "эргономичное кресло {criteria}", "компьютерное кресло {criteria}"),
        ranking_preferences=("ergonomic_facts", "lumbar_support", "adjustments"),
    ),
    "monitor": _spec(
        "monitor",
        names=("монитор", "monitor", "дисплей для компьютера"),
        required_product_markers=("монитор", "monitor"),
        accessory_markers=("кронштейн", "подставка", "кабель", "матрица", "экран для ноутбука", "защитное стекло"),
        useful_facts=("brand", "model", "diagonal", "resolution", "refresh_rate", "panel", "response_time", "adaptive_sync"),
        known_brands=("AOC", "Acer", "Asus", "BenQ", "Dell", "Gigabyte", "Huawei", "LG", "MSI", "Samsung", "Xiaomi", "Philips"),
        weak_indicators=("60 Гц", "75 Гц", "TN"),
        quality_requirements=("diagonal", "refresh_rate"),
        minimum_plausible_price=3_000,
        query_variants=("монитор {diagonal} {refresh_rate}", "монитор {diagonal} QHD {refresh_rate}", "monitor IPS {refresh_rate}"),
        ranking_preferences=("exact_diagonal", "required_refresh", "qhd", "ips", "adaptive_sync"),
    ),
    "robot_vacuum": _spec(
        "robot_vacuum",
        names=("робот-пылесос", "робот пылесос", "robot vacuum", "робот для уборки"),
        required_product_markers=("робот-пылесос", "робот пылесос", "robot vacuum"),
        accessory_markers=("щетка", "щётка", "фильтр", "мешок", "станция отдельно", "аккумулятор", "запчасть"),
        useful_facts=("brand", "model", "wet_cleaning", "navigation", "lidar", "suction", "self_empty_station", "mapping"),
        known_brands=("Xiaomi", "Dreame", "Roborock", "Ecovacs", "iRobot", "Polaris", "Redmond", "Midea"),
        weak_indicators=("гироскоп", "хаотичная навигация", "без карты"),
        quality_requirements=("navigation", "requested_wet_cleaning"),
        minimum_plausible_price=5_000,
        query_variants=("робот-пылесос {criteria}", "робот-пылесос лидар {criteria}", "robot vacuum lidar {criteria}"),
        ranking_preferences=("wet_cleaning", "lidar", "mapping", "known_brand"),
    ),
    "vacuum": _spec(
        "vacuum",
        names=("вертикальный пылесос", "ручной пылесос", "пылесос", "vacuum cleaner"),
        required_product_markers=("пылесос", "vacuum"),
        accessory_markers=("щетка", "щётка", "фильтр", "мешок", "шланг", "насадка отдельно", "аккумулятор"),
        useful_facts=("brand", "model", "type", "power", "suction", "battery", "wet_cleaning", "attachments"),
        known_brands=("Dyson", "Xiaomi", "Dreame", "Samsung", "LG", "Bosch", "Philips", "Tefal", "Karcher", "Polaris"),
        weak_indicators=("автомобильный", "игрушечный", "USB"),
        quality_requirements=("type", "power_or_suction"),
        minimum_plausible_price=1_500,
        query_variants=("пылесос {criteria}", "вертикальный пылесос {criteria}", "vacuum cleaner {criteria}"),
        ranking_preferences=("requested_type", "suction", "battery", "attachments"),
    ),
    "microwave": _spec(
        "microwave",
        names=("микроволновка", "микроволновая печь", "свч", "microwave"),
        required_product_markers=("микроволновка", "микроволновая печь", "свч", "microwave"),
        accessory_markers=("тарелка", "слюда", "крышка", "кронштейн", "магнетрон", "деталь", "посуда"),
        useful_facts=("brand", "model", "volume", "power", "grill", "inverter", "controls"),
        known_brands=("Samsung", "LG", "Bosch", "Gorenje", "Midea", "Haier", "Horizont", "BBK", "Polaris", "Redmond"),
        weak_indicators=("менее 15 л", "менее 500 Вт"),
        quality_requirements=("volume", "power"),
        minimum_plausible_price=2_500,
        query_variants=("микроволновка {criteria}", "микроволновая печь {volume}", "microwave {criteria}"),
        ranking_preferences=("requested_volume", "power", "inverter", "grill"),
    ),
    "coffee_machine": _spec(
        "coffee_machine",
        names=("кофемашина", "кофе-машина", "кофеварка", "coffee machine", "espresso machine"),
        required_product_markers=("кофемашина", "кофеварка", "coffee machine", "espresso machine"),
        accessory_markers=("кофемолка", "капсулы", "кофе в зернах", "зерно", "фильтр", "таблетки", "очиститель", "молочник отдельно"),
        useful_facts=("brand", "model", "machine_type", "cappuccinator", "pressure", "grinder", "milk_system"),
        known_brands=("DeLonghi", "Philips", "Saeco", "Krups", "Bosch", "Melitta", "Jura", "Nivona", "Kitfort", "Polaris", "Redmond"),
        weak_indicators=("ручная", "без капучинатора", "капсульная"),
        quality_requirements=("machine_type", "requested_cappuccinator"),
        minimum_plausible_price=2_000,
        query_variants=("автоматическая кофемашина {criteria}", "рожковая кофеварка {criteria}", "coffee machine milk system {criteria}"),
        ranking_preferences=("requested_type", "cappuccinator", "grinder", "milk_system"),
    ),
    "mattress": _spec(
        "mattress",
        names=("матрас", "mattress", "ортопедический матрас"),
        required_product_markers=("матрас", "mattress"),
        accessory_markers=("наматрасник", "чехол", "топпер", "защитный наматрасник", "простыня"),
        useful_facts=("brand", "model", "size", "firmness", "spring_type", "height", "load_per_bed", "materials"),
        known_brands=("Askona", "Ormatek", "Dreamline", "Dimax", "Promtex", "Lonax", "Sontelle"),
        weak_indicators=("тонкий", "ватный", "топпер"),
        quality_requirements=("size", "firmness"),
        minimum_plausible_price=2_000,
        query_variants=("матрас {size}", "ортопедический матрас {size}", "независимые пружины {size}"),
        ranking_preferences=("exact_size", "firmness", "spring_type", "load_per_bed"),
    ),
    "bed": _spec(
        "bed",
        names=("кровать", "bed frame", "двуспальная кровать", "односпальная кровать"),
        required_product_markers=("кровать", "bed frame"),
        accessory_markers=("изголовье отдельно", "ламели", "основание отдельно", "матрас", "чехол", "ящик отдельно", "подъемный механизм отдельно", "подъёмный механизм отдельно"),
        useful_facts=("brand", "model", "size", "material", "lift_mechanism", "base", "mattress_included", "storage"),
        known_brands=("Askona", "Ormatek", "Hoff", "Sonum", "Nuvola", "Лазурит", "Столплит"),
        weak_indicators=("без основания", "каркас без ламелей"),
        quality_requirements=("size", "base"),
        minimum_plausible_price=3_000,
        query_variants=("кровать {size} {criteria}", "двуспальная кровать {size}", "кровать с подъемным механизмом {size}"),
        ranking_preferences=("exact_size", "base", "lift_mechanism", "storage"),
    ),
    # A deliberately narrow escape hatch for technical products that do not
    # belong to one of the verticals above.  It is never selected by
    # ``detect_category``: the V2 request normalizer enters it only after it
    # has extracted both a known technical brand and a concrete model code.
    # This keeps a request such as "хорошая дрель" unsupported instead of
    # pretending that arbitrary search snippets are comparable offers.
    "generic_tech": _spec(
        "generic_tech",
        names=(),
        required_product_markers=(),
        accessory_markers=("запчасть", "аксессуар", "чехол", "кабель", "зарядка", "аккумулятор"),
        useful_facts=("brand", "model", "availability"),
        known_brands=(
            "Acer", "Apple", "Asus", "Bosch", "Brother", "Canon", "DeWalt", "Dell", "DJI",
            "Dyson", "Epson", "Garmin", "GoPro", "HP", "Huawei", "JBL", "Lenovo", "LG",
            "Makita", "Meta", "MSI", "Nikon", "Nintendo", "Philips", "Samsung", "Sony",
            "Steam", "Xiaomi",
        ),
        weak_indicators=("без модели", "неизвестная комплектация"),
        quality_requirements=("model", "product_card", "price", "availability"),
        minimum_plausible_price=500,
        query_variants=("{identity} купить",),
        ranking_preferences=("exact_model", "product_card", "price", "availability"),
    ),
    "unknown": _spec(
        "unknown",
        names=(),
        required_product_markers=(),
        accessory_markers=("запчасть", "аксессуар", "чехол", "кабель"),
        useful_facts=("brand", "model", "availability"),
        weak_indicators=("неизвестная категория",),
        quality_requirements=("product_card", "price", "availability"),
        minimum_plausible_price=100,
        query_variants=("{identity} купить",),
        ranking_preferences=("exact_product", "product_card", "price", "availability"),
    ),
}


_CATEGORY_ORDER = (
    "robot_vacuum", "coffee_machine", "microwave", "mattress", "bed",
    "monitor", "laptop", "phone", "tv", "headphones", "chair", "vacuum",
)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").lower().replace("ё", "е")).strip()


def contains_marker(text: str, marker: str) -> bool:
    normalized = normalize_text(text)
    marker_text = normalize_text(marker)
    if not marker_text:
        return False
    pattern = re.escape(marker_text).replace(r"\ ", r"[\s-]+")
    return re.search(rf"(?<![a-zа-я0-9]){pattern}(?![a-zа-я0-9])", normalized) is not None


def get_category_spec(category: str) -> CategorySpec:
    return CATEGORY_SPECS.get(normalize_text(category).replace(" ", "_"), CATEGORY_SPECS["unknown"])


def detect_category(text: str) -> str:
    normalized = normalize_text(text)
    if re.search(r"\bwh[\s-]*1000xm\d\b", normalized):
        return "headphones"
    for key in _CATEGORY_ORDER:
        spec = CATEGORY_SPECS[key]
        if any(contains_marker(normalized, marker) for marker in spec.names):
            return key
    return "unknown"


def known_brand(category: str, text: str) -> str:
    spec = get_category_spec(category)
    for brand in sorted(spec.known_brands, key=len, reverse=True):
        if contains_marker(text, brand):
            return brand
    return ""


def is_accessory_text(category: str, text: str) -> bool:
    spec = get_category_spec(category)
    return any(contains_marker(text, marker) for marker in spec.accessory_markers)
