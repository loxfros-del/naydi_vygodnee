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
from app.link_checks import store_url_matches
from app.product_config import QUICK_SELECTION, get_service_package
from app.services.ai_review import is_ai_card_client_approved
from app.ai_cards_service import is_ai_card_candidate_eligible
from app.services.recommendations import RecommendationService
from app.verification_state import resolve_final_presentation
from app.ui_formatters import (
    format_client_card,
    format_client_result_summary,
    source_display_name,
)



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


def _final_state(item: SearchResult) -> dict:
    return resolve_final_presentation(getattr(item, "facts_json", "") or {})


def _final_value(item: SearchResult, key: str, fallback: object = None) -> object:
    final = _final_state(item)
    return (final.get("final_facts") or {}).get(key, fallback)


def _active_alice_items(req: Request) -> list[SearchResult]:
    return [
        item for item in get_alice_results(req.id)
        if item.status not in {"REJECTED", "REJECTED_AUTO", "DO_NOT_BUY", "CAUTION"}
        and is_ai_card_client_approved(item)
        and is_ai_card_candidate_eligible(item)
    ]


def _approved_alice_top(req: Request) -> SearchResult | None:
    return next((item for item in _active_alice_items(req) if item.status in {"BEST", "TOP", "TOP1"}), None)


def _alice_item_is_report_ready(item: SearchResult) -> bool:
    """Карточка допускается к финальному клиентскому отчёту только после проверки."""
    final = _final_state(item)
    url = str((final.get("final_facts") or {}).get("url") or item.url or "")
    return bool(
        is_ai_card_candidate_eligible(item)
        and is_ai_card_client_approved(item)
        and item.status in ("BEST", "APPROVED", "BACKUP", "APPROVED_BACKUP", "BUDGET", "APPROVED_BUDGET")
        and final.get("presentation_ready")
        and final.get("price_verified")
        and final.get("link_verified")
        and (final.get("final_facts") or {}).get("price", item.price)
        and _has_direct_link(url)
        and store_url_matches(item.source, url) is not False
    )


def get_alice_report_issues(req: Request) -> tuple[str, int]:
    """Возвращает блокирующую причину и число неготовых не-ТОП карточек."""
    alice_items = _active_alice_items(req)
    if get_alice_results(req.id) and not alice_items:
        return "Нельзя отправить отчёт: утвердите хотя бы одну карточку.", 0
    if not alice_items:
        return "", 0
    top = _approved_alice_top(req)
    if not top:
        return "Нельзя отправить отчёт: выберите и утвердите ТОП-1.", 0
    if req.budget.isdigit() and top.price and top.price > int(req.budget):
        return "Нельзя отправить отчёт: ТОП-1 выше бюджета, перенесите его в блок «Осторожно».", 0
    final = _final_state(top)
    final_facts = final.get("final_facts") or {}
    final_url = str(final_facts.get("url") or top.url or "")
    final_price = final_facts.get("price", top.price)
    if not final.get("presentation_ready"):
        fields = ", ".join(final.get("unresolved_fields") or [])
        return f"Нельзя отправить отчёт: завершите финальную проверку ТОП-1 ({fields or 'есть блокирующая причина'}).", 0
    if not final.get("link_verified"):
        return "Нельзя отправить отчёт: ссылка у ТОП-1 не подтверждена.", 0
    if not final_price or not final.get("price_verified"):
        return "Нельзя отправить отчёт: цена у ТОП-1 не подтверждена.", 0
    if not _has_direct_link(final_url):
        return "Нельзя отправить отчёт: у ТОП-1 должна быть прямая ссылка на товар.", 0
    if store_url_matches(top.source, final_url) is False:
        return "Нельзя отправить отчёт: магазин и ссылка у ТОП-1 не совпадают.", 0
    excluded_other = sum(
        1 for item in alice_items
        if item.id != top.id and item.status in ("BEST", "APPROVED", "BACKUP", "APPROVED_BACKUP", "BUDGET") and not _alice_item_is_report_ready(item)
    )
    return "", excluded_other


