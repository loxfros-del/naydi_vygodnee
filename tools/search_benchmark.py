"""Benchmark автопоиска по набору реальных запросов.

Запуск:
    python tools/search_benchmark.py
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
from queue import Empty
import sys
import time
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# Позволяет запускать файл напрямую из tools/.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.candidate_verifier import (
    NEED_MANUAL_CHECK,
    PRICE_MISSING,
    REMOVED_LISTING,
    UNAVAILABLE,
    VERIFY_BLOCKED,
    detect_product_category,
)
from app.db import Request
from app.product_search import CITY_MISMATCH_RISK, LOW_PRICE_RISK, collect_product_candidates
from app.request_parser import full_parse
from app.search_cache import CACHE_VERSION, CacheLookup, SearchCache


BENCHMARK_QUERIES = [
    "Нужен смартфон Samsung Galaxy A55 256 ГБ до 35к в Москве",
    "Нужен телевизор 55 дюймов 4К до 45к в Ярославле",
    "Нужны беспроводные наушники с ANC до 12к",
    "Нужен ноутбук Ryzen 5 16 ГБ 512 ГБ до 55к в Москве",
    "Нужно офисное кресло с поясничной поддержкой до 15к",
    "Нужен iPhone 15 Pro 256 ГБ до 100к",
    "Нужен iPhone 13 б/у до 40к в Ярославле",
    "Нужен вертикальный пылесос до 20к для квартиры",
    "Нужен матрас 160x200 до 18к",
    "Нужна кровать 160x200 до 20к в Ярославле",
]


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    query: str


EXTENDED_CASES = (
    BenchmarkCase("samsung_a55", "Нужен смартфон Samsung Galaxy A55 256 ГБ до 35к в Москве"),
    BenchmarkCase("tv_55_4k", "Нужен телевизор 55 дюймов 4К до 45к в Ярославле"),
    BenchmarkCase("headphones_anc", "Нужны беспроводные наушники с ANC до 12к"),
    BenchmarkCase("laptop_ryzen_16_512", "Нужен ноутбук Ryzen 5 16 ГБ 512 ГБ до 55к в Москве"),
    BenchmarkCase("chair_lumbar", "Нужно офисное кресло с поясничной поддержкой до 15к"),
    BenchmarkCase("iphone_13_used", "Нужен iPhone 13 б/у до 40к в Ярославле"),
    BenchmarkCase("vacuum_vertical", "Нужен вертикальный пылесос до 20к для квартиры"),
    BenchmarkCase("mattress_160_200", "Нужен матрас 160x200 до 18к"),
    BenchmarkCase("bed_160_200", "Нужна кровать 160x200 до 20к в Ярославле"),
    BenchmarkCase("monitor_27_144", "Нужен монитор 27 дюймов 144 Гц до 30к"),
)
CORE_CASE_IDS = {
    "samsung_a55", "tv_55_4k", "headphones_anc", "laptop_ryzen_16_512", "chair_lumbar", "iphone_13_used",
}
SMOKE_CASES = (
    BenchmarkCase("smoke_robot_vacuum", "Нужен робот-пылесос с влажной уборкой до 25к"),
    BenchmarkCase("smoke_monitor_27", "Нужен монитор 27 дюймов 144 Гц до 30к"),
    BenchmarkCase("smoke_microwave", "Нужна микроволновка до 12к"),
)
ALL_CASES = {case.case_id: case for case in (*EXTENDED_CASES, *SMOKE_CASES)}
CACHE_STAGE = "benchmark_snapshot"
CACHE_SOURCE = "search_benchmark"


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


def _risk_flags(candidate: Any) -> list[str]:
    return [str(item) for item in (getattr(candidate, "risk_flags", []) or [])]


def _has_risk(candidate: Any, risk: str) -> bool:
    return risk in {item.strip() for item in _risk_flags(candidate)}


def _diag_text(value: Any) -> str:
    text = str(value or "").strip()
    return text if text else "-"


def _diag_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _fetch_diagnostics(candidate: Any) -> dict[str, Any]:
    return {
        "fetch_provider": _diag_text(getattr(candidate, "fetch_provider", "")),
        "fetch_status_code": _diag_int(getattr(candidate, "fetch_status_code", 0)),
        "blocked_reason": _diag_text(getattr(candidate, "blocked_reason", "")),
        "used_proxy": bool(getattr(candidate, "used_proxy", False)),
        "retry_count": _diag_int(getattr(candidate, "retry_count", 0)),
    }


def _print_fetch_diagnostics(candidate: Any, prefix: str = "       ") -> None:
    diagnostics = _fetch_diagnostics(candidate)
    for key in ("fetch_provider", "fetch_status_code", "blocked_reason", "used_proxy", "retry_count"):
        print(f"{prefix}{key}: {diagnostics[key]}")


def _verified_candidates(collection: Any) -> list[Any]:
    candidates = list(getattr(collection, "candidates", []) or [])
    for item in getattr(collection, "verified_rejections", []) or []:
        candidate = getattr(item, "candidate", None)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _fetch_stats(candidates: list[Any]) -> dict[str, Any]:
    providers: Counter[str] = Counter()
    blocked_reasons: Counter[str] = Counter()
    used_proxy = 0
    retry_count_total = 0

    for item in candidates:
        diagnostics = _fetch_diagnostics(item)
        provider = diagnostics["fetch_provider"]
        if provider == "http":
            providers["http_fetch"] += 1
        elif provider == "http_cache":
            providers["http_cache"] += 1
        if diagnostics["blocked_reason"] != "-":
            blocked_reasons[diagnostics["blocked_reason"]] += 1
        if diagnostics["used_proxy"]:
            used_proxy += 1
        retry_count_total += diagnostics["retry_count"]

    return {
        "used_proxy": used_proxy,
        "http_fetch": providers["http_fetch"],
        "http_cache": providers["http_cache"],
        "blocked_reasons": dict(blocked_reasons) if blocked_reasons else "-",
        "retry_count_total": retry_count_total,
    }


def _counter_attr(candidates: list[Any], attr: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in candidates:
        value = getattr(item, attr, "")
        if isinstance(value, bool):
            if value:
                counter[attr] += 1
            continue
        text = str(value or "").strip()
        if text:
            counter[text] += 1
    return dict(counter) if counter else {}


def _bool_attr_count(candidates: list[Any], attr: str) -> int:
    return sum(1 for item in candidates if bool(getattr(item, attr, False)))


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


def _has_price_count(candidates: list[Any]) -> int:
    return sum(1 for item in candidates if getattr(item, "price", None) is not None)


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
        verify_status = getattr(item, "verify_status", getattr(item, "quality", "-"))
        playwright_used = bool(getattr(item, "playwright_used", False))
        playwright_verified = bool(getattr(item, "playwright_verified", False))
        price_source = getattr(item, "price_source", "") or facts.get("price_source") or "-"
        price_reliability = getattr(item, "price_reliability", "") or "-"
        price_rejected_reason = getattr(item, "price_rejected_reason", "") or "-"
        price_from_budget_suspect = bool(getattr(item, "price_from_budget_suspect", False))
        bad_price_context = bool(getattr(item, "bad_price_context", False))
        score_cap_applied = getattr(item, "score_cap_applied", "") or "-"
        product_quality_level = getattr(item, "product_quality_level", "") or "-"
        brand_quality = getattr(item, "brand_quality", "") or "-"
        why_not_verified_good = getattr(item, "why_not_verified_good", "") or "-"
        score = float(getattr(item, "score", 0) or 0)
        risks = _risk_flags(item)
        classification = next((flag for flag in risks if "классификация:" in str(flag)), "-")
        print(f"    {index}. {title}")
        print(f"       score/rank: {score:.0f} / #{index}")
        print(f"       price: {price_text}")
        print(f"       source: {getattr(item, 'source', '')}")
        print(f"       verify_status: {verify_status}")
        print(f"       risk_flags: {risks}")
        print(f"       low_price_suspect: {_has_risk(item, LOW_PRICE_RISK)}")
        print(f"       city_mismatch: {_has_risk(item, CITY_MISMATCH_RISK)}")
        print(f"       playwright: used={playwright_used}; verified={playwright_verified}")
        _print_fetch_diagnostics(item)
        print(f"       price_source: {price_source}")
        print(f"       price_reliability: {price_reliability}")
        print(f"       price_rejected_reason: {price_rejected_reason}")
        print(f"       price_from_budget_suspect: {price_from_budget_suspect}")
        print(f"       bad_price_context: {bad_price_context}")
        print(f"       classification: {classification}")
        print(f"       product_quality_level: {product_quality_level}")
        print(f"       brand_quality: {brand_quality}")
        print(f"       why_not_verified_good: {why_not_verified_good}")
        print(f"       score_cap_applied: {score_cap_applied}")
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
        _print_fetch_diagnostics(candidate, prefix="      ")
        print(f"      price_reliability: {getattr(candidate, 'price_reliability', '') or '-'}")
        print(f"      price_rejected_reason: {getattr(candidate, 'price_rejected_reason', '') or '-'}")
        print(f"      price_from_budget_suspect: {bool(getattr(candidate, 'price_from_budget_suspect', False))}")
        print(f"      bad_price_context: {bool(getattr(candidate, 'bad_price_context', False))}")
        print(f"      score_cap_applied: {getattr(candidate, 'score_cap_applied', '') or '-'}")


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
    ranked_top = saved[:3]
    checked_candidates = _verified_candidates(collection)
    fetch_stats = _fetch_stats(checked_candidates)
    quality = _quality_label(len(saved), has_missing_price)
    saved_statuses = Counter(str(getattr(item, "verify_status", item.quality) or "") for item in saved)
    stats = {
        "parsed_product_name": parsed.get("product_name") or "",
        "category": category,
        "RAW": len(raw_candidates),
        "checked": verify_stats.get("checked", 0),
        "saved_for_admin": len(saved),
        "VERIFIED_GOOD": saved_statuses["VERIFIED_GOOD"],
        "VERIFIED_OK": saved_statuses["VERIFIED_OK"],
        "NEED_MANUAL_CHECK": saved_statuses[NEED_MANUAL_CHECK],
        "VERIFY_BLOCKED": saved_statuses[VERIFY_BLOCKED],
        "PRICE_MISSING saved": saved_statuses[PRICE_MISSING],
        "WRONG_PRODUCT": verify_stats.get("WRONG_PRODUCT", 0),
        "UNAVAILABLE": verify_stats.get(UNAVAILABLE, 0),
        "OVER_BUDGET_SOFT": verify_stats.get("OVER_BUDGET_SOFT", 0),
        "OVER_BUDGET_HARD": verify_stats.get("OVER_BUDGET_HARD", 0),
        "in_budget": _in_budget_count(saved, budget),
        "low_price_suspect": sum(1 for item in saved if _has_risk(item, LOW_PRICE_RISK)),
        "city_mismatch": sum(1 for item in saved if _has_risk(item, CITY_MISMATCH_RISK)),
        "ranked_top_has_price": _has_price_count(ranked_top),
        "ranked_top_in_budget": _in_budget_count(ranked_top, budget),
        "not_product_page_hidden": verify_stats.get("NOT_PRODUCT_PAGE", 0),
        "removed_listing_hidden": verify_stats.get(REMOVED_LISTING, 0),
        "duplicates_hidden": hidden_duplicates,
        "verify_error": verify_stats.get("VERIFY_ERROR", 0),
        "playwright_used": verify_stats.get("playwright_used", 0),
        "playwright_verified": verify_stats.get("playwright_verified", 0),
        "playwright_failed": verify_stats.get("playwright_failed", 0),
        "playwright_skipped": verify_stats.get("playwright_skipped", 0),
        "playwright_skipped_reason": verify_stats.get("playwright_skipped_reason", "-") or "-",
        "browser_provider": verify_stats.get("browser_provider", "-") or "-",
        "browser_provider_fallback_reason": verify_stats.get("browser_provider_fallback_reason", "-") or "-",
        "manual_check_after_playwright": verify_stats.get("manual_check_after_playwright", 0),
        "manual_check_saved_without_price": verify_stats.get("manual_check_saved_without_price", 0),
        "price_reliability": _counter_attr(checked_candidates, "price_reliability"),
        "price_rejected_reason": _counter_attr(checked_candidates, "price_rejected_reason"),
        "price_from_budget_suspect": _bool_attr_count(checked_candidates, "price_from_budget_suspect"),
        "bad_price_context": _bool_attr_count(checked_candidates, "bad_price_context"),
        "score_cap_applied": _counter_attr(checked_candidates, "score_cap_applied"),
        "used_proxy": fetch_stats["used_proxy"],
        "http_fetch": fetch_stats["http_fetch"],
        "http_cache": fetch_stats["http_cache"],
        "blocked_reasons": fetch_stats["blocked_reasons"],
        "retry_count_total": fetch_stats["retry_count_total"],
        "bad_facts_count": _bad_facts_count(saved, category),
    }
    print(f"  QUALITY: {quality}")
    print("  Статистика:")
    for key, value in stats.items():
        print(f"    {key}: {value}")
    _print_top_candidates(saved)
    _print_debug_reasons(collection)
    return {"quality": quality, **stats}


def legacy_main() -> int:
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


def _candidate_snapshot(candidate: Any) -> dict[str, Any]:
    fields = (
        "title", "url", "source", "price", "snippet", "score", "risk_flags", "status", "quality",
        "source_type", "origin", "rating", "reviews_count", "seller", "city", "availability",
        "product_facts", "facts_json", "price_source", "price_reliability", "price_rejected_reason",
        "price_from_budget_suspect", "bad_price_context", "score_cap_applied", "low_price_suspect",
        "external_source", "verify_status", "product_quality_level", "brand_quality", "why_not_verified_good",
    )
    return {field: getattr(candidate, field, None) for field in fields if getattr(candidate, field, None) is not None}


def _snapshot_collection(collection: Any, parsed: dict[str, Any], category: str) -> dict[str, Any]:
    now = int(time.time())
    attempts = [
        {
            "source": getattr(item, "source", ""),
            "query": getattr(item, "query", ""),
            "status": getattr(item, "status", ""),
            "found_count": getattr(item, "found_count", 0),
            "kept_count": getattr(item, "kept_count", 0),
            "error_text": getattr(item, "error_text", ""),
        }
        for item in getattr(collection, "attempts", [])
    ]
    return {
        "parsed": parsed,
        "category": category,
        "candidates": [_candidate_snapshot(item) for item in getattr(collection, "candidates", [])],
        "raw_candidates": [_candidate_snapshot(item) for item in getattr(collection, "raw_candidates", [])],
        "verify_stats": dict(getattr(collection, "verify_stats", {}) or {}),
        "quality_stats": dict(getattr(collection, "quality_stats", {}) or {}),
        "attempts": attempts,
        "source_data_timestamp": now,
    }


def _hydrate_collection(snapshot: dict[str, Any]) -> Any:
    return SimpleNamespace(
        candidates=[SimpleNamespace(**item) for item in snapshot.get("candidates", []) if isinstance(item, dict)],
        raw_candidates=[SimpleNamespace(**item) for item in snapshot.get("raw_candidates", []) if isinstance(item, dict)],
        verify_stats=dict(snapshot.get("verify_stats", {}) or {}),
        quality_stats=dict(snapshot.get("quality_stats", {}) or {}),
        attempts=[SimpleNamespace(**item) for item in snapshot.get("attempts", []) if isinstance(item, dict)],
        verified_rejections=[],
    )


def _snapshot_key(cache: SearchCache, request: Request, parsed: dict[str, Any]) -> str:
    return cache.make_cache_key(
        stage=CACHE_STAGE,
        source=CACHE_SOURCE,
        query=request.clean_search_query or request.original_query or request.product_name or request.product,
        category=detect_product_category(request),
        city=request.city,
        budget=request.budget,
        use_case=request.use_case or request.purpose,
        is_used_allowed=request.is_used_allowed,
    )


def cached_snapshot_only(cache: SearchCache, cache_key: str) -> CacheLookup:
    """Cached mode helper. It intentionally has no loader or network fallback."""
    return cache.get(cache_key)


def snapshot_or_load(
    cache: SearchCache,
    cache_key: str,
    *,
    mode: str,
    loader: Any,
) -> tuple[str, dict[str, Any] | None, CacheLookup | None]:
    """Testable mode switch: cached mode never evaluates ``loader``."""
    lookup = cached_snapshot_only(cache, cache_key)
    if mode == "cached":
        return ("CACHE_HIT", lookup.payload, lookup) if lookup.state == "HIT" else (lookup.state, None, lookup)
    if mode == "auto" and lookup.state == "HIT":
        return "CACHE_HIT", lookup.payload, lookup
    return "LIVE_REQUIRED", loader(), lookup


def _live_case_worker(case_id: str, query: str, sequence: int, output: Any) -> None:
    try:
        request, parsed = _make_request(query, sequence)
        category = detect_product_category(request)
        collection = collect_product_candidates(request, max_results=15)
        output.put({"ok": True, "case_id": case_id, "snapshot": _snapshot_collection(collection, parsed, category)})
    except Exception as exc:  # pragma: no cover - exercised through parent orchestration
        output.put({"ok": False, "case_id": case_id, "error": f"{type(exc).__name__}: {exc}"})


def _run_live_case(case: BenchmarkCase, sequence: int, timeout_seconds: int) -> tuple[str, dict[str, Any] | None, str]:
    context = mp.get_context("spawn")
    output = context.Queue(maxsize=1)
    process = context.Process(target=_live_case_worker, args=(case.case_id, case.query, sequence, output))
    process.start()
    process.join(max(1, timeout_seconds))
    if process.is_alive():
        process.terminate()
        process.join(5)
        output.close()
        output.join_thread()
        return "CASE_TIMEOUT", None, f"case exceeded {timeout_seconds}s"
    try:
        message = output.get(timeout=2)
    except Empty:
        message = {"ok": False, "error": f"worker exited with code {process.exitcode} without a result"}
    finally:
        output.close()
        output.join_thread()
    if not message.get("ok"):
        return "ERROR", None, str(message.get("error") or "live worker failed")
    return "DONE", message["snapshot"], ""


def _store_source_entries(cache: SearchCache, snapshot: dict[str, Any], request: Request) -> None:
    candidates_by_source: dict[str, list[dict[str, Any]]] = {}
    for candidate in snapshot.get("raw_candidates", []):
        if isinstance(candidate, dict):
            candidates_by_source.setdefault(str(candidate.get("source") or "unknown"), []).append(candidate)
    for attempt in snapshot.get("attempts", []):
        if not isinstance(attempt, dict):
            continue
        source = str(attempt.get("source") or "unknown")
        query = str(attempt.get("query") or request.clean_search_query or request.original_query)
        key = cache.make_cache_key(
            stage="source",
            source=source,
            query=query,
            category=str(snapshot.get("category") or ""),
            city=request.city,
            budget=request.budget,
            use_case=request.use_case or request.purpose,
            is_used_allowed=request.is_used_allowed,
        )
        payload = {
            "attempt": attempt,
            "candidates": candidates_by_source.get(source, []),
            "source_data_timestamp": snapshot.get("source_data_timestamp"),
        }
        cache.put(
            cache_key=key,
            stage="source",
            query=query,
            source=source,
            payload=payload,
            status=str(attempt.get("status") or "ERROR"),
            error_text=str(attempt.get("error_text") or ""),
        )


def _result_from_snapshot(case: BenchmarkCase, snapshot: dict[str, Any], cache_info: dict[str, Any]) -> dict[str, Any]:
    collection = _hydrate_collection(snapshot)
    parsed = dict(snapshot.get("parsed", {}) or {})
    saved = collection.candidates
    saved_statuses = Counter(str(getattr(item, "verify_status", "") or "") for item in saved)
    verify_stats = collection.verify_stats
    top: list[dict[str, Any]] = []
    for item in saved[:3]:
        facts = _facts(item)
        top.append({
            "title": getattr(item, "title", ""),
            "price": getattr(item, "price", None),
            "source": getattr(item, "source", ""),
            "status": getattr(item, "verify_status", ""),
            "score": round(float(getattr(item, "score", 0) or 0)),
            "product_quality_level": getattr(item, "product_quality_level", ""),
            "brand": facts.get("brand", ""),
            "flags": list(getattr(item, "risk_flags", []) or []),
            "why_not_verified_good": getattr(item, "why_not_verified_good", ""),
        })
    return {
        "case_id": case.case_id,
        "query": case.query,
        "parsed_product_name": parsed.get("product_name") or "",
        "category": snapshot.get("category") or "unknown",
        "saved_for_admin": len(saved),
        "VERIFIED_GOOD": saved_statuses["VERIFIED_GOOD"],
        "VERIFIED_OK": saved_statuses["VERIFIED_OK"],
        "NEED_MANUAL_CHECK": saved_statuses[NEED_MANUAL_CHECK],
        "VERIFY_BLOCKED": saved_statuses[VERIFY_BLOCKED],
        "PRICE_MISSING saved": saved_statuses[PRICE_MISSING],
        "WRONG_PRODUCT": int(verify_stats.get("WRONG_PRODUCT", 0) or 0),
        "UNAVAILABLE": int(verify_stats.get(UNAVAILABLE, 0) or 0),
        "OVER_BUDGET_SOFT": int(verify_stats.get("OVER_BUDGET_SOFT", 0) or 0),
        "OVER_BUDGET_HARD": int(verify_stats.get("OVER_BUDGET_HARD", 0) or 0),
        "cached_at": cache_info.get("cached_at"),
        "cache_age_seconds": cache_info.get("cache_age_seconds", 0),
        "is_stale": bool(cache_info.get("is_stale", False)),
        "source_data_timestamp": snapshot.get("source_data_timestamp"),
        "top": top,
    }


def _print_case_result(index: int, total: int, state: str, duration_ms: int, result: dict[str, Any] | None, error: str = "") -> None:
    if result is None:
        print(f"[{index}/{total}] {state} {duration_ms}ms {error}", flush=True)
        return
    print(
        f"[{index}/{total}] {state} {duration_ms}ms case_id={result['case_id']} "
        f"saved_for_admin={result['saved_for_admin']} cache_age={result.get('cache_age_seconds', 0)}s",
        flush=True,
    )
    print(
        f"  parsed_product_name={result['parsed_product_name']}; category={result['category']}; "
        f"GOOD={result['VERIFIED_GOOD']}; OK={result['VERIFIED_OK']}; "
        f"MANUAL={result['NEED_MANUAL_CHECK']}; BLOCKED={result['VERIFY_BLOCKED']}; "
        f"PRICE_MISSING={result['PRICE_MISSING saved']}",
        flush=True,
    )
    for rank, item in enumerate(result["top"], 1):
        price = f"{item['price']} ₽" if item["price"] else "price missing"
        print(
            f"  top-{rank}: {item['title']} | {price} | {item['source']} | {item['status']} | "
            f"score={item['score']} | quality={item['product_quality_level'] or '-'} | "
            f"brand={item['brand'] or '-'} | why={item['why_not_verified_good'] or '-'}",
            flush=True,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cached and resumable search benchmark")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--live", action="store_const", const="live", dest="mode")
    modes.add_argument("--cached", action="store_const", const="cached", dest="mode")
    modes.add_argument("--auto", action="store_const", const="auto", dest="mode")
    modes.add_argument("--refresh", action="store_const", const="refresh", dest="mode")
    modes.add_argument("--no-cache", action="store_const", const="no-cache", dest="mode")
    parser.set_defaults(mode="auto")
    parser.add_argument("--suite", choices=("smoke", "core", "extended"), default="extended")
    parser.add_argument("--case", dest="case_id")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume")
    parser.add_argument("--case-timeout", type=int, default=90)
    parser.add_argument("--cache-stats", action="store_true")
    parser.add_argument("--purge-expired", action="store_true")
    parser.add_argument("--cache-path", default="")
    return parser.parse_args()


def _select_cases(args: argparse.Namespace) -> list[BenchmarkCase]:
    if args.case_id:
        case = ALL_CASES.get(args.case_id)
        if case is None:
            raise ValueError(f"unknown case_id: {args.case_id}")
        cases = [case]
    elif args.suite == "smoke":
        cases = list(SMOKE_CASES)
    elif args.suite == "core":
        cases = [case for case in EXTENDED_CASES if case.case_id in CORE_CASE_IDS]
    else:
        cases = list(EXTENDED_CASES)
    return cases[:args.limit] if args.limit > 0 else cases


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args()
    cache = SearchCache(args.cache_path or None)
    if args.purge_expired:
        print(f"purged_expired={cache.purge_expired()}", flush=True)
        return 0
    if args.cache_stats:
        print(json.dumps(cache.cache_stats(), ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    try:
        cases = _select_cases(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", flush=True)
        return 2

    use_store = args.mode != "no-cache"
    if args.resume and not use_store:
        print("ERROR: --resume requires cache storage", flush=True)
        return 2
    if args.resume:
        if cache.get_benchmark_run(args.resume) is None:
            print(f"ERROR: unknown run_id: {args.resume}", flush=True)
            return 2
        run_id = cache.start_benchmark_run(mode=args.mode, total_cases=len(cases), run_id=args.resume)
        completed = cache.completed_case_ids(run_id)
    elif use_store:
        run_id = cache.start_benchmark_run(mode=args.mode, total_cases=len(cases))
        completed = set()
    else:
        run_id = "no-cache"
        completed = set()

    print(f"BENCHMARK run_id={run_id} mode={args.mode} suite={args.suite} cases={len(cases)}", flush=True)
    results: list[dict[str, Any]] = []
    network_calls = 0
    started_total = time.monotonic()
    try:
        for index, case in enumerate(cases, 1):
            if case.case_id in completed:
                saved_case = cache.get_benchmark_case(run_id, case.case_id)
                result = dict(saved_case.get("result") or {}) if saved_case else {}
                print(f"[{index}/{len(cases)}] SKIP completed case_id={case.case_id}", flush=True)
                if result:
                    results.append(result)
                continue
            print(f"[{index}/{len(cases)}] START case_id={case.case_id} {case.query}", flush=True)
            case_started_at = int(time.time())
            started = time.monotonic()
            request, parsed = _make_request(case.query, index)
            cache_key = _snapshot_key(cache, request, parsed)
            lookup = cached_snapshot_only(cache, cache_key) if use_store and args.mode not in {"live", "refresh"} else CacheLookup("CACHE_MISS")
            state = ""
            snapshot: dict[str, Any] | None = None
            error = ""
            cache_info: dict[str, Any] = {}
            if lookup.state == "HIT":
                state = "CACHE_HIT"
                snapshot = lookup.payload
                cache_info = {"cached_at": lookup.created_at, "cache_age_seconds": lookup.cache_age_seconds, "is_stale": lookup.is_stale}
            elif args.mode == "cached":
                state = lookup.state
                error = "cached mode does not call sources"
            else:
                state, snapshot, error = _run_live_case(case, index, args.case_timeout)
                network_calls += 1
                if state == "DONE" and snapshot is not None and use_store:
                    cache.put(
                        cache_key=cache_key,
                        stage=CACHE_STAGE,
                        query=request.clean_search_query or case.query,
                        source=CACHE_SOURCE,
                        payload=snapshot,
                        status="OK",
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                    _store_source_entries(cache, snapshot, request)
                    cache_info = {"cached_at": int(time.time()), "cache_age_seconds": 0, "is_stale": False}
                elif use_store:
                    snapshot = {
                        "parsed": parsed,
                        "category": detect_product_category(request),
                        "candidates": [],
                        "raw_candidates": [],
                        "verify_stats": {},
                        "quality_stats": {},
                        "attempts": [],
                        "source_data_timestamp": int(time.time()),
                        "case_status": state,
                        "error": error,
                    }
                    cache.put(
                        cache_key=cache_key,
                        stage=CACHE_STAGE,
                        query=request.clean_search_query or case.query,
                        source=CACHE_SOURCE,
                        payload=snapshot,
                        status=state,
                        duration_ms=int((time.monotonic() - started) * 1000),
                        error_text=error,
                    )
                    cache_info = {"cached_at": int(time.time()), "cache_age_seconds": 0, "is_stale": False}
            duration_ms = int((time.monotonic() - started) * 1000)
            result = _result_from_snapshot(case, snapshot, cache_info) if snapshot is not None else {
                "case_id": case.case_id,
                "query": case.query,
                "saved_for_admin": 0,
                "cache_state": state,
                "error": error,
            }
            _print_case_result(index, len(cases), state, duration_ms, result if snapshot is not None else None, error)
            if use_store:
                cache.save_benchmark_case(
                    run_id=run_id,
                    case_id=case.case_id,
                    query=case.query,
                    started_at=case_started_at,
                    finished_at=int(time.time()),
                    duration_ms=duration_ms,
                    status=state,
                    result=result,
                    error_text=error,
                )
            results.append(result)
    except KeyboardInterrupt:
        if use_store:
            cache.finish_benchmark_run(run_id, summary={"completed": len(results), "network_calls": network_calls}, interrupted=True)
        print(f"INTERRUPTED run_id={run_id}", flush=True)
        return 130

    total_ms = int((time.monotonic() - started_total) * 1000)
    summary = {
        "total_cases": len(cases),
        "completed_cases": len(results),
        "network_calls": network_calls,
        "duration_ms": total_ms,
        "saved_for_admin": sum(int(item.get("saved_for_admin", 0) or 0) for item in results),
    }
    if use_store:
        cache.finish_benchmark_run(run_id, summary=summary)
    print(f"SUMMARY {json.dumps(summary, ensure_ascii=False, sort_keys=True)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
