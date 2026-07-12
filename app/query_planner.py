"""Ограниченный и объяснимый план поисковых запросов."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

from app.category_registry import get_category_spec
from app.request_parser import parse_request_details
from app.search_links import build_search_query


@dataclass(frozen=True)
class QueryPlanItem:
    text: str
    priority: int
    reason: str
    kind: str
    direct_eligible: bool = False


def _value(request: Any, name: str, default: Any = "") -> Any:
    if isinstance(request, dict):
        return request.get(name, default)
    return getattr(request, name, default)


def _normalize_query(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip(" ,")


def _criteria_text(details: dict[str, Any]) -> str:
    values: list[str] = []
    criteria = {**details.get("required_criteria", {}), **details.get("desired_criteria", {})}
    for key, value in criteria.items():
        if value in (None, "", False):
            continue
        if key == "size":
            values.append(str(value))
        elif key == "diagonal":
            values.append(f"{value} дюймов")
        elif key == "refresh_rate":
            values.append(f"{value} Гц")
        elif key == "storage_gb":
            values.append(f"{value} ГБ")
        elif key == "ram_gb":
            values.append(f"RAM {value} ГБ")
        elif key == "ssd_gb":
            values.append(f"SSD {value} ГБ")
        elif key == "volume_l":
            if isinstance(value, list) and len(value) == 2:
                values.append(f"{value[0]}-{value[1]} литров")
            else:
                values.append(f"{value} литров")
        elif key in {"resolution", "cpu"}:
            values.append(str(value))
        elif key == "wet_cleaning":
            values.append("влажная уборка")
        elif key == "lidar":
            values.append("лидар")
        elif key == "cappuccinator":
            values.append("капучинатор")
        elif key == "lift_mechanism":
            values.append("подъёмный механизм")
        elif key == "anc":
            values.append("ANC")
        elif key == "wireless":
            values.append("беспроводные")
        elif key == "ips":
            values.append("IPS")
        elif key == "adaptive_sync":
            values.append("Adaptive Sync")
        elif key == "medium_firmness":
            values.append("средняя жёсткость")
        elif key == "automatic":
            values.append("автоматическая")
        elif key == "pet_hair":
            values.append("для шерсти животных")
    return " ".join(dict.fromkeys(values))


def _template_values(request: Any, details: dict[str, Any]) -> dict[str, str]:
    identity = str(details.get("model") or _value(request, "product_name") or _value(request, "product") or "товар")
    criteria = _criteria_text(details)
    volume = details.get("volume_l")
    if isinstance(volume, list) and len(volume) == 2:
        volume_text = f"{volume[0]}-{volume[1]} литров"
    else:
        volume_text = f"{volume} литров" if volume else ""
    return {
        "identity": identity,
        "criteria": criteria,
        "size": str(details.get("size") or ""),
        "diagonal": f"{details['diagonal']} дюймов" if details.get("diagonal") else "",
        "refresh_rate": f"{details['refresh_rate']} Гц" if details.get("refresh_rate") else "",
        "resolution": str(details.get("resolution") or ""),
        "storage": f"{details['storage_gb']} ГБ" if details.get("storage_gb") else "",
        "volume": volume_text,
    }


def _add_unique(items: list[QueryPlanItem], item: QueryPlanItem, seen: set[str]) -> None:
    text = _normalize_query(item.text)
    key = text.casefold()
    if not text or key in seen:
        return
    seen.add(key)
    items.append(QueryPlanItem(text, item.priority, item.reason, item.kind, item.direct_eligible))


def plan_search_queries(
    request: Any,
    *,
    max_queries: int = 14,
    site_domains: Iterable[str] = (),
) -> list[QueryPlanItem]:
    """Возвращает один main, до трёх category и feature variants плюс site queries."""
    if max_queries < 1:
        return []
    original = str(_value(request, "original_query") or _value(request, "clean_search_query") or "")
    details = parse_request_details(original)
    product = str(_value(request, "product_name") or _value(request, "product") or details.get("model") or "товар")
    budget = str(_value(request, "budget") or details.get("budget") or "")
    city = str(_value(request, "city") or details.get("city") or "")
    use_case = str(_value(request, "use_case") or _value(request, "purpose") or details.get("use_case") or "")
    old_criteria = str(_value(request, "important_criteria") or _value(request, "criteria") or "")
    main = str(_value(request, "clean_search_query") or "").strip() or build_search_query(
        product, use_case, budget, city, old_criteria,
    )

    result: list[QueryPlanItem] = []
    seen: set[str] = set()
    _add_unique(result, QueryPlanItem(main, 100, "main_request", "main", True), seen)

    category = str(details.get("category") or "unknown")
    spec = get_category_spec(category)
    values = _template_values(request, details)
    category_added = 0
    for template in spec.query_variants:
        if category_added >= 3:
            break
        query = _normalize_query(template.format_map(values))
        if budget and f"до {budget}" not in query:
            query = f"{query} до {budget}"
        before = len(result)
        _add_unique(
            result,
            QueryPlanItem(query, 80 - category_added, f"category_variant:{category}", "category", category_added == 0),
            seen,
        )
        if len(result) > before:
            category_added += 1

    feature_candidates: list[tuple[str, str]] = []
    identity = values["identity"]
    criteria = values["criteria"]
    brand = str(details.get("brand") or "")
    if brand:
        feature_candidates.append((f"{identity} {criteria}", "requested_brand_or_model"))
    elif category == "robot_vacuum":
        feature_candidates.append((f"Xiaomi Dreame Roborock robot vacuum {criteria}", "useful_brand_group"))
    if criteria:
        feature_candidates.append((f"{product} {criteria}", "required_features"))
    if use_case:
        feature_candidates.append((f"{product} для {use_case} {criteria}", "use_case"))

    feature_added = 0
    for query, reason in feature_candidates:
        if feature_added >= 3:
            break
        query = _normalize_query(query)
        if budget and f"до {budget}" not in query:
            query = f"{query} до {budget}"
        before = len(result)
        _add_unique(
            result,
            QueryPlanItem(query, 65 - feature_added, reason, "feature", feature_added == 0),
            seen,
        )
        if len(result) > before:
            feature_added += 1

    site_base = _normalize_query(f"{identity} {criteria}" if criteria else identity)
    if budget:
        site_base = f"{site_base} до {budget}"
    for offset, domain in enumerate(dict.fromkeys(str(item).lower().strip() for item in site_domains if str(item).strip())):
        _add_unique(
            result,
            QueryPlanItem(f"site:{domain} {site_base}", 40 - offset, f"site:{domain}", "site", False),
            seen,
        )

    result.sort(key=lambda item: (-item.priority, item.text.casefold()))
    return result[:max_queries]
