"""Reusable client keyboards with short, stable callback payloads."""

from __future__ import annotations

from collections.abc import Iterable

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from urllib.parse import urlsplit

from app.config import settings
from app.product_config import AUTO_CATEGORIES, ServicePackage, get_service_packages
from app.ui_texts import (
    BTN_BACK,
    BTN_CANCEL,
    BTN_COMPARE_LINKS,
    BTN_CONFIRM,
    BTN_EDIT,
    BTN_FAQ,
    BTN_HOME,
    BTN_HOW_IT_WORKS,
    BTN_MANUAL_SELECTION,
    BTN_MY_REQUESTS,
    BTN_OPEN_NOVA,
    BTN_PRICING,
    BTN_QUICK_REQUEST,
    BTN_SELECT_PRODUCT,
    BTN_SKIP,
    BTN_SEND_LINKS,
    BTN_SUPPORT,
    CONDITION_LABELS,
    FEEDBACK_LABELS,
    PRIORITY_LABELS,
)


TELEGRAM_CALLBACK_DATA_LIMIT = 64

CB_SELECT_PRODUCT = "ui:select"
CB_COMPARE_LINKS = "ui:compare"
CB_MY_REQUESTS = "ui:requests"
CB_HOW_IT_WORKS = "ui:how"
CB_PRICING = "ui:pricing"
CB_SUPPORT = "ui:support"
CB_FAQ = "ui:faq"
CB_HOME = "ui:home"
CB_QUICK_REQUEST = "ui:quick"

CB_CATEGORY_PREFIX = "wiz:cat:"
CB_CONDITION_PREFIX = "wiz:condition:"
CB_PRIORITY_PREFIX = "wiz:priority:"
CB_PACKAGE_PREFIX = "pkg:"
CB_FEEDBACK_PREFIX = "fb:"
CB_RESULT_COMPARE_PREFIX = "result:compare:"
CB_RESULT_DETAIL_PREFIX = "result:detail:"
CB_RESULT_SAVE_PREFIX = "result:save:"
CB_WIZARD_EDIT_PREFIX = "wiz:edit:"

CB_WIZARD_BACK = "wiz:back"
CB_WIZARD_SKIP = "wiz:skip"
CB_WIZARD_CANCEL = "wiz:cancel"
CB_WIZARD_CONFIRM = "wiz:confirm"
CB_WIZARD_EDIT = "wiz:edit"
CB_WIZARD_MANUAL = "wiz:cat:manual"
CB_LINKS_CONFIRM = "links:confirm"
CB_LINKS_CANCEL = "links:cancel"


def ensure_callback_data(value: str) -> str:
    """Validate Bot API's 1–64 byte callback_data contract."""

    size = len(value.encode("utf-8"))
    if not 1 <= size <= TELEGRAM_CALLBACK_DATA_LIMIT:
        raise ValueError(f"callback_data must be 1..64 UTF-8 bytes, got {size}: {value!r}")
    return value


def _inline_button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=ensure_callback_data(callback_data))


def _safe_miniapp_url(value: object) -> str:
    """Return only a public HTTPS launch URL suitable for Telegram Web Apps."""
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return url


def main_menu_keyboard(*, miniapp_url: str | None = None) -> ReplyKeyboardMarkup:
    """Compact client menu; add the Mini App only once its public URL is set."""

    url = _safe_miniapp_url(settings.MINI_APP_URL if miniapp_url is None else miniapp_url)
    rows = [
        [KeyboardButton(text=BTN_SELECT_PRODUCT)],
        [KeyboardButton(text=BTN_COMPARE_LINKS), KeyboardButton(text=BTN_MY_REQUESTS)],
        [KeyboardButton(text=BTN_HOW_IT_WORKS), KeyboardButton(text=BTN_PRICING)],
        [KeyboardButton(text=BTN_SUPPORT), KeyboardButton(text=BTN_FAQ)],
    ]
    if url:
        rows.insert(0, [KeyboardButton(text=BTN_OPEN_NOVA, web_app=WebAppInfo(url=url))])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выбери действие",
    )


def main_menu_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_inline_button(BTN_SELECT_PRODUCT, CB_SELECT_PRODUCT)],
            [_inline_button(BTN_COMPARE_LINKS, CB_COMPARE_LINKS)],
            [_inline_button(BTN_MY_REQUESTS, CB_MY_REQUESTS)],
            [
                _inline_button(BTN_HOW_IT_WORKS, CB_HOW_IT_WORKS),
                _inline_button(BTN_PRICING, CB_PRICING),
            ],
            [_inline_button(BTN_SUPPORT, CB_SUPPORT), _inline_button(BTN_FAQ, CB_FAQ)],
        ]
    )


