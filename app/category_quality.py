"""Единая category analysis: качество товара, не статус workflow."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.category_registry import get_category_spec, normalize_text
from app.exact_match import ACCESSORY, MODEL_MISMATCH, REQUIRED_SPEC_MISMATCH, UNKNOWN, match_candidate
from app.request_parser import normalize_request_data
from app.search_evidence import assess_product_card


@dataclass(frozen=True)
class CategoryQualityResult:
    level: str
    score: float
    reasons: tuple[str, ...] = ()
    confirmed_facts: tuple[str, ...] = ()
    missing_required: tuple[str, ...] = ()
    score_cap: int | None = None


def _value(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _request(request: Any) -> dict[str, Any]:
    if isinstance(request, dict) and "category" in request:
        return dict(request)
    attached = getattr(request, "parsed_details", None)
    if isinstance(attached, dict):
        return dict(attached)
    return normalize_request_data(request)


def _facts(candidate: Any) -> dict[str, Any]:
    facts = _value(candidate, "product_facts", {}) or _value(candidate, "facts", {}) or {}
    return facts if isinstance(facts, dict) else {}


def _present(value: Any) -> bool:
    return value not in (None, "", [], {}, False, "unknown", "UNKNOWN")


def _price(candidate: Any) -> int | None:
    try:
        return int(_value(candidate, "price", None))
    except (TypeError, ValueError):
        return None


def _finish(
    score: float,
    reasons: list[str],
    confirmed: list[str],
    missing: list[str],
    *,
    forced_level: str = "",
    cap: int | None = None,
) -> CategoryQualityResult:
    score = max(0.0, min(10.0, score))
    level = forced_level or ("good" if score >= 8.0 and not missing else "ok" if score >= 6.5 and not missing else "weak")
    if level == "bad":
        cap = min(cap or 30, 30)
    elif level == "weak":
        cap = min(cap or 70, 70)
    return CategoryQualityResult(
        level,
        round(score, 2),
        tuple(dict.fromkeys(reasons)),
        tuple(dict.fromkeys(confirmed)),
        tuple(dict.fromkeys(missing)),
        cap,
    )


def evaluate_category_quality(request: Any, candidate: Any) -> CategoryQualityResult:
    parsed = _request(request)
    category = str(parsed.get("category") or "unknown")
    facts = _facts(candidate)
    title = str(_value(candidate, "title", "") or "")
    text = normalize_text(f"{title} {_value(candidate, 'snippet', '')}")
    exact = match_candidate(parsed, candidate, category)
    if exact.status in {ACCESSORY, MODEL_MISMATCH, REQUIRED_SPEC_MISMATCH}:
        return _finish(0.0, [exact.reason], [], [], forced_level="bad", cap=30)

    spec = get_category_spec(category)
    reasons: list[str] = []
    confirmed: list[str] = []
    missing: list[str] = []
    score = 5.5
    required = dict(parsed.get("required_criteria") or {})
    card = assess_product_card(candidate)
    if card.confidence == "high":
        score += 1.0
        confirmed.append("product_card_high")
    elif card.confidence == "medium":
        score += 0.5
        confirmed.append("product_card_medium")
    else:
        score -= 1.0
        reasons.append("низкая уверенность карточки товара")

    price = _price(candidate)
    if price is None:
        score -= 1.0
        missing.append("price")
    elif price < spec.minimum_plausible_price:
        score -= 2.0
        reasons.append("подозрительно низкая цена для категории")
    else:
        score += 0.5
        confirmed.append("price")

    useful_present = [key for key in spec.useful_facts if _present(facts.get(key))]
    confirmed.extend(useful_present)
    score += min(1.5, len(useful_present) * 0.25)

    if category == "unknown":
        if card.confidence == "high" and price is not None and str(facts.get("availability_text") or "").upper() == "AVAILABLE" and len(useful_present) >= 2:
            return _finish(7.0, ["unknown category: manual-first"], confirmed, missing, forced_level="ok", cap=75)
        return _finish(4.5, ["неизвестная категория: нужна ручная проверка"], confirmed, missing, forced_level="weak", cap=60)

    if exact.status == UNKNOWN:
        score -= 1.5
        reasons.append(exact.reason or "точный товар не подтверждён")

    if category == "phone":
        if parsed.get("model") and exact.status not in {"EXACT", "COMPATIBLE_VARIANT"}:
            missing.append("exact_model")
        if _present(facts.get("storage_gb") or facts.get("storage")):
            score += 0.5
    elif category == "laptop":
        cpu = normalize_text(str(facts.get("cpu") or title))
        if re.search(r"\b(?:n95|n100|n150|n5095|celeron|pentium silver)\b", cpu):
            return _finish(4.0, ["слабый CPU"], confirmed, missing, forced_level="weak", cap=55)
        if re.search(r"\b(?:ryzen\s*[3579]|(?:core\s+)?i[3579])\b", cpu):
            score += 1.0
            confirmed.append("normal_cpu")
        ram = int(re.search(r"\d+", str(facts.get("ram") or "0")).group()) if re.search(r"\d+", str(facts.get("ram") or "")) else 0
        ssd = int(re.search(r"\d+", str(facts.get("ssd") or "0")).group()) if re.search(r"\d+", str(facts.get("ssd") or "")) else 0
        if ram >= 16:
            score += 0.5
        if ssd >= 512:
            score += 0.5
        if "rtx 4090" in text and price is not None and price < 80_000:
            return _finish(4.5, ["подозрительная игровая конфигурация"], confirmed, missing, forced_level="weak", cap=55)
    elif category == "tv":
        resolution = str(facts.get("resolution") or "")
        if required.get("resolution") == "4K" and resolution != "4K":
            missing.append("4K")
            score -= 2.0
            reasons.append("4K не подтверждено")
        if str(facts.get("refresh_rate") or "").startswith("120"):
            score += 0.8
        elif str(facts.get("refresh_rate") or "").startswith("60"):
            reasons.append("60 Гц допустимо, но не идеально")
        if "ps5" in normalize_text(str(parsed.get("use_case") or parsed.get("original_query") or "")) and resolution == "Full HD":
            return _finish(4.0, ["Full HD не top для PS5"], confirmed, missing, forced_level="weak", cap=55)
        if not _present(facts.get("hdmi")):
            reasons.append("HDMI не подтверждён")
    elif category == "headphones":
        if not _present(facts.get("brand")):
            score -= 1.5
            reasons.append("бренд не распознан")
        connection = str(facts.get("connection") or facts.get("headphone_type") or "")
        if required.get("wireless") and connection == "wired":
            return _finish(3.5, ["нужны беспроводные, найден проводной вариант"], confirmed, missing, forced_level="weak", cap=50)
        if connection in {"wireless", "TWS"}:
            score += 0.7
        if facts.get("anc") is True:
            score += 0.5
        if any(marker in text for marker in ("копия", "реплика", "fake")):
            return _finish(1.0, ["fake-like title"], confirmed, missing, forced_level="bad", cap=25)
    elif category == "chair":
        ergonomic = sum(_present(facts.get(key)) for key in ("lumbar_support", "headrest", "adjustments", "mechanism", "load_capacity"))
        if ergonomic < 2:
            missing.append("ergonomic_facts")
            score -= 1.5
            reasons.append("недостаточно подтверждённых ergonomic facts")
        else:
            score += 1.0
    elif category == "monitor":
        if facts.get("panel") == "IPS":
            score += 0.5
        if facts.get("resolution") == "QHD":
            score += 0.5
        if _present(facts.get("adaptive_sync")):
            score += 0.4
    elif category == "robot_vacuum":
        if required.get("wet_cleaning") and facts.get("wet_cleaning") is not True:
            missing.append("wet_cleaning")
            score -= 2.0
        if facts.get("lidar") is True:
            score += 0.8
        if _present(facts.get("mapping")):
            score += 0.4
    elif category == "vacuum":
        if not _present(facts.get("type")):
            missing.append("vacuum_type")
        if _present(facts.get("suction") or facts.get("power")):
            score += 0.6
    elif category == "microwave":
        if not _present(facts.get("volume")):
            missing.append("volume")
        if not _present(facts.get("power")):
            missing.append("power")
    elif category == "coffee_machine":
        if required.get("cappuccinator") and facts.get("cappuccinator") is not True:
            missing.append("cappuccinator")
            score -= 2.0
        if _present(facts.get("machine_type")):
            score += 0.5
        if facts.get("grinder") is True:
            score += 0.4
    elif category == "mattress":
        if not _present(facts.get("size")):
            missing.append("size")
        if _present(facts.get("firmness")):
            score += 0.5
        if _present(facts.get("spring_type")):
            score += 0.4
    elif category == "bed":
        if not _present(facts.get("size")):
            missing.append("size")
        if _present(facts.get("base")):
            score += 0.6
        else:
            reasons.append("основание не подтверждено")
        if required.get("lift_mechanism") and facts.get("lift_mechanism") is not True:
            missing.append("lift_mechanism")

    return _finish(score, reasons, confirmed, missing)
