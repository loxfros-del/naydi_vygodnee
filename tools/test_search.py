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
        product=parsed["product_name"],
        purpose=parsed["use_case"],
        criteria=parsed["important_criteria"],
        **parsed,
    )

    print("РАСПАРСЕННЫЙ ЗАПРОС")
    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    print("\nSEARCH QUERIES")
    for query in generate_search_queries(request):
        print(f"- {query}")

    collection = collect_product_candidates(request, max_results=15)
    stats = collection.quality_stats
    print(
        "\nКАЧЕСТВО ВЫДАЧИ: "
        f"RAW={len(collection.raw_candidates)}; категории={stats['categories']}; "
        f"статьи={stats['articles']}; не тот товар={stats['wrong_type']}; "
        f"NORMAL={stats['normal']}; WEAK={stats['weak']}"
    )
    normal = [item for item in collection.candidates if item.status == "CANDIDATE"]
    weak = [item for item in collection.candidates if item.status == "WEAK_CANDIDATE"]
    budget = int(parsed["budget"]) if str(parsed.get("budget", "")).isdigit() else None
    in_budget = sum(1 for item in collection.raw_candidates if item.price and budget and item.price <= budget)
    strongly_over_budget = sum(
        1 for item in collection.raw_candidates
        if item.price and budget and item.price > budget * 1.15
    )
    print(f"\nКАНДИДАТОВ: {len(collection.candidates)} (NORMAL={len(normal)}, WEAK={len(weak)})")
    if budget:
        print(f"Среди RAW — в бюджете: {in_budget}; сильно выше бюджета: {strongly_over_budget}")
    for index, item in enumerate(normal + weak, 1):
        price = f"{item.price} ₽" if item.price else "цена не найдена"
        if item.price and budget and item.price > budget:
            price += " — выше бюджета"
        risks = ", ".join(item.risk_flags) if item.risk_flags else "нет"
        label = "WEAK" if item.status == "WEAK_CANDIDATE" else "NORMAL"
        print(f"{index}. [{label}] {item.title}\n   {item.source} | {price} | score {int(round(item.score))}\n   {item.url}\n   причины: {risks}")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