def service_packages_keyboard(
    packages: Iterable[ServicePackage] | None = None,
) -> InlineKeyboardMarkup:
    package_items = tuple(packages or get_service_packages())
    rows = [
        [_inline_button(f"{item.title} · {item.price_label}", f"{CB_PACKAGE_PREFIX}{item.code}")]
        for item in package_items
        if item.available
    ]
    rows.append([_inline_button(BTN_HOME, CB_HOME)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [_inline_button(f"{category.emoji} {category.title}", f"{CB_CATEGORY_PREFIX}{category.code}")]
        for category in AUTO_CATEGORIES
    ]
    rows.extend(
        [
            [_inline_button(BTN_QUICK_REQUEST, CB_QUICK_REQUEST)],
            [_inline_button(BTN_MANUAL_SELECTION, CB_WIZARD_MANUAL)],
            [
                _inline_button(BTN_HOME, CB_HOME),
                _inline_button(BTN_CANCEL, CB_WIZARD_CANCEL),
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def wizard_navigation_keyboard(show_back: bool = True, show_skip: bool = False) -> InlineKeyboardMarkup:
    first_row: list[InlineKeyboardButton] = []
    if show_back:
        first_row.append(_inline_button(BTN_BACK, CB_WIZARD_BACK))
    first_row.append(_inline_button(BTN_HOME, CB_HOME))
    rows = [first_row]
    if show_skip:
        rows.append([_inline_button(BTN_SKIP, CB_WIZARD_SKIP)])
    rows.append([_inline_button(BTN_CANCEL, CB_WIZARD_CANCEL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def condition_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [_inline_button(label, f"{CB_CONDITION_PREFIX}{value}")]
        for value, label in CONDITION_LABELS.items()
    ]
    rows.extend(wizard_navigation_keyboard().inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def priority_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [_inline_button(label, f"{CB_PRIORITY_PREFIX}{value}")]
        for value, label in PRIORITY_LABELS.items()
    ]
    rows.extend(wizard_navigation_keyboard().inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def request_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_inline_button(BTN_CONFIRM, CB_WIZARD_CONFIRM)],
            [_inline_button(BTN_EDIT, CB_WIZARD_EDIT)],
            [
                _inline_button(BTN_BACK, CB_WIZARD_BACK),
                _inline_button(BTN_HOME, CB_HOME),
            ],
            [_inline_button(BTN_CANCEL, CB_WIZARD_CANCEL)],
        ]
    )


def request_edit_keyboard() -> InlineKeyboardMarkup:
    fields = (
        ("Товар", "product"),
        ("Бюджет", "budget"),
        ("Город", "city"),
        ("Состояние", "condition"),
        ("Требования", "requirements"),
        ("Приоритет", "priority"),
    )
    rows = [
        [_inline_button(label, f"{CB_WIZARD_EDIT_PREFIX}{field}")]
        for label, field in fields
    ]
    rows.append([
        _inline_button(BTN_BACK, CB_WIZARD_BACK),
        _inline_button(BTN_HOME, CB_HOME),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def unsupported_category_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_inline_button(BTN_MANUAL_SELECTION, CB_WIZARD_MANUAL)],
        [_inline_button(BTN_COMPARE_LINKS, CB_COMPARE_LINKS)],
        [_inline_button(BTN_SUPPORT, CB_SUPPORT)],
        [_inline_button(BTN_HOME, CB_HOME)],
    ])


def link_comparison_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_inline_button(BTN_SEND_LINKS, CB_LINKS_CONFIRM)],
            [
                _inline_button(BTN_BACK, CB_WIZARD_BACK),
                _inline_button(BTN_HOME, CB_HOME),
            ],
            [_inline_button(BTN_CANCEL, CB_LINKS_CANCEL)],
        ]
    )


def feedback_keyboard(request_id: int) -> InlineKeyboardMarkup:
    request_id = int(request_id)
    if request_id <= 0:
        raise ValueError("request_id must be positive")
    rows = [
        [
            _inline_button(
                label,
                f"{CB_FEEDBACK_PREFIX}{request_id}:{rating}",
            )
        ]
        for rating, label in sorted(FEEDBACK_LABELS.items(), reverse=True)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def result_card_keyboard(result_id: int, url: str = "") -> InlineKeyboardMarkup:
    result_id = int(result_id)
    rows: list[list[InlineKeyboardButton]] = []
    try:
        parsed = urlsplit(str(url or "").strip())
    except ValueError:
        parsed = None
    if parsed and parsed.scheme in {"http", "https"} and parsed.netloc:
        rows.append([InlineKeyboardButton(text="🔗 Открыть товар", url=str(url).strip())])
    rows.append([
        _inline_button("⚖️ Сравнить", f"{CB_RESULT_COMPARE_PREFIX}{result_id}"),
        _inline_button("📋 Подробнее", f"{CB_RESULT_DETAIL_PREFIX}{result_id}"),
    ])
    rows.append([_inline_button("❤️ Сохранить", f"{CB_RESULT_SAVE_PREFIX}{result_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def callback_data_values(markup: InlineKeyboardMarkup) -> tuple[str, ...]:
    """Expose callbacks for deterministic validation and smoke tests."""

    return tuple(
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    )


__all__ = [
    name
    for name in globals()
    if name.startswith("CB_")
    or name
    in {
        "TELEGRAM_CALLBACK_DATA_LIMIT",
        "callback_data_values",
        "category_keyboard",
        "condition_keyboard",
        "ensure_callback_data",
        "feedback_keyboard",
        "link_comparison_keyboard",
        "main_menu_inline_keyboard",
        "main_menu_keyboard",
        "priority_keyboard",
        "request_confirmation_keyboard",
        "request_edit_keyboard",
        "result_card_keyboard",
        "service_packages_keyboard",
        "unsupported_category_keyboard",
        "wizard_navigation_keyboard",
    }
]