def _alice_risks(item: SearchResult) -> list[str]:
    try:
        facts = json.loads(getattr(item, "facts_json", "") or "{}")
    except json.JSONDecodeError:
        facts = {}
    facts = facts if isinstance(facts, dict) else {}
    try:
        raw_risks = json.loads(item.risk_flags) if item.risk_flags else []
    except json.JSONDecodeError:
        raw_risks = [item.risk_flags] if item.risk_flags else []
    if not isinstance(raw_risks, list):
        raw_risks = [raw_risks]
    automatic = dict(facts.get("automatic_verification") or facts)
    existing = automatic.get("warnings")
    if not isinstance(existing, list):
        existing = [existing] if existing else []
    automatic["warnings"] = [*existing, *raw_risks]
    facts["automatic_verification"] = automatic
    final = resolve_final_presentation(facts)
    return [str(value) for value in (final.get("warnings") or []) if str(value).strip()]


def _format_alice_item(item: SearchResult) -> list[str]:
    final_facts = _final_state(item).get("final_facts") or {}
    final_price = final_facts.get("price", item.price)
    final_url = str(final_facts.get("url") or item.url or "")
    lines = [f"Название: {html.escape(item.title or 'не указано')}"]
    lines.append(f"Цена: {format_price(final_price) if final_price else 'уточнить'}")
    lines.append(f"Где: {html.escape(item.source or 'уточнить')}")
    if _has_direct_link(final_url):
        lines.append(f"Ссылка: {_format_link(final_url)}")
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
    """Безопасный клиентский отчёт: только APPROVED lifecycle и максимум 3 роли."""
    issue, _excluded = get_alice_report_issues(req)
    if issue:
        return issue
    recommendations = [
        item for item in RecommendationService().for_request(req.id)
        if _alice_item_is_report_ready(item.card)
    ][:3]
    if not recommendations or recommendations[0].role != "BEST":
        return "Нельзя отправить отчёт: утверждённый ТОП-1 не готов."
    cards = [item.card for item in recommendations]
    lines = [
        format_client_result_summary(req, cards, found_count=count_search_results(req.id)),
        "",
    ]
    for recommendation in recommendations:
        lines.extend([format_client_card(recommendation.card, recommendation.role), ""])
    lines.append("<i>Цена и наличие актуальны на дату проверки. Перед покупкой ещё раз уточните доставку и гарантию.</i>")
    return "\n".join(lines).strip()


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
            final = _final_state(item)
            link_status = "✅ ссылка подтверждена" if final.get("link_verified") else "⚠️ ссылку нужно подтвердить"
            price_status = "✅ цена подтверждена" if final.get("price_verified") else "⚠️ цену нужно подтвердить"
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
        has_best = _approved_alice_top(req) is not None
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
    lines.append("Варианты собраны и прошли проверку специалиста.")
    lines.append("")
    lines.append("<b>Что уже сделано:</b>")
    if has_best:
        lines.append("• найден лучший вариант")
    lines.append("• проверены соответствие модели, цена и основные риски")
    if has_reserve:
        lines.append("• есть запасные варианты")
    if has_market_check:
        lines.append("• проверил, нет ли варианта дешевле без серьёзных рисков")
    if has_caution:
        lines.append("• отдельно отмечены варианты, которые лучше не брать")

    package = get_service_package(getattr(req, "package_code", "") or QUICK_SELECTION.code) or QUICK_SELECTION
    lines.extend([
        "", "<b>В полном отчёте будет:</b>",
        "• ТОП-1 с прямой ссылкой",
        "• 1–3 запасных варианта",
        "• проверка на самый дешёвый вариант",
        "• риски по каждому варианту",
        "• итоговый совет перед покупкой",
        "", f"Стоимость: <b>{html.escape(package.price_label)}</b>",
        "Оплата после того, как подборка готова.",
    ])
    return "\n".join(lines)


