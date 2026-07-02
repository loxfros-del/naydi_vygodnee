"""Формирование предпросмотра и полного отчёта для клиента."""
import html
import json
import re
from urllib.parse import urlparse

from app.db import (
    get_search_results, get_alice_results, get_alice_top_result,
    count_search_results, count_approved_results, Request, SearchResult,
    get_market_checks, MarketCheck,
)
from app.price_extractor import format_price
from app.link_checks import LinkCheckStatus, store_url_matches



# ──────────────────────────────────────────────
#  Вспомогательные функции
# ──────────────────────────────────────────────

def _is_ps5_request(req: Request) -> bool:
    """Проверяет, связан ли запрос с PS5."""
    use_case = (req.use_case or req.purpose or "").lower()
    original = (req.original_query or "").lower()
    combined = use_case + " " + original
    return any(kw in combined for kw in [
        "ps5", "плейстейшен", "playstation", "приставк", "игры"
    ])


def _get_ps5_criteria_text(req: Request) -> str:
    """Возвращает текст про критерии для PS5."""
    budget_str = req.budget or ""
    budget_val = 0
    if budget_str.isdigit():
        budget_val = int(budget_str)

    text = (
        "Под задачу PS5 важны 4K, HDMI, диагональ 43–55, нормальные отзывы. "
    )
    if budget_val > 0 and budget_val < 30000:
        text += (
            "120 Гц в этом бюджете может не попасть, поэтому выбираем лучший компромисс."
        )
    else:
        text += (
            "Если бюджет позволяет — ищем 120 Гц. Full HD хуже, если рядом есть 4K за похожую цену."
        )
    return text


def _get_product_display_name(req: Request) -> str:
    """Возвращает отображаемое название товара."""
    return req.product_name or req.product or "товар"


