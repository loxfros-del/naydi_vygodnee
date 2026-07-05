"""Обработчики админа — панель управления, автопоиск, работа с вариантами."""
import asyncio
import html
import json
from aiogram.exceptions import TelegramBadRequest
from urllib.parse import urlparse
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from app.config import settings
from app.states import AdminStates
from app.keyboards import (
    kb_admin_menu, kb_admin_request, kb_admin_back,
    kb_admin_product, kb_admin_results_list, kb_alice_product,
    kb_alice_search_links,
    kb_market_check_actions, kb_market_check_reasons, kb_market_check_skip,
    kb_ready_for_payment, kb_payment_confirmed, kb_send_report_blocked,
    kb_send_without_payment_confirm, kb_admin_detail_bottom,
)
from app.db import (
    get_all_requests, get_request, update_request, Request,
    get_search_results, get_all_search_results, update_search_result,
    delete_search_result, count_search_results, count_approved_results,
    create_search_result, get_search_result, get_search_attempts,
    get_manual_search_links, SearchResult, get_alice_results,
    replace_alice_results, set_alice_top_result,
    create_market_check, get_market_checks, get_market_check,
    update_market_check, delete_market_check, has_market_check,
    MarketCheck,
)
from app.search_links import generate_search_links, generate_product_search_links
from app.link_checks import LinkCheckStatus, avito_warnings, store_url_warning
from app.product_search import run_product_search
from app.ai_cards_service import generate_ai_cards_from_candidates
from app.report_builder import (
    build_preview, build_admin_preview, build_full_report, get_alice_report_issues,
)
from app.readiness import check_readiness, format_readiness, format_readiness_short
from app.price_extractor import format_price, extract_price as _extract_price

router = Router()


def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_IDS


def format_request_card(req: Request) -> str:
    """Карточка заявки для списка."""
    status_emoji = {
        "NEW": "🆕", "QUESTIONS": "❓", "SEARCHING": "🔎",
        "HUMAN_REVIEW": "👀", "PREVIEW_SENT": "👁",
        "WAITING_PAYMENT": "⏳", "PAID": "💰",
        "REPORT_SENT": "📤", "CLOSED": "🔒"
    }
    emoji = status_emoji.get(req.status, "❓")
    username = f"@{req.username}" if req.username else str(req.user_id)
    product_label = req.product_name or req.product or "?"
    info = f"{emoji} <b>#{req.id}</b> | {username} | {product_label[:40]}"
    if req.use_case:
        info += f" | 🎯 {req.use_case[:20]}"
    if req.budget:
        budget_display = req.budget
        if budget_display.isdigit():
            budget_display = format_price(int(budget_display))
        info += f" | 💰 {budget_display}"
    if req.city:
        info += f" | 📍 {req.city[:15]}"
    return info


def format_request_detail(req: Request) -> str:
    """Детальная карточка заявки."""
    lines = [
        f"📋 <b>Заявка #{req.id}</b>",
        f"👤 Пользователь: @{req.username or req.user_id}",
    ]

    # Новые поля
    if req.original_query:
        lines.append(f"💬 Исходный запрос: <i>{req.original_query[:100]}</i>")
    if req.product_name:
        lines.append(f"📦 Товар: <b>{req.product_name}</b>")
    elif req.product:
        lines.append(f"📦 Товар: {req.product}")
    if req.use_case:
        lines.append(f"🎯 Цель: {req.use_case}")
    if req.budget:
        budget_display = req.budget
        if budget_display.isdigit():
            budget_display = format_price(int(budget_display))
        lines.append(f"💰 Бюджет: {budget_display}")
    if req.city:
        lines.append(f"📍 Город: {req.city}")
    if req.important_criteria:
        lines.append(f"📌 Критерии: {req.important_criteria}")
    if req.clean_search_query:
        lines.append(f"🔎 Чистый запрос: <i>{req.clean_search_query}</i>")
    if req.is_used_allowed:
        lines.append("🔄 Можно б/у")

    lines.append(f"📊 Статус: <b>{req.status}</b>")

    # Счётчики результатов поиска
    total = count_search_results(req.id)
    approved = count_approved_results(req.id)
    if total > 0:
        lines.append(f"\n🔎 Найдено кандидатов: {total}")
        lines.append(f"✅ Подтверждено: {approved}")

    # Краткая проверка готовности (проблемы одной строкой)
    readiness = check_readiness(req)
    lines.append(format_readiness_short(readiness))

    # Legacy found_products
    found = json.loads(req.found_products) if req.found_products else []
    if found:
        lines.append(f"\n🛒 Ручные варианты: {len(found)}")
        for i, item in enumerate(found, 1):
            name = item.get("name", "?")
            price = item.get("price", "")
            source = item.get("source", "")
            entry = f"  {i}. {name}"
            if price:
                entry += f" — {price}"
            if source:
                entry += f" ({source})"
            lines.append(entry)

    return "\n".join(lines)


def format_search_result_card(sr: SearchResult, idx: int) -> str:
    """Карточка найденного варианта."""
    status_emoji = {
        "CANDIDATE": "📦", "APPROVED": "✅", "REJECTED": "❌",
        "BEST": "⭐", "CHEAP": "💸", "RELIABLE": "🛡", "DO_NOT_BUY": "🚫"
    }
    emoji = status_emoji.get(sr.status, "📦")
    line = f"{emoji} #{idx}\n<b>{html.escape(sr.title[:120])}</b>"
    if sr.price:
        line += f"\n💰 Цена: {format_price(sr.price)}"
    else:
        line += "\n💰 Цена: <i>цена не найдена</i>"
    line += f"\n🏪 Источник: {html.escape(sr.source or 'generic_web')}"
    line += f"\n📊 Score: {int(round(sr.score))}"
    try:
        risk_flags = json.loads(sr.risk_flags) if sr.risk_flags else []
    except json.JSONDecodeError:
        risk_flags = []
    line += f"\n⚠️ Риски: {html.escape(', '.join(risk_flags) if risk_flags else 'нет')}"
    if sr.admin_note:
        line += f"\n📝 {html.escape(sr.admin_note)}"
    return line


RESULTS_PAGE_SIZE = 6
RESULTS_TEXT_LIMIT = 3500
RESULTS_HARD_LIMIT = 3900


