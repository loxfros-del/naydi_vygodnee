"""Safe Telegram-facing presentation helpers."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
import html
import re
from typing import Any
import json
from datetime import datetime

from app.product_config import ServicePackage, get_service_packages
from app.verification_state import resolve_final_presentation
from app.purchase_claims import has_price_advantage_claim
from app.ui_texts import (
    CONDITION_LABELS,
    PRICING_INTRO,
    PRIORITY_LABELS,
    PROGRESS_STAGES,
    UNSUPPORTED_CATEGORY_TEXT,
)


TELEGRAM_TEXT_LIMIT = 4096
DEFAULT_CHUNK_LIMIT = 3900


def escape_html(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def format_price(value: object) -> str:
    if value is None or value == "":
        return "Стоимость уточняется"
    if isinstance(value, bool):
        return "Стоимость уточняется"
    try:
        price = int(str(value).replace(" ", ""))
    except (TypeError, ValueError):
        return escape_html(value)
    if price < 0:
        return "Стоимость уточняется"
    if price == 0:
        return "Бесплатно"
    return f"{price:,} ₽".replace(",", " ")


def format_pricing(packages: Iterable[ServicePackage] | None = None) -> str:
    items = tuple(packages or get_service_packages())
    lines = [PRICING_INTRO]
    for item in items:
        lines.extend(
            [
                "",
                f"<b>{escape_html(item.title)}</b>",
                escape_html(item.description),
            ]
        )
        lines.extend(f"• {escape_html(feature)}" for feature in item.features)
        if item.available:
            lines.append(f"Стоимость: <b>{escape_html(item.price_label)}</b>")
        else:
            lines.append("<i>Временно недоступно</i>")
    return "\n".join(lines)


_SOURCE_NAMES = {
    "yandex_market": "Яндекс Маркет",
    "yandex_market_direct": "Яндекс Маркет",
    "market_yandex": "Яндекс Маркет",
    "ozon": "Ozon",
    "ozon_direct": "Ozon",
    "wildberries": "Wildberries",
    "wildberries_direct": "Wildberries",
    "wb": "Wildberries",
    "dns": "DNS",
    "dns_shop": "DNS",
    "mvideo": "М.Видео",
    "m_video": "М.Видео",
    "citilink": "Ситилинк",
    "megamarket": "Мегамаркет",
    "avito": "Авито",
    "generic_web": "Интернет-магазин",
    "generic": "Интернет-магазин",
    "manual": "Проверено специалистом",
    "market_check": "Проверено специалистом",
}


def source_display_name(source: object) -> str:
    raw = str(source or "").strip()
    if not raw:
        return "Магазин не указан"
    key = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    if key in _SOURCE_NAMES:
        return _SOURCE_NAMES[key]
    for technical, display in _SOURCE_NAMES.items():
        if key.startswith(f"{technical}_"):
            return display
    return raw.replace("_", " ").strip().title()


CLIENT_ROLE_LABELS = {
    "BEST": "⭐ Лучший выбор",
    "BUDGET": "💰 Дешевле, но есть нюансы",
    "BACKUP": "🛡 Запасной вариант",
}

_CLIENT_FACT_LABELS = {
    "brand": "Бренд",
    "model": "Модель",
    "memory": "Память",
    "ram": "RAM",
    "ssd": "SSD",
    "diagonal": "Диагональ",
    "resolution": "Разрешение",
    "refresh_rate": "Частота",
    "panel": "Матрица",
    "connection": "Подключение",
    "anc": "Шумоподавление",
    "form_factor": "Формат",
    "lumbar_support": "Поясничная поддержка",
    "headrest": "Подголовник",
    "load_capacity": "Допустимая нагрузка",
}

_CLIENT_TECH_MARKERS = (
    "weak_candidate", "verified_good", "verified_ok", "verify_blocked",
    "exact_match", "confidence", "evidence", "score", "browser", "proxy",
    "captcha", "403", "401", "429", "http ", "network block", "raw status",
    "страница заблокировала", "сайт заблокировал", "не подтверждена прямая",
    "классификация:", "price mismatch", "bad encoding",
)
_PROMOTIONAL_MARKERS = (
    "успейте", "купите", "закажите", "акция", "промокод", "реклама",
    "хит продаж", "лучшая цена только сегодня",
)


def _json_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        try:
            raw = json.loads(str(value or "[]"))
        except (TypeError, json.JSONDecodeError):
            raw = [value] if str(value or "").strip() else []
    if not isinstance(raw, list):
        raw = [raw]
    return [str(item).strip() for item in raw if str(item).strip()]


def _safe_client_items(values: Iterable[object], limit: int = 3) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split()).strip(" ;,.-")
        lowered = text.casefold()
        if not text or any(marker in lowered for marker in _CLIENT_TECH_MARKERS):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _seller_label(
    facts: dict[str, Any],
    platform_type: str,
    platform: str,
    seller_state: str,
) -> str:
    seller = str(facts.get("seller") or "").strip()
    if seller:
        return seller
    if platform_type == "RETAIL":
        return platform
    if seller_state == "VERIFIED":
        return "проверен"
    return "требует проверки"


def _resolve_client_state(
    facts: dict[str, Any],
    extra_warnings: Iterable[object] = (),
) -> dict[str, Any]:
    """Resolve client warnings after adding editorial/manual-check text."""
    prepared = dict(facts)
    automatic = dict(prepared.get("automatic_verification") or facts)
    automatic["warnings"] = _json_list(automatic.get("warnings")) + [
        str(item).strip() for item in extra_warnings if str(item).strip()
    ]
    prepared["automatic_verification"] = automatic
    return resolve_final_presentation(prepared)


def format_client_card(card: Any, role: str) -> str:
    """Короткая клиентская карточка без score, verify/cache и evidence-полей."""
    normalized_role = str(role or "").upper()
    facts = _json_dict(getattr(card, "facts_json", ""))
    meta = _json_dict(getattr(card, "admin_note", ""))
    editorial_checks = _json_list(meta.get("manual_check"))
    raw_risks = _json_list(getattr(card, "risk_flags", ""))
    final = _resolve_client_state(facts, [*editorial_checks, *raw_risks])
    display_facts = dict(facts)
    display_facts.update(final.get("final_facts") or {})
    platform = str(display_facts.get("platform_name") or source_display_name(getattr(card, "source", "")))
    platform_type = str(display_facts.get("platform_type") or "").upper()
    seller = _seller_label(display_facts, platform_type, platform, str(final.get("seller_state") or ""))
    price_value = display_facts.get("price", getattr(card, "price", None))
    lines = [
        f"<b>{CLIENT_ROLE_LABELS.get(normalized_role, '📌 Рекомендация')}</b>",
        "",
        f"<b>{escape_html(getattr(card, 'title', '') or 'Название уточняется')}</b>",
        f"Цена: {format_price(price_value)}",
        f"Площадка: {escape_html(platform)}",
        f"Продавец: {escape_html(seller)}",
    ]
    if final.get("specialist_verified"):
        lines.append("Статус: ✅ Проверено специалистом")
    elif final.get("presentation_ready"):
        lines.append("Статус: ✅ Подтверждено по данным источника")

    why_items: list[str] = []
    why = " ".join(str(getattr(card, "snippet", "") or "").split())
    if why and not has_price_advantage_claim(why) and not any(marker in why.casefold() for marker in (*_CLIENT_TECH_MARKERS, *_PROMOTIONAL_MARKERS)):
        why_items.append(why[:300])
    exact = str(display_facts.get("exact_match") or display_facts.get("exact_match_status") or "").upper()
    if exact == "EXACT":
        why_items.append("точная модель и обязательные характеристики совпадают")
    elif exact == "COMPATIBLE_VARIANT":
        why_items.append("найден вариант модели; комплектацию нужно уточнить")
    budget_status = str(display_facts.get("budget_status") or "").upper()
    if budget_status == "IN_BUDGET":
        why_items.append("цена укладывается в бюджет")
    if platform_type == "RETAIL":
        why_items.append("предложение магазина; условия гарантии и возврата нужно сверить")
    elif platform_type == "MARKETPLACE":
        why_items.append("предложение найдено на известной торговой площадке")
    elif platform_type == "CLASSIFIED":
        why_items.append("объявление продавца; состояние требует отдельной проверки")
    why_items = _safe_client_items(why_items, 3) or ["причины выбора нужно уточнить у специалиста"]
    lines.extend(["", "<b>Почему рекомендуем:</b>"])
    lines.extend(f"• {escape_html(item)}" for item in why_items)
    lines.append("Сравнение рынка: доказательства экономии в этой карточке не приложены.")

    confirmed: list[str] = []
    confirmation_labels = (
        ("link_verified", "карточка товара"),
        ("model_verified", "модель и обязательные характеристики"),
        ("price_verified", "цена"),
        ("availability_verified", "наличие"),
        ("seller_verified", "продавец"),
    )
    confirmed.extend(label for key, label in confirmation_labels if final.get(key))
    if confirmed:
        lines.extend(["", "<b>Что подтверждено:</b>"])
        lines.extend(f"• {escape_html(item)}" for item in _safe_client_items(confirmed, 4))

    manual_check = _safe_client_items(final.get("warnings") or [], 3)
    if exact == "COMPATIBLE_VARIANT":
        manual_check.insert(0, "уточнить точную комплектацию и обязательные характеристики")
    if not manual_check:
        manual_check = ["итоговую сумму с доставкой и обязательными доплатами", "условия гарантии и возврата на момент заказа"]
    manual_check = _safe_client_items(manual_check, 3)
    lines.extend(["", "<b>Что проверить:</b>"])
    lines.extend(f"• {escape_html(item)}" for item in manual_check)

    fact_parts = []
    for key, label in _CLIENT_FACT_LABELS.items():
        value = display_facts.get(key)
        if value not in (None, "", [], {}):
            fact_parts.append(f"{label}: {value}")
        if len(fact_parts) >= 5:
            break
    if fact_parts:
        lines.extend(["", f"Характеристики: {escape_html('; '.join(fact_parts))}"])
    return "\n".join(lines)


def format_client_result_summary(
    request: Any,
    cards: Iterable[Any],
    *,
    found_count: int | None = None,
    checked_at: str | None = None,
) -> str:
    items = list(cards)
    prices = [int(item.price) for item in items if getattr(item, "price", None)]
    checked = checked_at or next(
        (str(getattr(item, "checked_at", "")) for item in items if getattr(item, "checked_at", "")),
        "",
    )
    try:
        checked_label = datetime.fromisoformat(checked).strftime("%d.%m.%Y %H:%M") if checked else "не указана"
    except ValueError:
        checked_label = "не указана"
    total = found_count if found_count is not None else len(items)
    lines = [
        "✅ <b>Подбор готов</b>",
        "",
        f"Товар: <b>{escape_html(getattr(request, 'product_name', '') or getattr(request, 'product', '') or 'товар')}</b>",
        f"Найдено предложений: {max(int(total), len(items))}",
        f"Отобрано специалистом: {len(items)}",
    ]
    if prices:
        lines.append(f"Диапазон цен: {format_price(min(prices))} — {format_price(max(prices))}")
    lines.append(f"Дата проверки: {checked_label}")
    lines.extend(["", "Ниже — до трёх вариантов с плюсами, рисками и тем, что важно проверить."])
    return "\n".join(lines)


def format_progress(current_stage: str | int, request_id: int | None = None) -> str:
    keys = [key for key, _label in PROGRESS_STAGES]
    if isinstance(current_stage, int):
        index = max(0, min(current_stage, len(keys) - 1))
    else:
        normalized = str(current_stage or "accepted").strip().lower()
        if normalized not in keys:
            raise ValueError(f"Unknown progress stage: {current_stage!r}")
        index = keys.index(normalized)

    finished = index == len(keys) - 1
    title = "✅ <b>Подбор готов</b>" if finished else "🔎 <b>Идёт подбор</b>"
    if request_id is not None:
        title += f" · заявка #{int(request_id)}"
    lines = [title, ""]
    for position, (_key, label) in enumerate(PROGRESS_STAGES):
        if finished or position < index:
            marker = "✅"
        elif position == index:
            marker = "🔄"
        else:
            marker = "⬜"
        lines.append(f"{marker} {label}")
    return "\n".join(lines)


def _display_value(value: object) -> str:
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    return str(value or "").strip()


def format_request_summary(data: Mapping[str, Any]) -> str:
    """Render a compact, escaped wizard summary from old or new field names."""

    product = data.get("product_name") or data.get("product") or "Не указано"
    budget = data.get("budget")
    city = data.get("city") or "Не указано"
    condition = str(data.get("condition") or "new")
    priority = str(data.get("priority") or "balance")
    requirements = data.get("requirements") or data.get("important_criteria") or "Не указаны"
    category = data.get("category_title") or data.get("category") or "Не указана"

    condition_label = CONDITION_LABELS.get(condition, _display_value(condition))
    priority_label = PRIORITY_LABELS.get(priority, _display_value(priority))
    lines = [
        "<b>Проверьте заявку</b>",
        "",
        f"Категория: <b>{escape_html(category)}</b>",
        f"Товар: <b>{escape_html(product)}</b>",
        f"Бюджет: {format_price(budget)}",
        f"Город: {escape_html(city)}",
        f"Состояние: {escape_html(condition_label)}",
        f"Обязательные требования: {escape_html(requirements)}",
        f"Приоритет: {escape_html(priority_label)}",
    ]
    return "\n".join(lines)


def format_unsupported_category(query: object = "") -> str:
    lines = ["<b>Нужна ручная проверка</b>", "", UNSUPPORTED_CATEGORY_TEXT]
    if str(query or "").strip():
        lines.extend(["", f"Ваш запрос: <i>{escape_html(query)}</i>"])
    return "\n".join(lines)


_HTML_TOKEN_RE = re.compile(
    r"<[^>]+>|&(?:#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]+);|\s+|[^\s<&]+|[<&]",
    flags=re.DOTALL,
)
_TAG_NAME_RE = re.compile(r"^<\s*/?\s*([a-zA-Z0-9-]+)")
_VOID_TAGS = {"br", "hr"}


def _stack_after(stack: list[tuple[str, str]], token: str) -> list[tuple[str, str]]:
    if not token.startswith("<") or token.startswith(("<!--", "<!", "<?")):
        return list(stack)
    match = _TAG_NAME_RE.match(token)
    if not match:
        return list(stack)
    name = match.group(1).lower()
    stripped = token.lstrip()
    if stripped.startswith("</"):
        updated = list(stack)
        for index in range(len(updated) - 1, -1, -1):
            if updated[index][0] == name:
                return updated[:index]
        return updated
    if token.rstrip().endswith("/>") or name in _VOID_TAGS:
        return list(stack)
    return [*stack, (name, token)]


def _closing_tags(stack: list[tuple[str, str]]) -> str:
    return "".join(f"</{name}>" for name, _opening in reversed(stack))


def _opening_tags(stack: list[tuple[str, str]]) -> str:
    return "".join(opening for _name, opening in stack)


def chunk_html(text: object, limit: int = DEFAULT_CHUNK_LIMIT) -> list[str]:
    """Split HTML text without cutting tags/entities; close and reopen active tags.

    The function constrains raw payload length, which is stricter than Telegram's
    post-entity 4096-character limit and therefore leaves a safe margin.
    """

    value = str(text or "")
    if limit <= 0 or limit > TELEGRAM_TEXT_LIMIT:
        raise ValueError(f"limit must be between 1 and {TELEGRAM_TEXT_LIMIT}")
    if len(value) <= limit:
        return [value]

    queue = deque(_HTML_TOKEN_RE.findall(value))
    chunks: list[str] = []
    stack: list[tuple[str, str]] = []
    current = ""
    has_content = False

    while queue:
        token = queue.popleft()
        next_stack = _stack_after(stack, token)
        candidate_size = len(current) + len(token) + len(_closing_tags(next_stack))
        if candidate_size <= limit:
            current += token
            stack = next_stack
            if not token.startswith("<"):
                has_content = True
            continue

        if has_content:
            chunk = current + _closing_tags(stack)
            if chunk:
                chunks.append(chunk)
            current = _opening_tags(stack)
            has_content = False
            queue.appendleft(token)
            continue

        # A single long plain token must be split; tags and entities stay atomic.
        is_atomic_markup = token.startswith("<") or (token.startswith("&") and token.endswith(";"))
        available = limit - len(current) - len(_closing_tags(stack))
        if is_atomic_markup or available <= 0:
            raise ValueError("An HTML tag/entity is too large for the requested chunk limit")
        head, tail = token[:available], token[available:]
        current += head
        has_content = bool(head)
        if tail:
            queue.appendleft(tail)

    final = current + _closing_tags(stack)
    if final:
        chunks.append(final)
    return chunks or [value]


__all__ = [
    "DEFAULT_CHUNK_LIMIT",
    "TELEGRAM_TEXT_LIMIT",
    "chunk_html",
    "escape_html",
    "format_price",
    "format_client_card",
    "format_client_result_summary",
    "format_pricing",
    "format_progress",
    "format_request_summary",
    "format_unsupported_category",
    "source_display_name",
]