def _has_direct_link(url: str) -> bool:
    """Проверяет форму ссылки, не делая внешних запросов к магазинам."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _active_alice_items(req: Request) -> list[SearchResult]:
    return [item for item in get_alice_results(req.id) if item.status != "REJECTED"]


def _alice_item_is_report_ready(item: SearchResult) -> bool:
    """Карточка допускается к финальному клиентскому отчёту только после проверки."""
    return bool(
        item.status in ("BEST", "APPROVED", "BACKUP", "APPROVED_BACKUP", "BUDGET", "APPROVED_BUDGET")
        and item.price
        and item.price_verified
        and item.link_check_status == LinkCheckStatus.VERIFIED.value
        and _has_direct_link(item.url)
        and store_url_matches(item.source, item.url) is not False
    )


def get_alice_report_issues(req: Request) -> tuple[str, int]:
    """Возвращает блокирующую причину и число неготовых не-ТОП карточек."""
    alice_items = _active_alice_items(req)
    if not alice_items:
        return "", 0
    top = get_alice_top_result(req.id)
    if not top or top.status == "REJECTED":
        return "Нельзя отправить отчёт: выберите ТОП-1 среди карточек Алисы.", 0
    if req.budget.isdigit() and top.price and top.price > int(req.budget):
        return "Нельзя отправить отчёт: ТОП-1 выше бюджета, перенесите его в блок «Осторожно».", 0
    if top.link_check_status != LinkCheckStatus.VERIFIED.value:
        return "Нельзя отправить отчёт: ссылка у ТОП-1 не подтверждена админом.", 0
    if not top.price or not top.price_verified:
        return "Нельзя отправить отчёт: цена у ТОП-1 не подтверждена админом.", 0
    if not _has_direct_link(top.url):
        return "Нельзя отправить отчёт: у ТОП-1 должна быть прямая ссылка на товар.", 0
    if store_url_matches(top.source, top.url) is False:
        return "Нельзя отправить отчёт: магазин и ссылка у ТОП-1 не совпадают.", 0
    excluded_other = sum(
        1 for item in alice_items
        if item.id != top.id and item.status in ("BEST", "APPROVED", "BACKUP", "APPROVED_BACKUP", "BUDGET") and not _alice_item_is_report_ready(item)
    )
    return "", excluded_other


def _alice_risks(item: SearchResult) -> list[str]:
    try:
        return json.loads(item.risk_flags) if item.risk_flags else []
    except json.JSONDecodeError:
        return []


def _format_alice_item(item: SearchResult) -> list[str]:
    lines = [f"Название: {html.escape(item.title or 'не указано')}"]
    lines.append(f"Цена: {format_price(item.price) if item.price else 'уточнить'}")
    lines.append(f"Где: {html.escape(item.source or 'уточнить')}")
    if _has_direct_link(item.url):
        lines.append(f"Ссылка: {_format_link(item.url)}")
    else:
        lines.append("Ссылку нужно уточнить вручную")
    lines.append(f"Почему: {html.escape(item.snippet or 'не указано')}")
    risks = _alice_risks(item)
    lines.append(f"Риск: {html.escape('; '.join(risks) if risks else 'не указан')}")
    return lines


def _alice_report_item_score(item: SearchResult, budget: int | None) -> int:
    """Скоринг SearchResult для сортировки в отчёте."""
    score = 0
    title = (item.title or "").lower()
    snippet = (item.snippet or "").lower()
    risks = _alice_risks(item)
    risk_text = " ".join(risks).lower()

    if budget and item.price:
        if item.price <= budget:
            score += 50
        elif item.price <= budget * 1.1:
            score += 20
        else:
            score -= 50

    hz120 = any(kw in title or kw in snippet for kw in ["120 гц", "120hz", "hdmi 2.1", "hdmi2.1", "игров"])
    if hz120:
        score += 30

    size_match = re.search(r'(\d{2})(?:\s*дюйм|")', title)
    if size_match:
        size = int(size_match.group(1))
        if 43 <= size <= 55:
            score += 15
        elif 32 <= size < 43:
            score += 5

    known_store = re.compile("|".join(re.escape(s) for s in [
        "DNS", "М.Видео", "Ситилинк", "Эльдорадо", "Ozon", "Wildberries", "Яндекс"
    ]), re.IGNORECASE).search(item.source or "")
    if known_store:
        score += 10

    high_risk = any(kw in risk_text for kw in ["подделка", "мошенник", "обман"])
    mild_risk = any(kw in risk_text for kw in ["60 гц", "60hz", "слабый звук", "мало отзыв", "выше бюджета"])
    if high_risk:
        score -= 40
    elif mild_risk:
        score -= 5

    if _has_direct_link(item.url):
        score += 10

    return score


def _format_link(url: str, label: str = "открыть товар") -> str:
    """Форматирует ссылку как HTML-ссылку с короткой подписью."""
    if not url or not _has_direct_link(url):
        return "Ссылку нужно уточнить вручную"
    safe_url = html.escape(url, quote=True)
    return f'<a href="{safe_url}">{html.escape(label)}</a>'


def _format_alice_price(item: SearchResult) -> str:
    """Форматирует цену карточки Алисы."""
    return format_price(item.price) if item.price else "уточнить"


def _format_alice_source(item: SearchResult) -> str:
    """Форматирует магазин карточки Алисы."""
    return html.escape(item.source or "магазин уточняется")


def _format_alice_reason(item: SearchResult) -> str:
    """Форматирует причину «почему подходит» для карточки Алисы."""
    text = item.snippet or ""
    return html.escape(text[:200]) if text.strip() else "не указано"


def _format_alice_item_full(item: SearchResult) -> list[str]:
    """Форматирует полную карточку Алисы для клиентского отчёта."""
    lines = [f"📌 Модель: {html.escape(item.title or 'не указано')}"]
    lines.append(f"💰 Цена: {_format_alice_price(item)}")
    lines.append(f"🏬 Где: {_format_alice_source(item)}")
    lines.append(f"🔗 Ссылка: {_format_link(item.url)}")
    lines.append(f"✅ Почему подходит: {_format_alice_reason(item)}")
    risk_text = "; ".join(_alice_risks(item)) if _alice_risks(item) else "не выявлено"
    lines.append(f"⚠️ Риск: {html.escape(risk_text)}")
    return lines


def _format_alice_item_caution(item: SearchResult) -> list[str]:
    """Форматирует карточку для блока «Осторожно»: все поля опциональны."""
    lines = [f"📌 Модель: {html.escape(item.title or 'не указано')}"]
    if item.price:
        lines.append(f"💰 Цена: {_format_alice_price(item)}")
    if item.source:
        lines.append(f"🏬 Где: {_format_alice_source(item)}")
    if _has_direct_link(item.url):
        lines.append(f"🔗 Ссылка: {_format_link(item.url)}")
    reasons = _alice_caution_reasons(item)
    lines.append(f"Почему не лучший вариант: {html.escape(reasons)}")
    risk_text = "; ".join(_alice_risks(item)) if _alice_risks(item) else "не указан"
    lines.append(f"Риск: {html.escape(risk_text)}")
    return lines


def _alice_caution_reasons(item: SearchResult) -> str:
    """Собирает текстовую причину для блока «Осторожно»."""
    parts = []
    if item.status in ("DO_NOT_BUY", "CAUTION"):
        parts.append("помечено админом как сомнительное")
    if item.status == "REJECTED":
        parts.append("убран админом")
    if not item.price:
        parts.append("цена не указана")
    if not _has_direct_link(item.url):
        parts.append("нет прямой ссылки")
    if not item.source:
        parts.append("магазин не указан")
    if not parts:
        parts.append("нужно проверить детали")
    return "; ".join(parts)


def build_alice_client_report(req: Request) -> str:
    """Полный отчёт клиенту — после оплаты. Все роли, полные блоки."""
    alice_items = get_alice_results(req.id)  # все, включая REJECTED
    active = _active_alice_items(req)  # без REJECTED
    top = get_alice_top_result(req.id)
    if not active or not top:
        return "Нельзя отправить отчёт: выберите и оставьте хотя бы одну карточку Алисы."

    # ТОП-1 должен быть report-ready
    if not _alice_item_is_report_ready(top):
        return "Нельзя отправить отчёт: ТОП-1 не прошёл ручную проверку ссылки и цены."

    budget = int(req.budget) if req.budget and req.budget.isdigit() else None

    lines = [
        "✅ <b>Подборка проверена вручную</b>",
        "",
        "Я посмотрел варианты, убрал слабые и оставил те, которые реально можно рассматривать к покупке.",
        "",
    ]

    # ── 🏆 ТОП-1 ──
    lines.append("🏆 <b>Лучший вариант — брать в первую очередь</b>")
    lines.extend(_format_alice_item_full(top))
    lines.append("")

    # ── 🔍 Проверка рынка ──
    market_checks = get_market_checks(req.id)
    if market_checks:
        lines.append("🔍 <b>Проверка на дешевле</b>")
        lines.append("Самый дешёвый вариант я тоже проверил.")
        for mc in market_checks:
            verdict_text = {
                "BUY": "Можно брать — вариант реальный и дешевле.",
                "RELIABLE": "Надёжнее, но дороже.",
                "DO_NOT_BUY": "Лучше не брать.",
                "PROMOTED": "Добавлен в подборку.",
            }.get(mc.verdict, "—")
            lines.append(f"Вердикт: {verdict_text}")
            if mc.reason:
                lines.append(f"Почему: {html.escape(mc.reason)}")
        lines.append("")

    # ── Распределение по ролям из report-ready ──
    eligible = [i for i in active if _alice_item_is_report_ready(i)]
    other = [i for i in eligible if i.id != top.id]
    other.sort(key=lambda i: _alice_report_item_score(i, budget), reverse=True)

    backup: list[SearchResult] = []
    budget_items: list[SearchResult] = []
    approved_items: list[SearchResult] = []
    leftover: list[SearchResult] = []

    for item in other:
        st = item.status or ""
        if st in ("BACKUP", "APPROVED_BACKUP"):
            backup.append(item)
        elif st in ("BUDGET", "APPROVED_BUDGET"):
            budget_items.append(item)
        elif st == "APPROVED":
            approved_items.append(item)
        elif st in ("DO_NOT_BUY", "CAUTION"):
            # эти не попадают в eligible для хороших блоков,
            # обработаем ниже отдельно
            pass
        else:
            leftover.append(item)

    # ── Caution: берём из ВСЕХ active (не только eligible) со статусами DO_NOT_BUY/CAUTION ──
    caution = [
        i for i in active
        if i.status in ("DO_NOT_BUY", "CAUTION") and i.id != top.id
    ]
    # Не исключаем caution без проверки ссылки — это блок «лучше не брать»
    # Но REJECTED не показываем (уже отфильтровано _active_alice_items)

    # ── ✅ Запасной ──
    if backup:
        lines.append("✅ <b>Запасной вариант</b>")
        for item in backup[:2]:
            lines.extend(_format_alice_item_full(item))
            lines.append("")
    elif leftover:
        best_leftover = leftover[0]
        lines.append("✅ <b>Запасной вариант</b>")
        lines.extend(_format_alice_item_full(best_leftover))
        lines.append("")

    # ── 💰 Бюджетный ──
    if budget_items:
        lines.append("💰 <b>Бюджетный вариант</b>")
        for item in budget_items[:2]:
            lines.extend(_format_alice_item_full(item))
            lines.append("")

    # ── 📌 Ещё можно рассмотреть (APPROVED) ──
    if approved_items:
        lines.append("📌 <b>Ещё можно рассмотреть</b>")
        for item in approved_items[:3]:
            lines.extend(_format_alice_item_full(item))
            lines.append("")

    # ── ⚠️ Осторожно / лучше не брать ──
    if caution:
        lines.append("⚠️ <b>Осторожно / лучше не брать</b>")
        for item in caution[:3]:
            lines.extend(_format_alice_item_caution(item))
            lines.append("")

    # ── 🧠 Итог ──
    top_title = html.escape(top.title or "ТОП-1")
    top_price = f"за {format_price(top.price)}" if top.price else ""
    top_source = f"в {html.escape(top.source)}" if top.source else ""
    lines.append("🧠 <b>Итог</b>")
    lines.append(f"Лучший выбор — {top_title} {top_price} {top_source}.".replace("  ", " ").strip())
    lines.append("Если хотите максимально безопасно — берите ТОП-1.")
    if budget_items:
        lines.append("Если хотите сэкономить — можно рассмотреть бюджетный вариант, но с учётом рисков.")
    if backup:
        lines.append("Есть запасной вариант на случай, если ТОП-1 не будет в наличии.")
    lines.append("")
    lines.append("📌 Цена и наличие актуальны на момент ручной проверки.")
    lines.append("Перед покупкой уточните наличие, доставку и гарантию.")
    return "\n".join(lines)


def _alice_caution_count(items: list[SearchResult], budget: int | None) -> int:
    return sum(
        1
        for item in items
        if (
            item.status in ("DO_NOT_BUY", "CAUTION")
            or not _has_direct_link(item.url)
            or not item.source
            or bool(budget and item.price and item.price > budget)
        )
    )


def build_admin_preview(req: Request) -> str:
    """Полный предпросмотр для админа: данные не отправляются клиенту."""
    alice_items = get_alice_results(req.id)
    if alice_items:
        top = get_alice_top_result(req.id)
        lines = [f"👁 <b>Полный предпросмотр для админа — заявка #{req.id}</b>", ""]
        if top:
            lines.append(f"🏆 ТОП-1: <b>{html.escape(top.title)}</b>")
        else:
            lines.append("⚠️ ТОП-1 ещё не выбран.")
        for index, item in enumerate(alice_items, 1):
            status = {
                "BEST": "🏆 ТОП-1", "APPROVED": "✅ оставлен", "REJECTED": "❌ убран",
                "CAUTION": "⚠️ осторожно", "DO_NOT_BUY": "🚫 не брать",
                "BACKUP": "🔄 запасной", "APPROVED_BACKUP": "🔄 запасной",
                "BUDGET": "💰 бюджетный", "APPROVED_BUDGET": "💰 бюджетный",
            }.get(item.status, "🟡 на проверке")
            lines.extend(["", f"<b>{index}. {status}</b>"])
            lines.extend(_format_alice_item(item))
            link_status = {
                LinkCheckStatus.NEEDED.value: "⚠️ ссылка нужна",
                LinkCheckStatus.FOUND_UNVERIFIED.value: "🔍 ссылка найдена, но не проверена",
                LinkCheckStatus.VERIFIED.value: "✅ ссылка проверена админом",
                LinkCheckStatus.UNSUITABLE.value: "❌ ссылка не подходит",
            }.get(item.link_check_status, "⚠️ ссылка нужна")
            price_status = "✅ цена проверена" if item.price and item.price_verified else "⚠️ цену нужно подтвердить"
            lines.append(f"Статус проверки: {link_status}")
            lines.append(f"Статус цены: {price_status}")
        issue, missing = get_alice_report_issues(req)
        if issue:
            lines.extend(["", f"⚠️ {issue}"])
        elif missing:
            lines.extend(["", f"⚠️ У {missing} карточек нет прямых ссылок."])

        # Блок проверки рынка
        market_checks = get_market_checks(req.id)
        if market_checks:
            lines.extend(["", "<b>🔍 Проверка рынка:</b>"])
            for mc in market_checks:
                verdict_label = {
                    "BUY": "💸 Самый дешёвый",
                    "RELIABLE": "🛡️ Надёжнее, но дороже",
                    "DO_NOT_BUY": "⚠️ Не брать",
                    "PROMOTED": "📌 Добавлен в подборку",
                }.get(mc.verdict, "—")
                mc_line = f"• {html.escape(mc.title or 'Вариант')}"
                if mc.price:
                    mc_line += f" — {format_price(mc.price)}"
                if mc.source:
                    mc_line += f" — {html.escape(mc.source)}"
                mc_line += f" [{verdict_label}]"
                lines.append(mc_line)
                if mc.reason:
                    lines.append(f"  Причина: {html.escape(mc.reason)}")

        return "\n".join(lines)

    found = get_search_results(req.id)
    lines = [f"👁 <b>Полный предпросмотр для админа — заявка #{req.id}</b>", ""]
    if not found:
        return "\n".join(lines + ["Вариантов пока нет."])
    for index, item in enumerate(found, 1):
        lines.append(f"<b>{index}. {html.escape(item.title or 'без названия')}</b> — {item.status}")
        lines.append(f"Цена: {format_price(item.price) if item.price else 'не указана'}")
        lines.append(f"Магазин: {html.escape(item.source or 'не указан')}")
        lines.append(f"Ссылка: {html.escape(item.url) if item.url else '⚠️ ссылка нужна'}")
        if item.snippet:
            lines.append(f"Почему: {html.escape(item.snippet)}")
        risks = _alice_risks(item)
        if risks:
            lines.append(f"Риски: {html.escape('; '.join(risks))}")
        if item.admin_note:
            lines.append(f"Заметка: {html.escape(item.admin_note)}")
        lines.append("")
    return "\n".join(lines).strip()


def _build_client_teaser(req: Request) -> str:
    """Предпросмотр до оплаты — без прямых ссылок и полного списка товаров."""
    alice_items = _active_alice_items(req)
    budget = int(req.budget) if req.budget and req.budget.isdigit() else None

    if alice_items:
        suitable = [item for item in alice_items if not budget or not item.price or item.price <= budget]
        total = len(suitable) or len(alice_items)
        has_best = get_alice_top_result(req.id) is not None
        has_reserve = sum(1 for i in alice_items if i.status in ("APPROVED", "BUDGET", "BACKUP", "APPROVED_BACKUP")) >= 1
        has_caution = _alice_caution_count(alice_items, budget) > 0
        market_checks = get_market_checks(req.id)
        has_market_check = len(market_checks) > 0
    else:
        found = get_search_results(req.id)
        approved = [item for item in found if item.status in ("BEST", "CHEAP", "RELIABLE", "APPROVED")]
        total = len(approved)
        has_best = bool(approved)
        has_reserve = len(approved) > 1
        has_caution = sum(1 for item in found if item.status in ("REJECTED", "DO_NOT_BUY")) > 0
        has_market_check = False

    lines = ["✅ <b>Подборка готова</b>", ""]
    lines.append("Я нашёл варианты под ваш запрос и вручную проверил основные риски.")
    lines.append("")
    lines.append("<b>Что уже сделано:</b>")
    if has_best:
        lines.append("• найден лучший вариант")
    lines.append("• проверены цена и ссылка")
    if has_reserve:
        lines.append("• есть запасные варианты")
    if has_market_check:
        lines.append("• проверил, нет ли варианта дешевле без серьёзных рисков")
    if has_caution:
        lines.append("• отдельно отмечены варианты, которые лучше не брать")

    lines.extend([
        "", "<b>В полном отчёте будет:</b>",
        "• ТОП-1 с прямой ссылкой",
        "• 1–3 запасных варианта",
        "• проверка на самый дешёвый вариант",
        "• риски по каждому варианту",
        "• итоговый совет перед покупкой",
        "", "Стоимость отчёта: <b>149–299 ₽</b>",
        "Оплата после того, как подборка готова.",
    ])
    return "\n".join(lines)


def _can_send_preview(req: Request) -> tuple[bool, str]:
    """Проверяет, можно ли отправить ограниченный клиентский предпросмотр."""
    if get_alice_results(req.id):
        if not _active_alice_items(req):
            return False, "Нельзя отправить предпросмотр: не осталось карточек для клиента."
        if not get_alice_top_result(req.id):
            return False, "Нельзя отправить предпросмотр: выберите ТОП-1 среди карточек ИИ."
        return True, ""
    total = count_search_results(req.id)
    approved = count_approved_results(req.id)

    if approved >= 1:
        return True, ""
    if total == 0:
        return False, "Нельзя отправить предпросмотр: автопоиск ничего не нашёл. Проверь debug поиска или добавь варианты вручную."
    return False, "Нельзя отправить предпросмотр: нет подтверждённых вариантов. Отметьте хотя бы один как лучший, нормальный, дешёвый или надёжный."


def _can_send_report(req: Request) -> tuple[bool, str]:
    """Проверяет, можно ли отправить полный отчёт."""
    alice_issue, _missing = get_alice_report_issues(req)
    if get_alice_results(req.id):
        return (not alice_issue, alice_issue)
    approved = count_approved_results(req.id)
    if approved >= 1:
        return True, ""
    return False, "Нельзя отправить полный отчёт: нет подтверждённых вариантов."


# ──────────────────────────────────────────────
#  Клиентский предпросмотр до оплаты
# ──────────────────────────────────────────────

def build_preview(req: Request) -> str:
    """Короткий тизер для клиента: без моделей, цен, магазинов и ссылок."""
    can_send, reason = _can_send_preview(req)
    if not can_send:
        return reason
    return _build_client_teaser(req)


# ──────────────────────────────────────────────
#  Полный отчёт (со ссылками)
# ──────────────────────────────────────────────

def build_full_report(req: Request) -> str:
    """
    Полный отчёт — со ссылками. Отправляется после подтверждения оплаты.
    """
    can_send, reason = _can_send_report(req)
    if not can_send:
        return reason

    if get_alice_results(req.id):
        return build_alice_client_report(req)

    product = _get_product_display_name(req)
    found = get_search_results(req.id)

    lines = [f"📊 <b>Полный отчёт: {product}</b>\n"]

    if req.budget:
        lines.append(f"💰 Бюджет: {format_price(int(req.budget)) if req.budget.isdigit() else req.budget}")
    if req.city:
        lines.append(f"📍 Город: {req.city}")
    if req.use_case or req.purpose:
        lines.append(f"🎯 Цель: {req.use_case or req.purpose}")
    if req.important_criteria or req.criteria:
        lines.append(f"📌 Критерии: {req.important_criteria or req.criteria}")

    # PS5 — специальный блок
    if _is_ps5_request(req):
        lines.append(f"\n<i>{_get_ps5_criteria_text(req)}</i>")

    lines.append("")

    # Показываем подтверждённые варианты (со ссылками)
    approved_items = [r for r in found if r.status in ("BEST", "CHEAP", "RELIABLE", "APPROVED")]

    if approved_items:
        lines.append("<b>Рекомендуемые варианты:</b>\n")

        # Сначала лучшие
        sorted_items = sorted(
            approved_items,
            key=lambda x: (
                0 if x.status == "BEST" else
                1 if x.status == "RELIABLE" else
                2 if x.status == "CHEAP" else 3
            )
        )

        for i, item in enumerate(sorted_items, 1):
            entry = f"{i}. <b>{item.title[:80]}</b>"
            if item.price:
                entry += f" — {format_price(item.price)}"
            if item.source:
                entry += f"\n   🏪 {item.source}"
            if item.url:
                entry += f"\n   🔗 {item.url}"

            # Метка статуса
            if item.status == "BEST":
                entry += "\n   ⭐ <b>Лучший выбор</b>"
            elif item.status == "CHEAP":
                entry += "\n   💸 <b>Выгодная цена</b>"
            elif item.status == "RELIABLE":
                entry += "\n   🛡 <b>Надёжный вариант</b>"

            if item.admin_note:
                entry += f"\n   📝 {item.admin_note}"

            lines.append(entry)
            lines.append("")
    else:
        lines.append("Подтверждённых вариантов нет.")

    # Поисковые ссылки
    search_links = json.loads(req.search_links) if req.search_links else []
    if search_links:
        lines.append("<b>Поисковые ссылки для самостоятельного поиска:</b>\n")
        for link in search_links:
            lines.append(f'• <a href="{link["url"]}">{link["site"]}</a>')

    lines.extend([
        "",
        "<i>Цена и наличие актуальны на момент ручной проверки. Перед покупкой нужно ещё раз уточнить наличие, доставку и гарантию.</i>",
    ])

    return "\n".join(lines)
