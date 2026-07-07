"""Category-aware product quality rules used after verification.

The module is deliberately independent from sources, DB, Playwright and
Telegram handlers. It only reads request/candidate-like objects.
"""
from __future__ import annotations

from typing import Any
import re


DIRECT_QUALITY_MANUAL_RISK = "нужна ручная проверка качества товара"
DIRECT_RETAIL_USED_RISK = "новый retail-вариант, не б/у предложение"
WEAK_CPU_MANUAL_RISK = "слабый процессор / нужна ручная проверка"
PS5_4K_UNCONFIRMED_RISK = "4K/UHD не подтверждено для PS5"
CHAIR_WEAK_MANUAL_RISK = "кресло требует ручной проверки эргономики"
CHAIR_CHEAP_MANUAL_RISK = "дешёвое кресло, нужна ручная проверка эргономики"
BAD_PRODUCT_RISK = "мусор/аксессуар, не товар"

KNOWN_GOOD_HEADPHONE_BRANDS = (
    "sony", "jbl", "anker", "soundcore", "xiaomi", "qcy", "baseus",
    "samsung", "huawei", "honor", "marshall", "sennheiser",
    "audio-technica", "audio technica", "edifier", "oneplus",
    "nothing", "realme",
)
HEADPHONE_WIRELESS_MARKERS = (
    "tws", "bluetooth", "беспровод", "anc", "шумоподав", "noise cancelling",
    "soundcore", "qcy", "xiaomi buds", "redmi buds", "baseus bowie",
    "jbl tune beam", "jbl tune wave", "jbl wave", "jbl tune flex",
)
HEADPHONE_WIRED_BASIC_MARKERS = (
    "проводн", "wired", "jack", "3.5", "3,5", "aux", "tune 110",
    "jblt110", "простые вкладыши", "вкладыши",
)
KNOWN_LAPTOP_BRANDS = (
    "acer", "asus", "apple", "dell", "hp", "huawei", "honor", "lenovo",
    "msi", "samsung", "xiaomi", "thunderobot", "gigabyte", "microsoft",
    "realme", "tecno", "irbis", "digma", "maibenben",
)
WEAK_LAPTOP_CPUS = ("n95", "n100", "n150", "celeron", "n4020", "n4500", "n5095", "j4005")

CHAIR_BAD_MARKERS = (
    "чехол", "накидка", "колесо", "колеса", "колесики", "ролик",
    "газлифт", "подлокотник", "подлокотники", "крестовина", "запчаст",
    "комплектующие",
)
CHAIR_PRODUCT_MARKERS = ("кресло", "стул")
CHAIR_ACCESSORY_CONTEXT_MARKERS = (
    "для кресл", "для стул", "к кресл", "к стул", "на кресл", "на стул",
    "от кресл", "от стул", "запчаст", "замена", "ремонт",
)
CHAIR_PART_START_MARKERS = (
    "чехол", "накидка", "колесо", "колеса", "колесики", "ролик",
    "ролики", "газлифт", "подлокотник", "подлокотники", "крестовина",
    "комплект колес", "комплект колёс",
)
CHAIR_WEAK_QUALITY_FEATURES = {"office_or_computer"}


def normalized_text(candidate: Any) -> str:
    return f"{getattr(candidate, 'title', '')} {getattr(candidate, 'snippet', '')}".lower().replace("ё", "е")


def _has_word(text: str, value: str) -> bool:
    return re.search(rf"(?<![a-zа-я0-9]){re.escape(value)}(?![a-zа-я0-9])", text) is not None


def _candidate_price(candidate: Any) -> int | None:
    price = getattr(candidate, "price", None)
    if price is None:
        return None
    try:
        return int(price)
    except (TypeError, ValueError):
        digits = re.sub(r"\D+", "", str(price))
        return int(digits) if digits else None


def headphone_brand_quality(candidate: Any, *, enabled: bool, budget: int | None = None) -> str:
    if not enabled or not ((budget or 0) >= 5_000):
        return ""
    text = normalized_text(candidate)
    if any(_has_word(text, brand) for brand in KNOWN_GOOD_HEADPHONE_BRANDS):
        return "known_headphone_brand"
    return "unknown_headphone_brand"


def headphone_feature_profile(candidate: Any, *, enabled: bool, budget: int | None = None) -> str:
    if not enabled or not ((budget or 0) >= 5_000):
        return ""
    text = normalized_text(candidate)
    if any(marker in text for marker in HEADPHONE_WIRELESS_MARKERS):
        return "wireless_good"
    if any(marker in text for marker in HEADPHONE_WIRED_BASIC_MARKERS):
        return "wired_basic"
    return ""


def direct_product_quality_level(
    candidate: Any,
    *,
    is_direct_retail: bool,
    is_used_allowed: bool,
    brand_quality: str = "",
    has_weak_classification: bool = False,
) -> str:
    if not is_direct_retail:
        return ""
    if is_used_allowed:
        return "retail_new_for_used_request"
    if has_weak_classification:
        return "weak"
    if brand_quality == "unknown_headphone_brand":
        return "unknown_brand"
    if brand_quality == "known_headphone_brand":
        return "known_brand"
    return "direct_store"


