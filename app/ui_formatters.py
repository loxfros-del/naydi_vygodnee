"""Safe Telegram-facing presentation helpers."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
import html
import re
from typing import Any

from app.product_config import ServicePackage, get_service_packages
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
    "format_pricing",
    "format_progress",
    "format_request_summary",
    "format_unsupported_category",
    "source_display_name",
]