def _clip_text(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _result_status_label(status: str) -> str:
    return {
        "CANDIDATE": "на проверке",
        "WEAK_CANDIDATE": "слабый",
        "APPROVED": "норм",
        "BEST": "лучший",
        "CHEAP": "дешёвый",
        "RELIABLE": "надёжный",
        "DO_NOT_BUY": "не брать",
        "REJECTED": "убран",
    }.get(status, status or "на проверке")


def _result_button_emoji(status: str) -> str:
    return {
        "CANDIDATE": "📦",
        "WEAK_CANDIDATE": "⚠️",
        "APPROVED": "✅",
        "BEST": "⭐",
        "CHEAP": "💸",
        "RELIABLE": "🛡",
        "DO_NOT_BUY": "🚫",
        "REJECTED": "❌",
    }.get(status, "📦")


def _parse_risk_flags(value: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [str(value)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item).strip()]
    if isinstance(parsed, str) and parsed.strip():
        return [parsed]
    return []


def _parse_facts(value: str) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _budget_status_label(status: str) -> str:
    return {
        "IN_BUDGET": "в бюджете",
        "OVER_BUDGET_SOFT": "чуть выше бюджета",
        "OVER_BUDGET_HARD": "сильно выше бюджета",
        "PRICE_MISSING": "цена не подтверждена",
    }.get(status or "", status or "бюджет не указан")


def _availability_label(facts: dict) -> str:
    available = facts.get("available")
    if available is True:
        return "есть"
    if available is False:
        return "нет"
    return "не подтверждено"


def _facts_compact_line(facts: dict) -> str:
    parts = []
    if facts.get("diagonal"):
        parts.append(f'{facts["diagonal"]}"')
    for key in ("resolution", "refresh_rate", "matrix_type"):
        if facts.get(key):
            parts.append(str(facts[key]))
    if facts.get("budget_status"):
        parts.append(_budget_status_label(str(facts["budget_status"])))
    return ", ".join(parts)


def _facts_ps5_line(facts: dict) -> str:
    flags = [str(item) for item in facts.get("ps5_flags") or [] if str(item).strip()]
    warnings = [str(item) for item in facts.get("warnings") or [] if str(item).strip()]
    items = flags + warnings[:2]
    return "; ".join(items)


def _facts_detail_lines(sr: SearchResult) -> list[str]:
    facts = _parse_facts(getattr(sr, "facts_json", ""))
    if not facts:
        return []
    model = facts.get("model") or facts.get("model_key") or "не подтверждена"
    diagonal = f'{facts.get("diagonal")}"' if facts.get("diagonal") else "не подтверждена"
    lines = [
        "",
        "<b>Факты:</b>",
        f"- Модель: {html.escape(str(model))}",
        f"- Диагональ: {html.escape(diagonal)}",
        f"- Разрешение: {html.escape(str(facts.get('resolution') or 'не подтверждено'))}",
        f"- Частота: {html.escape(str(facts.get('refresh_rate') or 'не подтверждена'))}",
        f"- Матрица: {html.escape(str(facts.get('matrix_type') or 'не подтверждена'))}",
        f"- Наличие: {html.escape(_availability_label(facts))}",
        f"- Бюджет: {html.escape(_budget_status_label(str(facts.get('budget_status') or '')))}",
    ]
    ps5_line = _facts_ps5_line(facts)
    if ps5_line:
        lines.append(f"- PS5: {html.escape(ps5_line)}")
    return lines


def format_search_result_summary(sr: SearchResult, idx: int) -> str:
    """Короткая строка результата для общего списка без URL и длинных полей."""
    title = html.escape(_clip_text(sr.title or "без названия", 95))
    price = format_price(sr.price) if sr.price else "цена не найдена"
    source = html.escape(_clip_text(sr.source or "generic_web", 45))
    status = html.escape(_result_status_label(sr.status))
    facts = _parse_facts(getattr(sr, "facts_json", ""))
    facts_line = _facts_compact_line(facts)
    ps5_line = _facts_ps5_line(facts)
    risks = _parse_risk_flags(sr.risk_flags)[:3]
    risk_text = _clip_text("; ".join(risks) if risks else "нет", 180)
    lines = [
        f"{idx}. <b>{title}</b>\n"
        f"   {html.escape(price)} | {source} | {status} | score {int(round(sr.score))}"
    ]
    if facts_line:
        lines.append(f"   {html.escape(facts_line)}")
    if ps5_line:
        lines.append(f"   PS5: {html.escape(_clip_text(ps5_line, 120))}")
    lines.append(f"   Риски: {html.escape(risk_text)}")
    return "\n".join(lines)


def _trim_message_text(text: str, limit: int = RESULTS_HARD_LIMIT) -> str:
    notice = "\n\nТекст обрезан, открой конкретную карточку для деталей."
    if len(text) <= limit:
        return text
    trimmed = text[: max(0, limit - len(notice))].rsplit("\n", 1)[0].rstrip()
    return trimmed + notice


async def _safe_edit_text(callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup) -> None:
    safe_text = _trim_message_text(text)
    try:
        await callback.message.edit_text(
            safe_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if "message is not modified" in message:
            return
        if "message is too long" in message or "message_too_long" in message:
            await callback.message.edit_text(
                _trim_message_text(safe_text, 3900),
                reply_markup=reply_markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        raise


def build_results_page(req_id: int, results: list[SearchResult], rejected_auto_count: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
    total = len(results)
    page_count = max(1, (total + RESULTS_PAGE_SIZE - 1) // RESULTS_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    start = page * RESULTS_PAGE_SIZE
    page_items = results[start:start + RESULTS_PAGE_SIZE]

    lines = [
        f"📦 <b>Найденные варианты (заявка #{req_id})</b>",
        f"Страница {page + 1} из {page_count}. Нормальных: {total}",
    ]
    if rejected_auto_count:
        lines.append(f"Автоматически отклонено: {rejected_auto_count}")
    lines.append("")

    rendered: list[tuple[int, SearchResult]] = []
    for offset, sr in enumerate(page_items, 1):
        idx = start + offset
        card = format_search_result_summary(sr, idx)
        candidate_text = "\n".join(lines + [card, ""])
        if len(candidate_text) > RESULTS_TEXT_LIMIT and rendered:
            break
        lines.append(card)
        lines.append("")
        rendered.append((idx, sr))

    shown = len(rendered)
    lines.append(f"Показано {shown} из {total}. Остальные доступны по кнопкам / страницам.")
    if not rendered and total:
        lines.append("Текст обрезан, открой конкретную карточку для деталей.")

    buttons: list[list[InlineKeyboardButton]] = []
    for idx, sr in rendered:
        title = _clip_text(sr.title or "без названия", 35)
        buttons.append([InlineKeyboardButton(
            text=f"{_result_button_emoji(sr.status)} {idx}. {title}",
            callback_data=f"viewresult_{sr.id}",
        )])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"results_{req_id}_{page - 1}"))
    if page < page_count - 1:
        nav.append(InlineKeyboardButton(text="➡️ Далее", callback_data=f"results_{req_id}_{page + 1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="🔎 Запустить автопоиск", callback_data=f"autosearch_{req_id}")])
    buttons.append([InlineKeyboardButton(text="🤖 Сделать ИИ-карточки из автопоиска", callback_data=f"aicards_{req_id}")])
    buttons.append([InlineKeyboardButton(text="🧪 Debug поиска", callback_data=f"debugsearch_{req_id}")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить вручную", callback_data=f"addprod_{req_id}")])
    buttons.append([InlineKeyboardButton(text="👁 Полный предпросмотр для админа", callback_data=f"adminpreview_{req_id}")])
    buttons.append([InlineKeyboardButton(text="👀 Клиентский предпросмотр до оплаты", callback_data=f"preview_{req_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)


def _link_status(url: str) -> str:
    """Статус формы ссылки без попытки открывать внешние сайты."""
    if not url:
        return "⚠️ ссылка нужна"
    try:
        parsed = urlparse(url)
    except ValueError:
        return "❌ битая"
    return "✅ есть" if parsed.scheme in ("http", "https") and parsed.netloc else "❌ битая"


def _link_check_status(item: SearchResult) -> str:
    return {
        LinkCheckStatus.NEEDED.value: "⚠️ ссылка нужна",
        LinkCheckStatus.FOUND_UNVERIFIED.value: "🔍 ссылка найдена, но не проверена",
        LinkCheckStatus.VERIFIED.value: "✅ ссылка проверена админом",
        LinkCheckStatus.UNSUITABLE.value: "❌ ссылка не подходит",
    }.get(item.link_check_status, "⚠️ ссылка нужна")


def _price_check_status(item: SearchResult) -> str:
    if item.price and item.price_verified:
        return "✅ цена проверена админом"
    return "⚠️ цену нужно подтвердить"


def _card_link_warnings(item: SearchResult) -> list[str]:
    req = get_request(item.request_id)
    if not req:
        return []
    warnings: list[str] = []
    store_warning = store_url_warning(item.source, item.url)
    if store_warning:
        warnings.append(store_warning)
    prices = [candidate.price for candidate in get_alice_results(req.id) if candidate.price]
    warnings.extend(avito_warnings(
        city=req.city,
        is_used_allowed=req.is_used_allowed,
        title=item.title,
        description=item.snippet,
        price=item.price,
        comparison_prices=prices,
        url=item.url,
    ))
    return warnings


def _alice_card_meta(item: SearchResult) -> dict:
    if not item.admin_note:
        return {}
    try:
        data = json.loads(item.admin_note)
    except json.JSONDecodeError:
        return {"note": item.admin_note}
    return data if isinstance(data, dict) else {}


def format_alice_card(sr: SearchResult, idx: int) -> str:
    """Карточка одного товара, полученного из ответа Алисы."""
    try:
        risks = json.loads(sr.risk_flags) if sr.risk_flags else []
    except json.JSONDecodeError:
        risks = []
    req = get_request(sr.request_id)
    meta = _alice_card_meta(sr)
    state = {
        "BEST": "🏆 ТОП-1",
        "TOP": "🏆 ТОП-1",
        "TOP1": "🏆 ТОП-1",
        "APPROVED": "✅ оставлен",
        "BACKUP": "✅ Запасной",
        "APPROVED_BACKUP": "✅ Запасной подтверждён",
        "BUDGET": "💰 Бюджетный",
        "APPROVED_BUDGET": "💰 Бюджетный",
        "DO_NOT_BUY": "⚠️ Осторожно / не брать",
        "CAUTION": "⚠️ Осторожно / не брать",
        "REJECTED": "❌ убран",
        "REJECTED_AUTO": "❌ авто-отклонён",
    }.get(sr.status, "🟡 на проверке")
    lines = [f"🧩 <b>Карточка {idx}</b> — {state}"]
    if meta.get("role"):
        lines.append(f"<b>Роль ИИ:</b> {html.escape(str(meta.get('role')))}")
    if meta.get("confidence"):
        lines.append(f"<b>Confidence:</b> {html.escape(str(meta.get('confidence')))}")
    lines.append(f"<b>Название:</b> {html.escape(sr.title or 'не указано')}")
    lines.append(f"<b>Цена:</b> {format_price(sr.price) if sr.price else 'уточнить'}")
    lines.append(f"<b>Магазин:</b> {html.escape(sr.source or 'не указан')}")
    if _link_status(sr.url) == "✅ есть":
        safe_url = html.escape(sr.url, quote=True)
        lines.append(f'<b>Ссылка:</b> <a href="{safe_url}">открыть</a>')
    else:
        lines.append("<b>Ссылка:</b> ⚠️ ссылку нужно искать вручную")
    lines.append(f"<b>Почему:</b> {html.escape(sr.snippet or 'не указано')}")
    lines.append(f"<b>Риск:</b> {html.escape('; '.join(risks) if risks else 'не указан')}")
    manual_check = meta.get("manual_check") or meta.get("notes") or []
    if isinstance(manual_check, str):
        manual_check = [manual_check] if manual_check.strip() else []
    if manual_check:
        lines.append(f"<b>Проверить вручную:</b> {html.escape('; '.join(str(item) for item in manual_check))}")
    elif meta.get("note"):
        lines.append(f"<b>Заметка:</b> {html.escape(str(meta.get('note')))}")
    lines.append(f"<b>Статус ссылки:</b> {_link_status(sr.url)}")
    lines.append(f"<b>Статус проверки:</b> {_link_check_status(sr)}")
    lines.append(f"<b>Статус цены:</b> {_price_check_status(sr)}")
    if req and req.budget and req.budget.isdigit() and sr.price and sr.price > int(req.budget):
        lines.append("⚠️ Цена выше бюджета клиента.")
    if _link_status(sr.url) != "✅ есть":
        lines.append("⚠️ Ссылка отсутствует или некорректна.")
    lines.extend(_card_link_warnings(sr))
    return "\n".join(lines)


def _alice_card_index(result_id: int, request_id: int) -> int:
    for index, item in enumerate(get_alice_results(request_id), 1):
        if item.id == result_id:
            return index
    return 1


async def _refresh_alice_card(message: Message, sr: SearchResult) -> None:
    index = _alice_card_index(sr.id, sr.request_id)
    link_status = getattr(sr, "link_check_status", "NEEDED") or "NEEDED"
    price_ok = bool(getattr(sr, "price_verified", False))

    try:
        await message.edit_text(
            format_alice_card(sr, index),
            reply_markup=kb_alice_product(sr.id, sr.status, link_check_status=link_status, price_verified=price_ok),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        raise


async def send_alice_cards(message: Message, req_id: int, include_header: bool = True) -> None:
    """Показывает карточки Алисы отдельными сообщениями с действиями под каждой."""
    cards = get_alice_results(req_id)
    if include_header:
        await message.answer(
            f"🧩 <b>Карточки ИИ для заявки #{req_id}</b>\n"
            f"Найдено товаров: {len(cards)}",
            parse_mode="HTML",
        )
    if not cards:
        await message.answer("Карточек ИИ пока нет. Нажми «🟡 Проверить через Алису» и вставь ответ Алисы или GigaChat.")
        return
    for index, card in enumerate(cards[:20], 1):
        link_status = getattr(card, "link_check_status", LinkCheckStatus.NEEDED.value) or LinkCheckStatus.NEEDED.value
        price_ok = bool(getattr(card, "price_verified", False))
        await message.answer(
            format_alice_card(card, index),
            reply_markup=kb_alice_product(card.id, card.status, link_check_status=link_status, price_verified=price_ok),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def _telegram_chunks(text: str, limit: int = 3900) -> list[str]:
    """Разбивает длинный админский предпросмотр по строкам для Telegram."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > limit:
            chunks.append(current.rstrip())
            current = ""
        current += line
    if current:
        chunks.append(current.rstrip())
    return chunks or [text]


def format_search_debug(req_id: int) -> str:
    """Компактный отчёт для Telegram: запрос, ответ и фильтрация по источнику."""
    attempts = list(reversed(get_search_attempts(req_id)))
    lines = [f"🧪 <b>Debug поиска: заявка #{req_id}</b>"]
    if not attempts:
        return "\n".join(lines + ["\nПопыток автопоиска ещё не было."])
    for item in attempts[-35:]:
        status_emoji = {"OK": "✅", "EMPTY": "▫️", "ERROR": "⚠️"}.get(item.status, "❔")
        lines.append(
            f"\n{status_emoji} <b>{html.escape(item.source)}</b> — {item.status}"
            f"\n<i>{html.escape(item.query[:180])}</i>"
            f"\nДо фильтрации: {item.found_count}; кандидатов: {item.kept_count}"
        )
        if item.error_text:
            lines.append(f"Ошибка: <code>{html.escape(item.error_text[:240])}</code>")
    links = get_manual_search_links(req_id)
    if links:
        lines.append("\n<b>Ручные ссылки fallback (не товары):</b>")
        for link in links[:8]:
            lines.append(f'• <a href="{html.escape(link["url"], quote=True)}">{html.escape(link["source"] or "поиск")}</a>')
    return "\n".join(lines)


# ---------- Команды ----------

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await message.answer(
        "🔧 <b>Панель админа</b>\n\nВыбери раздел:",
        reply_markup=kb_admin_menu(),
        parse_mode="HTML"
    )


# ---------- Список заявок ----------

@router.callback_query(F.data == "admin_all")
async def admin_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    reqs = get_all_requests()
    if not reqs:
        await callback.message.edit_text("Заявок пока нет.", reply_markup=kb_admin_back())
        return
    text = "📋 <b>Все заявки:</b>\n\n"
    text += "\n".join(format_request_card(r) for r in reqs[:20])
    buttons = [[InlineKeyboardButton(text=f"#{r.id} {r.product[:25]}", callback_data=f"view_{r.id}")]
               for r in reqs[:20]]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_new")
async def admin_new(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    reqs = get_all_requests(status="NEW")
    if not reqs:
        await callback.message.edit_text("Новых заявок нет.", reply_markup=kb_admin_back())
        return
    text = "🆕 <b>Новые заявки:</b>\n\n"
    text += "\n".join(format_request_card(r) for r in reqs[:20])
    buttons = [[InlineKeyboardButton(text=f"#{r.id} {r.product[:25]}", callback_data=f"view_{r.id}")]
               for r in reqs[:20]]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_searching")
async def admin_searching(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    reqs = get_all_requests(status="SEARCHING")
    if not reqs:
        await callback.message.edit_text("Заявок в поиске нет.", reply_markup=kb_admin_back())
        return
    text = "🔎 <b>В поиске:</b>\n\n"
    text += "\n".join(format_request_card(r) for r in reqs[:20])
    buttons = [[InlineKeyboardButton(text=f"#{r.id} {r.product[:25]}", callback_data=f"view_{r.id}")]
               for r in reqs[:20]]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_waiting")
async def admin_waiting(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    reqs = get_all_requests(status="PREVIEW_SENT")
    if not reqs:
        await callback.message.edit_text("Ожидающих оплаты заявок нет.", reply_markup=kb_admin_back())
        return
    text = "⏳ <b>Ожидают оплаты:</b>\n\n"
    text += "\n".join(format_request_card(r) for r in reqs[:20])
    buttons = [[InlineKeyboardButton(text=f"#{r.id} {r.product[:25]}", callback_data=f"view_{r.id}")]
               for r in reqs[:20]]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🔧 <b>Панель админа</b>\n\nВыбери раздел:",
        reply_markup=kb_admin_menu(),
        parse_mode="HTML"
    )
    await callback.answer()


# ---------- Просмотр заявки ----------

@router.callback_query(F.data.startswith("view_"))
async def view_request(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    text = format_request_detail(req)

    # Поисковые ссылки
    links = json.loads(req.search_links) if req.search_links else []
    if links:
        text += "\n\n🔗 <b>Поисковые ссылки:</b>\n"
        for link in links:
            text += f'• <a href="{link["url"]}">{link["site"]}</a>\n'

    await callback.message.edit_text(
        text,
        reply_markup=kb_admin_request(req_id, req.status),
        parse_mode="HTML"
    )
    await callback.answer()


# ---------- Взять в работу ----------

@router.callback_query(F.data.startswith("take_"))
async def take_request(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    update_request(req_id, status="SEARCHING")
    await callback.answer("Заявка взята в работу")

    # Уведомляем пользователя
    req = get_request(req_id)
    if req:
        try:
            await callback.bot.send_message(
                req.user_id,
                f"🔎 Твоя заявка <b>#{req_id}</b> взята в работу!\n"
                f"Ищем: <b>{req.product_name or req.product}</b>\n\n"
                f"Скоро пришлю результаты.",
                parse_mode="HTML"
            )
        except Exception:
            pass

    # Обновляем сообщение
    req = get_request(req_id)
    text = format_request_detail(req)
    links = json.loads(req.search_links) if req.search_links else []
    if links:
        text += "\n\n🔗 <b>Поисковые ссылки:</b>\n"
        for link in links:
            text += f'• <a href="{link["url"]}">{link["site"]}</a>\n'
    await callback.message.edit_text(
        text,
        reply_markup=kb_admin_request(req_id, req.status),
        parse_mode="HTML"
    )


# ---------- Автопоиск ----------

@router.callback_query(F.data.startswith("autosearch_"))
async def auto_search(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    # Убедимся что статус SEARCHING
    if req.status == "NEW":
        update_request(req_id, status="SEARCHING")

    await callback.answer("Запускаю автопоиск...")

    # Запускаем поиск вне event loop, потому что внутри есть сетевые/блокирующие операции.
    result = await asyncio.to_thread(run_product_search, req)

    if result["success"]:
        await callback.message.answer(
            f"✅ {result['message']}\n\n"
            f"Теперь нажми «📦 Показать найденные варианты» для проверки.",
            reply_markup=kb_admin_request(req_id, "SEARCHING")
        )
    else:
        await callback.message.answer(
            f"⚠️ {result['message']}",
            reply_markup=kb_admin_request(req_id, "SEARCHING")
        )


@router.callback_query(F.data.startswith("aicards_"))
async def make_ai_cards(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    candidates = [item for item in get_all_search_results(req_id) if item.status != "REJECTED_AUTO"]
    if len(candidates) < 3:
        await callback.answer("Мало результатов. Сначала запустите автопоиск.", show_alert=True)
        return

    await callback.answer("Делаю ИИ-карточки...")
    result = await asyncio.to_thread(generate_ai_cards_from_candidates, req, candidates)
    if not result.get("success"):
        await callback.message.answer(
            f"⚠️ {html.escape(result.get('message') or 'Не удалось сделать ИИ-карточки.')}\n\n"
            "Можно нажать «🟡 Проверить через Алису» и вставить ответ вручную.",
            parse_mode="HTML",
            reply_markup=kb_admin_request(req_id, req.status),
        )
        return

    cards = result.get("cards") or []
    replace_alice_results(req_id, cards)
    update_request(
        req_id,
        alice_response=json.dumps({
            "source": "ai_cards_from_candidates",
            "raw_response": result.get("raw_response", ""),
            "parsed_items": cards,
        }, ensure_ascii=False),
    )
    if req.status == "NEW":
        update_request(req_id, status="SEARCHING")

    await callback.message.answer(
        f"✅ ИИ-карточки готовы. Найдено: {len(cards)}.\n"
        "Проверь ТОП-3–5, подтверди цену и ссылку.",
        parse_mode="HTML",
    )
    await send_alice_cards(callback.message, req_id, include_header=False)


@router.callback_query(F.data.startswith("debugsearch_"))
async def debug_search(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    if not get_request(req_id):
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await callback.message.answer(format_search_debug(req_id), parse_mode="HTML", disable_web_page_preview=True)
    await callback.answer()


@router.message(Command("debug_search"))
async def cmd_debug_search(message: Message):
    """/debug_search 12 — диагностика автопоиска для админа."""
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        await message.answer("Формат: <code>/debug_search REQUEST_ID</code>", parse_mode="HTML")
        return
    req_id = int(parts[1].strip())
    if not get_request(req_id):
        await message.answer("Заявка не найдена.")
        return
    await message.answer(format_search_debug(req_id), parse_mode="HTML", disable_web_page_preview=True)


# ---------- Показать найденные варианты ----------

@router.callback_query(F.data.startswith("results_"))
@router.callback_query(F.data.startswith("showresults_"))
async def show_results(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return

    data_parts = callback.data.split("_")
    page = 0
    # Кнопка из карточки товара имеет вид showresults_back_RESULT_ID.
    if len(data_parts) == 3 and data_parts[1] == "back":
        previous = get_search_result(int(data_parts[2]))
        if not previous:
            await callback.answer("Вариант не найден", show_alert=True)
            return
        req_id = previous.request_id
    elif data_parts[0] == "results":
        req_id = int(data_parts[1])
        if len(data_parts) >= 3 and data_parts[2].isdigit():
            page = int(data_parts[2])
    else:
        req_id = int(data_parts[1])
        if len(data_parts) >= 3 and data_parts[2].isdigit():
            page = int(data_parts[2])

    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    all_results = get_all_search_results(req_id)
    # Автоматически отклонённые статьи, категории и не те типы товаров
    # остаются в БД для диагностики, но не попадают в очередь проверки.
    rejected_auto = [item for item in all_results if item.status == "REJECTED_AUTO"]
    results = [item for item in all_results if item.status != "REJECTED_AUTO"]

    if not results:
        await callback.message.edit_text(
            f"📦 Заявка #{req_id}\n\nНормальных вариантов пока не найдено."
            + (f" Автоматически отклонено: {len(rejected_auto)}." if rejected_auto else "")
            + "\n"
            f"Нажми «🔎 Запустить автопоиск» или «➕ Добавить вручную».",
            reply_markup=kb_admin_results_list(req_id),
            parse_mode="HTML"
        )
        await callback.answer()
        return

    text, markup = build_results_page(req_id, results, len(rejected_auto), page)
    await _safe_edit_text(callback, text, markup)
    await callback.answer()


# ---------- Просмотр конкретного найденного товара ----------

@router.callback_query(F.data.startswith("viewresult_"))
async def view_result(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])

    sr = get_search_result(result_id)
    if not sr:
        await callback.answer("Вариант не найден", show_alert=True)
        return

    # Карточка товара
    lines = [f"📦 <b>Вариант</b>\n"]
    lines.append(f"<b>{html.escape(sr.title)}</b>\n")
    if sr.price:
        lines.append(f"💰 Цена: {format_price(sr.price)}")
    else:
        lines.append("💰 Цена: <i>цена не найдена</i>")
    lines.append(f"🏪 Источник: {html.escape(sr.source or 'generic_web')}")
    if sr.url:
        lines.append(f"🔗 <a href=\"{html.escape(sr.url, quote=True)}\">Ссылка</a>")
    if sr.snippet:
        lines.append(f"\n<i>{html.escape(sr.snippet[:200])}</i>")
    lines.append(f"\n📊 Score: {int(round(sr.score))}")
    lines.append(f"📋 Статус: {sr.status}")
    lines.extend(_facts_detail_lines(sr))

    try:
        risk_flags = json.loads(sr.risk_flags) if sr.risk_flags else []
    except json.JSONDecodeError:
        risk_flags = []
    if risk_flags:
        lines.append(f"⚠️ Флаги: {', '.join(risk_flags)}")

    if sr.admin_note:
        lines.append(f"\n📝 Заметка: {sr.admin_note}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_admin_product(result_id, sr.status),
        parse_mode="HTML"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("nextresult_"))
async def next_result(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    current = get_search_result(int(callback.data.split("_")[1]))
    if not current:
        await callback.answer("Вариант не найден", show_alert=True)
        return
    results = get_all_search_results(current.request_id)
    try:
        index = next(i for i, item in enumerate(results) if item.id == current.id)
    except StopIteration:
        await callback.answer("Вариант не найден", show_alert=True)
        return
    next_item = results[(index + 1) % len(results)]
    lines = ["📦 <b>Вариант</b>\n", f"<b>{html.escape(next_item.title)}</b>\n"]
    lines.append(f"💰 Цена: {format_price(next_item.price)}" if next_item.price else "💰 Цена: <i>цена не найдена</i>")
    lines.append(f"🏪 Источник: {html.escape(next_item.source or 'generic_web')}")
    if next_item.url:
        lines.append(f"🔗 <a href=\"{html.escape(next_item.url, quote=True)}\">Ссылка</a>")
    if next_item.snippet:
        lines.append(f"\n<i>{html.escape(next_item.snippet[:200])}</i>")
    lines.append(f"\n📊 Score: {int(round(next_item.score))}")
    lines.append(f"📋 Статус: {next_item.status}")
    lines.extend(_facts_detail_lines(next_item))
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=kb_admin_product(next_item.id, next_item.status), parse_mode="HTML"
    )
    await callback.answer()


# ---------- Маркировка товаров ----------

async def _mark_result(callback: CallbackQuery, new_status: str, label: str):
    """Общая функция для маркировки результата."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])

    # Находим результат для получения request_id
    from app.db import get_conn
    conn = get_conn()
    row = conn.execute("SELECT * FROM search_results WHERE id = ?", (result_id,)).fetchone()
    conn.close()
    if not row:
        await callback.answer("Вариант не найден", show_alert=True)
        return

    sr = SearchResult(**dict(row))
    update_search_result(result_id, status=new_status)
    await callback.answer(f"Отмечено: {label}")

    # Обновляем карточку товара
    sr.status = new_status
    lines = [f"📦 <b>Вариант</b>\n"]
    lines.append(f"<b>{sr.title}</b>\n")
    if sr.price:
        lines.append(f"💰 Цена: {format_price(sr.price)}")
    if sr.source:
        lines.append(f"🏪 Источник: {sr.source}")
    if sr.url:
        lines.append(f"🔗 <a href=\"{sr.url}\">Ссылка</a>")
    lines.append(f"\n📋 Статус: {new_status}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=kb_admin_product(result_id, new_status),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("markbest_"))
async def mark_best(callback: CallbackQuery):
    await _mark_result(callback, "BEST", "⭐ Лучший")


@router.callback_query(F.data.startswith("markcheap_"))
async def mark_cheap(callback: CallbackQuery):
    await _mark_result(callback, "CHEAP", "💸 Дешёвый норм")


@router.callback_query(F.data.startswith("markreliable_"))
async def mark_reliable(callback: CallbackQuery):
    await _mark_result(callback, "RELIABLE", "🛡 Надёжный")


@router.callback_query(F.data.startswith("markapproved_"))
async def mark_approved(callback: CallbackQuery):
    await _mark_result(callback, "APPROVED", "✅ Одобрено")


@router.callback_query(F.data.startswith("markreject_"))
async def mark_reject(callback: CallbackQuery):
    await _mark_result(callback, "DO_NOT_BUY", "❌ Не брать")


@router.callback_query(F.data.startswith("deleteresult_"))
async def delete_result(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])

    # Находим request_id перед удалением
    from app.db import get_conn
    conn = get_conn()
    row = conn.execute("SELECT request_id FROM search_results WHERE id = ?", (result_id,)).fetchone()
    conn.close()
    if not row:
        await callback.answer("Вариант не найден", show_alert=True)
        return

    req_id = row["request_id"]
    delete_search_result(result_id)
    await callback.answer("Удалено")

    # Возвращаемся к списку
    req = get_request(req_id)
    if req:
        all_results = get_all_search_results(req_id)
        rejected_auto = [item for item in all_results if item.status == "REJECTED_AUTO"]
        results = [item for item in all_results if item.status != "REJECTED_AUTO"]
        if results:
            text, markup = build_results_page(req_id, results, len(rejected_auto), 0)
            await _safe_edit_text(callback, text, markup)
        else:
            await callback.message.edit_text(
                f"📦 Заявка #{req_id}\n\nВариантов больше нет.",
                reply_markup=kb_admin_results_list(req_id),
                parse_mode="HTML"
            )


# ---------- Добавить вариант вручную ----------

def _save_manual_result(req: Request, raw: str) -> tuple[int, str]:
    """Сохраняет ручной ввод через общий парсер ИИ-карточек."""
    budget_num = int(req.budget) if req.budget and req.budget.isdigit() else None
    parsed_items = parse_alice_response(raw, budget=budget_num)
    if not parsed_items:
        raise ValueError("Не удалось собрать карточку из текста.")

    first_id = 0
    first_title = ""
    sort_order = len(get_alice_results(req.id)) + 1
    found = json.loads(req.found_products) if req.found_products else []

    for offset, item in enumerate(parsed_items):
        title = (item.get("name") or item.get("title") or "").strip()
        if not title:
            continue
        price = item.get("price_num")
        source = (item.get("store") or item.get("source") or "магазин нужно уточнить").strip()
        url = (item.get("link") or item.get("url") or "").strip()
        pluses = item.get("pluses") or []
        risks = item.get("risks") or []
        if isinstance(pluses, str):
            pluses = [pluses]
        if isinstance(risks, str):
            risks = [risks]
        snippet = "; ".join(pluses)
        risk_flags = list(risks) + ["добавлено вручную"]
        link_status = (
            LinkCheckStatus.FOUND_UNVERIFIED.value
            if url.startswith(("http://", "https://"))
            else LinkCheckStatus.NEEDED.value
        )

        result_id = create_search_result(
            request_id=req.id,
            title=title[:300],
            url=url[:500],
            source=source[:100],
            price=price,
            snippet=snippet[:1000],
            score=55.0,
            risk_flags=json.dumps(risk_flags, ensure_ascii=False),
            status="APPROVED",
            origin="alice",
            sort_order=sort_order + offset,
            price_verified=False,
            link_check_status=link_status,
        )
        if not first_id:
            first_id = result_id
            first_title = title
        found.append({
            "name": title,
            "price": item.get("price") or (str(price) if price else ""),
            "source": source,
            "url": url,
            "note": snippet,
        })

    if not first_id:
        raise ValueError("Не удалось найти название товара.")

    # Legacy-поле оставляем синхронным для уже созданных заявок.
    update_request(req.id, found_products=json.dumps(found, ensure_ascii=False))
    return first_id, first_title


@router.message(Command("add_result"))
async def cmd_add_result(message: Message):
    """/add_result REQUEST_ID | Название | Цена | Магазин | Ссылка | Плюсы | Риски"""
    if not is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    raw = (message.text or "").partition(" ")[2].strip()
    request_part, separator, payload = raw.partition(" ")
    if not separator or not request_part.strip().isdigit():
        await message.answer(
            "Формат: <code>/add_result REQUEST_ID текст карточки</code>",
            parse_mode="HTML",
        )
        return
    req = get_request(int(request_part.strip()))
    if not req:
        await message.answer("Заявка не найдена.")
        return
    try:
        _, title = _save_manual_result(req, payload.strip().lstrip("|").strip())
    except ValueError as exc:
        await message.answer(f"Не добавлено: {exc}")
        return
    await message.answer(f"✅ Вариант «{html.escape(title)}» добавлен. Он уже виден в «📦 Показать найденные варианты».", parse_mode="HTML")

@router.callback_query(F.data.startswith("addprod_"))
async def add_product_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    await state.update_data(addprod_req_id=req_id)
    await state.set_state(AdminStates.adding_product)
    await callback.message.answer(
        f"Заявка #{req_id}.\n\n"
        "Вставь вариант любым текстом. Можно одной строкой или блоком. Я сам соберу карточку.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminStates.adding_product)
async def add_product_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    fsm_data = await state.get_data()
    req_id = fsm_data.get("addprod_req_id")
    if not req_id:
        await state.clear()
        return

    req = get_request(req_id)
    if not req:
        await message.answer("Заявка не найдена.")
        await state.clear()
        return

    try:
        _, title = _save_manual_result(req, message.text.strip())
    except ValueError as exc:
        await message.answer(f"Не добавлено: {exc}")
        return

    await state.clear()
    total = count_search_results(req_id)
    await message.answer(
        f"✅ Вариант «{html.escape(title)}» добавлен. Всего кандидатов: {total}",
        reply_markup=kb_admin_request(req_id, req.status),
        parse_mode="HTML",
    )


# ---------- Полный предпросмотр для админа ----------

@router.callback_query(F.data.startswith("adminpreview_"))
async def send_admin_preview(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    for chunk in _telegram_chunks(build_admin_preview(req)):
        await callback.message.answer(
            chunk,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    await callback.answer("Полный предпросмотр показан только вам")


# ---------- Проверка готовности (readiness) ----------

@router.callback_query(F.data.startswith("readiness_"))
async def readiness_check(callback: CallbackQuery):
    """Админ нажал «🔍 Проверить готовность»."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    report = check_readiness(req)
    await callback.message.answer(
        format_readiness(report),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer("Проверка готовности")


# ---------- Ограниченный предпросмотр для клиента ----------

@router.callback_query(F.data.startswith("preview_"))
async def send_preview(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    preview = build_preview(req)

    # Проверяем, можно ли отправить
    if preview.startswith("Нельзя"):
        await callback.answer(preview, show_alert=True)
        return

    try:
        await callback.bot.send_message(req.user_id, preview, parse_mode="HTML")
        update_request(req_id, status="PREVIEW_SENT", preview_text=preview)
        await callback.answer("Клиентский предпросмотр отправлен")
    except Exception as e:
        await callback.answer(f"Ошибка отправки: {e}", show_alert=True)
        return

    # Обновляем сообщение админа
    req = get_request(req_id)
    text = format_request_detail(req)
    links = json.loads(req.search_links) if req.search_links else []
    if links:
        text += "\n\n🔗 <b>Поисковые ссылки:</b>\n"
        for link in links:
            text += f'• <a href="{link["url"]}">{link["site"]}</a>\n'
    await callback.message.edit_text(
        text,
        reply_markup=kb_admin_request(req_id, req.status),
        parse_mode="HTML"
    )


# ---------- Оплата получена ----------

@router.callback_query(F.data.startswith("paid_"))
async def mark_paid(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if req.status != "PREVIEW_SENT":
        await callback.answer("Сначала отправьте клиентский предпросмотр до оплаты.", show_alert=True)
        return
    issue, _missing_links = get_alice_report_issues(req)
    if issue:
        await callback.answer(issue, show_alert=True)
        return
    if count_approved_results(req_id) < 1:
        await callback.answer("Нельзя открыть полный отчёт: нет подтверждённых вариантов.", show_alert=True)
        return
    update_request(req_id, status="PAID")
    await callback.answer("Оплата подтверждена")

    req = get_request(req_id)
    if req:
        try:
            await callback.bot.send_message(
                req.user_id,
                f"✅ Оплата по заявке <b>#{req_id}</b> подтверждена.\n"
                f"Скоро пришлю полный отчёт.",
                parse_mode="HTML"
            )
        except Exception:
            pass

    # Обновляем сообщение
    req = get_request(req_id)
    text = format_request_detail(req)
    links = json.loads(req.search_links) if req.search_links else []
    if links:
        text += "\n\n🔗 <b>Поисковые ссылки:</b>\n"
        for link in links:
            text += f'• <a href="{link["url"]}">{link["site"]}</a>\n'
    await callback.message.edit_text(
        text,
        reply_markup=kb_admin_request(req_id, req.status),
        parse_mode="HTML"
    )


# ---------- Отправить отчёт ----------

async def _send_report(callback: CallbackQuery, req_id: int, force_without_links: bool = False):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if req.status != "PAID":
        await callback.answer("Сначала подтвердите оплату вручную.", show_alert=True)
        return

    issue, excluded_cards = get_alice_report_issues(req)
    if issue:
        await callback.answer(issue, show_alert=True)
        return
    if excluded_cards:
        cards_word = "карточка" if excluded_cards == 1 else "карточки" if excluded_cards < 5 else "карточек"
        await callback.message.answer(
            f"⚠️ {excluded_cards} {cards_word} не пройдут в полный отчёт: "
            "ссылка или цена не подтверждены админом.",
        )

    report = build_full_report(req)

    # Проверяем, можно ли отправить
    if report.startswith("Нельзя"):
        await callback.answer(report, show_alert=True)
        return

    try:
        # Разбиваем на чанки по 3900 символов (ограничение Telegram)
        chunks = _telegram_chunks(report)
        for chunk in chunks:
            await callback.bot.send_message(
                req.user_id, chunk, parse_mode="HTML",
                disable_web_page_preview=True,
            )
        update_request(req_id, status="REPORT_SENT", report_text=report)
        await callback.answer("Отчёт отправлен клиенту")
    except Exception as e:
        await callback.answer(f"Ошибка отправки: {e}", show_alert=True)
        return

    # Обновляем сообщение админа
    req = get_request(req_id)
    text = format_request_detail(req)
    text += f"\n\n✅ Отчёт отправлен. Статус: REPORT_SENT"
    await callback.message.edit_text(
        text,
        reply_markup=kb_admin_request(req_id, req.status),
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("sendreportok_"))
async def send_report_without_all_links(callback: CallbackQuery):
    req_id = int(callback.data.split("_")[1])
    await _send_report(callback, req_id, force_without_links=True)


# ---------- Изменить цену товара ----------

@router.callback_query(F.data.startswith("editprice_"))
async def edit_price_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    sr = get_search_result(result_id)
    if not sr:
        await callback.answer("Вариант не найден", show_alert=True)
        return
    await state.update_data(editprice_result_id=result_id)
    await state.set_state(AdminStates.editing_price)
    await callback.message.answer(
        f"Введи новую цену в рублях для:\n"
        f"<b>{html.escape(sr.title[:120])}</b>\n\n"
        f"Текущая: {format_price(sr.price) if sr.price else 'не задана'}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"viewresult_{result_id}")]
        ]),
    )
    await callback.answer()


@router.message(AdminStates.editing_price)
async def edit_price_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    fsm_data = await state.get_data()
    result_id = fsm_data.get("editprice_result_id")
    await state.clear()
    if not result_id:
        return

    new_price = _extract_price(message.text.strip())
    if new_price is None:
        await message.answer("❌ Не удалось определить цену. Введи число, например: <code>45000</code>", parse_mode="HTML")
        return

    update_search_result(result_id, price=new_price, price_verified=True)
    await message.answer(
        f"✅ Цена обновлена и подтверждена: <b>{format_price(new_price)}</b>",
        parse_mode="HTML",
    )

    # Обновить карточку
    sr = get_search_result(result_id)
    if sr:
        if sr.origin == "alice":
            await message.answer(
                format_alice_card(sr, _alice_card_index(sr.id, sr.request_id)),
                reply_markup=kb_alice_product(sr.id, sr.status),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            return
        lines = [f"📦 <b>Вариант</b>\n", f"<b>{html.escape(sr.title)}</b>\n"]
        lines.append(f"💰 Цена: {format_price(sr.price)}" if sr.price else "💰 Цена: <i>не задана</i>")
        lines.append(f"🏪 Источник: {html.escape(sr.source or 'generic_web')}")
        if sr.url:
            lines.append(f"🔗 <a href=\"{html.escape(sr.url, quote=True)}\">Ссылка</a>")
        await message.answer("\n".join(lines), parse_mode="HTML",
                             reply_markup=kb_admin_product(result_id, sr.status))


# ──────────────────────────────────────────────
#  Новая воронка: ready → preview → paid → send
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("readytopay_"))
@router.callback_query(F.data.startswith("readyforpay_"))
async def ready_for_payment(callback: CallbackQuery):
    """Админ нажал «Подборка готова / запросить оплату»."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    # Проверяем готовность
    readiness = check_readiness(req)
    if readiness.percent < 75:
        await callback.message.answer(
            f"⚠️ Готовность заявки: {readiness.percent}% — отправлять предпросмотр не рекомендуется.\n\n"
            f"{format_readiness(readiness)}",
            reply_markup=kb_ready_for_payment(req_id, readiness.percent),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await callback.answer()
        return

    # Отправляем предпросмотр
    preview = build_preview(req)
    if preview.startswith("Нельзя"):
        await callback.answer(preview, show_alert=True)
        return

    try:
        await callback.bot.send_message(req.user_id, preview, parse_mode="HTML")
        update_request(req_id, status="PREVIEW_SENT", preview_text=preview)
        await callback.answer("Клиентский предпросмотр отправлен")
    except Exception as e:
        await callback.answer(f"Ошибка отправки: {e}", show_alert=True)
        return

    # Показываем кнопку «Оплата подтверждена»
    await callback.message.edit_text(
        f"✅ <b>Заявка #{req_id}</b> — предпросмотр отправлен.\n"
        f"Готовность: {readiness.percent}%\n\n"
        "Когда клиент оплатит — нажми кнопку ниже.",
        reply_markup=kb_payment_confirmed(req_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("forcepreview_"))
async def force_preview(callback: CallbackQuery):
    """Принудительная отправка предпросмотра даже при слабой готовности."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    preview = build_preview(req)
    if preview.startswith("Нельзя"):
        await callback.answer(preview, show_alert=True)
        return

    try:
        await callback.bot.send_message(req.user_id, preview, parse_mode="HTML")
        update_request(req_id, status="PREVIEW_SENT", preview_text=preview)
        await callback.answer("Клиентский предпросмотр отправлен принудительно")
    except Exception as e:
        await callback.answer(f"Ошибка отправки: {e}", show_alert=True)
        return

    await callback.message.edit_text(
        f"✅ <b>Заявка #{req_id}</b> — предпросмотр отправлен принудительно.\n"
        "Когда клиент оплатит — нажми «💰 Оплата подтверждена».",
        reply_markup=kb_payment_confirmed(req_id),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("markpaid_"))
async def mark_paid_new(callback: CallbackQuery):
    """Админ нажал «💰 Оплата подтверждена» в любой точке воронки."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    # Проверяем, можно ли открыть полный отчёт
    issue, _missing_links = get_alice_report_issues(req)
    if issue:
        await callback.answer(issue, show_alert=True)
        return
    if count_approved_results(req_id) < 1 and not get_alice_results(req_id):
        await callback.answer("Нельзя открыть полный отчёт: нет подтверждённых вариантов.", show_alert=True)
        return

    update_request(req_id, status="PAID")
    await callback.answer("Оплата подтверждена ✅")

    # Уведомление клиенту
    if req:
        try:
            await callback.bot.send_message(
                req.user_id,
                f"✅ Оплата по заявке <b>#{req_id}</b> подтверждена.\n"
                f"Скоро пришлю полный отчёт.",
                parse_mode="HTML",
            )
        except Exception:
            pass

    # Обновляем сообщение
    req = get_request(req_id)
    readiness = check_readiness(req)
    can_send = not bool(issue) and readiness.can_send
    await callback.message.edit_text(
        f"💰 <b>Заявка #{req_id}</b> — оплата подтверждена.\n"
        f"Готовность: {readiness.percent}%\n\n"
        "Теперь можно отправить полный отчёт клиенту.",
        reply_markup=kb_admin_detail_bottom(
            req_id,
            readiness.percent,
            is_paid=True,
            report_sent=False,
            can_send_report=can_send,
        ),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("sendreportblocked_"))
async def send_report_blocked(callback: CallbackQuery):
    """Отправка отчёта заблокирована: сначала подтвердить оплату."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    await callback.message.edit_text(
        f"⚠️ <b>Заявка #{req_id}</b> — полный отчёт ещё не готов.\n\n"
        "Сначала подтвердите оплату кнопкой «💰 Оплата подтверждена».\n"
        "Или отправьте предпросмотр без оплаты (без прямых ссылок).",
        reply_markup=kb_send_report_blocked(req_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sos_sendreport_"))
async def sos_send_without_payment(callback: CallbackQuery):
    """Диалог: точно отправить без оплаты?"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    readiness = check_readiness(req)
    issue, _missing_links = get_alice_report_issues(req)

    await callback.message.edit_text(
        f"⚠️ <b>Заявка #{req_id}</b> — отправить полный отчёт без оплаты?\n\n"
        f"Готовность: {readiness.percent}%\n"
        + (f"Блокирующая проблема: {issue}\n\n" if issue else "\n")
        + "Этот вариант только для теста или когда клиент не может оплатить.",
        reply_markup=kb_send_without_payment_confirm(req_id),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sendwithoutpay_"))
async def send_without_payment(callback: CallbackQuery):
    """Отправить полный отчёт без подтверждения оплаты."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    issue, excluded_cards = get_alice_report_issues(req)
    if issue:
        await callback.answer(issue, show_alert=True)
        return

    report = build_full_report(req)
    if report.startswith("Нельзя"):
        await callback.answer(report, show_alert=True)
        return

    try:
        await callback.bot.send_message(req.user_id, report, parse_mode="HTML",
                                        disable_web_page_preview=True)
        update_request(req_id, status="REPORT_SENT", report_text=report)
        await callback.answer("Отчёт отправлен клиенту (без оплаты) ⚠️")
    except Exception as e:
        await callback.answer(f"Ошибка отправки: {e}", show_alert=True)
        return

    # Обновляем сообщение
    req = get_request(req_id)
    readiness = check_readiness(req)
    await callback.message.edit_text(
        f"📤 <b>Заявка #{req_id}</b> — отчёт отправлен.\n\n"
        "⚠️ Без оплаты. Статус: REPORT_SENT",
        reply_markup=kb_admin_detail_bottom(
            req_id,
            readiness.percent,
            is_paid=False,
            report_sent=True,
            can_send_report=True,
        ),
        parse_mode="HTML",
    )


# ──────────────────────────────────────────────
#  Проверка рынка (market check)
# ──────────────────────────────────────────────

def _format_market_check_card(mc: MarketCheck, idx: int) -> str:
    """Карточка проверки рынка для админа."""
    verdict_emoji = {
        "BUY": "💸", "RELIABLE": "🛡️", "DO_NOT_BUY": "⚠️",
    }
    emoji = verdict_emoji.get(mc.verdict, "📦")
    lines = [f"{emoji} <b>Проверка рынка #{idx}</b>"]
    lines.append(f"<b>Название:</b> {html.escape(mc.title or 'не указано')}")
    lines.append(f"<b>Цена:</b> {format_price(mc.price) if mc.price else 'уточнить'}")
    lines.append(f"<b>Магазин:</b> {html.escape(mc.source or 'не указан')}")
    if mc.url:
        safe_url = html.escape(mc.url, quote=True)
        lines.append(f'<b>Ссылка:</b> <a href="{safe_url}">открыть</a>')
    else:
        lines.append("<b>Ссылка:</b> ⚠️ не указана")
    if mc.rating_reviews:
        lines.append(f"<b>Рейтинг/отзывы:</b> {html.escape(mc.rating_reviews)}")
    if mc.city:
        lines.append(f"<b>Город:</b> {html.escape(mc.city)}")
    if mc.condition:
        lines.append(f"<b>Состояние:</b> {html.escape(mc.condition)}")
    if mc.verdict:
        verdict_label = {"BUY": "💸 Самый дешёвый", "RELIABLE": "🛡️ Надёжнее, но дороже", "DO_NOT_BUY": "⚠️ Не брать"}.get(mc.verdict, mc.verdict)
        lines.append(f"<b>Вердикт:</b> {verdict_label}")
    if mc.reason:
        lines.append(f"<b>Причина:</b> {html.escape(mc.reason)}")
    if mc.promoted_to_card_id:
        lines.append(f"<b>Добавлен в подборку:</b> карточка #{mc.promoted_to_card_id}")
    return "\n".join(lines)


@router.callback_query(F.data.startswith("mcstart_"))
async def market_check_start(callback: CallbackQuery, state: FSMContext):
    """Админ нажал «🔍 Проверил рынок»."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    await state.update_data(market_req_id=req_id)
    await state.set_state(AdminStates.waiting_market_check)

    await callback.message.answer(
        f"🔍 <b>Проверка рынка — заявка #{req_id}</b>\n\n"
        "Введи самый дешёвый найденный вариант в формате:\n\n"
        "<code>Название | Цена | Магазин | Ссылка | Рейтинг/отзывы | Город | Состояние</code>\n\n"
        "Пример:\n"
        "<code>Hisense 55E7NQ PRO | 37 000 ₽ | Мегамаркет | https://... | 4.8 / 120 отзывов | Москва | новый</code>\n\n"
        "Можно короче (минимум название и цена):\n"
        "<code>Hisense 55E7NQ PRO | 37 000 ₽</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")]
        ]),
    )
    await callback.answer()


@router.message(AdminStates.waiting_market_check)
async def market_check_receive(message: Message, state: FSMContext):
    """Админ прислал вариант проверки рынка."""
    if not is_admin(message.from_user.id):
        return

    fsm_data = await state.get_data()
    req_id = fsm_data.get("market_req_id")
    if not req_id:
        await state.clear()
        await message.answer("Что-то пошло не так. Начни заново.")
        return

    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Введи данные варианта.")
        return

    parts = [p.strip() for p in raw.split("|")]
    title = parts[0] if len(parts) > 0 else ""
    price_str = parts[1] if len(parts) > 1 else ""
    source = parts[2] if len(parts) > 2 else ""
    url = parts[3] if len(parts) > 3 else ""
    rating_reviews = parts[4] if len(parts) > 4 else ""
    city = parts[5] if len(parts) > 5 else ""
    condition = parts[6] if len(parts) > 6 else ""

    price = _extract_price(price_str)

    check_id = create_market_check(
        request_id=req_id,
        title=title[:300],
        price=price,
        source=source[:100],
        url=url[:500],
        rating_reviews=rating_reviews[:200],
        city=city[:100],
        condition=condition[:100],
    )

    await state.clear()

    mc = get_market_check(check_id)
    if mc:
        await message.answer(
            _format_market_check_card(mc, 1),
            reply_markup=kb_market_check_actions(check_id),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await message.answer(
            "Выбери вердикт и при необходимости добавь причину.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")]
            ]),
        )


@router.callback_query(F.data.startswith("mcverdict_"))
async def market_check_verdict(callback: CallbackQuery):
    """Установка вердикта для проверки рынка."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    # mcverdict_BUY_123
    parts = callback.data.split("_", 2)
    verdict = parts[1]
    check_id = int(parts[2])

    mc = get_market_check(check_id)
    if not mc:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    update_market_check(check_id, verdict=verdict)
    mc = get_market_check(check_id)

    # Найти индекс среди всех проверок
    checks = get_market_checks(mc.request_id)
    idx = next((i + 1 for i, c in enumerate(checks) if c.id == check_id), 1)

    await callback.message.edit_text(
        _format_market_check_card(mc, idx),
        reply_markup=kb_market_check_actions(check_id, verdict=verdict),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    verdict_label = {"BUY": "💸 Самый дешёвый", "RELIABLE": "🛡️ Надёжнее, но дороже", "DO_NOT_BUY": "⚠️ Не брать"}.get(verdict, verdict)
    await callback.answer(f"Вердикт: {verdict_label}")


@router.callback_query(F.data.startswith("mcreason_"))
async def market_check_reason_prompt(callback: CallbackQuery, state: FSMContext):
    """Запрос причины для проверки рынка."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    check_id = int(callback.data.split("_")[1])
    mc = get_market_check(check_id)
    if not mc:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    await state.update_data(market_check_id=check_id)
    await state.set_state(AdminStates.editing_market_reason)

    await callback.message.answer(
        f"Выбери причину или напиши свою для:\n"
        f"<b>{html.escape(mc.title[:120])}</b>",
        parse_mode="HTML",
        reply_markup=kb_market_check_reasons(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mcreason_set_"))
async def market_check_reason_set(callback: CallbackQuery, state: FSMContext):
    """Быстрая установка причины."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    reason = callback.data[len("mcreason_set_"):]
    fsm_data = await state.get_data()
    check_id = fsm_data.get("market_check_id")
    await state.clear()

    if not check_id:
        await callback.answer("Что-то пошло не так", show_alert=True)
        return

    update_market_check(check_id, reason=reason)
    mc = get_market_check(check_id)
    if mc:
        checks = get_market_checks(mc.request_id)
        idx = next((i + 1 for i, c in enumerate(checks) if c.id == check_id), 1)
        await callback.message.edit_text(
            _format_market_check_card(mc, idx),
            reply_markup=kb_market_check_actions(check_id, verdict=mc.verdict),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    await callback.answer("Причина сохранена")


@router.callback_query(F.data == "mcreason_cancel")
async def market_check_reason_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена выбора причины."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    fsm_data = await state.get_data()
    check_id = fsm_data.get("market_check_id")
    await state.clear()

    if check_id:
        mc = get_market_check(check_id)
        if mc:
            checks = get_market_checks(mc.request_id)
            idx = next((i + 1 for i, c in enumerate(checks) if c.id == check_id), 1)
            await callback.message.edit_text(
                _format_market_check_card(mc, idx),
                reply_markup=kb_market_check_actions(check_id, verdict=mc.verdict),
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
    await callback.answer()


@router.message(AdminStates.editing_market_reason)
async def market_check_reason_custom(message: Message, state: FSMContext):
    """Своя причина для проверки рынка."""
    if not is_admin(message.from_user.id):
        return
    fsm_data = await state.get_data()
    check_id = fsm_data.get("market_check_id")
    await state.clear()

    if not check_id:
        return

    reason = (message.text or "").strip()
    if reason:
        update_market_check(check_id, reason=reason)
        await message.answer(f"📝 Причина сохранена: <i>{html.escape(reason)}</i>", parse_mode="HTML")

    mc = get_market_check(check_id)
    if mc:
        checks = get_market_checks(mc.request_id)
        idx = next((i + 1 for i, c in enumerate(checks) if c.id == check_id), 1)
        await message.answer(
            _format_market_check_card(mc, idx),
            reply_markup=kb_market_check_actions(check_id, verdict=mc.verdict),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


@router.callback_query(F.data.startswith("mcprice_"))
async def market_check_price_prompt(callback: CallbackQuery, state: FSMContext):
    """Изменение цены проверки рынка."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    check_id = int(callback.data.split("_")[1])
    mc = get_market_check(check_id)
    if not mc:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    await state.update_data(market_check_id=check_id)
    await state.set_state(AdminStates.editing_market_price)

    await callback.message.answer(
        f"Введи новую цену для:\n"
        f"<b>{html.escape(mc.title[:120])}</b>\n\n"
        f"Текущая: {format_price(mc.price) if mc.price else 'не задана'}",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.editing_market_price)
async def market_check_price_process(message: Message, state: FSMContext):
    """Обработка новой цены."""
    if not is_admin(message.from_user.id):
        return
    fsm_data = await state.get_data()
    check_id = fsm_data.get("market_check_id")
    await state.clear()

    if not check_id:
        return

    new_price = _extract_price(message.text.strip())
    if new_price is None:
        await message.answer("❌ Не удалось определить цену. Введи число.")
        return

    update_market_check(check_id, price=new_price)
    await message.answer(f"✅ Цена обновлена: <b>{format_price(new_price)}</b>", parse_mode="HTML")

    mc = get_market_check(check_id)
    if mc:
        checks = get_market_checks(mc.request_id)
        idx = next((i + 1 for i, c in enumerate(checks) if c.id == check_id), 1)
        await message.answer(
            _format_market_check_card(mc, idx),
            reply_markup=kb_market_check_actions(check_id, verdict=mc.verdict),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


@router.callback_query(F.data.startswith("mclink_"))
async def market_check_link_prompt(callback: CallbackQuery, state: FSMContext):
    """Изменение ссылки проверки рынка."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    check_id = int(callback.data.split("_")[1])
    mc = get_market_check(check_id)
    if not mc:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    await state.update_data(market_check_id=check_id)
    await state.set_state(AdminStates.editing_market_link)

    await callback.message.answer(
        f"Пришли прямую ссылку для:\n"
        f"<b>{html.escape(mc.title[:120])}</b>\n\n"
        f"Текущая: {mc.url or 'не задана'}",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.editing_market_link)
async def market_check_link_process(message: Message, state: FSMContext):
    """Обработка новой ссылки."""
    if not is_admin(message.from_user.id):
        return
    fsm_data = await state.get_data()
    check_id = fsm_data.get("market_check_id")
    await state.clear()

    if not check_id:
        return

    new_url = (message.text or "").strip()
    if new_url and _link_status(new_url) != "✅ есть":
        await message.answer("❌ Нужна корректная прямая ссылка с http:// или https://")
        return

    update_market_check(check_id, url=new_url[:500] if new_url else "")
    await message.answer("✅ Ссылка обновлена.")

    mc = get_market_check(check_id)
    if mc:
        checks = get_market_checks(mc.request_id)
        idx = next((i + 1 for i, c in enumerate(checks) if c.id == check_id), 1)
        await message.answer(
            _format_market_check_card(mc, idx),
            reply_markup=kb_market_check_actions(check_id, verdict=mc.verdict),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


@router.callback_query(F.data.startswith("mcpromote_"))
async def market_check_promote(callback: CallbackQuery):
    """Добавить вариант проверки рынка в подборку как обычную карточку."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    check_id = int(callback.data.split("_")[1])
    mc = get_market_check(check_id)
    if not mc:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    # Создаём SearchResult из MarketCheck
    card_id = create_search_result(
        request_id=mc.request_id,
        title=mc.title,
        price=mc.price,
        source=mc.source,
        url=mc.url,
        snippet=f"Рейтинг: {mc.rating_reviews}; Город: {mc.city}; Состояние: {mc.condition}",
        score=60.0,
        risk_flags=json.dumps(["проверка рынка"], ensure_ascii=False),
        status="APPROVED",
        origin="market_check",
        price_verified=True,
        link_check_status=(
            LinkCheckStatus.VERIFIED.value
            if _link_status(mc.url) == "✅ есть"
            else LinkCheckStatus.NEEDED.value
        ),
    )

    update_market_check(check_id, promoted_to_card_id=card_id)
    await callback.answer(f"✅ Добавлен в подборку как карточка #{card_id}")

    # Обновить карточку проверки рынка
    mc = get_market_check(check_id)
    if mc:
        checks = get_market_checks(mc.request_id)
        idx = next((i + 1 for i, c in enumerate(checks) if c.id == check_id), 1)
        await callback.message.edit_text(
            _format_market_check_card(mc, idx),
            reply_markup=kb_market_check_actions(check_id, verdict=mc.verdict),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


# ---------- Обновлённая отправка отчёта с проверкой рынка ----------

@router.callback_query(F.data.startswith("sendreport_"))
async def send_report_with_market_check(callback: CallbackQuery):
    """Отправка отчёта с проверкой наличия market check."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if req.status != "PAID":
        await callback.answer("Сначала подтвердите оплату вручную.", show_alert=True)
        return

    # Проверка: есть ли проверка рынка
    if not has_market_check(req_id):
        await callback.message.answer(
            "⚠️ <b>Перед отправкой отчёта нужно проверить рынок.</b>\n\n"
            "Нажми «🔍 Проверил рынок» и введи самый дешёвый найденный вариант.\n"
            "Или нажми «Отправить без проверки рынка» если уверен.",
            reply_markup=kb_market_check_skip(req_id),
            parse_mode="HTML",
        )
        await callback.answer()
        return

    await _send_report(callback, req_id)


@router.callback_query(F.data.startswith("mcskip_"))
async def market_check_skip(callback: CallbackQuery):
    """Отправить отчёт без проверки рынка."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    await _send_report(callback, req_id)


# ---------- Алиса ----------

from app.alice_service import build_alice_prompt, parse_alice_response


@router.callback_query(F.data.startswith("alice_"))
async def alice_start(callback: CallbackQuery, state: FSMContext):
    """Админ нажал «🟡 Проверить через Алису»."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    prompt = build_alice_prompt(req)

    await state.update_data(alice_req_id=req_id)
    await state.set_state(AdminStates.waiting_alice_response)

    text = (
        "🟡 <b>Проверка через Алису или GigaChat — заявка #{req_id}</b>\n\n"
        "<b>📋 Готовый промт для Алисы:</b>\n"
        "<code>{prompt}</code>\n\n"
        "🔗 <a href=\"https://alice.yandex.ru/\">Открыть Алису</a>\n\n"
        "<b>Инструкция:</b>\n"
        "1. Открой Алису по ссылке выше или GigaChat\n"
        "2. Вставь промт (скопируй выше)\n"
        "3. Скопируй ответ ИИ\n"
        "4. Отправь ответ сюда (просто вставь текст)\n\n"
        "⏳ Бот ждёт ответ Алисы или GigaChat..."
    ).format(req_id=req_id, prompt=html.escape(prompt))

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")]
        ])
    )
    await callback.answer()


@router.message(AdminStates.waiting_alice_response)
async def alice_receive_response(message: Message, state: FSMContext):
    """Админ прислал ответ Алисы."""
    if not is_admin(message.from_user.id):
        return

    fsm_data = await state.get_data()
    req_id = fsm_data.get("alice_req_id")
    if not req_id:
        await state.clear()
        await message.answer("Что-то пошло не так. Начни заново.")
        return

    req = get_request(req_id)
    if not req:
        await state.clear()
        await message.answer("Заявка не найдена.")
        return

    raw_response = (message.text or "").strip()
    if not raw_response:
        await message.answer("Пришли текст ответа Алисы одним сообщением.")
        return

    # Определяем бюджет для парсинга
    budget_num: int | None = None
    if req.budget and req.budget.isdigit():
        budget_num = int(req.budget)

    # Парсим ответ Алисы (с бюджетом для правильной категоризации)
    parsed_items = parse_alice_response(raw_response, budget=budget_num)

    # Сохраняем сырой ответ и разобранные товары
    alice_data = json.dumps({
        "raw_response": raw_response,
        "parsed_items": [
            {
                "name": item.get("name", ""),
                "price": item.get("price", ""),
                "price_num": item.get("price_num"),
                "store": item.get("store", ""),
                "link": item.get("link", ""),
                "pluses": item.get("pluses", []),
                "risks": item.get("risks", []),
                "within_budget": item.get("within_budget"),
            }
            for item in parsed_items
        ]
    }, ensure_ascii=False)
    update_request(req_id, alice_response=alice_data)
    replace_alice_results(req_id, parsed_items)
    if req.status == "NEW":
        update_request(req_id, status="SEARCHING")

    await state.clear()

    # Сначала показываем карточки и даём админу выбрать ТОП-1/ссылки.
    low_count_warning = ""
    if len(parsed_items) < 3:
        low_count_warning = (
            "\n\n⚠️ Алиса дала мало вариантов. Лучше запросить ещё раз или добавить вручную."
        )
    await message.answer(
        f"✅ <b>Ответ ИИ сохранён для заявки #{req_id}</b>\n"
        f"Найдено товаров: {len(parsed_items)}\n\n"
        "Выбери ТОП-1, проверь ссылки и при необходимости поправь цену."
        f"{low_count_warning}",
        parse_mode="HTML",
    )
    await send_alice_cards(message, req_id, include_header=False)


# ---------- Карточки Алисы ----------

@router.callback_query(F.data.startswith("alicecards_"))
async def show_alice_cards(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    cards = get_alice_results(req_id)
    await callback.message.edit_text(
        f"🧩 <b>Карточки ИИ — заявка #{req_id}</b>\n"
        f"Найдено товаров: {len(cards)}\n\n"
        "Карточки отправлены ниже. Убраные варианты не попадут в предпросмотр.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁 Полный предпросмотр для админа", callback_data=f"adminpreview_{req_id}")],
            [InlineKeyboardButton(text="👀 Клиентский предпросмотр до оплаты", callback_data=f"preview_{req_id}")],
            [InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")],
        ]),
        parse_mode="HTML",
    )
    await send_alice_cards(callback.message, req_id, include_header=False)
    await callback.answer()


@router.callback_query(F.data.startswith("alicetop_"))
async def alice_top(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    candidate = get_search_result(result_id)
    req = get_request(candidate.request_id) if candidate else None
    if not candidate or candidate.origin != "alice" or not req:
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    if req.budget.isdigit() and candidate.price and candidate.price > int(req.budget):
        await callback.answer(
            "Этот вариант выше бюджета: он попадёт в «Осторожно / только если готовы доплатить».",
            show_alert=True,
        )
        return
    item = set_alice_top_result(candidate.request_id, result_id)
    if not item:
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    await _refresh_alice_card(callback.message, item)
    await callback.answer("ТОП-1 выбран")


@router.callback_query(F.data.startswith("alicekeep_"))
async def alice_keep(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    update_search_result(result_id, status="APPROVED")
    item = get_search_result(result_id)
    await _refresh_alice_card(callback.message, item)
    await callback.answer("Карточка оставлена")


@router.callback_query(F.data.startswith("aliceremove_"))
async def alice_remove(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    update_search_result(result_id, status="REJECTED")
    item = get_search_result(result_id)
    await _refresh_alice_card(callback.message, item)
    await callback.answer("Карточка убрана из отчёта")


@router.callback_query(F.data.startswith("alicereserve_"))
async def alice_reserve(callback: CallbackQuery):
    """✅ Запасной — ставит карточке роль BACKUP или APPROVED_BACKUP."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    # Toggle: если уже BACKUP → APPROVED_BACKUP, иначе BACKUP
    if item.status in ("BACKUP", "APPROVED_BACKUP"):
        new_status = "APPROVED_BACKUP" if item.status == "BACKUP" else "BACKUP"
    else:
        new_status = "BACKUP"
    update_search_result(result_id, status=new_status)
    item = get_search_result(result_id)
    try:
        await _refresh_alice_card(callback.message, item)
    except Exception:
        pass
    label = "Запасной (подтверждён)" if new_status == "APPROVED_BACKUP" else "Запасной вариант"
    await callback.answer(f"✅ {label}")


@router.callback_query(F.data.startswith("alicebudget_"))
async def alice_budget(callback: CallbackQuery):
    """💰 Бюджетный — ставит карточке роль BUDGET."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    new_status = "BUDGET" if item.status == "BUDGET" else "BUDGET"
    update_search_result(result_id, status=new_status)
    item = get_search_result(result_id)
    try:
        await _refresh_alice_card(callback.message, item)
    except Exception:
        pass
    await callback.answer("Карточка помечена как бюджетный вариант")


@router.callback_query(F.data.startswith("alicecaution_"))
async def alice_caution(callback: CallbackQuery):
    """⚠️ Осторожно — ставит карточке роль DO_NOT_BUY."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    new_status = "DO_NOT_BUY" if item.status == "DO_NOT_BUY" else "DO_NOT_BUY"
    update_search_result(result_id, status=new_status)
    item = get_search_result(result_id)
    try:
        await _refresh_alice_card(callback.message, item)
    except Exception:
        pass
    await callback.answer("Карточка помечена как осторожно / не брать")


async def _navigate_alice_card(callback: CallbackQuery, direction: int) -> None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    cards = get_alice_results(item.request_id)
    ids = [card.id for card in cards]
    try:
        current_index = ids.index(result_id)
    except ValueError:
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    target_index = current_index + direction
    if target_index < 0:
        await callback.answer("Это первая карточка")
        return
    if target_index >= len(cards):
        await callback.answer("Это последняя карточка")
        return
    await _refresh_alice_card(callback.message, cards[target_index])
    await callback.answer()


@router.callback_query(F.data.startswith("aliceup_"))
async def alice_up(callback: CallbackQuery):
    await _navigate_alice_card(callback, -1)


@router.callback_query(F.data.startswith("alicedown_"))
async def alice_down(callback: CallbackQuery):
    await _navigate_alice_card(callback, 1)


@router.callback_query(F.data.startswith("alicesearch_"))
async def alice_search_link(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    req = get_request(item.request_id)
    links = generate_product_search_links(item.title, req.city if req else "")
    query = " ".join(part for part in (item.title, "купить", req.city if req else "") if part)
    await callback.message.answer(
        f"🔎 <b>Быстрый поиск ссылки</b>\n<code>{html.escape(query)}</code>\n\n"
        "Открой магазин, проверь карточку товара и вставь прямую ссылку в эту карточку.",
        reply_markup=kb_alice_search_links(links, item.request_id),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("alicelink_"))
async def alice_edit_link_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    await state.update_data(alicelink_result_id=result_id)
    await state.set_state(AdminStates.editing_link)
    await callback.message.answer(
        f"Пришли прямую ссылку для:\n<b>{html.escape(item.title[:120])}</b>\n\n"
        "Нужна ссылка, начинающаяся с <code>https://</code> или <code>http://</code>.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К карточкам", callback_data=f"alicecards_{item.request_id}")]
        ]),
    )
    await callback.answer()


@router.message(AdminStates.editing_link)
async def alice_edit_link_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    result_id = data.get("alicelink_result_id")
    item = get_search_result(result_id) if result_id else None
    new_url = (message.text or "").strip()
    if not item or item.origin != "alice":
        await state.clear()
        await message.answer("Карточка не найдена. Начни заново.")
        return
    if _link_status(new_url) != "✅ есть":
        await message.answer("❌ Нужна корректная прямая ссылка с http:// или https:// Попробуй ещё раз.")
        return
    update_search_result(
        result_id,
        url=new_url[:500],
        link_check_status=LinkCheckStatus.FOUND_UNVERIFIED.value,
    )
    await state.clear()
    item = get_search_result(result_id)
    await message.answer(
        "✅ Ссылка сохранена.",
        reply_markup=kb_alice_product(item.id, item.status),
    )
    await message.answer(format_alice_card(item, _alice_card_index(item.id, item.request_id)),
                         reply_markup=kb_alice_product(item.id, item.status),
                         parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data.startswith("alicechecklink_"))
async def alice_check_link(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    if _link_status(item.url) != "✅ есть":
        await callback.answer("Сначала вставьте корректную прямую ссылку.", show_alert=True)
        return
    warning = store_url_warning(item.source, item.url)
    if warning:
        await callback.answer(f"{warning}. Исправьте ссылку или отметьте её неподходящей.", show_alert=True)
        return
    update_search_result(result_id, link_check_status=LinkCheckStatus.VERIFIED.value)
    item = get_search_result(result_id)
    await _refresh_alice_card(callback.message, item)
    await callback.answer("Ссылка подтверждена админом")


@router.callback_query(F.data.startswith("alicebadlink_"))
async def alice_bad_link(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    update_search_result(result_id, link_check_status=LinkCheckStatus.UNSUITABLE.value)
    item = get_search_result(result_id)
    await _refresh_alice_card(callback.message, item)
    await callback.answer("Ссылка помечена как неподходящая")


@router.callback_query(F.data.startswith("aliceprice_"))
async def alice_edit_price_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    await state.update_data(editprice_result_id=result_id)
    await state.set_state(AdminStates.editing_price)
    await callback.message.answer(
        f"Введи подтверждённую цену для <b>{html.escape(item.title[:120])}</b>.\n"
        f"Текущая: {format_price(item.price) if item.price else 'не задана'}\n\n"
        "Отправь актуальную цену даже если она не изменилась — так она станет проверенной.",
        parse_mode="HTML",
    )
    await callback.answer()


# ---------- Заметка к товару ----------

# ---------- Изменить магазин (карточки ИИ) ----------

@router.callback_query(F.data.startswith("alicestore_"))
async def alice_edit_store_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    await state.update_data(alicestore_result_id=result_id)
    await state.set_state(AdminStates.editing_store)
    await callback.message.answer(
        f"Отправь новое название магазина для:\n"
        f"<b>{html.escape(item.title[:120])}</b>\n\n"
        f"Текущий: {html.escape(item.source or 'не указан')}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 К карточкам", callback_data=f"alicecards_{item.request_id}")]
        ]),
    )
    await callback.answer()


@router.message(AdminStates.editing_store)
async def alice_edit_store_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    result_id = data.get("alicestore_result_id")
    await state.clear()
    if not result_id:
        return

    new_store = (message.text or "").strip()[:100]
    if not new_store:
        await message.answer("Название магазина не может быть пустым.")
        return

    update_search_result(result_id, source=new_store)
    await message.answer("✅ Магазин обновлён")

    item = get_search_result(result_id)
    if item and item.origin == "alice":
        await message.answer(
            format_alice_card(item, _alice_card_index(item.id, item.request_id)),
            reply_markup=kb_alice_product(item.id, item.status),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


# ---------- Нет в наличии (карточки ИИ) ----------

@router.callback_query(F.data.startswith("aliceoutofstock_"))
async def alice_out_of_stock(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item or item.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return

    update_search_result(
        result_id,
        status="REJECTED",
        admin_note="Нет в наличии на момент проверки",
    )
    item = get_search_result(result_id)
    await _refresh_alice_card(callback.message, item)
    await callback.answer("Товар убран: нет в наличии")


# ---------- Заметка к товару ----------

@router.callback_query(F.data.startswith("editnote_"))
async def edit_note_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    sr = get_search_result(result_id)
    if not sr:
        await callback.answer("Вариант не найден", show_alert=True)
        return
    await state.update_data(editnote_result_id=result_id)
    await state.set_state(AdminStates.editing_note)
    current_note = sr.admin_note or "нет заметки"
    await callback.message.answer(
        f"Введи заметку для:\n"
        f"<b>{html.escape(sr.title[:120])}</b>\n\n"
        f"Текущая: <i>{html.escape(current_note)}</i>\n\n"
        f"Для удаления заметки отправь <code>-</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data=f"viewresult_{result_id}")]
        ]),
    )
    await callback.answer()


@router.message(AdminStates.editing_note)
async def edit_note_process(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    fsm_data = await state.get_data()
    result_id = fsm_data.get("editnote_result_id")
    await state.clear()
    if not result_id:
        return

    note = message.text.strip()
    if note == "-":
        note = ""

    update_search_result(result_id, admin_note=note)
    if note:
        await message.answer(f"📝 Заметка сохранена: <i>{html.escape(note)}</i>", parse_mode="HTML")
    else:
        await message.answer("📝 Заметка удалена.", parse_mode="HTML")

    # Обновить карточку
    sr = get_search_result(result_id)
    if sr:
        lines = [f"📦 <b>Вариант</b>\n", f"<b>{html.escape(sr.title)}</b>\n"]
        lines.append(f"💰 Цена: {format_price(sr.price)}" if sr.price else "💰 Цена: <i>не задана</i>")
        lines.append(f"🏪 Источник: {html.escape(sr.source or 'generic_web')}")
        if sr.admin_note:
            lines.append(f"📝 {html.escape(sr.admin_note)}")
        if sr.url:
            lines.append(f"🔗 <a href=\"{html.escape(sr.url, quote=True)}\">Ссылка</a>")
        await message.answer("\n".join(lines), parse_mode="HTML",
                             reply_markup=kb_admin_product(result_id, sr.status))
