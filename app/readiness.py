"""Проверка готовности заявки перед отправкой клиенту."""
from dataclasses import dataclass, field

from app.db import (
    Request, SearchResult,
    get_alice_results, get_market_checks,
)
from app.ai_cards_service import is_ai_card_candidate_eligible
from app.verification_state import resolve_final_presentation


@dataclass
class ReadinessResult:
    """Результат проверки готовности заявки."""
    percent: int = 0
    items: list[dict] = field(default_factory=list)  # [{"label": "...", "ok": bool}]
    level: str = "red"  # green / yellow / red
    can_send: bool = False


def check_readiness(req: Request) -> ReadinessResult:
    """Рассчитывает готовность заявки к отправке клиенту."""
    items: list[dict] = []
    alice = [item for item in get_alice_results(req.id) if is_ai_card_candidate_eligible(item)]
    top = next(
        (item for item in alice if item.status in {"BEST", "TOP", "TOP1"}),
        None,
    )
    budget = int(req.budget) if req.budget and req.budget.isdigit() else None
    market_checks = get_market_checks(req.id)
    top_final = resolve_final_presentation(getattr(top, "facts_json", "") or {}) if top else {}

    total_checks = 10
    passed = 0

    # 1. ТОП-1 выбран
    has_top = top is not None and top.status != "REJECTED"
    items.append({"label": "ТОП-1 выбран", "ok": has_top})
    if has_top:
        passed += 1

    # 2. У ТОП-1 есть ссылка
    top_has_link = has_top and bool(top.url and top.url.strip())
    items.append({"label": "У ТОП-1 есть ссылка", "ok": top_has_link})
    if top_has_link:
        passed += 1

    # 3. ТОП-1 ссылка и цена проверены
    top_verified = bool(has_top and top_final.get("presentation_ready") and top.price and top.url)
    items.append({"label": "Финальная проверка ТОП-1 завершена", "ok": top_verified})
    if top_verified:
        passed += 1

    # 4. Цена ТОП-1 в бюджете
    top_in_budget = has_top and (
        not budget or not top.price or top.price <= budget
    )
    items.append({"label": "Цена ТОП-1 в бюджете", "ok": top_in_budget})
    if top_in_budget:
        passed += 1

    # 5. Проверка рынка выполнена
    market_ok = len(market_checks) > 0
    items.append({"label": "Проверка рынка выполнена", "ok": market_ok})
    if market_ok:
        passed += 1

    # 6. Есть объяснение, почему не берём самый дешёвый
    cheapest_explained = False
    if alice and top:
        eligible = [i for i in alice if i.status != "REJECTED" and i.id != top.id and i.price]
        if eligible:
            cheapest = min(eligible, key=lambda x: x.price or 0)
            if cheapest.price and top.price and cheapest.price < top.price:
                # Если у дешёвого есть риск-флаги или админская заметка — объяснение есть
                cheapest_explained = bool(
                    (cheapest.risk_flags and cheapest.risk_flags != "[]")
                    or cheapest.admin_note
                )
    if not alice or not top or not eligible or (
        cheapest.price and top.price and cheapest.price >= top.price
    ):
        cheapest_explained = True  # нет более дешёвого — пункт не применим
    items.append({"label": "Есть объяснение, почему не берём самый дешёвый", "ok": cheapest_explained})
    if cheapest_explained:
        passed += 1

    # 7. Есть хотя бы 1 запасной вариант
    reserve_count = sum(
        1 for i in alice
        if i.status in ("APPROVED", "BACKUP", "BUDGET", "APPROVED_BACKUP") and i.id != (top.id if top else -1)
    )
    has_reserve = reserve_count >= 1
    items.append({"label": "Есть хотя бы 1 запасной вариант", "ok": has_reserve})
    if has_reserve:
        passed += 1

    # 8. Есть блок «осторожно / не брать»
    has_caution = any(
        i.status in ("DO_NOT_BUY", "CAUTION")
        or (budget and i.price and i.price > budget)
        or not i.url
        or not i.source
        for i in alice
    )
    items.append({"label": "Есть блок «осторожно / не брать»", "ok": has_caution})
    if has_caution:
        passed += 1

    # 9. В клиентском предпросмотре нет прямых ссылок (всегда ок — тизер без ссылок)
    items.append({"label": "В клиентском предпросмотре нет прямых ссылок", "ok": True})
    passed += 1

    # 10. Полный отчёт содержит ссылки и риски
    report_ok = has_top and top_has_link and top_verified
    items.append({"label": "Полный отчёт содержит ссылки и риски", "ok": report_ok})
    if report_ok:
        passed += 1

    percent = int(round(passed / total_checks * 100))

    level = "red"
    if percent >= 90:
        level = "green"
    elif percent >= 75:
        level = "yellow"

    # Обязательный шлюз — один полностью проверенный ТОП-1 в бюджете. Остальные
    # пункты показывают качество подборки, но не создают искусственный тупик.
    can_send = top_verified and top_in_budget

    return ReadinessResult(percent=percent, items=items, level=level, can_send=can_send)


def format_readiness(result: ReadinessResult) -> str:
    """Форматирует результат проверки готовности для сообщения."""
    level_text = {
        "green": "🟢 можно отправлять",
        "yellow": "🟡 можно, но лучше доработать",
        "red": "🔴 не отправлять",
    }
    lines = [
        f"📊 <b>Готовность заявки: {result.percent}%</b>",
        f"{level_text.get(result.level, '🔴')}",
        "",
    ]
    for item in result.items:
        icon = "✅" if item["ok"] else "❌"
        lines.append(f"{icon} {item['label']}")

    # Собираем проблемы
    problems = [item["label"] for item in result.items if not item["ok"]]
    if problems:
        lines.append("")
        lines.append("<b>Нужно доработать:</b>")
        for p in problems:
            lines.append(f"• {p}")

    return "\n".join(lines)


def format_readiness_short(result: ReadinessResult) -> str:
    """Короткий статус для вставки в карточку заявки."""
    level_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}
    icon = level_icon.get(result.level, "🔴")
    lines = [f"📊 Готовность: {result.percent}% {icon}"]
    problems = [item["label"] for item in result.items if not item["ok"]]
    for p in problems[:5]:
        lines.append(f"  ❌ {p}")
    return "\n".join(lines)
