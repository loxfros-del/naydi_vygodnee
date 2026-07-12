"""Deterministic + cached quality evaluator. В cached mode сеть не вызывается."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.candidate_dedupe import candidate_keys
from app.category_facts import extract_category_facts
from app.category_quality import evaluate_category_quality
from app.category_registry import get_category_spec
from app.db import SearchResult
from app.exact_match import ACCESSORY, MODEL_MISMATCH, match_candidate
from app.price_extractor import extract_price
from app.query_planner import plan_search_queries
from app.ranking import rank_candidate
from app.request_parser import full_parse
from app.search_cache import SearchCache
from app.search_evidence import assess_product_card, assess_source_trust
from app.search_policy import NORMAL_STATUSES, normalize_for_admin_save, should_save_for_admin, summarize_saved_statuses
from tools.search_benchmark import (
    CORE_CASE_IDS,
    EXTENDED_CASES,
    QUALITY_V3_CASES,
    SMOKE_CASES,
    _make_request,
    preferred_snapshot,
)


WEIGHTS = {
    "parsing": 0.10,
    "links": 0.12,
    "price": 0.12,
    "exact": 0.14,
    "category": 0.16,
    "ranking": 0.16,
    "admin": 0.10,
    "performance": 0.10,
}


PRICE_AUDIT_CASES = (
    ("49 999 ₽", 49_999), ("цена 45999", 45_999), ("стоимость 8 990 руб.", 8_990),
    ("SSD 1024 ГБ", None), ("RAM 16 ГБ SSD 512 ГБ", None), ("1920x1080 144 Гц", None),
    ("10000 mAh", None), ("нагрузка 120 кг", None), ("артикул 123456", None),
    ("до 35000", None), ("модель 2026 года", None), ("матрас 160x200", None),
    ("Купить ноутбук 59990", 59_990), ("45к", 45_000), ("49999", None),
)

PRODUCT_CARD_CASES = (
    ({"title": "Samsung Galaxy A55", "url": "https://shop.test/product/100", "price": 35_000, "source": "direct_retail", "price_source": "direct_store"}, True),
    ({"title": "Wildberries товар", "url": "https://www.wildberries.ru/catalog/123456/detail.aspx", "price": 10_000, "source": "wildberries"}, True),
    ({"title": "Ozon товар", "url": "https://www.ozon.ru/product/item-123456/", "price": 10_000, "source": "ozon_search"}, True),
    ({"title": "Avito товар", "url": "https://www.avito.ru/moskva/telefony/iphone_123456", "price": 50_000, "source": "avito_search"}, True),
    ({"title": "Обзор лучших ноутбуков 2026", "url": "https://site.test/article/best-laptops", "price": None, "source": "generic_web"}, False),
    ({"title": "Каталог ноутбуков", "url": "https://shop.test/catalog/laptops", "price": None, "source": "generic_web"}, False),
    ({"title": "Результаты поиска", "url": "https://shop.test/search?q=phone", "price": None, "source": "generic_web"}, False),
    ({"title": "Инструкция PDF", "url": "https://shop.test/manual/device.pdf", "price": None, "source": "generic_web"}, False),
    ({"title": "Отзывы покупателей", "url": "https://shop.test/reviews/device", "price": None, "source": "generic_web"}, False),
    ({"title": "Samsung brand", "url": "https://shop.test/brand/samsung", "price": None, "source": "generic_web"}, False),
)

FACT_AUDIT_CASES = (
    ("phone", "Samsung Galaxy A55 256 ГБ", {"brand": "Samsung", "storage_gb": 256}),
    ("laptop", "LUNNEN Ground Ryzen 5 RAM 16 ГБ SSD 512 ГБ", {"brand": "LUNNEN", "cpu": "RYZEN 5", "ram": "16 ГБ"}),
    ("laptop", "HP 250 G10 Core i5", {"brand": "HP"}),
    ("tv", "Hisense TV 55 дюймов 4K 120 Гц QLED", {"brand": "Hisense", "diagonal": "55", "resolution": "4K"}),
    ("monitor", "AOC монитор 27 дюймов QHD IPS 144 Гц", {"brand": "AOC", "diagonal": "27", "refresh_rate": "144 Гц"}),
    ("headphones", "Sony WH-1000XM5 беспроводные ANC", {"brand": "Sony", "connection": "wireless", "anc": True}),
    ("robot_vacuum", "Dreame робот-пылесос с лидаром и влажной уборкой", {"brand": "Dreame", "lidar": True, "wet_cleaning": True}),
    ("microwave", "Samsung микроволновка 23 л 800 Вт", {"brand": "Samsung", "volume": "23 л", "power": "800 Вт"}),
    ("coffee_machine", "Philips автоматическая кофемашина с капучинатором", {"brand": "Philips", "machine_type": "automatic", "cappuccinator": True}),
    ("mattress", "Askona матрас 160x200 средней жёсткости", {"brand": "Askona", "size": "160x200", "firmness": "средняя"}),
    ("bed", "Askona кровать 160x200 с подъёмным механизмом", {"brand": "Askona", "size": "160x200", "lift_mechanism": True}),
    ("chair", "Chairman кресло с поясничной поддержкой", {"brand": "Chairman", "lumbar_support": True}),
)


def _rate(numerator: int | float, denominator: int | float) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _score(rate: float) -> float:
    return round(max(0.0, min(1.0, rate)) * 10.0, 2)


def _normalize_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip().casefold().replace("ё", "е")


def _criteria_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return all(_criteria_match((actual or {}).get(key), value) for key, value in expected.items())
    return _normalize_scalar(actual) == _normalize_scalar(expected)


def _load_golden() -> dict[str, Any]:
    path = ROOT / "tools" / "golden_cases.json"
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data.get("queries"), list) or len(data["queries"]) < 40:
        raise ValueError("golden_cases.json must contain at least 40 queries")
    if not isinstance(data.get("synthetic_candidates"), list):
        raise ValueError("golden_cases.json must contain synthetic_candidates")
    return data


def audit_parsing(cases: list[dict[str, Any]]) -> tuple[dict[str, float], list[str]]:
    counters = Counter()
    failures: list[str] = []
    for case in cases:
        parsed = full_parse(str(case.get("query") or ""))
        checks: list[bool] = []
        expected_category = case.get("expected_category", case.get("category"))
        if expected_category is not None:
            ok = parsed.get("category") == expected_category
            counters["category_total"] += 1
            counters["category_ok"] += int(ok)
            checks.append(ok)
        if "budget" in case:
            expected_budget = "" if case.get("budget") is None else str(case.get("budget"))
            ok = str(parsed.get("budget") or "") == expected_budget
            counters["budget_total"] += 1
            counters["budget_ok"] += int(ok)
            checks.append(ok)
        if "city" in case:
            ok = _normalize_scalar(parsed.get("city")) == _normalize_scalar(case.get("city"))
            counters["city_total"] += 1
            counters["city_ok"] += int(ok)
            checks.append(ok)
        expected_criteria = dict(case.get("required_criteria") or {})
        storage_key = "ssd_gb" if expected_category == "laptop" else "storage_gb"
        for target, parsed_key in ((case.get("expected_size"), "size"), (case.get("expected_storage"), storage_key), (case.get("expected_diagonal"), "diagonal")):
            if target is not None:
                expected_criteria.setdefault(parsed_key, target)
        if expected_criteria:
            ok = _criteria_match(parsed.get("required_criteria") or parsed, expected_criteria)
            counters["criteria_total"] += 1
            counters["criteria_ok"] += int(ok)
            checks.append(ok)
        if "expected_model_modifiers" in case:
            expected_modifiers = set(case.get("expected_model_modifiers") or [])
            ok = expected_modifiers.issubset(set(parsed.get("model_modifiers") or []))
            counters["model_total"] += 1
            counters["model_ok"] += int(ok)
            checks.append(ok)
        counters["full_total"] += 1
        full_ok = all(checks) if checks else True
        counters["full_ok"] += int(full_ok)
        if not full_ok:
            failures.append(str(case.get("id")))

    planner_ok = 0
    for case in cases:
        parsed = full_parse(str(case.get("query") or ""))
        request, _ = _make_request(str(case.get("query") or ""), 0)
        plan = plan_search_queries(request, max_queries=14, site_domains=("dns-shop.ru", "mvideo.ru"))
        unique = len({item.text.casefold() for item in plan}) == len(plan)
        bounded = len(plan) <= 14 and sum(item.kind == "main" for item in plan) == 1
        explained = all(item.reason and item.priority for item in plan)
        brand = str(parsed.get("brand") or "").casefold()
        no_aggressive_brand = not brand or not any(
            group in " ".join(item.text.casefold() for item in plan)
            for group in ("xiaomi dreame roborock", "sony jbl qcy")
        )
        planner_ok += int(unique and bounded and explained and no_aggressive_brand)

    metrics = {
        "category_accuracy": _rate(counters["category_ok"], counters["category_total"]),
        "budget_accuracy": _rate(counters["budget_ok"], counters["budget_total"]),
        "city_accuracy": _rate(counters["city_ok"], counters["city_total"]),
        "model_criteria_accuracy": _rate(counters["criteria_ok"] + counters["model_ok"], counters["criteria_total"] + counters["model_total"]),
        "full_parse_accuracy": _rate(counters["full_ok"], counters["full_total"]),
        "query_planner_accuracy": _rate(planner_ok, len(cases)),
    }
    return metrics, failures


def _make_candidate(row: dict[str, Any], parsed: dict[str, Any], index: int) -> dict[str, Any]:
    category = str(parsed.get("category") or "unknown")
    title = str(row.get("title") or "")
    facts = extract_category_facts(category, title, title)
    availability = str(row.get("availability") or "UNKNOWN")
    facts.update({
        "availability_text": availability,
        "available": True if availability == "AVAILABLE" else False if availability == "UNAVAILABLE" else None,
    })
    candidate = {
        "title": title,
        "url": str(row.get("url") or f"https://shop.example/product/{index + 10000}"),
        "source": str(row.get("source") or "direct_retail"),
        "price": row.get("price"),
        "price_source": str(row.get("price_source") or "direct_store"),
        "verify_status": str(row.get("status") or "VERIFIED_GOOD"),
        "status": str(row.get("status") or "VERIFIED_GOOD"),
        "availability": availability,
        "product_facts": facts,
        "risk_flags": [],
    }
    quality = evaluate_category_quality(parsed, candidate)
    candidate["product_quality_level"] = quality.level
    candidate["category_quality_score"] = quality.score
    exact = match_candidate(parsed, candidate)
    candidate["exact_match_status"] = exact.status
    candidate["exact_match_reason"] = exact.reason
    return candidate


def audit_synthetic(suites: list[dict[str, Any]]) -> tuple[dict[str, float], list[str]]:
    counters = Counter()
    failures: list[str] = []
    for suite in suites:
        parsed = full_parse(str(suite.get("query") or ""))
        ranked: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
        for index, row in enumerate(suite.get("candidates") or []):
            candidate = _make_candidate(row, parsed, index)
            exact = match_candidate(parsed, candidate)
            counters["exact_total"] += 1
            counters["exact_ok"] += int(exact.status == row.get("expected_exact"))
            if row.get("accessory"):
                counters["accessory_total"] += 1
                counters["accessory_rejected"] += int(exact.status == ACCESSORY)

            policy = normalize_for_admin_save({
                **candidate,
                "reasons": [exact.reason],
                "budget_status": "",
            })
            saved = should_save_for_admin(policy)
            expected_trash = exact.status in {MODEL_MISMATCH, ACCESSORY} or policy.get("verify_status") in {"UNAVAILABLE", "OVER_BUDGET_HARD", "NOT_PRODUCT_PAGE"}
            if expected_trash:
                counters["trash_total"] += 1
                counters["trash_rejected"] += int(not saved)
            if row.get("relevant") and str(row.get("status")) in {"NEED_MANUAL_CHECK", "VERIFY_BLOCKED", "PRICE_MISSING"}:
                counters["manual_total"] += 1
                counters["manual_saved"] += int(saved)
            status = str(policy.get("verify_status") or "")
            consistent = status not in NORMAL_STATUSES or (
                candidate.get("price") is not None
                and candidate.get("product_quality_level") not in {"weak", "bad"}
                and assess_product_card(candidate).confidence in {"high", "medium"}
            )
            counters["status_total"] += 1
            counters["status_ok"] += int(consistent)
            if saved:
                candidate["verify_status"] = status
                candidate["status"] = status
                result = rank_candidate(parsed, candidate)
                ranked.append((result.score, candidate, row))

        ranked.sort(key=lambda item: (-item[0], item[1]["title"]))
        top = ranked[:3]
        expected_top = str(suite.get("expected_top_title") or "")
        top1_ok = bool(top and top[0][1]["title"] == expected_top)
        top3_ok = any(bool(item[2].get("relevant")) for item in top)
        counters["top1_total"] += 1
        counters["top1_ok"] += int(top1_ok)
        counters["top3_total"] += 1
        counters["top3_ok"] += int(top3_ok)
        counters["wrong_top3"] += sum(not bool(item[2].get("relevant")) for item in top)
        counters["top3_items"] += len(top)
        if not top1_ok:
            failures.append(str(suite.get("id")))

        relevant_good = [item for item in ranked if item[2].get("relevant") and item[1].get("verify_status") in NORMAL_STATUSES]
        relevant_manual = [item for item in ranked if item[2].get("relevant") and item[1].get("verify_status") not in NORMAL_STATUSES]
        if relevant_good and relevant_manual:
            counters["good_manual_total"] += 1
            counters["good_above_manual"] += int(max(item[0] for item in relevant_good) > max(item[0] for item in relevant_manual))

    metrics = {
        "exact_match_precision": _rate(counters["exact_ok"], counters["exact_total"]),
        "accessory_rejection_rate": _rate(counters["accessory_rejected"], counters["accessory_total"]),
        "relevant_top1": _rate(counters["top1_ok"], counters["top1_total"]),
        "relevant_top3": _rate(counters["top3_ok"], counters["top3_total"]),
        "wrong_product_top3_rate": _rate(counters["wrong_top3"], counters["top3_items"]),
        "good_above_manual_rate": _rate(counters["good_above_manual"], counters["good_manual_total"]) if counters["good_manual_total"] else 1.0,
        "manual_preservation_rate": _rate(counters["manual_saved"], counters["manual_total"]) if counters["manual_total"] else 1.0,
        "trash_rejection_rate": _rate(counters["trash_rejected"], counters["trash_total"]),
        "status_consistency": _rate(counters["status_ok"], counters["status_total"]),
    }
    return metrics, failures


def audit_deterministic_misc() -> dict[str, float]:
    price_ok = sum(extract_price(text) == expected for text, expected in PRICE_AUDIT_CASES)
    price_false_positive = sum(expected is None and extract_price(text) is not None for text, expected in PRICE_AUDIT_CASES)
    card_ok = sum(assess_product_card(candidate).is_product_card == expected for candidate, expected in PRODUCT_CARD_CASES)
    predicted_cards = [candidate for candidate, _ in PRODUCT_CARD_CASES if assess_product_card(candidate).is_product_card]
    true_predicted = sum(expected for candidate, expected in PRODUCT_CARD_CASES if assess_product_card(candidate).is_product_card)
    facts_ok = 0
    fact_fields = 0
    for category, title, expected in FACT_AUDIT_CASES:
        facts = extract_category_facts(category, title, title)
        for key, value in expected.items():
            fact_fields += 1
            facts_ok += int(facts.get(key) == value)
    return {
        "valid_price_rate": _rate(price_ok, len(PRICE_AUDIT_CASES)),
        "price_false_positive_rate": _rate(price_false_positive, sum(expected is None for _, expected in PRICE_AUDIT_CASES)),
        "product_card_classification_accuracy": _rate(card_ok, len(PRODUCT_CARD_CASES)),
        "product_card_precision": _rate(true_predicted, len(predicted_cards)),
        "category_fact_accuracy": _rate(facts_ok, fact_fields),
    }


def _suite_cases(name: str) -> list[Any]:
    if name == "quality_v3":
        return list(QUALITY_V3_CASES)
    if name == "smoke":
        return list(SMOKE_CASES)
    if name == "core":
        return [case for case in EXTENDED_CASES if case.case_id in CORE_CASE_IDS]
    return list(EXTENDED_CASES)


def _cached_candidates(snapshot: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return [item for item in snapshot.get(key, []) if isinstance(item, dict)]


def audit_cached(cache: SearchCache, suite_name: str) -> tuple[dict[str, float], list[dict[str, Any]], list[str]]:
    cases = _suite_cases(suite_name)
    counters = Counter()
    case_rows: list[dict[str, Any]] = []
    failures: list[str] = []
    latencies: list[float] = []
    for index, case in enumerate(cases, 1):
        request, parsed = _make_request(case.query, index)
        started = time.perf_counter()
        snapshot_status, lookup, _ = preferred_snapshot(cache, request, parsed)
        latency_ms = (time.perf_counter() - started) * 1000
        latencies.append(latency_ms)
        hit = lookup.state == "HIT" and isinstance(lookup.payload, dict)
        row = {
            "case_id": case.case_id,
            "state": "CACHE_HIT" if hit else lookup.state,
            "snapshot_status": snapshot_status,
            "duration_ms": round(latency_ms, 2),
            "saved_for_admin": 0,
            "top1": "",
            "error": "" if hit else "CACHE_MISS",
        }
        counters["cases"] += 1
        counters["hits"] += int(hit)
        if not hit:
            failures.append(case.case_id)
            case_rows.append(row)
            continue

        snapshot = lookup.payload
        saved = _cached_candidates(snapshot, "candidates")
        raw = _cached_candidates(snapshot, "raw_candidates")
        top = saved[:3]
        row["saved_for_admin"] = len(saved)
        row["top1"] = str(top[0].get("title") or "") if top else ""
        status = str(snapshot.get("snapshot_status") or snapshot_status or "")
        row["snapshot_status"] = status
        counters["success_like"] += int(status in {"SUCCESS", "PARTIAL_SUCCESS"})
        counters["timeout_error"] += int(status in {"CASE_TIMEOUT", "ERROR"})
        counters["saved_nonempty"] += int(bool(saved))

        top_relevance: list[bool] = []
        used_sources: set[str] = set()
        seen_keys: set[str] = set()
        for item in top:
            card = assess_product_card(item)
            exact = match_candidate(parsed, item)
            relevant = card.is_product_card and exact.status not in {MODEL_MISMATCH, ACCESSORY}
            top_relevance.append(relevant)
            counters["top_items"] += 1
            counters["product_cards"] += int(card.is_product_card)
            counters["exact_relevant"] += int(exact.status not in {MODEL_MISMATCH, ACCESSORY})
            counters["wrong_top"] += int(not relevant)
            price = item.get("price")
            if price not in (None, "", 0):
                counters["priced"] += 1
                try:
                    value = int(price)
                except (TypeError, ValueError):
                    value = 0
                counters["valid_prices"] += int(get_category_spec(str(parsed.get("category") or "unknown")).minimum_plausible_price <= value <= 10_000_000)
                budget = int(parsed["budget"]) if str(parsed.get("budget") or "").isdigit() else None
                counters["budgeted_items"] += int(budget is not None)
                counters["in_budget_items"] += int(budget is not None and value <= budget)
            facts = item.get("product_facts") if isinstance(item.get("product_facts"), dict) else {}
            counters["fact_items"] += 1
            counters["fact_category_ok"] += int(str(facts.get("category") or snapshot.get("category") or "unknown") == str(parsed.get("category") or "unknown"))
            availability = str(item.get("availability") or facts.get("availability_text") or "").upper()
            counters["availability_items"] += 1
            counters["available_items"] += int(availability == "AVAILABLE" or facts.get("available") is True)
            used_sources.add(str(item.get("source") or ""))
            keys = set(candidate_keys(item))
            counters["duplicates"] += int(bool(keys & seen_keys))
            seen_keys.update(keys)
        counters["top1_cases"] += int(bool(top))
        counters["top1_relevant"] += int(bool(top_relevance and top_relevance[0]))
        counters["top3_cases"] += 1
        counters["top3_relevant"] += int(any(top_relevance))
        counters["source_slots"] += min(3, len(top))
        counters["distinct_sources"] += len(used_sources)

        saved_keys = {str(item.get("url") or item.get("title") or "") for item in saved}
        accessories = [item for item in raw if match_candidate(parsed, item).status == ACCESSORY]
        counters["raw_accessories"] += len(accessories)
        counters["rejected_accessories"] += sum(str(item.get("url") or item.get("title") or "") not in saved_keys for item in accessories)

        normal_consistent = all(
            str(item.get("verify_status") or "") not in NORMAL_STATUSES
            or (
                item.get("price") not in (None, "", 0)
                and str(item.get("product_quality_level") or "") not in {"weak", "bad"}
            )
            for item in saved
        )
        counters["status_cases"] += 1
        counters["status_consistent"] += int(normal_consistent)
        case_rows.append(row)

    metrics = {
        "cache_coverage": _rate(counters["hits"], counters["cases"]),
        "cached_latency_ms": statistics.mean(latencies) if latencies else 0.0,
        "network_calls": 0.0,
        "success_partial_rate": _rate(counters["success_like"], counters["hits"]),
        "timeout_error_rate": _rate(counters["timeout_error"], counters["hits"]),
        "saved_nonempty_rate": _rate(counters["saved_nonempty"], counters["hits"]),
        "product_card_precision_cached": _rate(counters["product_cards"], counters["top_items"]),
        "exact_match_precision_cached": _rate(counters["exact_relevant"], counters["top_items"]),
        "accessory_rejection_cached": _rate(counters["rejected_accessories"], counters["raw_accessories"]) if counters["raw_accessories"] else 1.0,
        "valid_price_rate_cached": _rate(counters["valid_prices"], counters["priced"]) if counters["priced"] else 0.0,
        "in_budget_top3_rate": _rate(counters["in_budget_items"], counters["budgeted_items"]) if counters["budgeted_items"] else 0.0,
        "availability_rate": _rate(counters["available_items"], counters["availability_items"]),
        "category_fact_accuracy_cached": _rate(counters["fact_category_ok"], counters["fact_items"]),
        "duplicate_rate": _rate(counters["duplicates"], counters["top_items"]),
        "relevant_top1_cached": _rate(counters["top1_relevant"], counters["top1_cases"]),
        "relevant_top3_cached": _rate(counters["top3_relevant"], counters["top3_cases"]),
        "wrong_product_top3_cached": _rate(counters["wrong_top"], counters["top_items"]),
        "source_diversity_top3": _rate(counters["distinct_sources"], counters["source_slots"]),
        "status_consistency_cached": _rate(counters["status_consistent"], counters["status_cases"]),
    }
    return metrics, case_rows, failures


def audit_telegram() -> tuple[float, list[str]]:
    failures: list[str] = []
    checks = 0
    passed = 0
    try:
        from app.handlers.admin import _telegram_chunks, format_search_result_card, format_search_result_summary
        facts = json.dumps({
            "verify_status": "VERIFIED_GOOD", "budget_status": "IN_BUDGET", "availability_text": "AVAILABLE",
            "category": "monitor", "diagonal": "27", "resolution": "QHD", "refresh_rate": "144 Гц",
            "score_breakdown": {"relevance": 15, "model_match": 10}, "price_confidence": "high",
        }, ensure_ascii=False)
        result = SearchResult(
            id=1, request_id=1, title="AOC <Q27> & test " + "X" * 350, price=34_990,
            source="direct_retail", url="https://shop.example/product/1", score=91,
            risk_flags=json.dumps(["причина 1", "причина 2", "причина 3", "лишняя причина"], ensure_ascii=False),
            status="CANDIDATE", facts_json=facts,
        )
        card = format_search_result_card(result, 1)
        summary = format_search_result_summary(result, 1)
        chunks = _telegram_chunks("A" * 9000)
        assertions = (
            ("html_escape", "<Q27>" not in card and "&lt;Q27&gt;" in card),
            ("card_length", len(card) < 4096),
            ("summary_length", len(summary) < 1200),
            ("chunks", bool(chunks) and all(len(chunk) <= 3900 for chunk in chunks)),
            ("normal_counter", NORMAL_STATUSES == {"VERIFIED_GOOD", "VERIFIED_OK"}),
        )
        for name, ok in assertions:
            checks += 1
            passed += int(ok)
            if not ok:
                failures.append(name)
    except Exception as exc:
        failures.append(f"telegram_import_or_render:{exc}")
        checks = max(checks, 1)
    return _rate(passed, checks), failures


def calculate_scores(
    parsing: dict[str, float],
    synthetic: dict[str, float],
    misc: dict[str, float],
    cached: dict[str, float],
    telegram_rate: float,
) -> tuple[dict[str, float], dict[str, float], float, bool]:
    parsing_rate = statistics.mean([
        parsing["category_accuracy"], parsing["budget_accuracy"], parsing["city_accuracy"],
        parsing["model_criteria_accuracy"], parsing["full_parse_accuracy"],
    ])
    query_rate = parsing["query_planner_accuracy"]
    cached_has_rows = cached.get("cache_coverage", 0.0) > 0
    links_rate = misc["product_card_precision"]
    if cached_has_rows:
        links_rate = 0.6 * links_rate + 0.4 * cached["product_card_precision_cached"]
    price_rate = 1.0 - misc["price_false_positive_rate"]
    price_rate = min(price_rate, misc["valid_price_rate"])
    if cached_has_rows and cached.get("valid_price_rate_cached", 0.0):
        price_rate = 0.75 * price_rate + 0.25 * cached["valid_price_rate_cached"]
    exact_rate = 0.8 * synthetic["exact_match_precision"] + 0.2 * synthetic["accessory_rejection_rate"]
    if cached_has_rows:
        exact_rate = 0.75 * exact_rate + 0.25 * cached["exact_match_precision_cached"]
    availability_rate = cached.get("availability_rate", 0.0) if cached_has_rows else 0.8
    facts_rate = misc["category_fact_accuracy"]
    if cached_has_rows:
        facts_rate = 0.75 * facts_rate + 0.25 * cached["category_fact_accuracy_cached"]
    category_quality_rate = statistics.mean([facts_rate, availability_rate, synthetic["trash_rejection_rate"]])
    ranking_rate = statistics.mean([
        synthetic["relevant_top1"], synthetic["relevant_top3"], synthetic["good_above_manual_rate"],
        1.0 - synthetic["wrong_product_top3_rate"],
    ])
    if cached_has_rows:
        ranking_rate = 0.65 * ranking_rate + 0.35 * statistics.mean([
            cached["relevant_top1_cached"], cached["relevant_top3_cached"],
            1.0 - cached["wrong_product_top3_cached"], cached["in_budget_top3_rate"],
        ])
    dedupe_rate = 1.0 - cached.get("duplicate_rate", 0.0) if cached_has_rows else 1.0
    source_rate = cached.get("source_diversity_top3", 0.0) if cached_has_rows else 0.8
    admin_rate = statistics.mean([
        synthetic["manual_preservation_rate"], synthetic["trash_rejection_rate"], synthetic["status_consistency"],
        cached.get("status_consistency_cached", 1.0) if cached_has_rows else 1.0,
    ])
    latency = cached.get("cached_latency_ms", 0.0)
    latency_rate = 1.0 if latency <= 1000 else 0.9 if latency <= 3000 else 0.7 if latency <= 10_000 else 0.4
    performance_rate = (
        0.5 * cached.get("cache_coverage", 0.0)
        + 0.25 * latency_rate
        + 0.25 * cached.get("success_partial_rate", 0.0)
    )

    directions = {
        "parsing": _score(parsing_rate),
        "query_planning": _score(query_rate),
        "links": _score(links_rate),
        "price": _score(price_rate),
        "exact_match": _score(exact_rate),
        "availability": _score(availability_rate),
        "category_facts": _score(facts_rate),
        "category_quality": _score(category_quality_rate),
        "ranking": _score(ranking_rate),
        "dedupe": _score(dedupe_rate),
        "source_reliability": _score(source_rate),
        "admin": _score(admin_rate),
        "telegram": _score(telegram_rate),
        "cache_performance": _score(performance_rate),
    }
    groups = {
        "parsing": 0.8 * directions["parsing"] + 0.2 * directions["query_planning"],
        "links": 0.8 * directions["links"] + 0.2 * directions["source_reliability"],
        "price": directions["price"],
        "exact": directions["exact_match"],
        "category": statistics.mean([directions["availability"], directions["category_facts"], directions["category_quality"]]),
        "ranking": 0.85 * directions["ranking"] + 0.15 * directions["dedupe"],
        "admin": 0.65 * directions["admin"] + 0.35 * directions["telegram"],
        "performance": directions["cache_performance"],
    }
    weighted = round(sum(groups[key] * WEIGHTS[key] for key in WEIGHTS), 2)
    achieved = (
        weighted >= 9.0
        and groups["exact"] >= 9.0
        and groups["price"] >= 9.0
        and groups["ranking"] >= 8.5
        and min(groups.values()) >= 8.0
    )
    return directions, {key: round(value, 2) for key, value in groups.items()}, weighted, achieved


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search quality audit")
    parser.add_argument("--cached", action="store_true", help="read cache only; never call sources")
    parser.add_argument("--suite", choices=("smoke", "core", "extended", "quality_v3"), default="extended")
    parser.add_argument("--cache-path", default="")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = _parse_args()
    if not args.cached:
        print("ERROR: evaluator supports cached mode only; live work belongs to search_benchmark.py", flush=True)
        return 2
    golden = _load_golden()
    parsing, parsing_failures = audit_parsing(golden["queries"])
    synthetic, ranking_failures = audit_synthetic(golden["synthetic_candidates"])
    misc = audit_deterministic_misc()
    cache = SearchCache(args.cache_path or None)
    cached, cases, cache_failures = audit_cached(cache, args.suite)
    telegram_rate, telegram_failures = audit_telegram()
    directions, groups, weighted, achieved = calculate_scores(parsing, synthetic, misc, cached, telegram_rate)
    report = {
        "mode": "cached",
        "suite": args.suite,
        "golden_cases": len(golden["queries"]),
        "synthetic_suites": len(golden["synthetic_candidates"]),
        "network_calls": 0,
        "metrics": {**parsing, **synthetic, **misc, **cached, "telegram_checks_rate": telegram_rate},
        "scores": directions,
        "weighted_groups": groups,
        "weighted_score": weighted,
        "achieved_9_10": achieved,
        "failing_cases": {
            "parsing": parsing_failures,
            "ranking": ranking_failures,
            "cache_miss": cache_failures,
            "telegram": telegram_failures,
        },
        "cached_cases": cases,
    }
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    else:
        print(f"QUALITY AUDIT mode=cached suite={args.suite} golden={len(golden['queries'])} synthetic={len(golden['synthetic_candidates'])}", flush=True)
        for row in cases:
            print(
                f"CACHE {row['case_id']}: {row['state']} status={row['snapshot_status'] or '-'} "
                f"{row['duration_ms']}ms saved={row['saved_for_admin']} top1={row['top1'][:80] or '-'} "
                f"error={row['error'] or '-'}",
                flush=True,
            )
        print("METRICS", flush=True)
        for key, value in report["metrics"].items():
            rendered = f"{value:.4f}" if isinstance(value, float) else str(value)
            print(f"  {key}={rendered}", flush=True)
        print("SCORES 0-10", flush=True)
        for key, value in directions.items():
            print(f"  {key}={value:.2f}", flush=True)
        print("WEIGHTED GROUPS", flush=True)
        for key, value in groups.items():
            print(f"  {key}={value:.2f} weight={int(WEIGHTS[key] * 100)}%", flush=True)
        print(f"weighted_score={weighted:.2f}", flush=True)
        print(f"network_calls=0", flush=True)
        print(f"achieved_9_10={'YES' if achieved else 'NO'}", flush=True)
        for group, items in report["failing_cases"].items():
            if items:
                print(f"FAIL {group}: {', '.join(items)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
