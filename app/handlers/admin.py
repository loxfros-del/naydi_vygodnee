"""Обработчики админа — панель управления, автопоиск, работа с вариантами."""
import asyncio
import html
import json
from datetime import datetime
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
    get_comparison_links, get_comparison_link, update_comparison_link,
)
from app.search_links import generate_search_links, generate_product_search_links
from app.link_checks import LinkCheckStatus, avito_warnings, store_url_warning
from app.ai_cards_service import (
    generate_ai_cards_from_candidates,
    select_verified_ai_card_candidates,
)
from app.report_builder import (
    build_preview, build_admin_preview, build_full_report, get_alice_report_issues,
)
from app.readiness import check_readiness, format_readiness, format_readiness_short
from app.price_extractor import format_price, extract_price as _extract_price
from app.product_config import SearchMode, classify_request, is_free_search_mode, is_supported_auto_category
from app.services.ai_review import (
    AI_CARD_APPROVED,
    AI_CARD_DRAFT,
    AI_CARD_GENERATED,
    AIReviewError,
    AdminReviewService,
    get_ai_card_status,
)
from app.services.analytics import build_product_metrics, get_low_feedback, track_event
from app.services.progress import update_client_progress
from app.services.product_services import DeliveryService, SearchOrchestrationService
from app.services.search_engine_bridge import load_shadow_comparison, run_search_for_request
from app.services.recommendations import RecommendationService
from app.services.request_wizard import parse_budget as _parse_admin_budget
from app.services.state_machine import (
    ADMIN_REVIEW,
    AI_CARDS_DRAFT,
    DELIVERED,
    FAILED,
    PAID,
    READY,
    SEARCHING,
    STATUS_LABELS,
    WAITING_PAYMENT,
    InvalidStatusTransition,
    normalize_request_status,
    transition_request,
)
from app.ui_formatters import chunk_html, escape_html, source_display_name
from app.ui_keyboards import feedback_keyboard, result_card_keyboard
from app.search_v2.shadow_compare import format_shadow_comparison
from app.verification_state import (
    SELLER_REQUIRES_CHECK,
    SELLER_VERIFIED,
    apply_manual_confirmation,
    normalize_verification_facts,
    resolve_final_presentation,
)

router = Router()


def parse_admin_price(value: object) -> int | None:
    """Parses deliberate admin input without weakening web price extraction."""
    return _parse_admin_budget(value)


def _manual_confirmation_facts(
    item: SearchResult,
    field: str,
    verified: bool,
    *,
    admin_id: int,
    note: str,
    value=None,
    seller_state: str | None = None,
    compatibility_updates: dict | None = None,
) -> dict:
    facts = apply_manual_confirmation(
        _parse_facts(getattr(item, "facts_json", "")),
        field,
        verified,
        verified_by=f"telegram_admin:{admin_id}",
        verified_at=datetime.now().astimezone(),
        note=note,
        value=value,
        seller_state=seller_state,
    )
    if compatibility_updates:
        facts.update(compatibility_updates)
    return facts


def _manual_price_facts(item: SearchResult, price: int, *, admin_id: int) -> str:
    facts = _manual_confirmation_facts(
        item,
        "price",
        True,
        admin_id=admin_id,
        note="Цена подтверждена администратором",
        value=price,
        compatibility_updates={
        "price": price,
        "price_verified": True,
        "price_confidence": "high",
        "price_evidence": "manual_admin",
        "price_verification": "VERIFIED",
        },
    )
    if str(facts.get("verify_status") or "").upper() == "PRICE_MISSING":
        facts["verify_status"] = "NEED_MANUAL_CHECK"
    return json.dumps(facts, ensure_ascii=False)


def is_admin(user_id: int) -> bool:
    return user_id in settings.ADMIN_IDS


def _track_admin_event(event_type: str, admin_id: int, request_id: int, metadata: dict | None = None) -> None:
    try:
        track_event(event_type, request_id=request_id, metadata={"actor": "admin", **(metadata or {})})
    except Exception:
        pass


def admin_queue_counts(requests: list[Request] | None = None) -> dict[str, int]:
    counts = {key: 0 for key in ("new", "searching", "review", "ready", "waiting", "delivered", "problem")}
    for request in requests if requests is not None else get_all_requests():
        try:
            status = normalize_request_status(request.status)
        except Exception:
            status = FAILED
        key = {
            "NEW": "new",
            "SEARCHING": "searching",
            "ADMIN_REVIEW": "review",
            "AI_CARDS_DRAFT": "review",
            "READY": "ready",
            "WAITING_PAYMENT": "waiting",
            "DELIVERED": "delivered",
            "FAILED": "problem",
            "NEED_CLARIFICATION": "problem",
        }.get(status)
        if key:
            counts[key] += 1
    counts["problem"] += len(get_low_feedback(max_rating=2, limit=1000))
    return counts


def _filter_requests(*statuses: str) -> list[Request]:
    wanted = set(statuses)
    result = []
    for request in get_all_requests():
        try:
            status = normalize_request_status(request.status)
        except Exception:
            status = FAILED
        if not wanted or status in wanted:
            result.append(request)
    return result


