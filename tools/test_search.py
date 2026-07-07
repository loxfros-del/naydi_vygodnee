"""Консольная проверка автопоиска без Telegram.

Пример:
    python tools/test_search.py "Нужен телевизор для PS5 до 45к в Ярославле"
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Позволяет запускать файл напрямую из tools/.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Request
from app.request_parser import full_parse
from app.product_search import collect_product_candidates, generate_search_queries
from app.candidate_verifier import (
    BAD_ENCODING,
    NEED_MANUAL_CHECK,
    PRICE_MISSING,
    REMOVED_LISTING,
    UNAVAILABLE,
    VERIFY_BLOCKED,
)


def main() -> int:
    # PowerShell на старой кодовой странице cp1251 не умеет символ ₽.
    # Настройка безопасна и не влияет на работу Telegram-бота.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print('Использование: python tools/test_search.py "текст заявки"')
        return 2

    raw_query = " ".join(sys.argv[1:]).strip()
    parsed = full_parse(raw_query)
    request = Request(
        id=0,
        user_id=0,
        username="test",
        product=parsed.get("product_name", ""),
        **parsed,
    )

    print("РАСПАРСЕННЫЙ ЗАПРОС")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    print("\nSEARCH QUERIES")
    for query in generate_search_queries(request):
        print(f"- {query}")

    collection = collect_product_candidates(request, max_results=15)
    stats = collection.quality_stats
    verify_stats = collection.verify_stats
    raw_candidates = collection.raw_candidates
    saved_candidates = collection.candidates
    trash = [item for item in raw_candidates if item.quality == "TRASH"]
    good = [item for item in raw_candidates if item.quality == "GOOD"]
    ok = [item for item in raw_candidates if item.quality == "OK"]
    weak = [item for item in raw_candidates if item.quality == "WEAK"]
    print(
        "\nКАЧЕСТВО ВЫДАЧИ: "
        f"RAW={len(raw_candidates)}; TRASH={len(trash)}; "
        f"GOOD={len(good)}; OK={len(ok)}; WEAK={len(weak)}; "
        f"сохранено={len(saved_candidates)}"
    )
    print(
        "ФИЛЬТРЫ: "
        f"категории={stats['categories']}; статьи={stats['articles']}; "
        f"не тот товар={stats['wrong_type']}"
    )
    failed_sources = sorted({attempt.source for attempt in collection.attempts if attempt.status == "ERROR"})
    productive_sources = sorted({item.source for item in raw_candidates if item.quality in {"GOOD", "OK"}})
    print("УПАЛИ ИСТОЧНИКИ: " + (", ".join(failed_sources) if failed_sources else "нет"))
    print("GOOD/OK ДАЛИ: " + (", ".join(productive_sources) if productive_sources else "нет"))
    print(
        "\nВЕРИФИКАЦИЯ СТРАНИЦ: "
        f"checked={verify_stats.get('checked', 0)}; "
        f"verified_good={verify_stats.get('VERIFIED_GOOD', 0)}; "
        f"verified_ok={verify_stats.get('VERIFIED_OK', 0)}; "
        f"need_manual_check={verify_stats.get(NEED_MANUAL_CHECK, 0)}; "
        f"verify_blocked={verify_stats.get(VERIFY_BLOCKED, 0)}; "
        f"unavailable={verify_stats.get(UNAVAILABLE, 0)}; "
        f"removed_listing={verify_stats.get(REMOVED_LISTING, 0)}; "
        f"price_missing={verify_stats.get(PRICE_MISSING, 0)}; "
        f"bad_encoding={verify_stats.get(BAD_ENCODING, 0)}; "
        f"over_budget_soft={verify_stats.get('OVER_BUDGET_SOFT', 0)}; "
        f"over_budget_hard={verify_stats.get('OVER_BUDGET_HARD', 0)}; "
        f"rejected={verify_stats.get('REJECTED', 0)}; "
        f"verify_error={verify_stats.get('VERIFY_ERROR', 0)}; "
        f"saved_for_admin={verify_stats.get('saved', 0)}"
    )

    budget = int(parsed["budget"]) if str(parsed.get("budget", "")).isdigit() else None
    in_budget = sum(1 for item in raw_candidates if item.price and budget and item.price <= budget)
    strongly_over_budget = sum(
        1 for item in raw_candidates
        if item.price and budget and item.price > budget * 1.15
    )
    print(f"\nОсновные кандидаты для админа: {len(saved_candidates)}")
    if budget:
        print(f"Среди RAW — в бюджете: {in_budget}; сильно выше бюджета: {strongly_over_budget}")
    if verify_stats.get("VERIFIED_GOOD", 0) + verify_stats.get("VERIFIED_OK", 0) < 3:
        print("Нормальных проверенных вариантов мало. Нужно ручное уточнение / Алиса / Gemini.")
    for index, item in enumerate(saved_candidates, 1):
        price = f"{item.price} ₽" if item.price else "цена не найдена"
        if item.price and budget and item.price > budget:
            price += " — выше бюджета"
        risks = ", ".join(item.risk_flags) if item.risk_flags else "нет"
        verify_status = getattr(item, "verify_status", item.quality)
        facts = getattr(item, "product_facts", {}) or {}
        if not facts and getattr(item, "facts_json", ""):
            try:
                facts = json.loads(item.facts_json)
            except json.JSONDecodeError:
                facts = {}
        print(
            f"{index}. [{verify_status}] {item.title}\n"
            f"   {item.source} / {item.source_type} | {price} | score {int(round(item.score))}\n"
            f"   {item.url}\n"
            f"   причины: {risks}"
        )
        classification = next((flag for flag in item.risk_flags if "классификация:" in str(flag)), "-")
        print(
            "   diagnostics: "
            f"classification={classification}; "
            f"product_quality_level={getattr(item, 'product_quality_level', '-') or '-'}; "
            f"brand_quality={getattr(item, 'brand_quality', '-') or '-'}; "
            f"price_source={item.price_source or '-'}; "
            f"price_reliability={item.price_reliability or '-'}; "
            f"score_cap_applied={item.score_cap_applied or '-'}; "
            f"why_not_verified_good={getattr(item, 'why_not_verified_good', '-') or '-'}"
        )
        if facts:
            print(
                "   facts: "
                f"model_key={facts.get('model_key') or '-'}; "
                f"price={facts.get('price')}; "
                f"budget_status={facts.get('budget_status') or '-'}; "
                f"availability={facts.get('availability_text') or '-'}; "
                f"diagonal={facts.get('diagonal') or '-'}; "
                f"resolution={facts.get('resolution') or '-'}; "
                f"refresh_rate={facts.get('refresh_rate') or '-'}; "
                f"matrix_type={facts.get('matrix_type') or '-'}; "
                f"ps5_flags={facts.get('ps5_flags') or []}; "
                f"warnings={facts.get('warnings') or []}"
            )

    if collection.verified_rejections:
        print("\nDEBUG ОТБРАКОВКИ VERIFY")
        for item in collection.verified_rejections[:20]:
            cand = item.candidate
            print(
                f"- [{item.verify_status}] {cand.source}: {cand.title[:120]}\n"
                f"  причина: {item.reason or ', '.join(item.risk_flags) or item.verify_status}\n"
                f"  {cand.url}"
            )

    def is_manual_check_without_price(item) -> bool:
        return item.price is None and getattr(item, "verify_status", "") in {VERIFY_BLOCKED, NEED_MANUAL_CHECK}

    bad_saved = [
        item for item in saved_candidates
        if "bing.com/aclick" in item.url.lower()
        or "amazon." in item.url.lower()
        or "ebay." in item.url.lower()
        or "/otzyv" in item.url.lower()
        or "/reviews" in item.url.lower()
        or "/review" in item.url.lower()
        or "/articles/" in item.url.lower()
        or "/article/" in item.url.lower()
        or "journal.citilink.ru" in item.url.lower()
        or (item.price is None and not is_manual_check_without_price(item))
        or getattr(item, "availability", "") in {"UNAVAILABLE", REMOVED_LISTING}
        or item.quality == "TRASH"
        or getattr(item, "verify_status", "") in {
            "UNAVAILABLE", REMOVED_LISTING, PRICE_MISSING, BAD_ENCODING,
            "PRICE_MISMATCH", "NOT_PRODUCT_PAGE", "WRONG_PRODUCT", "REJECTED", "VERIFY_ERROR",
        }
        or any("обзор/подборка" in flag or "страница категории" in flag for flag in item.risk_flags)
    ]
    print("\nПРОВЕРКИ")
    if bad_saved:
        print("FAIL: в кандидатах есть реклама/статьи/категории/иностранный мусор:")
        for item in bad_saved:
            print(f"- [{item.quality}] {item.title} — {item.url}")
    else:
        print("OK: в кандидатах нет Bing Ads, Amazon/eBay, статей, категорий и TRASH.")

    print("\nDEBUG ПО ИСТОЧНИКАМ")
    for attempt in collection.attempts:
        print(
            f"{attempt.status:5} {attempt.source:22} "
            f"получено={attempt.found_count} кандидатов={attempt.kept_count} | {attempt.query}"
        )
        if attempt.error_text:
            print(f"      ошибка: {attempt.error_text}")

    if collection.manual_links:
        print("\nРУЧНЫЕ FALLBACK-ССЫЛКИ (не кандидаты)")
        for link in collection.manual_links:
            print(f"- {link['site']}: {link['url']}")
    return 1 if bad_saved else 0


if __name__ == "__main__":
    raise SystemExit(main())
