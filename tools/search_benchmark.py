"""Benchmark автопоиска по набору реальных запросов.

Запуск:
    python tools/search_benchmark.py
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

# Позволяет запускать файл напрямую из tools/.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.candidate_verifier import (
    PRICE_MISSING,
    REMOVED_LISTING,
    UNAVAILABLE,
    VERIFY_BLOCKED,
    detect_product_category,
)
from app.db import Request
from app.product_search import collect_product_candidates
from app.request_parser import full_parse


BENCHMARK_QUERIES = [
    "Нужен телевизор для PS5 до 65к в Ярославле",
    "Нужен телевизор для PS5 до 45к в Ярославле",
    "Нужен ноутбук для учёбы до 50к в Москве",
    "Нужен iPhone 13 б/у до 40к в Ярославле",
    "Нужны наушники до 10к",
    "Нужно офисное кресло для учёбы до 15к",
]


def _budget_value(parsed: dict[str, Any]) -> int | None:
    value = str(parsed.get("budget") or "").replace(" ", "")
    return int(value) if value.isdigit() else None


def _make_request(raw_query: str, request_id: int) -> tuple[Request, dict[str, Any]]:
    parsed = full_parse(raw_query)
    request = Request(
        id=request_id,
        user_id=0,
        username="benchmark",
        product=parsed.get("product_name", ""),
        **parsed,
    )
    return request, parsed


def _facts(candidate: Any) -> dict[str, Any]:
    facts = getattr(candidate, "product_facts", None) or {}
    if facts:
        return facts
    raw = getattr(candidate, "facts_json", "") or ""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _hidden_count_from_risks(candidates: list[Any]) -> int:
    total = 0
    for item in candidates:
        for risk in getattr(item, "risk_flags", []) or []:
            text = str(risk)
            marker = "скрыто дублей:"
            if marker not in text:
                continue
            tail = text.split(marker, 1)[1].strip()
            first = tail.split(" ", 1)[0]
            if first.isdigit():
                total += int(first)
    return total


def _in_budget_count(candidates: list[Any], budget: int | None) -> int:
    if not budget:
        return 0
    return sum(1 for item in candidates if getattr(item, "price", None) and item.price <= budget)


def _has_missing_price(candidates: list[Any]) -> bool:
    return any(getattr(item, "price", None) is None for item in candidates)


def _quality_label(saved_for_admin: int, has_missing_price: bool) -> str:
    if saved_for_admin >= 3 and not has_missing_price:
        return "GOOD"
    if saved_for_admin >= 1:
        return "OK"
    return "BAD"


def _facts_summary(facts: dict[str, Any]) -> str:
    if not facts:
        return "нет facts_json"
    parts = []
    for key in ("category", "model_key", "diagonal", "resolution", "refresh_rate", "matrix_type"):
        value = facts.get(key)
        if value:
            parts.append(f"{key}={value}")
    return "; ".join(parts) if parts else "facts есть, ключевые поля пустые"


def _bad_facts_count(candidates: list[Any], expected_category: str) -> int:
    tv_fields = ("diagonal", "resolution", "refresh_rate", "hdmi", "matrix_type")
    total = 0
    for item in candidates:
        facts = _facts(item)
        category = facts.get("category") or "unknown"
        if expected_category != "tv" and (category == "tv" or any(facts.get(field) for field in tv_fields)):
            total += 1
        elif category != "tv" and any(facts.get(field) for field in tv_fields):
            total += 1
    return total


def _print_top_candidates(candidates: list[Any]) -> None:
    if not candidates:
        print("  Топ-3: нет сохранённых кандидатов")
        return
    print("  Топ-3:")
    for index, item in enumerate(candidates[:3], 1):
        facts = _facts(item)
        warnings = facts.get("warnings") or []
        title = facts.get("model") or facts.get("model_key") or getattr(item, "title", "")
        price = getattr(item, "price", None)
        price_text = f"{price} ₽" if price else "цена не найдена"
        print(f"    {index}. {title}")
        print(f"       price: {price_text}")
        print(f"       source: {getattr(item, 'source', '')}")
        print(f"       saved_as: {getattr(item, 'verify_status', getattr(item, 'quality', '-'))}")
        print(f"       url: {getattr(item, 'url', '')}")
        print(f"       budget_status: {facts.get('budget_status') or '-'}")
        print(f"       availability: {facts.get('availability_text') or getattr(item, 'availability', '') or '-'}")
        print(f"       facts: {_facts_summary(facts)}")
        print(f"       warnings: {warnings if warnings else []}")


def _print_debug_reasons(collection: Any) -> None:
    rejections = getattr(collection, "verified_rejections", []) or []
    if not rejections:
        return
    print("  Причины debug:")
    for item in rejections[:6]:
        candidate = item.candidate
        reason = item.reason or ", ".join(getattr(candidate, "risk_flags", []) or []) or item.verify_status
        print(f"    - [{item.verify_status}] {getattr(candidate, 'source', '')}: {reason}")


def run_one(raw_query: str, index: int) -> dict[str, Any]:
    print("=" * 92)
    print(f"{index}. {raw_query}")
    request, parsed = _make_request(raw_query, index)
    budget = _budget_value(parsed)
    category = detect_product_category(request)
    print(
        f"  parsed: product={parsed.get('product_name')}; "
        f"category={category}; budget={parsed.get('budget')}; city={parsed.get('city')}"
    )

    try:
        collection = collect_product_candidates(request, max_results=15)
    except Exception as exc:
        print(f"  QUALITY: BAD")
        print(f"  ERROR: {type(exc).__name__}: {exc}")
        print("  traceback:")
        print("".join(traceback.format_exception_only(type(exc), exc)).strip())
        return {"quality": "BAD", "saved_for_admin": 0, "error": str(exc)}

    verify_stats = collection.verify_stats
    raw_candidates = collection.raw_candidates
    saved = collection.candidates
    hidden_duplicates = _hidden_count_from_risks(saved)
    has_missing_price = _has_missing_price(saved)
    quality = _quality_label(len(saved), has_missing_price)
    stats = {
        "parsed_product_name": parsed.get("product_name") or "",
        "category": category,
        "RAW": len(raw_candidates),
        "checked": verify_stats.get("checked", 0),
        "saved_for_admin": len(saved),
        "verified_good": verify_stats.get("VERIFIED_GOOD", 0),
        "verified_ok": verify_stats.get("VERIFIED_OK", 0),
        "need_manual_check": verify_stats.get(VERIFY_BLOCKED, 0),
        "verify_blocked": verify_stats.get(VERIFY_BLOCKED, 0),
        "blocked_by_site": verify_stats.get(VERIFY_BLOCKED, 0),
        "in_budget": _in_budget_count(saved, budget),
        "over_budget_soft": verify_stats.get("OVER_BUDGET_SOFT", 0),
        "over_budget_hard": verify_stats.get("OVER_BUDGET_HARD", 0),
        "price_missing_hidden": verify_stats.get(PRICE_MISSING, 0),
        "unavailable_hidden": verify_stats.get(UNAVAILABLE, 0),
        "not_product_page_hidden": verify_stats.get("NOT_PRODUCT_PAGE", 0),
        "removed_listing_hidden": verify_stats.get(REMOVED_LISTING, 0),
        "duplicates_hidden": hidden_duplicates,
        "verify_error": verify_stats.get("VERIFY_ERROR", 0),
        "bad_facts_count": _bad_facts_count(saved, category),
    }
    print(f"  QUALITY: {quality}")
    print("  Статистика:")
    for key, value in stats.items():
        print(f"    {key}: {value}")
    _print_top_candidates(saved)
    _print_debug_reasons(collection)
    return {"quality": quality, **stats}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    queries = sys.argv[1:] or BENCHMARK_QUERIES
    print("BENCHMARK АВТОПОИСКА")
    print(f"Запросов: {len(queries)}")
    print("БД не используется: collect_product_candidates() работает без записи.\n")

    results = []
    for index, query in enumerate(queries, 1):
        results.append(run_one(query, index))

    summary = {"GOOD": 0, "OK": 0, "BAD": 0}
    for result in results:
        summary[result.get("quality", "BAD")] = summary.get(result.get("quality", "BAD"), 0) + 1
    print("=" * 92)
    print("ИТОГ")
    print(f"  GOOD: {summary.get('GOOD', 0)}")
    print(f"  OK: {summary.get('OK', 0)}")
    print(f"  BAD: {summary.get('BAD', 0)}")
    print(f"  saved_for_admin total: {sum(int(item.get('saved_for_admin', 0)) for item in results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