async def _show_admin_queue(callback: CallbackQuery, title: str, requests: list[Request]) -> None:
    if not requests:
        await callback.message.edit_text(f"{title}\n\nЗаявок нет.", reply_markup=kb_admin_back(), parse_mode="HTML")
        await callback.answer()
        return
    text = f"{title}\n\n" + "\n\n".join(format_request_card(item) for item in requests[:20])
    buttons = [
        [InlineKeyboardButton(text=f"#{item.id} {(item.product_name or item.product or 'товар')[:28]}", callback_data=f"view_{item.id}")]
        for item in requests[:20]
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    await callback.answer()


def format_request_card(req: Request) -> str:
    """Компактная строка очереди без debug-полей."""
    try:
        status = normalize_request_status(req.status)
    except Exception:
        status = str(req.status or "NEW").upper()
    emoji = {
        "NEW": "📥", "NEED_CLARIFICATION": "❓", "SEARCHING": "🔎",
        "ADMIN_REVIEW": "🧑‍💻", "AI_CARDS_DRAFT": "🧩",
        "WAITING_PAYMENT": "💳", "PAID": "💰", "READY": "✅",
        "DELIVERED": "📤", "FAILED": "⚠️", "CANCELLED": "❌",
    }.get(status, "📋")
    username = f"@{req.username}" if req.username else str(req.user_id)
    product_label = req.product_name or req.product or "товар"
    category = getattr(req, "category", "") or "не указана"
    budget = format_price(int(req.budget)) if req.budget and req.budget.isdigit() else (req.budget or "не указан")
    waited = ""
    try:
        created = datetime.fromisoformat(req.created_at)
        minutes = max(0, int((datetime.now() - created).total_seconds() // 60))
        waited = f" · {minutes // 60}ч {minutes % 60}м" if minutes >= 60 else f" · {minutes}м"
    except (TypeError, ValueError):
        pass
    return (
        f"{emoji} <b>#{req.id}</b> · {escape_html(username)} · {escape_html(product_label[:45])}\n"
        f"{escape_html(category)} · {escape_html(budget)} · {escape_html(STATUS_LABELS.get(status, status))}{waited}"
    )


def format_request_detail(req: Request) -> str:
    """Детальная карточка заявки; технические данные доступны только в Debug."""
    try:
        status = normalize_request_status(req.status)
    except Exception:
        status = str(req.status or "NEW").upper()
    username = f"@{req.username}" if req.username else str(req.user_id)
    budget = format_price(int(req.budget)) if req.budget and req.budget.isdigit() else (req.budget or "не указан")
    lines = [
        f"📋 <b>Заявка #{req.id}</b>",
        f"Клиент: {escape_html(username)}",
        f"Товар: <b>{escape_html(req.product_name or req.product or 'не указан')}</b>",
        f"Категория: {escape_html(getattr(req, 'category', '') or 'не указана')}",
        f"Режим: {escape_html(getattr(req, 'request_mode', '') or 'обычный')}",
        f"Бюджет: {escape_html(budget)}",
        f"Город: {escape_html(req.city or 'не указан')}",
        f"Состояние: {escape_html(getattr(req, 'condition', '') or ('можно б/у' if req.is_used_allowed else 'новое'))}",
        f"Приоритет: {escape_html(getattr(req, 'priority', '') or 'не указан')}",
    ]
    if req.original_query:
        lines.append(f"Исходный запрос: <i>{escape_html(req.original_query[:500])}</i>")
    if req.important_criteria:
        lines.append(f"Требования: {escape_html(req.important_criteria[:1000])}")
    lines.extend([
        "",
        f"Статус: <b>{escape_html(STATUS_LABELS.get(status, status))}</b>",
        f"Оплата: {'подтверждена' if status in {'PAID', 'READY', 'DELIVERED'} else 'не подтверждена'}",
        f"Кандидатов: {count_search_results(req.id)}",
        f"Выбрано: {count_approved_results(req.id)}",
    ])
    attempts = get_search_attempts(req.id)
    blocked = sum(1 for item in attempts if item.status == "ERROR" or item.error_text)
    if blocked:
        lines.append(f"Проблемных источников: {blocked} — подробности в Debug")
    if getattr(req, "admin_note", ""):
        lines.append(f"Заметка: {escape_html(req.admin_note)}")
    lines.append(format_readiness_short(check_readiness(req)))
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
    line += f"\n🏪 Источник: {html.escape(source_display_name(sr.source))}"
    line += f"\n📋 Решение: {html.escape(_result_status_label(sr.status))}"
    main_risks = _admin_safe_items(_resolved_admin_warnings(sr), 3)
    line += f"\n⚠️ Риски: {html.escape(_clip_text('; '.join(main_risks) if main_risks else 'нет', 360))}"
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


def _admin_safe_items(values, limit: int = 3) -> list[str]:
    technical = (
        "weak_candidate", "verified_good", "verify_blocked", "confidence", "evidence",
        "score", "browser", "proxy", "captcha", "403", "401", "429", "network block",
    )
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = " ".join(str(value or "").split()).strip(" ;,.-")
        lowered = text.casefold()
        if not text or any(marker in lowered for marker in technical) or lowered in seen:
            continue
        seen.add(lowered)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _parse_facts(value: str) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalised_verification(item: SearchResult) -> dict:
    try:
        return normalize_verification_facts(_parse_facts(getattr(item, "facts_json", "")))
    except (TypeError, ValueError):
        return normalize_verification_facts({})


def _checklist_display_state(verification: dict) -> dict:
    """Merge automatic and manual decisions for keyboard status only."""
    manual = dict(verification.get("manual_verification") or {})
    final = dict(verification.get("final_presentation_state") or {})
    for field in ("model", "link", "price", "availability"):
        if final.get(f"{field}_verified"):
            manual[f"manual_{field}_verified"] = True
    final_seller = str(final.get("seller_state") or "UNSET").upper()
    if final_seller in {SELLER_VERIFIED, SELLER_REQUIRES_CHECK}:
        manual["manual_seller_state"] = final_seller
    return manual


def _optional_request(request_id: int) -> Request | None:
    """Rendering must remain usable even when its optional DB context is absent."""
    try:
        return get_request(request_id)
    except Exception:
        return None


async def _require_final_role_ready(callback: CallbackQuery, item: SearchResult) -> bool:
    final = resolve_final_presentation(getattr(item, "facts_json", "") or {})
    if final.get("presentation_ready") and not final.get("blocking_reasons"):
        return True
    blockers = [str(value) for value in final.get("blocking_reasons") or []]
    unresolved = [str(value) for value in final.get("unresolved_fields") or []]
    detail = "; ".join(blockers) or ", ".join(unresolved) or "проверка не завершена"
    await callback.answer(
        f"Сначала завершите финальный checklist: {detail}",
        show_alert=True,
    )
    return False


def _alice_product_keyboard(item: SearchResult) -> InlineKeyboardMarkup:
    verification = _normalised_verification(item)
    return kb_alice_product(
        item.id,
        item.status,
        link_check_status=getattr(item, "link_check_status", LinkCheckStatus.NEEDED.value),
        price_verified=bool(getattr(item, "price_verified", False)),
        ai_card_status=getattr(item, "ai_card_status", ""),
        manual_verification=_checklist_display_state(verification),
    )


def _admin_product_keyboard(item: SearchResult) -> InlineKeyboardMarkup:
    verification = _normalised_verification(item)
    return kb_admin_product(
        item.id,
        item.status,
        manual_verification=_checklist_display_state(verification),
    )


def _manual_checklist_lines(item: SearchResult) -> list[str]:
    verification = _normalised_verification(item)
    manual = verification.get("manual_verification", {})
    final = verification.get("final_presentation_state", {})

    sources = final.get("verification_source") if isinstance(final.get("verification_source"), dict) else {}

    def field_text(field: str, *, feminine: bool = True) -> str:
        if not final.get(f"{field}_verified"):
            return "⬜ не подтверждена" if feminine else "⬜ не подтверждено"
        source = str(sources.get(field) or "").lower()
        suffix = "автоматически" if source == "automatic" else "специалистом"
        return f"✅ подтверждена ({suffix})" if feminine else f"✅ подтверждено ({suffix})"

    model_ok = bool(final.get("model_verified"))
    if manual.get("manual_model_verified") and not model_ok:
        model_text = "⚠️ отмечена, но automatic hard mismatch остаётся"
    else:
        model_text = field_text("model")
    link_text = field_text("link")
    price_text = field_text("price")
    availability_text = field_text("availability", feminine=False)
    seller_state = str(final.get("seller_state") or manual.get("manual_seller_state") or "UNSET").upper()
    seller_text = {
        SELLER_VERIFIED: "✅ подтверждён",
        SELLER_REQUIRES_CHECK: "🟡 требует дополнительной проверки",
    }.get(seller_state, "⬜ решение не указано")
    final_status = {
        "VERIFIED": "✅ VERIFIED (automatic)",
        "APPROVED": "✅ APPROVED (specialist)",
        "BLOCKED": "⛔ BLOCKED",
        "NEEDS_REVIEW": "⬜ не завершён",
    }.get(str(final.get("status") or "NEEDS_REVIEW"), "⬜ не завершён")

    lines = [
        "<b>Финальный checklist:</b>",
        f"• Модель: {model_text}",
        f"• Ссылка: {link_text}",
        f"• Цена: {price_text}",
        f"• Наличие: {availability_text}",
        f"• Продавец: {seller_text}",
        f"<b>Итог проверки:</b> {final_status}",
    ]
    checked_at = str(manual.get("manual_verified_at") or "").strip()
    checked_by = str(manual.get("manual_verified_by") or "").strip()
    if checked_at or checked_by:
        lines.append(f"<b>Последнее действие:</b> {html.escape(checked_at)} · {html.escape(checked_by)}")
    return lines


def _stored_verify_status(sr: SearchResult) -> str:
    facts = _parse_facts(getattr(sr, "facts_json", ""))
    return str(facts.get("verify_status") or "").upper()


def _verify_status_label(status: str) -> str:
    return {
        "VERIFIED_GOOD": "проверен: хороший",
        "VERIFIED_OK": "проверен: подходит",
        "NEED_MANUAL_CHECK": "нужна ручная проверка",
        "VERIFY_BLOCKED": "проверка страницы заблокирована",
        "PRICE_MISSING": "цена не подтверждена",
        "OVER_BUDGET_SOFT": "чуть выше бюджета",
    }.get(status, "статус проверки не подтверждён")


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
    category = str(facts.get("category") or "")
    parts = []
    if category == "phone":
        for key in ("model", "memory", "color", "condition"):
            if facts.get(key):
                parts.append(str(facts[key]))
    elif category == "laptop":
        for key in ("brand", "cpu", "ram", "ssd", "screen"):
            if facts.get(key):
                parts.append(str(facts[key]))
    elif category == "headphones":
        for key in ("brand", "headphone_type", "model"):
            if facts.get(key):
                parts.append(str(facts[key]))
        if facts.get("anc"):
            parts.append("ANC")
    elif category == "chair":
        if facts.get("ergonomics"):
            parts.append("эргономика")
        if facts.get("lumbar_support"):
            parts.append("поясничная поддержка")
        if facts.get("adjustments"):
            parts.append("регулировки: " + ", ".join(str(item) for item in facts["adjustments"]))
        if facts.get("headrest"):
            parts.append("подголовник")
        if facts.get("load_capacity"):
            parts.append(str(facts["load_capacity"]))
    elif category == "robot_vacuum":
        for key in ("brand", "model", "navigation", "suction"):
            if facts.get(key):
                parts.append(str(facts[key]))
        if facts.get("wet_cleaning"):
            parts.append("влажная уборка")
        if facts.get("self_empty_station"):
            parts.append("станция самоочистки")
    elif category == "vacuum":
        for key in ("brand", "model", "type", "power", "suction", "battery"):
            if facts.get(key):
                parts.append(str(facts[key]))
        if facts.get("wet_cleaning"):
            parts.append("влажная уборка")
    elif category == "microwave":
        for key in ("brand", "model", "volume", "power", "controls"):
            if facts.get(key):
                parts.append(str(facts[key]))
        if facts.get("grill"):
            parts.append("гриль")
        if facts.get("inverter"):
            parts.append("инвертор")
    elif category == "coffee_machine":
        for key in ("brand", "model", "machine_type", "pressure"):
            if facts.get(key):
                parts.append(str(facts[key]))
        if facts.get("cappuccinator"):
            parts.append("капучинатор")
        if facts.get("grinder"):
            parts.append("кофемолка")
    elif category == "mattress":
        for key in ("brand", "model", "size", "firmness", "spring_type", "height", "load_per_bed"):
            if facts.get(key):
                parts.append(str(facts[key]))
    elif category == "bed":
        for key in ("brand", "model", "size", "material", "base"):
            if facts.get(key):
                parts.append(str(facts[key]))
        if facts.get("lift_mechanism"):
            parts.append("подъёмный механизм")
        if facts.get("storage"):
            parts.append("хранение")
    elif facts.get("diagonal"):
        parts.append(f'{facts["diagonal"]}"')
        for key in ("resolution", "refresh_rate", "matrix_type"):
            if facts.get(key):
                parts.append(str(facts[key]))
    if not parts:
        parts.append("факты не подтверждены")
    if facts.get("budget_status"):
        parts.append(_budget_status_label(str(facts["budget_status"])))
    return _clip_text(", ".join(parts), 420)


def _facts_ps5_line(facts: dict) -> str:
    flags = [str(item) for item in facts.get("ps5_flags") or [] if str(item).strip()]
    warnings = [str(item) for item in facts.get("warnings") or [] if str(item).strip()]
    items = flags + warnings[:2]
    return "; ".join(items)


def _facts_detail_lines(sr: SearchResult) -> list[str]:
    facts = _normalised_verification(sr)
    if not facts:
        return []
    category = str(facts.get("category") or "unknown")
    lines = ["", "<b>Факты:</b>"]
    if category == "phone":
        items = (
            ("Модель", facts.get("model")),
            ("Память", facts.get("memory")),
            ("Цвет", facts.get("color")),
            ("Состояние", facts.get("condition")),
        )
    elif category == "laptop":
        items = (
            ("Бренд", facts.get("brand")),
            ("CPU", facts.get("cpu")),
            ("RAM", facts.get("ram")),
            ("SSD", facts.get("ssd")),
            ("Экран", facts.get("screen")),
        )
    elif category == "headphones":
        items = (
            ("Бренд", facts.get("brand")),
            ("Тип", facts.get("headphone_type")),
            ("ANC", "есть" if facts.get("anc") else ""),
            ("Модель", facts.get("model")),
        )
    elif category == "chair":
        adjustments = ", ".join(str(item) for item in facts.get("adjustments") or [])
        items = (
            ("Эргономика", "есть" if facts.get("ergonomics") else ""),
            ("Поясничная поддержка", "есть" if facts.get("lumbar_support") else ""),
            ("Регулировки", adjustments),
            ("Подголовник", "есть" if facts.get("headrest") else ""),
            ("Нагрузка", facts.get("load_capacity")),
        )
    elif category == "tv":
        diagonal = f'{facts.get("diagonal")}"' if facts.get("diagonal") else ""
        items = (
            ("Модель", facts.get("model") or facts.get("model_key")),
            ("Диагональ", diagonal),
            ("Разрешение", facts.get("resolution")),
            ("Частота", facts.get("refresh_rate")),
            ("HDMI", facts.get("hdmi")),
            ("Матрица", facts.get("matrix_type")),
        )
    elif category == "monitor":
        diagonal = f'{facts.get("diagonal")}"' if facts.get("diagonal") else ""
        items = (
            ("Бренд", facts.get("brand")), ("Модель", facts.get("model")),
            ("Диагональ", diagonal), ("Разрешение", facts.get("resolution")),
            ("Частота", facts.get("refresh_rate")), ("Матрица", facts.get("panel")),
            ("Отклик", facts.get("response_time")), ("Adaptive Sync", facts.get("adaptive_sync")),
        )
    elif category == "robot_vacuum":
        items = (
            ("Бренд", facts.get("brand")), ("Модель", facts.get("model")),
            ("Влажная уборка", "есть" if facts.get("wet_cleaning") else ""),
            ("Навигация", facts.get("navigation")), ("Лидар", "есть" if facts.get("lidar") else ""),
            ("Всасывание", facts.get("suction")),
            ("Станция", "есть" if facts.get("self_empty_station") else ""),
            ("Карта", "есть" if facts.get("mapping") else ""),
        )
    elif category == "vacuum":
        attachments = ", ".join(str(item) for item in facts.get("attachments") or [])
        items = (
            ("Бренд", facts.get("brand")), ("Модель", facts.get("model")),
            ("Тип", facts.get("type")), ("Мощность", facts.get("power")),
            ("Всасывание", facts.get("suction")), ("Батарея", facts.get("battery")),
            ("Влажная уборка", "есть" if facts.get("wet_cleaning") else ""),
            ("Насадки", attachments),
        )
    elif category == "microwave":
        items = (
            ("Бренд", facts.get("brand")), ("Модель", facts.get("model")),
            ("Объём", facts.get("volume")), ("Мощность", facts.get("power")),
            ("Гриль", "есть" if facts.get("grill") else ""),
            ("Инвертор", "есть" if facts.get("inverter") else ""),
            ("Управление", facts.get("controls")),
        )
    elif category == "coffee_machine":
        items = (
            ("Бренд", facts.get("brand")), ("Модель", facts.get("model")),
            ("Тип", facts.get("machine_type")),
            ("Капучинатор", "есть" if facts.get("cappuccinator") else ""),
            ("Давление", facts.get("pressure")),
            ("Кофемолка", "есть" if facts.get("grinder") else ""),
            ("Молочная система", "есть" if facts.get("milk_system") else ""),
        )
    elif category == "mattress":
        materials = ", ".join(str(item) for item in facts.get("materials") or [])
        items = (
            ("Бренд", facts.get("brand")), ("Модель", facts.get("model")),
            ("Размер", facts.get("size")), ("Жёсткость", facts.get("firmness")),
            ("Пружины", facts.get("spring_type")), ("Высота", facts.get("height")),
            ("Нагрузка", facts.get("load_per_bed")), ("Материалы", materials),
        )
    elif category == "bed":
        items = (
            ("Бренд", facts.get("brand")), ("Модель", facts.get("model")),
            ("Размер", facts.get("size")), ("Материал", facts.get("material")),
            ("Подъёмный механизм", "есть" if facts.get("lift_mechanism") else ""),
            ("Основание", facts.get("base")),
            ("Матрас в комплекте", "да" if facts.get("mattress_included") else ""),
            ("Хранение", "есть" if facts.get("storage") else ""),
        )
    else:
        items = ()
    confirmed = False
    for label, value in items:
        text = str(value or "").strip()
        if text:
            confirmed = True
        lines.append(f"- {label}: {html.escape(text or 'не подтверждено')}")
    if not confirmed:
        lines.append("- факты не подтверждены")
    lines.extend([
        f"- Наличие: {html.escape(_availability_label(facts))}",
        f"- Бюджет: {html.escape(_budget_status_label(str(facts.get('budget_status') or '')))}",
    ])
    ps5_line = _facts_ps5_line(facts)
    if category == "tv" and ps5_line:
        lines.append(f"- PS5: {html.escape(ps5_line)}")
    diagnostics = (
        ("Exact match", facts.get("exact_match")),
        ("Причина exact", facts.get("exact_match_reason")),
        ("Цена confidence/evidence", " / ".join(str(item) for item in (facts.get("price_confidence"), facts.get("price_evidence")) if item)),
        ("Карточка", " / ".join(str(item) for item in (facts.get("product_card_confidence"), facts.get("product_card_reason")) if item)),
        ("Источник", facts.get("source_confidence")),
        ("Verification", facts.get("verification_confidence")),
        ("Platform trust", " / ".join(str(item) for item in (facts.get("platform_name"), facts.get("platform_trust")) if item)),
        ("Seller trust", facts.get("seller_trust")),
        ("Verification access", facts.get("verification_access")),
        ("HTTP/blocked", " / ".join(str(item) for item in (facts.get("fetch_status_code"), facts.get("blocked_reason")) if item)),
        ("Browser/proxy", f"browser={bool(facts.get('browser_used'))}, proxy={bool(facts.get('proxy_used'))}"),
        ("Category quality", facts.get("category_quality_score")),
    )
    for label, value in diagnostics:
        text = str(value or "").strip()
        if text:
            lines.append(f"- {label}: {html.escape(_clip_text(text, 500))}")
    breakdown = facts.get("score_breakdown")
    if isinstance(breakdown, dict) and breakdown:
        rendered = ", ".join(f"{key}={value:+g}" for key, value in breakdown.items() if isinstance(value, (int, float)))
        lines.append(f"- Score breakdown: {html.escape(_clip_text(rendered, 700))}")
    cap_reasons = [str(item) for item in facts.get("score_cap_reasons") or [] if str(item).strip()]
    if cap_reasons:
        lines.append(f"- Caps: {html.escape(', '.join(cap_reasons[:6]))}")
    if facts.get("cache_age_seconds") is not None:
        lines.append(f"- Cache age: {html.escape(str(facts.get('cache_age_seconds')))} сек.")
    fact_evidence = facts.get("fact_evidence")
    if isinstance(fact_evidence, dict) and fact_evidence:
        evidence_parts = []
        for key, value in list(fact_evidence.items())[:12]:
            if isinstance(value, dict):
                evidence_parts.append(f"{key}:{value.get('confidence', '-')}/{value.get('evidence', '-')}")
        if evidence_parts:
            lines.append(f"- Facts evidence: {html.escape(_clip_text(', '.join(evidence_parts), 800))}")
    automatic = facts.get("automatic_verification") if isinstance(facts.get("automatic_verification"), dict) else {}
    manual = facts.get("manual_verification") if isinstance(facts.get("manual_verification"), dict) else {}
    final = facts.get("final_presentation_state") if isinstance(facts.get("final_presentation_state"), dict) else {}
    lines.extend(["", "<b>Automatic verification (immutable):</b>"])
    lines.append(
        "- status/exact/access: "
        + html.escape(" / ".join(str(value or "-") for value in (
            automatic.get("verify_status"),
            automatic.get("exact_match") or automatic.get("exact_match_status"),
            automatic.get("verification_access"),
        )))
    )
    auto_warnings = [str(value) for value in automatic.get("warnings") or [] if str(value).strip()]
    lines.append(f"- warnings: {html.escape(_clip_text('; '.join(auto_warnings) or 'нет', 1000))}")
    lines.extend(["", "<b>Manual verification (audit):</b>"])
    decisions = manual.get("decisions") if isinstance(manual.get("decisions"), dict) else {}
    decisions_text = ", ".join(f"{key}={value}" for key, value in decisions.items()) or "нет решений"
    lines.append(f"- decisions: {html.escape(_clip_text(decisions_text, 800))}")
    lines.append(
        f"- actor/time: {html.escape(str(manual.get('manual_verified_by') or '-'))} / "
        f"{html.escape(str(manual.get('manual_verified_at') or '-'))}"
    )
    lines.append(f"- note: {html.escape(_clip_text(str(manual.get('manual_note') or 'нет'), 800))}")
    history = manual.get("history") if isinstance(manual.get("history"), list) else []
    lines.append(f"- history events: {len(history)}")
    lines.extend(["", "<b>Final presentation state:</b>"])
    lines.append(
        f"- status/ready/specialist: {html.escape(str(final.get('status') or '-'))} / "
        f"{bool(final.get('presentation_ready'))} / {bool(final.get('specialist_verified'))}"
    )
    lines.append(f"- confirmed: {html.escape(', '.join(final.get('confirmed_fields') or []) or 'нет')}")
    lines.append(f"- unresolved: {html.escape(', '.join(final.get('unresolved_fields') or []) or 'нет')}")
    lines.append(f"- blockers: {html.escape('; '.join(final.get('blocking_reasons') or []) or 'нет')}")
    lines.append(f"- client warnings: {html.escape(_clip_text('; '.join(final.get('warnings') or []) or 'нет', 1000))}")
    lines.append(
        f"- suppressed automatic: "
        f"{html.escape(_clip_text('; '.join(final.get('suppressed_automatic_warnings') or []) or 'нет', 1000))}"
    )
    return lines


def format_search_result_summary(sr: SearchResult, idx: int) -> str:
    """Короткая строка результата для общего списка без URL и длинных полей."""
    title = html.escape(_clip_text(sr.title or "без названия", 95))
    price = format_price(sr.price) if sr.price else "цена не найдена"
    source = html.escape(_clip_text(source_display_name(sr.source), 45))
    status = html.escape(_result_status_label(sr.status))
    facts = _parse_facts(getattr(sr, "facts_json", ""))
    facts_line = _facts_compact_line(facts)
    ps5_line = _facts_ps5_line(facts)
    warnings = _admin_safe_items(_resolved_admin_warnings(sr), 3)
    verification = "нужна ручная проверка" if warnings else "без замечаний"
    lines = [
        f"{idx}. <b>{title}</b>\n"
        f"   {html.escape(price)} | {source} | {status}"
    ]
    if facts_line:
        lines.append(f"   {html.escape(facts_line)}")
    if ps5_line:
        lines.append(f"   PS5: {html.escape(_clip_text(ps5_line, 120))}")
    lines.append(f"   Проверка: {verification}")
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
    normal_count = sum(
        1 for item in results
        if _stored_verify_status(item) in {"VERIFIED_GOOD", "VERIFIED_OK"}
    )
    manual_count = len(results) - normal_count
    total = len(results)
    page_count = max(1, (total + RESULTS_PAGE_SIZE - 1) // RESULTS_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    start = page * RESULTS_PAGE_SIZE
    page_items = results[start:start + RESULTS_PAGE_SIZE]

    lines = [
        f"📦 <b>Найденные варианты (заявка #{req_id})</b>",
        (
            f"Страница {page + 1} из {page_count}. Проверенных хороших: {normal_count}"
            if normal_count
            else f"Страница {page + 1} из {page_count}. Проверенных хороших: 0. Требуют ручной проверки: {manual_count}"
        ),
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
    buttons.append([InlineKeyboardButton(text="🤖 Сделать карточки рекомендаций", callback_data=f"aicards_{req_id}")])
    buttons.append([InlineKeyboardButton(text="🧪 Debug поиска", callback_data=f"debugsearch_{req_id}")])
    buttons.append([InlineKeyboardButton(text="➕ Добавить вручную", callback_data=f"addprod_{req_id}")])
    buttons.append([InlineKeyboardButton(text="👁 Полный предпросмотр для админа", callback_data=f"adminpreview_{req_id}")])
    buttons.append([InlineKeyboardButton(text="👀 Клиентский статус", callback_data=f"preview_{req_id}")])
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
    req = _optional_request(item.request_id)
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


def _resolved_admin_warnings(item: SearchResult) -> list[str]:
    facts = _parse_facts(getattr(item, "facts_json", ""))
    prepared = dict(facts)
    automatic = dict(prepared.get("automatic_verification") or facts)
    raw = automatic.get("warnings")
    existing = raw if isinstance(raw, list) else ([raw] if raw else [])
    meta = _alice_card_meta(item)
    manual_check = meta.get("manual_check") or meta.get("notes") or []
    if isinstance(manual_check, str):
        manual_check = [manual_check]
    if not isinstance(manual_check, list):
        manual_check = []
    automatic["warnings"] = [*existing, *_parse_risk_flags(item.risk_flags), *manual_check]
    prepared["automatic_verification"] = automatic
    final = resolve_final_presentation(prepared)
    return [str(value) for value in final.get("warnings") or [] if str(value).strip()]


def format_alice_card(sr: SearchResult, idx: int) -> str:
    """Карточка рекомендации для админа; raw diagnostics остаются в Debug."""
    risks = _admin_safe_items(_resolved_admin_warnings(sr), 3)
    req = _optional_request(sr.request_id)
    meta = _alice_card_meta(sr)
    verification = _normalised_verification(sr)
    facts = dict(verification)
    final = verification.get("final_presentation_state") if isinstance(verification.get("final_presentation_state"), dict) else {}
    facts.update(final.get("final_facts") or {})
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
    lifecycle = {
        "DRAFT": "черновик", "GENERATED": "готова к проверке",
        "APPROVED": "утверждена", "REJECTED": "отклонена", "ERROR": "нужна ручная правка",
    }.get(get_ai_card_status(sr), "черновик")
    platform = str(facts.get("platform_name") or source_display_name(sr.source))
    platform_type = str(facts.get("platform_type") or "").upper()
    seller_state = str(final.get("seller_state") or "UNSET").upper()
    seller = str(facts.get("seller") or (platform if platform_type == "RETAIL" else "проверен" if seller_state == SELLER_VERIFIED else "требует проверки"))
    exact_label = "совпадает" if final.get("model_verified") else "требует проверки"
    lines = [f"🧩 <b>Карточка рекомендации {idx}</b> — {state}", f"<b>Подготовка:</b> {html.escape(lifecycle)}"]
    lines.append(f"<b>Название:</b> {html.escape(sr.title or 'не указано')}")
    lines.append(f"<b>Цена:</b> {format_price(sr.price) if sr.price else 'уточнить'}")
    lines.append(f"<b>Площадка:</b> {html.escape(platform)}")
    lines.append(f"<b>Продавец:</b> {html.escape(seller)}")
    lines.append(f"<b>Соответствие товару:</b> {html.escape(exact_label)}")
    if _link_status(sr.url) == "✅ есть":
        safe_url = html.escape(sr.url, quote=True)
        lines.append(f'<b>Ссылка:</b> <a href="{safe_url}">открыть</a>')
    else:
        lines.append("<b>Ссылка:</b> ⚠️ ссылку нужно искать вручную")
    why = _admin_safe_items([sr.snippet], 1)
    if why and any(marker in why[0].casefold() for marker in ("успейте", "купите", "акция", "промокод")):
        why = []
    lines.append(f"<b>Почему:</b> {html.escape(why[0] if why else 'соответствует сохранённым фактам; проверьте пункты ниже')}")
    lines.append(f"<b>Главные риски:</b> {html.escape('; '.join(risks) if risks else 'явные риски не выявлены')}")
    confirmed = [
        label for key, label in (
            ("link_verified", "карточка товара"),
            ("model_verified", "модель и обязательные характеристики"),
            ("price_verified", "цена"),
            ("availability_verified", "наличие"),
            ("seller_verified", "продавец"),
        ) if final.get(key)
    ]
    lines.append(f"<b>Подтверждено:</b> {html.escape('; '.join(confirmed) if confirmed else 'пока ничего')}")
    if meta.get("note"):
        lines.append(f"<b>Заметка:</b> {html.escape(str(meta.get('note')))}")
    lines.append(f"<b>Статус ссылки:</b> {_link_status(sr.url)}")
    lines.append(f"<b>Статус проверки:</b> {'✅ ссылка подтверждена' if final.get('link_verified') else '⚠️ ссылку нужно подтвердить'}")
    lines.append(f"<b>Статус цены:</b> {'✅ цена подтверждена' if final.get('price_verified') else '⚠️ цену нужно подтвердить'}")
    lines.extend(_manual_checklist_lines(sr))
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

    try:
        await message.edit_text(
            format_alice_card(sr, index),
            reply_markup=_alice_product_keyboard(sr),
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
            f"🧩 <b>Карточки рекомендаций для заявки #{req_id}</b>\n"
            f"Найдено товаров: {len(cards)}",
            parse_mode="HTML",
        )
    if not cards:
        await message.answer("Карточек рекомендаций пока нет. Запустите генерацию или добавьте варианты вручную.")
        return
    for index, card in enumerate(cards[:20], 1):
        await message.answer(
            format_alice_card(card, index),
            reply_markup=_alice_product_keyboard(card),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


def _telegram_chunks(text: str, limit: int = 3900) -> list[str]:
    """Совместимый HTML-aware splitter с безопасным лимитом Telegram."""
    return chunk_html(text, limit=limit)


async def _send_search_debug(message: Message, req_id: int) -> None:
    """Send every part of a diagnostic report within Telegram's message limit."""
    for chunk in _telegram_chunks(format_search_debug(req_id)):
        await message.answer(chunk, parse_mode="HTML", disable_web_page_preview=True)


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
        reply_markup=kb_admin_menu(admin_queue_counts()),
        parse_mode="HTML"
    )


# ---------- Список заявок ----------

@router.callback_query(F.data == "admin_all")
async def admin_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_admin_queue(callback, "📋 <b>Все заявки</b>", get_all_requests())


@router.callback_query(F.data == "admin_new")
async def admin_new(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_admin_queue(callback, "📥 <b>Новые заявки</b>", _filter_requests("NEW"))


@router.callback_query(F.data == "admin_searching")
async def admin_searching(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_admin_queue(callback, "🔎 <b>В поиске</b>", _filter_requests("SEARCHING"))


@router.callback_query(F.data == "admin_waiting")
async def admin_waiting(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_admin_queue(callback, "💳 <b>Ожидают оплаты</b>", _filter_requests("WAITING_PAYMENT"))


@router.callback_query(F.data == "admin_review")
async def admin_review_queue(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_admin_queue(callback, "🧑‍💻 <b>Требуют проверки</b>", _filter_requests("ADMIN_REVIEW", "AI_CARDS_DRAFT"))


@router.callback_query(F.data == "admin_ready")
async def admin_ready_queue(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_admin_queue(callback, "✅ <b>Готовые</b>", _filter_requests("READY"))


@router.callback_query(F.data == "admin_delivered")
async def admin_delivered_queue(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await _show_admin_queue(callback, "📤 <b>Отправленные</b>", _filter_requests("DELIVERED"))


@router.callback_query(F.data == "admin_problem")
async def admin_problem_queue(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    requests = _filter_requests("FAILED", "NEED_CLARIFICATION")
    low_feedback = get_low_feedback(max_rating=2, limit=20)
    if not low_feedback:
        await _show_admin_queue(callback, "⚠️ <b>Проблемные</b>", requests)
        return
    lines = ["⚠️ <b>Проблемные заявки и низкие оценки</b>", ""]
    lines.extend(format_request_card(item) for item in requests[:10])
    lines.append("\n<b>Низкие оценки:</b>")
    for item in low_feedback[:10]:
        lines.append(
            f"• Заявка #{item.request_id or '—'} · {item.rating}/5"
            + (f" · {escape_html(item.comment[:200])}" if item.comment else "")
        )
    buttons = [
        [InlineKeyboardButton(text=f"Открыть #{item.id}", callback_data=f"view_{item.id}")]
        for item in requests[:10]
    ]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back")])
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    metrics = build_product_metrics()
    low = len(get_low_feedback(max_rating=2, limit=1000))
    average = metrics.get("average_delivery_seconds")
    average_text = "нет данных" if average is None else f"{round(average / 60)} мин"
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"Заявки начаты: {metrics['requests_started']}\n"
        f"Заявки завершены: {metrics['requests_completed']}\n"
        f"Оплаты: {metrics['payments']}\n"
        f"Готовые подборы: {metrics['ready_recommendations']}\n"
        f"Отправлено: {metrics['delivered']}\n"
        f"Среднее время: {average_text}\n"
        f"Ручная проверка: {metrics['manual_review_percent']}%\n"
        f"Низкие оценки: {low}\n"
        f"Неподдерживаемые категории: {metrics['unsupported_categories']}"
    )
    await callback.message.edit_text(text, reply_markup=kb_admin_back(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "admin_back")
async def admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🔧 <b>Панель админа</b>\n\nВыбери раздел:",
        reply_markup=kb_admin_menu(admin_queue_counts()),
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
            text += f'• <a href="{html.escape(link["url"], quote=True)}">{html.escape(link["site"])}</a>\n'

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
    try:
        transition_request(req_id, SEARCHING, actor=f"admin:{callback.from_user.id}", reason="Заявка взята в работу")
    except InvalidStatusTransition as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Заявка взята в работу")

    # Уведомляем пользователя
    req = get_request(req_id)
    if req:
        try:
            await update_client_progress(callback.bot, req_id, "analysis")
        except Exception:
            pass

    # Обновляем сообщение
    req = get_request(req_id)
    text = format_request_detail(req)
    links = json.loads(req.search_links) if req.search_links else []
    if links:
        text += "\n\n🔗 <b>Поисковые ссылки:</b>\n"
        for link in links:
            text += f'• <a href="{html.escape(link["url"], quote=True)}">{html.escape(link["site"])}</a>\n'
    await callback.message.edit_text(
        text,
        reply_markup=kb_admin_request(req_id, req.status),
        parse_mode="HTML"
    )


# ---------- Автопоиск ----------

async def _prepare_verified_ai_cards(req: Request, *, actor: str) -> dict:
    """Create final cards only from products that already passed verification."""
    candidates = select_verified_ai_card_candidates([
        item for item in get_all_search_results(req.id)
        if str(item.origin or "").lower() != "alice"
    ])
    if not candidates:
        return {
            "success": False,
            "message": "Нет товара с подтверждёнными моделью, ценой, ссылкой, наличием и продавцом.",
            "cards": [],
        }
    try:
        transition_request(
            req.id,
            AI_CARDS_DRAFT,
            actor=actor,
            reason="Автоматически создаются карточки из проверенных товаров",
        )
    except InvalidStatusTransition as exc:
        return {"success": False, "message": str(exc), "cards": []}

    result = await asyncio.to_thread(generate_ai_cards_from_candidates, req, candidates)
    if not result.get("success"):
        return result
    cards = result.get("cards") or []
    replace_alice_results(req.id, cards)
    update_request(
        req.id,
        alice_response=json.dumps({
            "source": "verified_candidates_auto",
            "raw_response": result.get("raw_response", ""),
            "parsed_items": cards,
        }, ensure_ascii=False),
    )
    return result

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

    try:
        current_status = normalize_request_status(req.status)
    except Exception:
        current_status = FAILED
    if current_status != SEARCHING:
        try:
            req = transition_request(req_id, SEARCHING, actor=f"admin:{callback.from_user.id}", reason="Запуск поиска")
        except InvalidStatusTransition as exc:
            await callback.answer(str(exc), show_alert=True)
            return

    route = SearchOrchestrationService.route(req)
    if route is not SearchMode.AUTO:
        transition_request(req_id, ADMIN_REVIEW, actor=f"admin:{callback.from_user.id}", reason="Категория требует ручной проверки")
        _track_admin_event("unsupported_category", callback.from_user.id, req_id, {"category": req.category or "unknown"})
        await update_client_progress(callback.bot, req_id, "admin_review")
        await callback.message.answer(
            "Автоматический поиск для этой заявки отключён. Используйте ручной вариант, сравнение ссылок или заметку специалиста.",
            reply_markup=kb_admin_request(req_id, ADMIN_REVIEW),
        )
        await callback.answer("Передано на ручную проверку")
        return

    await callback.answer("Запускаю автопоиск...")
    _track_admin_event("search_started", callback.from_user.id, req_id)
    await update_client_progress(callback.bot, req_id, "search")

    # Feature-flag bridge: default legacy; shadow не меняет заявку результатами V2.
    result = await run_search_for_request(req)

    if result["success"]:
        transition_request(req_id, ADMIN_REVIEW, actor=f"admin:{callback.from_user.id}", reason="Предложения собраны")
        _track_admin_event("admin_review_started", callback.from_user.id, req_id)
        req = get_request(req_id)
        prepared = await _prepare_verified_ai_cards(req, actor=f"admin:{callback.from_user.id}")
        if prepared.get("success"):
            await update_client_progress(callback.bot, req_id, "verification")
            await callback.message.answer(
                f"✅ {html.escape(result['message'])}\n\n"
                f"Карточки подготовлены автоматически: {len(prepared.get('cards') or [])}. "
                "Остался один финальный контроль подборки.",
                reply_markup=kb_admin_request(req_id, AI_CARDS_DRAFT),
                parse_mode="HTML",
            )
            await send_alice_cards(callback.message, req_id, include_header=False)
        else:
            await update_client_progress(callback.bot, req_id, "admin_review")
            await callback.message.answer(
                f"✅ {html.escape(result['message'])}\n\n"
                "Автопроверка не смогла подтвердить безопасный товар. "
                "Откройте подходящий вариант, сверьте страницу и нажмите «✅ Проверил товар целиком».",
                reply_markup=kb_admin_request(req_id, ADMIN_REVIEW),
                parse_mode="HTML",
            )
    else:
        transition_request(req_id, FAILED, actor=f"admin:{callback.from_user.id}", reason="Автопоиск не дал результата")
        await update_client_progress(callback.bot, req_id, "admin_review")
        await callback.message.answer(
            f"⚠️ {html.escape(result['message'])}",
            reply_markup=kb_admin_request(req_id, FAILED)
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

    await callback.answer("Создаю карточки...")
    await callback.message.answer("🧩 Готовим карточки из уже проверенных товаров.")
    await update_client_progress(callback.bot, req_id, "verification")
    result = await _prepare_verified_ai_cards(req, actor=f"admin:{callback.from_user.id}")
    if not result.get("success"):
        await callback.message.answer(
            f"⚠️ {html.escape(result.get('message') or 'Не удалось сделать карточки.')}\n\n"
            "Откройте подходящий вариант и нажмите «✅ Проверил товар целиком».",
            parse_mode="HTML",
            reply_markup=kb_admin_request(req_id, req.status),
        )
        return

    cards = result.get("cards") or []
    await callback.message.answer(
        f"✅ Карточки созданы: {len(cards)}. Товарные факты уже подтверждены; "
        "повторно утверждать каждую карточку не нужно.",
        parse_mode="HTML",
    )
    await send_alice_cards(callback.message, req_id, include_header=False)


@router.callback_query(F.data.startswith("aiapprove_"))
async def approve_ai_card(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    try:
        card = AdminReviewService().approve(result_id)
    except AIReviewError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    update_search_result(result_id, checked_at=datetime.now().isoformat())
    card = get_search_result(result_id)
    await _refresh_alice_card(callback.message, card)
    await callback.answer("Карточка утверждена")


@router.callback_query(F.data.startswith("aiedit_"))
async def edit_ai_card_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    card = get_search_result(result_id)
    if not card or card.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    await state.update_data(ai_edit_result_id=result_id)
    await state.set_state(AdminStates.editing_ai_text)
    await callback.message.answer(
        f"Отправьте новый текст «Почему рекомендуем» для:\n<b>{escape_html(card.title)}</b>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("complinks_"))
async def comparison_links_view(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    items = get_comparison_links(req_id)
    if not items:
        await callback.answer("У заявки нет ссылок для сравнения", show_alert=True)
        return
    lines = [f"⚖️ <b>Ссылки заявки #{req_id}</b>", ""]
    buttons = []
    for index, item in enumerate(items, 1):
        lines.append(
            f"{index}. <b>{escape_html(item.title or 'Факты не заполнены')}</b>\n"
            f"{escape_html(source_display_name(item.source))} · {format_price(item.price) if item.price else 'цена не указана'}\n"
            f"Статус: {escape_html(item.status)}"
        )
        buttons.append([InlineKeyboardButton(text=f"✏️ Исправить ссылку #{index}", callback_data=f"compedit_{item.id}")])
    buttons.append([InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")])
    await callback.message.answer(
        "\n\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("compedit_"))
async def comparison_link_edit_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    link_id = int(callback.data.split("_")[1])
    item = get_comparison_link(link_id)
    if not item:
        await callback.answer("Ссылка не найдена", show_alert=True)
        return
    await state.update_data(comparison_link_id=link_id)
    await state.set_state(AdminStates.editing_comparison)
    await callback.message.answer(
        "Отправьте исправленные факты одной строкой:\n"
        "<code>Название | Цена | Модель | Продавец | Наличие</code>\n\n"
        "Цена может быть пустой, остальные поля не выдумывайте.",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.editing_comparison, F.text)
async def comparison_link_edit_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    link_id = int(data.get("comparison_link_id") or 0)
    item = get_comparison_link(link_id)
    parts = [part.strip() for part in message.text.split("|")]
    if not item or len(parts) != 5:
        await message.answer("Нужны ровно 5 полей, разделённых символом |.")
        return
    title, price_text, model, seller, availability = parts
    price = _extract_price(price_text) if price_text else None
    if price_text and price is None:
        await message.answer("Не удалось распознать цену.")
        return
    facts = {"model": model} if model else {}
    updated = update_comparison_link(
        link_id,
        title=title[:500],
        price=price,
        model=model[:300],
        seller=seller[:300],
        availability=availability[:200],
        facts_json=json.dumps(facts, ensure_ascii=False),
        status="MANUAL_CHECKED",
        manual_check_required=0,
        manual_note=f"Исправлено администратором {message.from_user.id}",
    )
    for result in get_all_search_results(item.request_id or 0):
        if result.url == item.url:
            update_search_result(
                result.id,
                title=title or result.title,
                price=price,
                source=item.source,
                facts_json=json.dumps(facts, ensure_ascii=False),
                admin_note="Факты ссылки исправлены администратором",
            )
    await state.clear()
    await message.answer(f"Факты сохранены для {escape_html(updated.source if updated else item.source)}.", parse_mode="HTML")


@router.callback_query(F.data.startswith("reqnote_"))
async def request_note_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await state.update_data(request_note_id=req_id)
    await state.set_state(AdminStates.editing_request_note)
    await callback.message.answer(
        f"Внутренняя заметка по заявке #{req_id}. Отправьте текст или «-» для удаления.\n"
        f"Текущая: {escape_html(req.admin_note or 'нет')}",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.editing_request_note, F.text)
async def request_note_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    req_id = int(data.get("request_note_id") or 0)
    note = "" if message.text.strip() == "-" else " ".join(message.text.split())[:2000]
    update_request(req_id, admin_note=note)
    await state.clear()
    await message.answer("Заметка сохранена." if note else "Заметка удалена.")


@router.callback_query(F.data.startswith("admincancel_"))
async def admin_cancel_request(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    try:
        transition_request(req_id, "CANCELLED", actor=f"admin:{callback.from_user.id}", reason="Отменено администратором")
    except Exception as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.message.edit_text(f"❌ Заявка #{req_id} отменена.", reply_markup=kb_admin_back())
    await callback.answer()


@router.callback_query(F.data.startswith("markready_"))
async def mark_request_ready(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    issue, _excluded = get_alice_report_issues(req)
    if issue:
        await callback.answer(issue, show_alert=True)
        return
    if not get_alice_results(req_id) and count_approved_results(req_id) < 1:
        await callback.answer("Нет выбранных вариантов.", show_alert=True)
        return
    try:
        transition_request(req_id, READY, actor=f"admin:{callback.from_user.id}", reason="Результат утверждён")
    except InvalidStatusTransition as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    _track_admin_event("recommendation_ready", callback.from_user.id, req_id)
    await update_client_progress(callback.bot, req_id, "ready")
    await callback.message.edit_text(
        f"✅ Результат по заявке #{req_id} утверждён и готов к отправке.",
        reply_markup=kb_admin_request(req_id, READY),
        parse_mode="HTML",
    )
    await callback.answer("Результат утверждён")


@router.message(AdminStates.editing_ai_text, F.text)
async def edit_ai_card_text(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    result_id = int(data.get("ai_edit_result_id") or 0)
    try:
        card = AdminReviewService().edit(result_id, why=message.text)
    except AIReviewError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer("Текст сохранён как DRAFT. Проверьте и утвердите карточку.")
    await message.answer(
        format_alice_card(card, _alice_card_index(card.id, card.request_id)),
        reply_markup=_alice_product_keyboard(card),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("aiimage_"))
async def edit_ai_image_prompt(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    if not get_search_result(result_id):
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    await state.update_data(ai_image_result_id=result_id)
    await state.set_state(AdminStates.editing_ai_image)
    await callback.message.answer("Пришлите изображение или Telegram file_id. Без изображения карточка останется текстовой.")
    await callback.answer()


@router.message(AdminStates.editing_ai_image)
async def edit_ai_image(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    result_id = int(data.get("ai_image_result_id") or 0)
    file_id = ""
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.text:
        file_id = message.text.strip()
    if not file_id:
        await message.answer("Нужно прислать изображение или file_id.")
        return
    try:
        service = AdminReviewService()
        card = get_search_result(result_id)
        if card and get_ai_card_status(card) == AI_CARD_APPROVED:
            service.reject(result_id)
            service.transition(result_id, AI_CARD_DRAFT)
        service.edit(result_id, image_file_id=file_id)
    except AIReviewError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer("Изображение сохранено. Карточка снова в DRAFT и требует утверждения.")


@router.callback_query(F.data.startswith("airegen_"))
async def regenerate_ai_card(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    card = get_search_result(result_id)
    if not card or card.origin != "alice":
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    meta = _alice_card_meta(card)
    candidate = get_search_result(int(meta.get("candidate_id") or 0))
    if not candidate:
        await callback.answer("Исходный кандидат не найден. Создайте карточки заново из заявки.", show_alert=True)
        return
    req = get_request(card.request_id)
    result = await asyncio.to_thread(generate_ai_cards_from_candidates, req, [candidate])
    generated = (result.get("cards") or [None])[0]
    if not generated:
        await callback.answer("Не удалось обновить карточку", show_alert=True)
        return
    service = AdminReviewService()
    current = get_ai_card_status(card)
    try:
        if current == AI_CARD_APPROVED:
            service.reject(result_id)
            service.transition(result_id, AI_CARD_DRAFT)
        elif current != AI_CARD_DRAFT:
            service.transition(result_id, AI_CARD_DRAFT)
        service.edit(
            result_id,
            why=generated.get("why") or "; ".join(generated.get("pluses") or []),
            risks=generated.get("risks") or [],
            manual_check=generated.get("manual_check") or [],
        )
        if not result.get("fallback"):
            service.transition(result_id, AI_CARD_GENERATED)
    except AIReviewError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    refreshed = get_search_result(result_id)
    await _refresh_alice_card(callback.message, refreshed)
    await callback.answer("Карточка перегенерирована")


@router.callback_query(F.data.startswith("debugsearch_"))
async def debug_search(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    if not get_request(req_id):
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await _send_search_debug(callback.message, req_id)
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
    await _send_search_debug(message, req_id)


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


@router.callback_query(F.data.startswith("v2compare_"))
async def compare_legacy_v2(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    if not get_request(req_id):
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    snapshot = load_shadow_comparison(req_id)
    if not snapshot:
        await callback.answer("Shadow snapshot ещё не создан", show_alert=True)
        return
    await callback.message.answer(
        f"<pre>{html.escape(format_shadow_comparison(snapshot))}</pre>",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    await callback.answer()


# ---------- Просмотр конкретного найденного товара ----------

@router.callback_query(F.data.startswith("viewresult_"))
async def view_result(callback: CallbackQuery, state: FSMContext):
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
    lines.append(f"🏪 Источник: {html.escape(source_display_name(sr.source))}")
    if sr.url:
        lines.append(f"🔗 <a href=\"{html.escape(sr.url, quote=True)}\">Ссылка</a>")
    if sr.snippet:
        lines.append(f"\n<i>{html.escape(sr.snippet[:200])}</i>")
    lines.append(f"\n📋 Решение: {_result_status_label(sr.status)}")

    risk_flags = _admin_safe_items(_resolved_admin_warnings(sr), 3)
    if risk_flags:
        lines.append(f"⚠️ Риски: {html.escape('; '.join(str(item) for item in risk_flags[:3]))}")

    if sr.admin_note:
        lines.append(f"\n📝 Заметка: {html.escape(sr.admin_note)}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_admin_product_keyboard(sr),
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
    lines.append(f"🏪 Источник: {html.escape(source_display_name(next_item.source))}")
    if next_item.url:
        lines.append(f"🔗 <a href=\"{html.escape(next_item.url, quote=True)}\">Ссылка</a>")
    if next_item.snippet:
        lines.append(f"\n<i>{html.escape(next_item.snippet[:200])}</i>")
    lines.append(f"\n📋 Решение: {_result_status_label(next_item.status)}")
    await callback.message.edit_text(
        "\n".join(lines), reply_markup=_admin_product_keyboard(next_item), parse_mode="HTML"
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
    if new_status != "DO_NOT_BUY" and not await _require_final_role_ready(callback, sr):
        return
    updates = {"status": new_status, "checked_at": datetime.now().isoformat()}
    update_search_result(result_id, **updates)
    await callback.answer(f"Отмечено: {label}")

    # Обновляем карточку товара
    sr.status = new_status
    lines = [f"📦 <b>Вариант</b>\n"]
    lines.append(f"<b>{escape_html(sr.title)}</b>\n")
    if sr.price:
        lines.append(f"💰 Цена: {format_price(sr.price)}")
    if sr.source:
        lines.append(f"🏪 Источник: {escape_html(source_display_name(sr.source))}")
    if sr.url:
        lines.append(f"🔗 <a href=\"{html.escape(sr.url, quote=True)}\">Ссылка</a>")
    lines.append(f"\n📋 Статус: {new_status}")

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=_admin_product_keyboard(sr),
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
    if await state.get_state() == AdminStates.editing_price.state:
        await state.set_state(None)
        await state.update_data(editprice_result_id=None)
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
        if not is_free_search_mode():
            transition_request(
                req_id,
                WAITING_PAYMENT,
                actor=f"admin:{callback.from_user.id}",
                reason="Клиенту отправлен предпросмотр",
                extra_fields={"preview_text": preview},
            )
            _track_admin_event("payment_started", callback.from_user.id, req_id)
        else:
            _track_admin_event("preview_sent", callback.from_user.id, req_id)
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
            text += f'• <a href="{html.escape(link["url"], quote=True)}">{html.escape(link["site"])}</a>\n'
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
    try:
        current_status = normalize_request_status(req.status)
    except Exception:
        current_status = FAILED
    if current_status != WAITING_PAYMENT:
        await callback.answer("Сначала отправьте клиентский предпросмотр до оплаты.", show_alert=True)
        return
    issue, _missing_links = get_alice_report_issues(req)
    if issue:
        await callback.answer(issue, show_alert=True)
        return
    if not get_alice_results(req_id) and count_approved_results(req_id) < 1:
        await callback.answer("Нельзя открыть полный отчёт: нет подтверждённых вариантов.", show_alert=True)
        return
    transition_request(req_id, PAID, actor=f"admin:{callback.from_user.id}", reason="Оплата подтверждена вручную")
    _track_admin_event("payment_succeeded", callback.from_user.id, req_id)
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
            text += f'• <a href="{html.escape(link["url"], quote=True)}">{html.escape(link["site"])}</a>\n'
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
    try:
        current_status = normalize_request_status(req.status)
    except Exception:
        current_status = FAILED
    if current_status == PAID:
        try:
            transition_request(
                req_id,
                READY,
                actor=f"admin:{callback.from_user.id}",
                reason="Оплаченный отчёт готов к отправке",
            )
        except InvalidStatusTransition as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        req = get_request(req_id)
        current_status = READY
    if current_status != READY:
        await callback.answer("Сначала утвердите результат.", show_alert=True)
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

    delivery = DeliveryService.prepare(req)
    if not delivery.allowed:
        await callback.answer(delivery.reason, show_alert=True)
        return
    report = delivery.report

    try:
        # Разбиваем на чанки по 3900 символов (ограничение Telegram)
        chunks = _telegram_chunks(report)
        for chunk in chunks:
            await callback.bot.send_message(
                req.user_id, chunk, parse_mode="HTML",
                disable_web_page_preview=True,
            )
        action_cards = [item.card for item in RecommendationService().for_request(req_id)]
        if not action_cards:
            action_cards = [
                item for item in get_search_results(req_id)
                if item.status in {"BEST", "CHEAP", "RELIABLE", "APPROVED"}
            ][:3]
        for card in action_cards[:3]:
            caption = f"Действия для <b>{escape_html(card.title)}</b>"
            if getattr(card, "image_file_id", ""):
                try:
                    await callback.bot.send_photo(
                        req.user_id,
                        card.image_file_id,
                        caption=caption,
                        reply_markup=result_card_keyboard(card.id, card.url),
                        parse_mode="HTML",
                    )
                    continue
                except Exception:
                    pass
            await callback.bot.send_message(
                req.user_id, caption,
                reply_markup=result_card_keyboard(card.id, card.url),
                parse_mode="HTML", disable_web_page_preview=True,
            )
        transition_request(
            req_id,
            DELIVERED,
            actor=f"admin:{callback.from_user.id}",
            reason="Полный отчёт отправлен клиенту",
            extra_fields={"report_text": report},
        )
        _track_admin_event("delivered", callback.from_user.id, req_id)
        await callback.bot.send_message(
            req.user_id,
            "<b>Насколько полезным был подбор?</b>",
            reply_markup=feedback_keyboard(req_id),
            parse_mode="HTML",
        )
        await callback.answer("Отчёт отправлен клиенту")
    except Exception as e:
        await callback.answer(f"Ошибка отправки: {e}", show_alert=True)
        return

    # Обновляем сообщение админа
    req = get_request(req_id)
    text = format_request_detail(req)
    text += "\n\n✅ Отчёт отправлен клиенту."
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
    if not result_id:
        return

    new_price = parse_admin_price(message.text or "")
    if new_price is None:
        await message.answer(
            "❌ Не удалось определить цену. Введи, например: <code>45000</code>, <code>45к</code> или <code>45 000 ₽</code>.",
            parse_mode="HTML",
        )
        return

    sr = get_search_result(result_id)
    if not sr:
        await message.answer("Вариант не найден.")
        return
    update_search_result(
        result_id,
        price=new_price,
        price_verified=True,
        facts_json=_manual_price_facts(sr, new_price, admin_id=message.from_user.id),
    )
    await state.set_state(None)
    await state.update_data(editprice_result_id=None)
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
                reply_markup=_alice_product_keyboard(sr),
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
                             reply_markup=_admin_product_keyboard(sr))


# ──────────────────────────────────────────────
#  Новая воронка: ready → preview → paid → send
# ──────────────────────────────────────────────

@router.callback_query(F.data.startswith("readytopay_"))
@router.callback_query(F.data.startswith("readyforpay_"))
async def ready_for_payment(callback: CallbackQuery):
    """Approve a verified selection; paid flow remains available behind config."""
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
    if not readiness.can_send:
        await callback.message.answer(
            f"⚠️ Готовность заявки: {readiness.percent}% — отправлять предпросмотр не рекомендуется.\n\n"
            f"{format_readiness(readiness)}",
            reply_markup=kb_ready_for_payment(req_id, readiness.percent),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await callback.answer()
        return

    if is_free_search_mode():
        report = build_full_report(req)
        if report.startswith("Нельзя"):
            await callback.answer(report, show_alert=True)
            return
        try:
            current_status = normalize_request_status(req.status)
            if current_status != READY:
                transition_request(
                    req_id,
                    READY,
                    actor=f"admin:{callback.from_user.id}",
                    reason="Бесплатная подборка утверждена для отправки",
                )
        except InvalidStatusTransition as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        _track_admin_event("recommendation_ready", callback.from_user.id, req_id)
        await update_client_progress(callback.bot, req_id, "ready")
        req = get_request(req_id)
        await callback.message.edit_text(
            f"✅ <b>Заявка #{req_id}</b> — подборка утверждена.\n"
            f"Готовность: {readiness.percent}%\n\n"
            "Можно отправить клиенту полный бесплатный отчёт.",
            reply_markup=kb_admin_request(req_id, req.status),
            parse_mode="HTML",
        )
        await callback.answer("Подборка готова к отправке")
        return

    # Платный режим: отправляем ограниченный предпросмотр.
    preview = build_preview(req)
    if preview.startswith("Нельзя"):
        await callback.answer(preview, show_alert=True)
        return

    try:
        await callback.bot.send_message(req.user_id, preview, parse_mode="HTML")
        transition_request(
            req_id,
            WAITING_PAYMENT,
            actor=f"admin:{callback.from_user.id}",
            reason="Предпросмотр отправлен, ожидается оплата",
            extra_fields={"preview_text": preview},
        )
        _track_admin_event("payment_started", callback.from_user.id, req_id)
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
    """Legacy callback; weak selections are never sent in free mode."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    if is_free_search_mode():
        await callback.answer(
            "Подборка пока слабая. Улучшите поиск и проверку перед отправкой клиенту.",
            show_alert=True,
        )
        return

    preview = build_preview(req)
    if preview.startswith("Нельзя"):
        await callback.answer(preview, show_alert=True)
        return

    try:
        await callback.bot.send_message(req.user_id, preview, parse_mode="HTML")
        transition_request(
            req_id,
            WAITING_PAYMENT,
            actor=f"admin:{callback.from_user.id}",
            reason="Legacy forcepreview адаптирован к безопасному предпросмотру",
            extra_fields={"preview_text": preview},
        )
        _track_admin_event("payment_started", callback.from_user.id, req_id)
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

    try:
        transition_request(req_id, PAID, actor=f"admin:{callback.from_user.id}", reason="Оплата подтверждена вручную")
    except InvalidStatusTransition as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    _track_admin_event("payment_succeeded", callback.from_user.id, req_id)
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
    """Legacy callback: совместимо объясняет новый безопасный порядок."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    await callback.answer(
        "Отправка без оплаты отключена. Подтвердите оплату и утвердите результат.",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("sendwithoutpay_"))
async def send_without_payment(callback: CallbackQuery):
    """Legacy callback сохранён, но больше не обходит оплату и READY-gate."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    req_id = int(callback.data.split("_")[1])
    req = get_request(req_id)
    if not req:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    await callback.answer(
        "Отправка без оплаты отключена. Подтвердите оплату, утвердите результат и отправьте из READY.",
        show_alert=True,
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


@router.callback_query(F.data.regexp(r"^mcreason_\d+$"))
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
    reasons = (
        "слабый рейтинг магазина", "мало отзывов", "не тот город",
        "б/у вместо нового", "другая память/цвет/модель",
        "серый товар/сомнительная гарантия",
        "цена ниже рынка и выглядит подозрительно",
        "нет нормальной доставки/возврата",
    )
    raw_reason = callback.data[len("mcreason_set_"):]
    if raw_reason.isdigit() and int(raw_reason) < len(reasons):
        reason = reasons[int(raw_reason)]
    else:
        # Legacy adapter для старых коротких callback_data.
        reason = raw_reason
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
    try:
        current_status = normalize_request_status(req.status)
    except Exception:
        current_status = FAILED
    if is_free_search_mode():
        if current_status != READY:
            await callback.answer("Сначала утвердите подборку.", show_alert=True)
            return
        await _send_report(callback, req_id)
        return
    if current_status not in {PAID, READY}:
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
    try:
        current = normalize_request_status(req.status)
        if current == "NEW":
            transition_request(req_id, SEARCHING, actor=f"admin:{message.from_user.id}", reason="Начата ручная подготовка карточек")
        transition_request(req_id, AI_CARDS_DRAFT, actor=f"admin:{message.from_user.id}", reason="Ручные карточки сохранены")
    except InvalidStatusTransition:
        pass

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
        f"🧩 <b>Карточки рекомендаций — заявка #{req_id}</b>\n"
        f"Найдено товаров: {len(cards)}\n\n"
        "Карточки отправлены ниже. Убраные варианты не попадут в предпросмотр.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👁 Полный предпросмотр для админа", callback_data=f"adminpreview_{req_id}")],
            [InlineKeyboardButton(text="👀 Клиентский статус", callback_data=f"preview_{req_id}")],
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
    if not await _require_final_role_ready(callback, candidate):
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
    if not await _require_final_role_ready(callback, item):
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
    try:
        AdminReviewService().reject(result_id)
    except AIReviewError as exc:
        await callback.answer(str(exc), show_alert=True)
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
    if not await _require_final_role_ready(callback, item):
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
    if not await _require_final_role_ready(callback, item):
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


async def _refresh_manual_checklist_card(message: Message, item: SearchResult) -> None:
    if item.origin == "alice":
        await _refresh_alice_card(message, item)
        return
    try:
        await message.edit_reply_markup(reply_markup=_admin_product_keyboard(item))
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


async def _save_manual_checklist_confirmation(
    callback: CallbackQuery,
    field: str,
    verified: bool,
    *,
    note: str,
    value=None,
    seller_state: str | None = None,
    compatibility_updates: dict | None = None,
    result_updates: dict | None = None,
) -> tuple[SearchResult, dict] | None:
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return None
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item:
        await callback.answer("Вариант не найден", show_alert=True)
        return None
    facts = _manual_confirmation_facts(
        item,
        field,
        verified,
        admin_id=callback.from_user.id,
        note=note,
        value=value,
        seller_state=seller_state,
        compatibility_updates=compatibility_updates,
    )
    updates = dict(result_updates or {})
    updates["facts_json"] = json.dumps(facts, ensure_ascii=False)
    update_search_result(result_id, **updates)
    refreshed = get_search_result(result_id)
    if not refreshed:
        await callback.answer("Карточка не найдена", show_alert=True)
        return None
    await _refresh_manual_checklist_card(callback.message, refreshed)
    return refreshed, resolve_final_presentation(facts)


@router.callback_query(F.data.startswith("manualall_"))
async def manual_confirm_product(callback: CallbackQuery):
    """One audited action after the admin has checked the whole product page."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item:
        await callback.answer("Вариант не найден", show_alert=True)
        return
    if not item.price:
        await callback.answer("Сначала укажите актуальную цену.", show_alert=True)
        return
    if _link_status(item.url) != "✅ есть":
        await callback.answer("Сначала укажите корректную прямую ссылку.", show_alert=True)
        return
    warning = store_url_warning(item.source, item.url)
    if warning:
        await callback.answer(f"{warning}. Исправьте ссылку перед подтверждением.", show_alert=True)
        return
    blocked = resolve_final_presentation(_parse_facts(item.facts_json)).get("blocking_reasons") or []
    if blocked:
        await callback.answer(
            f"Товар заблокирован проверкой: {', '.join(str(reason) for reason in blocked)}.",
            show_alert=True,
        )
        return

    checked_at = datetime.now().astimezone()
    facts = _parse_facts(item.facts_json)
    values = {
        "model": item.title,
        "link": item.url,
        "price": int(item.price),
        "availability": "in_stock",
        "seller": item.source,
    }
    notes = {
        "model": "Модель и комплектация сверены администратором",
        "link": "Прямая ссылка открыта администратором",
        "price": "Актуальная цена сверена администратором",
        "availability": "Возможность заказа подтверждена администратором",
        "seller": "Продавец подтверждён администратором",
    }
    for field in ("model", "link", "price", "availability", "seller"):
        facts = apply_manual_confirmation(
            facts,
            field,
            True,
            verified_by=f"telegram_admin:{callback.from_user.id}",
            verified_at=checked_at,
            note=notes[field],
            value=values[field],
            seller_state=SELLER_VERIFIED if field == "seller" else None,
        )
    facts.update({
        "price": int(item.price),
        "price_verified": True,
        "price_confidence": "high",
        "price_evidence": "manual_admin",
        "product_page_verified": True,
        "link_verified": True,
        "availability_verified": True,
        "availability": "in_stock",
        "available": True,
        "seller_verified": True,
        "seller_verification": SELLER_VERIFIED,
    })
    update_search_result(
        result_id,
        price_verified=True,
        link_check_status=LinkCheckStatus.VERIFIED.value,
        facts_json=json.dumps(facts, ensure_ascii=False),
        checked_at=checked_at.isoformat(),
    )
    refreshed = get_search_result(result_id)
    if not refreshed:
        await callback.answer("Вариант не найден после сохранения", show_alert=True)
        return
    final = resolve_final_presentation(facts)
    if not final.get("presentation_ready"):
        await _refresh_manual_checklist_card(callback.message, refreshed)
        await callback.answer(
            "Проверка сохранена, но остались незакрытые поля: "
            + ", ".join(final.get("unresolved_fields") or []),
            show_alert=True,
        )
        return

    await _refresh_manual_checklist_card(callback.message, refreshed)
    await callback.answer("Товар полностью подтверждён")
    req = get_request(refreshed.request_id)
    if not req or refreshed.origin == "alice":
        return
    prepared = await _prepare_verified_ai_cards(req, actor=f"admin:{callback.from_user.id}")
    if prepared.get("success"):
        await callback.message.answer(
            f"✅ Товар подтверждён. Карточки созданы автоматически: {len(prepared.get('cards') or [])}.",
        )
        await send_alice_cards(callback.message, req.id, include_header=False)
    else:
        await callback.message.answer(
            f"✅ Товар подтверждён, но карточки не созданы: {html.escape(prepared.get('message') or 'ошибка')}.",
            parse_mode="HTML",
        )


@router.callback_query(F.data.startswith("manualmodel_"))
async def manual_confirm_model(callback: CallbackQuery):
    result = await _save_manual_checklist_confirmation(
        callback,
        "model",
        True,
        note="Модель и обязательная комплектация сверены администратором",
    )
    if not result:
        return
    _item, final = result
    hard_mismatch = any(
        reason in {"MODEL_MISMATCH", "REQUIRED_SPEC_MISMATCH", "ACCESSORY", "WRONG_PRODUCT"}
        for reason in final.get("blocking_reasons", [])
    )
    if hard_mismatch:
        await callback.answer(
            "Подтверждение записано, но hard mismatch не снят. Обычная отметка модель не переопределяет.",
            show_alert=True,
        )
        return
    await callback.answer("Модель и комплектация подтверждены")


@router.callback_query(F.data.startswith("manuallink_"))
async def manual_confirm_link(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item:
        await callback.answer("Вариант не найден", show_alert=True)
        return
    if _link_status(item.url) != "✅ есть":
        await callback.answer("Сначала укажите корректную прямую ссылку.", show_alert=True)
        return
    warning = store_url_warning(item.source, item.url)
    if warning:
        await callback.answer(f"{warning}. Исправьте ссылку перед подтверждением.", show_alert=True)
        return
    result = await _save_manual_checklist_confirmation(
        callback,
        "link",
        True,
        note="Прямая ссылка открыта и подтверждена администратором",
        value=item.url,
        compatibility_updates={"product_page_verified": True},
        result_updates={"link_check_status": LinkCheckStatus.VERIFIED.value},
    )
    if result:
        await callback.answer("Ссылка подтверждена")


@router.callback_query(F.data.startswith("manualavailable_"))
async def manual_confirm_availability(callback: CallbackQuery):
    result = await _save_manual_checklist_confirmation(
        callback,
        "availability",
        True,
        note="Наличие подтверждено администратором",
        value="in_stock",
        compatibility_updates={
            "availability_verified": True,
            "available": True,
            "availability": "in_stock",
        },
    )
    if result:
        await callback.answer("Наличие подтверждено")


@router.callback_query(F.data.startswith("manualprice_"))
async def manual_confirm_current_price(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item:
        await callback.answer("Вариант не найден", show_alert=True)
        return
    if not item.price:
        await callback.answer("Сначала укажите актуальную цену.", show_alert=True)
        return
    update_search_result(
        result_id,
        price_verified=True,
        facts_json=_manual_price_facts(item, int(item.price), admin_id=callback.from_user.id),
    )
    refreshed = get_search_result(result_id)
    if refreshed:
        await _refresh_manual_checklist_card(callback.message, refreshed)
    await callback.answer("Текущая цена подтверждена")


@router.callback_query(F.data.startswith("manualsellerok_"))
async def manual_confirm_seller(callback: CallbackQuery):
    result = await _save_manual_checklist_confirmation(
        callback,
        "seller",
        True,
        note="Продавец подтверждён администратором",
        seller_state=SELLER_VERIFIED,
        compatibility_updates={
            "seller_verified": True,
            "seller_verification": SELLER_VERIFIED,
        },
    )
    if result:
        await callback.answer("Продавец подтверждён")


@router.callback_query(F.data.startswith("manualsellercheck_"))
async def manual_mark_seller_requires_check(callback: CallbackQuery):
    result = await _save_manual_checklist_confirmation(
        callback,
        "seller",
        False,
        note="Администратор отметил необходимость дополнительной проверки продавца",
        seller_state=SELLER_REQUIRES_CHECK,
        compatibility_updates={
            "seller_verified": False,
            "seller_verification": SELLER_REQUIRES_CHECK,
        },
    )
    if result:
        await callback.answer("Продавец оставлен с явным требованием проверки")


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


@router.callback_query(F.data.startswith("debugresult_"))
async def debug_result(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Нет доступа", show_alert=True)
        return
    result_id = int(callback.data.split("_")[1])
    item = get_search_result(result_id)
    if not item:
        await callback.answer("Вариант не найден", show_alert=True)
        return
    lines = [
        f"🛠 <b>Debug варианта #{item.id}</b>",
        f"Score: {int(round(item.score))}",
        f"Raw status: {escape_html(item.status)}",
        f"Raw risks: {escape_html('; '.join(_parse_risk_flags(item.risk_flags)) or 'нет')}",
        *_facts_detail_lines(item),
    ]
    await callback.message.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
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
        facts_json=json.dumps(
            _manual_confirmation_facts(
                item,
                "link",
                False,
                admin_id=message.from_user.id,
                note="Ссылка изменена и требует повторного подтверждения",
                value=new_url[:500],
                compatibility_updates={"product_page_verified": False},
            ),
            ensure_ascii=False,
        ),
    )
    await state.clear()
    item = get_search_result(result_id)
    await message.answer(
        "✅ Ссылка сохранена.",
        reply_markup=_alice_product_keyboard(item),
    )
    await message.answer(format_alice_card(item, _alice_card_index(item.id, item.request_id)),
                         reply_markup=_alice_product_keyboard(item),
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
    update_search_result(
        result_id,
        link_check_status=LinkCheckStatus.VERIFIED.value,
        facts_json=json.dumps(
            _manual_confirmation_facts(
                item,
                "link",
                True,
                admin_id=callback.from_user.id,
                note="Прямая ссылка открыта и подтверждена администратором",
                value=item.url,
                compatibility_updates={"product_page_verified": True},
            ),
            ensure_ascii=False,
        ),
    )
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
    update_search_result(
        result_id,
        link_check_status=LinkCheckStatus.UNSUITABLE.value,
        facts_json=json.dumps(
            _manual_confirmation_facts(
                item,
                "link",
                False,
                admin_id=callback.from_user.id,
                note="Ссылка вручную признана неподходящей",
                value=item.url,
                compatibility_updates={"product_page_verified": False},
            ),
            ensure_ascii=False,
        ),
    )
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

    item = get_search_result(result_id)
    if not item:
        await message.answer("Карточка не найдена.")
        return
    seller_facts = _manual_confirmation_facts(
        item,
        "seller",
        False,
        admin_id=message.from_user.id,
        note="Магазин изменён; продавца нужно проверить повторно",
        value=new_store,
        seller_state=SELLER_REQUIRES_CHECK,
        compatibility_updates={
            "seller_verified": False,
            "seller_verification": SELLER_REQUIRES_CHECK,
        },
    )
    update_search_result(
        result_id,
        source=new_store,
        facts_json=json.dumps(seller_facts, ensure_ascii=False),
    )
    await message.answer("✅ Магазин обновлён")

    item = get_search_result(result_id)
    if item and item.origin == "alice":
        await message.answer(
            format_alice_card(item, _alice_card_index(item.id, item.request_id)),
            reply_markup=_alice_product_keyboard(item),
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
        facts_json=json.dumps(
            _manual_confirmation_facts(
                item,
                "availability",
                False,
                admin_id=callback.from_user.id,
                note="Администратор подтвердил отсутствие товара в наличии",
                value="unavailable",
                compatibility_updates={
                    "availability_verified": True,
                    "available": False,
                    "availability": "unavailable",
                },
            ),
            ensure_ascii=False,
        ),
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
                             reply_markup=_admin_product_keyboard(sr))