def _can_send_preview(req: Request) -> tuple[bool, str]:
    """Проверяет, можно ли отправить ограниченный клиентский предпросмотр."""
    if get_alice_results(req.id):
        if not _active_alice_items(req):
            return False, "Нельзя отправить предпросмотр: не осталось карточек для клиента."
        if not _approved_alice_top(req):
            return False, "Нельзя отправить предпросмотр: выберите и утвердите ТОП-1."
        return True, ""
    total = count_search_results(req.id)
    approved = count_approved_results(req.id)

    if approved >= 1:
        best = next((item for item in get_search_results(req.id) if item.status == "BEST"), None)
        if not best:
            return False, "Нельзя отправить предпросмотр: выберите лучший вариант."
        final = _final_state(best)
        final_facts = final.get("final_facts") or {}
        if not final.get("presentation_ready"):
            return False, "Нельзя отправить предпросмотр: завершите финальную проверку лучшего варианта."
        if not final_facts.get("price", best.price) or not _has_direct_link(str(final_facts.get("url") or best.url or "")):
            return False, "Нельзя отправить предпросмотр: у лучшего варианта нужны цена и прямая ссылка."
        return True, ""
    if total == 0:
        return False, "Нельзя отправить предпросмотр: автопоиск ничего не нашёл. Проверь debug поиска или добавь варианты вручную."
    return False, "Нельзя отправить предпросмотр: нет подтверждённых вариантов. Отметьте хотя бы один как лучший, нормальный, дешёвый или надёжный."


def _can_send_report(req: Request) -> tuple[bool, str]:
    """Проверяет, можно ли отправить полный отчёт."""
    alice_issue, _missing = get_alice_report_issues(req)
    if get_alice_results(req.id):
        return (not alice_issue, alice_issue)
    approved = [item for item in get_search_results(req.id) if item.status in {"BEST", "CHEAP", "RELIABLE", "APPROVED"}]
    best = next((item for item in approved if item.status == "BEST"), None)
    if best:
        final = _final_state(best)
        final_facts = final.get("final_facts") or {}
        if not final.get("presentation_ready"):
            return False, "Нельзя отправить полный отчёт: финальная проверка лучшего варианта не завершена."
        if not final_facts.get("price", best.price) or not final.get("price_verified"):
            return False, "Нельзя отправить полный отчёт: цена лучшего варианта не подтверждена."
        if not _has_direct_link(str(final_facts.get("url") or best.url or "")) or not final.get("link_verified"):
            return False, "Нельзя отправить полный отчёт: ссылка лучшего варианта не подтверждена."
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
    found = get_search_results(req.id)
    approved_items = [r for r in found if r.status in ("BEST", "CHEAP", "RELIABLE", "APPROVED")]
    if not approved_items:
        return "Нельзя отправить полный отчёт: нет подтверждённых вариантов."
    role_map = {"BEST": "BEST", "CHEAP": "BUDGET", "RELIABLE": "BACKUP", "APPROVED": "BACKUP"}
    role_order = {"BEST": 0, "BUDGET": 1, "BACKUP": 2}
    selected: list[tuple[SearchResult, str]] = []
    seen_roles: set[str] = set()
    for item in sorted(approved_items, key=lambda row: (role_order[role_map[row.status]], row.sort_order, row.id)):
        role = role_map[item.status]
        if role in seen_roles:
            continue
        seen_roles.add(role)
        selected.append((item, role))
        if len(selected) == 3:
            break
    if not any(role == "BEST" for _item, role in selected):
        return "Нельзя отправить полный отчёт: выберите лучший вариант."
    cards = [item for item, _role in selected]
    lines = [format_client_result_summary(req, cards, found_count=len(found)), ""]
    for item, role in selected:
        lines.extend([format_client_card(item, role), ""])
    lines.append("<i>Цена и наличие актуальны на дату проверки. Перед покупкой ещё раз уточните доставку и гарантию.</i>")
    return "\n".join(lines).strip()
