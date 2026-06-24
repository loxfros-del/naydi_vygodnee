#!/usr/bin/env python3
"""QA-проверка качества поиска для разработчика.

Запуск:
    python tools/qa_search.py
    python tools/qa_search.py "Нужен телевизор для PS5 до 45к в Ярославле"

Анализирует кандидатов из автопоиска и выдаёт компактный отчёт:
- оценка качества 0–100
- сравнение с предыдущим запуском (динамика)
- топ-5 кандидатов
- отклонённый мусор
- разбивка по источникам
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import Request
from app.request_parser import full_parse
from app.product_search import collect_product_candidates

LAST_REPORT = Path(__file__).resolve().parent / "last_qa_report.json"
DEFAULT_QUERY = "Нужен телевизор для PS5 до 45к в Ярославле"


# ─────────────────────────────────────────────────────────
#  Scoring
# ─────────────────────────────────────────────────────────


def compute_qa_score(
    normal: int,
    weak: int,
    with_price: int,
    in_budget: int,
    over_budget: int,
    trash: int,
    duplicates: int,
    sources: set[str],
) -> int:
    """Оценка качества поиска от 0 до 100."""

    score = 0
    usable = normal + weak

    # ── положительные баллы ──────────────────────────
    if usable >= 5:
        score += 20
    elif usable >= 3:
        score += 15
    elif usable >= 2:
        score += 10
    elif usable >= 1:
        score += 5

    if with_price >= 3:
        score += 20
    elif with_price >= 2:
        score += 10
    elif with_price >= 1:
        score += 5

    if in_budget >= 1:
        score += 20
    elif usable >= 1:
        score += 5

    if over_budget == 0:
        score += 15

    if trash == 0:
        score += 15
    elif trash <= 2:
        score += 10
    elif trash <= 4:
        score += 5

    # Разнообразие источников
    target_sources = {
        "wildberries",
        "ozon_search",
        "yandex_market_search",
        "dns_search",
        "mvideo_search",
    }
    matched = len(set(sources) & target_sources)
    if matched >= 3:
        score += 10
    elif matched >= 2:
        score += 5
    elif matched >= 1:
        score += 2

    # ── штрафы ────────────────────────────────────────
    if over_budget > 0:
        score -= min(over_budget * 2, 10)

    if trash > 0:
        score -= min(trash * 3, 10)

    if with_price == 0 and usable > 0:
        score -= 10

    if duplicates > 2:
        score -= min(duplicates, 10)

    if usable == 0:
        score -= 20

    return max(0, min(score, 100))


# ─────────────────────────────────────────────────────────
#  Основной отчёт
# ─────────────────────────────────────────────────────────


def build_report(raw_query: str) -> dict:
    """Собирает QA-отчёт и выводит его в консоль."""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parsed = full_parse(raw_query)
    request = Request(
        id=0,
        product=parsed["product_name"],
        purpose=parsed["use_case"],
        criteria=parsed["important_criteria"],
        **parsed,
    )

    collection = collect_product_candidates(request, max_results=15)

    # Бюджет
    budget: int | None = None
    if parsed.get("budget") and str(parsed["budget"]).isdigit():
        budget = int(parsed["budget"])

    normal_candidates: list = []
    weak_candidates: list = []
    rejected_candidates: list = []
    with_price = 0
    in_budget = 0
    over_budget = 0
    sources: set[str] = set()
    seen_titles: set[str] = set()
    duplicates = 0

    # Per-source stats
    source_stats: dict[str, dict] = defaultdict(lambda: {"raw": 0, "normal": 0, "weak": 0, "rejected": 0, "error": ""})

    # RAW — это все разобранные результаты до quality_filter. В основном
    # collection.candidates уже только NORMAL/WEAK, поэтому для QA берём
    # отдельный снимок raw_candidates.
    raw_candidates = collection.raw_candidates or collection.candidates
    for c in raw_candidates:
        src = c.source
        source_stats[src]["raw"] += 1

        if c.status == "REJECTED_AUTO":
            rejected_candidates.append(c)
            source_stats[src]["rejected"] += 1
            continue

        if c.status == "WEAK_CANDIDATE":
            weak_candidates.append(c)
            source_stats[src]["weak"] += 1
        else:
            normal_candidates.append(c)
            source_stats[src]["normal"] += 1

        sources.add(c.source)

        if c.price and c.price > 0:
            with_price += 1
            if budget and c.price <= budget:
                in_budget += 1
            elif budget and c.price > budget:
                over_budget += 1

        # Дубли по первым 60 символам названия
        key = c.title[:60].lower().strip()
        if key in seen_titles:
            duplicates += 1
        seen_titles.add(key)

    # Mark errors from attempts
    for attempt in collection.attempts:
        if attempt.status == "ERROR":
            src = attempt.source
            if src not in source_stats:
                source_stats[src] = {"raw": 0, "normal": 0, "weak": 0, "rejected": 0, "error": ""}
            source_stats[src]["error"] = attempt.error_text[:80]

    usable = len(normal_candidates) + len(weak_candidates)
    score = compute_qa_score(
        normal=len(normal_candidates),
        weak=len(weak_candidates),
        with_price=with_price,
        in_budget=in_budget,
        over_budget=over_budget,
        trash=len(rejected_candidates),
        duplicates=duplicates,
        sources=sources,
    )

    # Предыдущий отчёт
    prev: dict = {}
    if LAST_REPORT.exists():
        try:
            prev = json.loads(LAST_REPORT.read_text(encoding="utf-8"))
        except Exception:
            prev = {}

    prev_score = prev.get("score")
    prev_with_price = prev.get("with_price")
    prev_trash = prev.get("trash")

    report = {
        "timestamp": datetime.now().isoformat(),
        "query": raw_query,
        "clean_search_query": parsed.get("clean_search_query", ""),
        "budget": str(parsed.get("budget", "")),
        "city": parsed.get("city", ""),
        "score": score,
        "total_raw": len(raw_candidates),
        "normal": len(normal_candidates),
        "weak": len(weak_candidates),
        "rejected": len(rejected_candidates),
        "with_price": with_price,
        "in_budget": in_budget,
        "over_budget": over_budget,
        "trash": len(rejected_candidates),
        "duplicates": duplicates,
        "sources": sorted(sources),
        "source_stats": {
            src: {
                "raw": s["raw"],
                "normal": s["normal"],
                "weak": s["weak"],
                "rejected": s["rejected"],
                "error": s["error"],
            }
            for src, s in sorted(source_stats.items())
        },
        "top5": [
            {
                "title": c.title,
                "price": c.price,
                "source": c.source,
                "score": round(c.score, 1),
                "status": c.status,
                "risks": c.risk_flags,
                "url": c.url,
            }
            for c in (normal_candidates + weak_candidates)[:5]
        ],
        "trash_items": [
            {
                "title": c.title,
                "price": c.price,
                "why": ", ".join(c.risk_flags) if c.risk_flags else "отклонено",
            }
            for c in rejected_candidates[:10]
        ],
    }

    LAST_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ── печать в консоль ─────────────────────────────
    sep = "=" * 60
    sub = "─" * 40
    budget_display = f"{budget:,} ₽".replace(",", " ") if budget else "не указан"

    print(sep)
    print("QA-ОТЧЁТ КАЧЕСТВА ПОИСКА")
    print(sep)
    print(f"Запрос:          {raw_query}")
    print(f"Чистый запрос:   {report['clean_search_query']}")
    print(f"Бюджет:          {budget_display}")
    print(f"Город:           {parsed.get('city', 'не указан')}")
    print()
    print(f"📊  SCORE:  {score}/100")

    # Мини-интерпретация
    if score >= 80:
        print("     Статус: отлично — поиск работает хорошо")
    elif score >= 60:
        print("     Статус: нормально — можно улучшать")
    elif score >= 40:
        print("     Статус: слабо — нужно дорабатывать")
    else:
        print("     Статус: плохо — автопоиск не справляется")

    print()
    print(f"  RAW найдено:       {len(raw_candidates)}")
    print(f"  Нормальных:        {len(normal_candidates)}")
    print(f"  Слабых (WEAK):     {len(weak_candidates)}")
    print(f"  Отклонено:         {len(rejected_candidates)}")
    print(f"  С ценой:           {with_price}")
    print(f"  В бюджете:         {in_budget}")
    print(f"  Выше бюджета:      {over_budget}")
    print(f"  Дублей:            {duplicates}")
    print(f"  Источников:        {', '.join(sorted(sources)) if sources else 'нет'}")

    # Диагностика
    if len(raw_candidates) < 10:
        print()
        print("  ⚠ Проблема: источники почти ничего не вернули или поиск слишком узкий")
    elif usable < 5 and len(raw_candidates) >= 10:
        print()
        print("  ⚠ Проблема: фильтр слишком жёсткий — много raw, но мало normal/weak")

    # ── по источникам ────────────────────────────────
    if source_stats:
        print()
        print(sub)
        print("ПО ИСТОЧНИКАМ")
        print(sub)
        for src, s in sorted(source_stats.items()):
            parts = [f"  {src:25s} raw={s['raw']:2d}  normal={s['normal']:2d}  weak={s['weak']:2d}  rejected={s['rejected']:2d}"]
            if s["error"]:
                parts.append(f"  ⚠ {s['error'][:60]}")
            print("".join(parts))

    # ── динамика ─────────────────────────────────────
    if prev_score is not None:
        print()
        print(sub)
        print("ДИНАМИКА")
        print(sub)
        delta = score - prev_score
        if delta > 0:
            verdict = "✅ стало лучше"
        elif delta < 0:
            verdict = "❌ стало хуже"
        else:
            verdict = "➖ без изменений"
        print(f"  Score:     было {prev_score}, стало {score} ({delta:+d})")
        if prev_with_price is not None:
            dp = with_price - prev_with_price
            print(f"  С ценой:   было {prev_with_price}, стало {with_price} ({dp:+d})")
        if prev_trash is not None:
            dt = len(rejected_candidates) - prev_trash
            print(f"  Мусора:    было {prev_trash}, стало {len(rejected_candidates)} ({dt:+d})")
        print(f"  Вердикт:   {verdict}")

    # ── топ-5 ────────────────────────────────────────
    all_usable = normal_candidates + weak_candidates
    if all_usable:
        print()
        print(sub)
        print("ТОП-5 КАНДИДАТОВ")
        print(sub)
        for i, c in enumerate(all_usable[:5], 1):
            price_str = (
                f"{c.price:,} ₽".replace(",", " ") if c.price else "цена не найдена"
            )
            status_label = "WEAK" if c.status == "WEAK_CANDIDATE" else c.status
            print(f"\n  #{i} [{status_label}]")
            print(f"  Название:   {c.title[:100]}")
            print(f"  Цена:       {price_str}")
            print(f"  Источник:   {c.source}")
            print(f"  Score:      {c.score:.0f}")
            print(
                f"  Риски:      {', '.join(c.risk_flags) if c.risk_flags else 'нет'}"
            )
            print(f"  URL:        {c.url}")
    else:
        print()
        print("⚠  Нет нормальных кандидатов!")

    # ── мусор ────────────────────────────────────────
    if rejected_candidates:
        print()
        print(sub)
        print("ОТКЛОНЕНО / ПОДОЗРИТЕЛЬНО")
        print(sub)
        for c in rejected_candidates[:10]:
            price_str = (
                f"{c.price:,} ₽".replace(",", " ") if c.price else "цена не найдена"
            )
            why = ", ".join(c.risk_flags) if c.risk_flags else "неизвестно"
            print(f"  - {c.title[:60]} | {price_str} — {why}")

    print()
    print(sep)
    print(f"Отчёт сохранён: {LAST_REPORT}")
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    raw_query = (
        " ".join(sys.argv[1:]).strip() if len(sys.argv) > 1 else DEFAULT_QUERY
    )
    build_report(raw_query)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
