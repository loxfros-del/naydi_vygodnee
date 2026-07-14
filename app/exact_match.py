"""Повторно используемое сопоставление запроса и конкретного товара."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.category_registry import contains_marker, detect_category, get_category_spec, normalize_text
from app.request_parser import normalize_request_data, parse_request_details


EXACT = "EXACT"
COMPATIBLE_VARIANT = "COMPATIBLE_VARIANT"
GENERIC_MATCH = "GENERIC_MATCH"
MODEL_MISMATCH = "MODEL_MISMATCH"
REQUIRED_SPEC_MISMATCH = "REQUIRED_SPEC_MISMATCH"
ACCESSORY = "ACCESSORY"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExactMatchResult:
    status: str
    reason: str = ""
    differences: tuple[str, ...] = ()
    requested_model: str = ""
    candidate_model: str = ""
    category: str = "unknown"
    confidence: str = "none"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "differences": list(self.differences),
            "requested_model": self.requested_model,
            "candidate_model": self.candidate_model,
            "category": self.category,
            "confidence": self.confidence,
        }


def _value(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _request_details(parsed_or_request: Any) -> dict[str, Any]:
    attached = getattr(parsed_or_request, "parsed_details", None)
    if isinstance(attached, dict):
        return dict(attached)
    return normalize_request_data(parsed_or_request)


def _facts(candidate: Any) -> dict[str, Any]:
    value = _value(candidate, "product_facts", {}) or _value(candidate, "facts", {}) or {}
    return value if isinstance(value, dict) else {}


def _candidate_title(candidate: Any) -> str:
    return str(_value(candidate, "title", "") or "").strip()


def _identity_text(candidate: Any) -> str:
    facts = _facts(candidate)
    title = _candidate_title(candidate)
    fact_model = str(facts.get("model") or facts.get("model_key") or "")
    fact_brand = str(facts.get("brand") or "")
    # Title всегда первичен; structured identity используется только как дополнение.
    return " ".join(part for part in (title, fact_brand, fact_model) if part).strip()


def _is_accessory(category: str, title: str) -> bool:
    normalized = normalize_text(title)
    spec = get_category_spec(category)
    accessory_hits = [marker for marker in spec.accessory_markers if contains_marker(normalized, marker)]
    if not accessory_hits:
        return False
    product_present = any(contains_marker(normalized, marker) for marker in spec.required_product_markers)
    for marker in accessory_hits:
        marker_text = normalize_text(marker)
        if normalized.startswith(marker_text) or re.search(
            rf"\b{re.escape(marker_text)}\s+(?:для|к)\b", normalized,
        ):
            return True
    return not product_present


def _model_key(value: str) -> str:
    text = normalize_text(value)
    text = re.sub(r"\b(?:apple|samsung|sony)\b", "", text)
    return re.sub(r"[^a-zа-я0-9]+", " ", text).strip()


def _requested_model_identity(request: dict[str, Any]) -> str:
    model = str(request.get("model") or "").strip()
    model_key = _model_key(model)
    display = {
        "pro max": "Pro Max", "pro": "Pro", "max": "Max", "plus": "Plus",
        "mini": "Mini", "se": "SE", "ultra": "Ultra", "fe": "FE",
        "lite": "Lite", "e": "e", "air": "Air",
    }
    modifiers = sorted(
        (str(item).strip().lower() for item in request.get("model_modifiers") or [] if str(item).strip()),
        key=lambda item: (-len(item), item),
    )
    for modifier in modifiers:
        modifier_key = _model_key(modifier)
        if modifier_key and modifier_key not in model_key.split() and modifier_key not in model_key:
            model = f"{model} {display.get(modifier, modifier)}".strip()
            model_key = _model_key(model)
    return model


def _number(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    return int(float(match.group(0).replace(",", "."))) if match else None


def _storage(facts: dict[str, Any], details: dict[str, Any]) -> int | None:
    for key in ("storage_gb", "storage", "memory", "ssd"):
        value = facts.get(key)
        number = _number(value)
        if number is not None:
            if "тб" in str(value).lower() or "tb" in str(value).lower():
                number *= 1024
            return number
    return _number(details.get("storage_gb"))


def _size(facts: dict[str, Any], details: dict[str, Any], text: str) -> str:
    raw = str(facts.get("size") or details.get("size") or "")
    match = re.search(r"(?<!\d)(\d{2,3})\s*[xх×]\s*(\d{2,3})(?!\d)", f"{raw} {text}")
    return f"{match.group(1)}x{match.group(2)}" if match else ""


def _diagonal(facts: dict[str, Any], details: dict[str, Any]) -> int | None:
    for key in ("diagonal", "screen"):
        number = _number(facts.get(key))
        if number is not None:
            return number
    return _number(details.get("diagonal"))


def _refresh_rate(facts: dict[str, Any], details: dict[str, Any]) -> int | None:
    for key in ("refresh_rate", "hz"):
        number = _number(facts.get(key))
        if number is not None:
            return number
    return _number(details.get("refresh_rate"))


def _condition(facts: dict[str, Any], details: dict[str, Any], text: str) -> str:
    raw = normalize_text(str(facts.get("condition") or details.get("condition") or ""))
    combined = normalize_text(f"{raw} {text}")
    if re.search(r"(?<![a-zа-я0-9])(?:б\s*/?\s*у|бу|used|подержан|восстанов)(?![a-zа-я0-9])", combined):
        return "used"
    if re.search(r"(?<![a-zа-я0-9])(?:новый|новая|new)(?![a-zа-я0-9])", combined):
        return "new"
    return "any"


def _required_differences(
    request: dict[str, Any],
    candidate_details: dict[str, Any],
    facts: dict[str, Any],
    title: str,
) -> tuple[list[str], list[str]]:
    required = dict(request.get("required_criteria") or {})
    mismatches: list[str] = []
    missing: list[str] = []

    requested_storage = _number(required.get("storage_gb") or request.get("storage_gb"))
    if requested_storage is not None:
        actual = _storage(facts, candidate_details)
        if actual is None:
            missing.append(f"storage {requested_storage} ГБ не подтверждён")
        elif actual != requested_storage:
            mismatches.append(f"storage: нужен {requested_storage} ГБ, найден {actual} ГБ")

    requested_size = str(required.get("size") or request.get("size") or "")
    if requested_size:
        actual = _size(facts, candidate_details, title)
        if not actual:
            missing.append(f"размер {requested_size} не подтверждён")
        elif actual != requested_size:
            mismatches.append(f"размер: нужен {requested_size}, найден {actual}")

    requested_diagonal = _number(required.get("diagonal") or request.get("diagonal"))
    if requested_diagonal is not None:
        actual = _diagonal(facts, candidate_details)
        if actual is None:
            missing.append(f"диагональ {requested_diagonal} не подтверждена")
        elif actual != requested_diagonal:
            mismatches.append(f"диагональ: нужна {requested_diagonal}, найдена {actual}")

    requested_refresh = _number(required.get("refresh_rate") or request.get("refresh_rate"))
    if requested_refresh is not None:
        actual = _refresh_rate(facts, candidate_details)
        if actual is None:
            missing.append(f"частота {requested_refresh} Гц не подтверждена")
        elif actual < requested_refresh:
            mismatches.append(f"частота: нужно {requested_refresh} Гц, найдено {actual} Гц")

    request_condition = str(request.get("condition") or "any")
    actual_condition = _condition(facts, candidate_details, title)
    if request_condition in {"new", "used"} and actual_condition not in {"any", request_condition}:
        mismatches.append(f"состояние: нужно {request_condition}, найдено {actual_condition}")

    for key, label in (("color", "цвет"), ("sim_variant", "SIM/регион")):
        requested_value = normalize_text(str(required.get(key) or ""))
        if not requested_value:
            continue
        actual_value = normalize_text(str(facts.get(key) or ""))
        if not actual_value:
            missing.append(f"{label} {requested_value} не подтверждён")
        elif requested_value not in actual_value and actual_value not in requested_value:
            mismatches.append(f"{label}: нужен {requested_value}, найден {actual_value}")

    boolean_fields = {
        "wet_cleaning": ("влажная уборка", ("влажн", "моющ")),
        "lidar": ("лидар", ("лидар", "lidar")),
        "cappuccinator": ("капучинатор", ("капучинатор", "milk system", "молочн")),
        "lift_mechanism": ("подъёмный механизм", ("подъемн", "подъёмн")),
        "anc": ("ANC", ("anc", "шумоподав", "noise cancell")),
    }
    normalized_title = normalize_text(title)
    for key, (label, markers) in boolean_fields.items():
        if required.get(key) is not True:
            continue
        fact_value = facts.get(key)
        if fact_value is True or any(marker in normalized_title for marker in markers):
            continue
        missing.append(f"{label} не подтверждён")
    return mismatches, missing


def match_candidate(
    parsed_or_request: Any,
    candidate: Any,
    category: str | None = None,
) -> ExactMatchResult:
    request = _request_details(parsed_or_request)
    requested_category = category or str(request.get("category") or "unknown")
    title = _candidate_title(candidate)
    if not title:
        return ExactMatchResult(UNKNOWN, "пустой title", category=requested_category, confidence="none")
    if _is_accessory(requested_category, title):
        return ExactMatchResult(ACCESSORY, "аксессуар вместо основного товара", category=requested_category, confidence="high")

    identity = _identity_text(candidate)
    candidate_category = detect_category(identity)
    if (
        requested_category != "unknown"
        and candidate_category != "unknown"
        and candidate_category != requested_category
    ):
        difference = f"категория: нужна {requested_category}, найдена {candidate_category}"
        return ExactMatchResult(MODEL_MISMATCH, difference, (difference,), category=requested_category, confidence="high")

    candidate_details = parse_request_details(identity)
    facts = _facts(candidate)
    requested_model = _requested_model_identity(request)
    candidate_model = str(candidate_details.get("model") or facts.get("model") or facts.get("model_key") or "")

    mismatches, missing = _required_differences(request, candidate_details, facts, title)
    if mismatches:
        return ExactMatchResult(
            REQUIRED_SPEC_MISMATCH,
            mismatches[0],
            tuple(mismatches),
            requested_model,
            candidate_model,
            requested_category,
            "high",
        )

    if requested_model:
        if not candidate_model:
            reason = f"модель {requested_model} не подтверждена"
            return ExactMatchResult(
                UNKNOWN, reason, tuple(missing or [reason]), requested_model, "", requested_category, "low",
            )
        if _model_key(requested_model) != _model_key(candidate_model):
            difference = f"модель: нужна {requested_model}, найдена {candidate_model}"
            return ExactMatchResult(
                MODEL_MISMATCH, difference, (difference,), requested_model, candidate_model, requested_category, "high",
            )
        if missing:
            return ExactMatchResult(
                GENERIC_MATCH, missing[0], tuple(missing), requested_model, candidate_model, requested_category, "medium",
            )
        requested_storage = _number(request.get("storage_gb"))
        candidate_storage = _storage(facts, candidate_details)
        status = COMPATIBLE_VARIANT if requested_storage is None and candidate_storage is not None else EXACT
        return ExactMatchResult(
            status,
            "точная модель" if status == EXACT else "точная модель, допустимая комплектация",
            (),
            requested_model,
            candidate_model,
            requested_category,
            "high",
        )

    if candidate_category == "unknown" and requested_category != "unknown":
        return ExactMatchResult(UNKNOWN, "тип товара не подтверждён", tuple(missing), category=requested_category, confidence="low")
    if missing:
        return ExactMatchResult(
            GENERIC_MATCH, missing[0], tuple(missing), category=requested_category, confidence="medium",
        )
    if requested_category == "unknown":
        return ExactMatchResult(UNKNOWN, "неизвестная категория", category="unknown", confidence="low")
    return ExactMatchResult(GENERIC_MATCH, "категория и обязательные признаки совпадают", category=requested_category, confidence="medium")