def has_known_laptop_brand(candidate: Any) -> bool:
    text = normalized_text(candidate)
    return any(_has_word(text, brand) for brand in KNOWN_LAPTOP_BRANDS)


def has_weak_laptop_cpu(candidate: Any) -> bool:
    text = normalized_text(candidate)
    return any(_has_word(text, cpu) for cpu in WEAK_LAPTOP_CPUS)


def chair_bad_reason(candidate: Any) -> str:
    text = normalized_text(candidate)
    stripped = text.strip()
    if any(stripped.startswith(marker) for marker in CHAIR_PART_START_MARKERS):
        return BAD_PRODUCT_RISK
    has_bad_marker = any(marker in text for marker in CHAIR_BAD_MARKERS)
    has_product_marker = any(marker in text for marker in CHAIR_PRODUCT_MARKERS)
    has_accessory_context = any(marker in text for marker in CHAIR_ACCESSORY_CONTEXT_MARKERS)
    if has_bad_marker and (not has_product_marker or has_accessory_context):
        return BAD_PRODUCT_RISK
    if any(marker in text for marker in ("чехол", "накидка", "подлокотник отдельно", "подлокотники отдельно")):
        return BAD_PRODUCT_RISK
    return ""


def chair_quality_features(candidate: Any) -> set[str]:
    text = normalized_text(candidate)
    features: set[str] = set()
    if "эргоном" in text:
        features.add("ergonomic")
    if "ортопед" in text:
        features.add("orthopedic_back")
    if "пояснич" in text or ("поддержк" in text and "поясниц" in text):
        features.add("lumbar_support")
    if "регулиров" in text and ("высот" in text or "сиден" in text):
        features.add("height_adjustment")
    if "регулиров" in text and "подлокот" in text:
        features.add("armrest_adjustment")
    if "подголовник" in text or "подголовн" in text:
        features.add("headrest")
    if "сетк" in text or "сетчат" in text:
        features.add("mesh")
    if "механизм качания" in text or ("механизм" in text and "качан" in text):
        features.add("rocking_mechanism")
    if re.search(r"(?<!\d)(120|150)\s*кг(?![a-zа-я0-9])", text):
        features.add("load_capacity")
    if (
        "компьютерное кресло" in text
        or "компьютерный стул" in text
        or "офисное кресло" in text
        or "офисный стул" in text
        or ("офисн" in text and any(marker in text for marker in CHAIR_PRODUCT_MARKERS))
    ):
        features.add("office_or_computer")
    return features


def chair_weak_reason(candidate: Any) -> str:
    text = normalized_text(candidate)
    features = chair_quality_features(candidate)
    if "ротанг" in text and not features:
        return CHAIR_WEAK_MANUAL_RISK
    price = _candidate_price(candidate)
    if price is not None and price < 5_000 and len(features) <= 1:
        return CHAIR_CHEAP_MANUAL_RISK
    strong_features = features - CHAIR_WEAK_QUALITY_FEATURES
    if len(features) < 2 or not strong_features:
        return CHAIR_WEAK_MANUAL_RISK
    return ""


def ps5_tv_has_confirmed_4k(candidate: Any) -> bool:
    text = normalized_text(candidate)
    if re.search(r"\b4\s*k\b", text) or "4к" in text or "uhd" in text or "ultra hd" in text or "3840" in text:
        return True
    facts = getattr(candidate, "product_facts", {}) or {}
    resolution = str(facts.get("resolution", "") or "").lower()
    if "4k" in resolution or "uhd" in resolution or "3840" in resolution:
        return True
    return False


def final_product_quality_level(
    candidate: Any,
    *,
    is_chair: bool = False,
    is_laptop: bool = False,
    is_ps5_tv: bool = False,
    has_weak_classification: bool = False,
    quality_is_weak: bool = False,
) -> tuple[str, str]:
    if is_chair:
        bad_reason = chair_bad_reason(candidate)
        if bad_reason:
            return "bad", bad_reason
        weak_reason = chair_weak_reason(candidate)
        if weak_reason:
            return "weak", weak_reason

    if is_laptop and has_weak_laptop_cpu(candidate) and not has_known_laptop_brand(candidate):
        return "weak", WEAK_CPU_MANUAL_RISK

    if is_ps5_tv and not ps5_tv_has_confirmed_4k(candidate):
        return "weak", PS5_4K_UNCONFIRMED_RISK

    current_level = getattr(candidate, "product_quality_level", "") or ""
    if current_level == "retail_new_for_used_request":
        return current_level, getattr(candidate, "why_not_verified_good", "") or DIRECT_RETAIL_USED_RISK
    if current_level == "bad":
        return "bad", getattr(candidate, "why_not_verified_good", "") or BAD_PRODUCT_RISK
    if current_level in {"weak", "unknown_brand"}:
        return "weak", getattr(candidate, "why_not_verified_good", "") or DIRECT_QUALITY_MANUAL_RISK
    if quality_is_weak or has_weak_classification:
        return "weak", DIRECT_QUALITY_MANUAL_RISK
    return current_level, ""
