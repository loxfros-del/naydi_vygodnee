"""Product-facing configuration independent from bot secrets and handlers.

The module intentionally does not import :mod:`app.config`: deterministic tests and
presentation code can use it without requiring ``BOT_TOKEN`` or touching ``.env``.
Runtime values are read from an injected mapping or directly from ``os.environ``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import os
from pathlib import Path
import re
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WELCOME_IMAGE_PATH = Path("assets") / "welcome.png"
PRICE_UNSET_LABEL = "Стоимость уточняется"


class SearchMode(str, Enum):
    """Supported product entry modes."""

    AUTO = "AUTO"
    MANUAL = "MANUAL"
    LINK_COMPARISON = "LINK_COMPARISON"


@dataclass(frozen=True, slots=True)
class ServicePackage:
    code: str
    title: str
    description: str
    features: tuple[str, ...]
    price_rub: int | None = None
    available: bool = True

    @property
    def price_label(self) -> str:
        if self.price_rub is None:
            return PRICE_UNSET_LABEL
        if self.price_rub == 0:
            return "Бесплатно"
        return f"{self.price_rub:,} ₽".replace(",", " ")

    @property
    def availability_label(self) -> str:
        return "Доступно" if self.available else "Временно недоступно"


QUICK_SELECTION = ServicePackage(
    code="QUICK_SELECTION",
    title="Быстрый подбор",
    description="Короткий поиск и до трёх понятных вариантов.",
    features=(
        "сравнение цен",
        "проверка основных рисков",
        "короткий итоговый отчёт",
    ),
)

DEEP_SELECTION = ServicePackage(
    code="DEEP_SELECTION",
    title="Глубокий подбор",
    description="Расширенная проверка товара, продавцов и рынка.",
    features=(
        "сравнение моделей и комплектаций",
        "проверка продавцов и рыночной цены",
        "подробные риски и ручная проверка",
    ),
)

LINK_COMPARISON = ServicePackage(
    code="LINK_COMPARISON",
    title="Сравнение ссылок",
    description="Сравним от 2 до 10 выбранных вами предложений.",
    features=(
        "проверка модели и характеристик",
        "сравнение цен и продавцов",
        "выбор наиболее удачного варианта",
    ),
)

SERVICE_PACKAGE_TEMPLATES: tuple[ServicePackage, ...] = (
    QUICK_SELECTION,
    DEEP_SELECTION,
    LINK_COMPARISON,
)


@dataclass(frozen=True, slots=True)
class CategoryConfig:
    code: str
    title: str
    emoji: str
    aliases: tuple[str, ...]


# Keep this list deliberately small: these are the six categories whose automatic
# flow the product currently promises to users.
AUTO_CATEGORIES: tuple[CategoryConfig, ...] = (
    CategoryConfig(
        "smartphones",
        "Смартфоны",
        "📱",
        ("смартфон", "смартфоны", "телефон", "iphone", "айфон", "android phone"),
    ),
    CategoryConfig(
        "laptops",
        "Ноутбуки",
        "💻",
        ("ноутбук", "ноутбуки", "ультрабук", "ultrabook", "macbook", "макбук"),
    ),
    CategoryConfig(
        "televisions",
        "Телевизоры",
        "📺",
        ("телевизор", "телевизоры", "тв", "smart tv", "tv"),
    ),
    CategoryConfig(
        "headphones",
        "Наушники",
        "🎧",
        ("наушники", "гарнитура", "tws", "earbuds", "полноразмерные наушники"),
    ),
    CategoryConfig(
        "monitors",
        "Мониторы",
        "🖥",
        ("монитор", "мониторы", "дисплей для компьютера"),
    ),
    CategoryConfig(
        "office_chairs",
        "Офисные кресла",
        "🪑",
        ("офисное кресло", "компьютерное кресло", "кресло для компьютера", "кресло"),
    ),
)

SUPPORTED_AUTO_SEARCH = frozenset(category.code for category in AUTO_CATEGORIES)
AUTO_CATEGORY_BY_CODE = {category.code: category for category in AUTO_CATEGORIES}
LEGACY_AUTO_CATEGORY_CODES = {
    "phone": "smartphones",
    "laptop": "laptops",
    "tv": "televisions",
    "headphones": "headphones",
    "monitor": "monitors",
    "chair": "office_chairs",
}

_EXPLICIT_UNSUPPORTED_ALIASES = (
    "автомобиль",
    "машина",
    "автозапчасть",
    "автозапчасти",
    "запчасть",
    "запчасти",
    "двигатель",
    "шины",
)
_URL_RE = re.compile(r"https?://[^\s<>\[\]{}]+", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CategoryDecision:
    mode: SearchMode
    category: CategoryConfig | None
    reason: str
    urls: tuple[str, ...] = ()

    @property
    def is_supported(self) -> bool:
        return self.mode is SearchMode.AUTO and self.category is not None


@dataclass(frozen=True, slots=True)
class WelcomeImageSource:
    """Resolved welcome image that a Telegram adapter can send."""

    kind: str  # ``file_id`` or ``local_path``
    value: str

    @property
    def is_file_id(self) -> bool:
        return self.kind == "file_id"

    @property
    def is_local_path(self) -> bool:
        return self.kind == "local_path"


def _optional_price(value: object) -> int | None:
    text = str(value or "").strip().replace(" ", "")
    if not text:
        return None
    try:
        price = int(text)
    except (TypeError, ValueError):
        return None
    return price if price >= 0 else None


def _env_bool(value: object, default: bool = True) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def get_service_packages(env: Mapping[str, object] | None = None) -> tuple[ServicePackage, ...]:
    """Return all package definitions with optional environment overrides.

    Accepted price variables are ``<CODE>_PRICE_RUB`` and the shorter
    ``<CODE>_PRICE``. Availability uses ``<CODE>_ENABLED`` and defaults to true.
    Missing or invalid prices stay unset; the UI must never invent a number.
    """

    values = os.environ if env is None else env
    packages: list[ServicePackage] = []
    for template in SERVICE_PACKAGE_TEMPLATES:
        raw_price = values.get(f"{template.code}_PRICE_RUB")
        if raw_price is None:
            raw_price = values.get(f"{template.code}_PRICE")
        packages.append(
            replace(
                template,
                price_rub=_optional_price(raw_price),
                available=_env_bool(values.get(f"{template.code}_ENABLED"), True),
            )
        )
    return tuple(packages)


def get_service_package(
    code: str,
    env: Mapping[str, object] | None = None,
) -> ServicePackage | None:
    normalized = str(code or "").strip().upper()
    return next((item for item in get_service_packages(env) if item.code == normalized), None)


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def _alias_position(text: str, alias: str) -> int | None:
    normalized_alias = _normalized_text(alias)
    pattern = r"(?<![\w])" + re.escape(normalized_alias).replace(r"\ ", r"\s+") + r"(?![\w])"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.start() if match else None


def detect_auto_category(text: object) -> CategoryConfig | None:
    """Detect one of the six supported categories in free text."""

    normalized = _normalized_text(text)
    matches: list[tuple[int, int, CategoryConfig]] = []
    for category in AUTO_CATEGORIES:
        for alias in category.aliases:
            position = _alias_position(normalized, alias)
            if position is not None:
                matches.append((position, -len(alias), category))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1]))
    return matches[0][2]


def is_supported_auto_category(value: object) -> bool:
    normalized = _normalized_text(value).replace(" ", "_")
    if normalized in SUPPORTED_AUTO_SEARCH or normalized in LEGACY_AUTO_CATEGORY_CODES:
        return True
    return detect_auto_category(value) is not None


def _extract_urls(text: object) -> tuple[str, ...]:
    urls: list[str] = []
    for match in _URL_RE.findall(str(text or "")):
        cleaned = match.rstrip(".,;:!?)]}'\"")
        if cleaned and cleaned not in urls:
            urls.append(cleaned)
    return tuple(urls)


def classify_request(text: object) -> CategoryDecision:
    """Choose automatic, manual or link-comparison entry without network calls."""

    urls = _extract_urls(text)
    if len(urls) >= 2:
        return CategoryDecision(
            mode=SearchMode.LINK_COMPARISON,
            category=None,
            reason="Найдены ссылки для сравнения.",
            urls=urls[:10],
        )

    category = detect_auto_category(text)
    if category:
        return CategoryDecision(
            mode=SearchMode.AUTO,
            category=category,
            reason="Категория поддерживает автоматический подбор.",
            urls=urls,
        )

    normalized = _normalized_text(text)
    explicit = any(_alias_position(normalized, alias) is not None for alias in _EXPLICIT_UNSUPPORTED_ALIASES)
    reason = (
        "Эта категория доступна только для ручного подбора."
        if explicit
        else "Категория не распознана; нужна ручная проверка."
    )
    return CategoryDecision(
        mode=SearchMode.MANUAL,
        category=None,
        reason=reason,
        urls=urls,
    )


def resolve_welcome_image_path(
    raw_path: object,
    base_dir: str | Path | None = None,
) -> Path | None:
    """Resolve an existing local welcome image, including relative paths."""

    value = str(raw_path or "").strip()
    if not value:
        return None
    expanded = Path(os.path.expandvars(value)).expanduser()
    if not expanded.is_absolute():
        expanded = Path(base_dir or PROJECT_ROOT) / expanded
    try:
        resolved = expanded.resolve()
        return resolved if resolved.is_file() else None
    except OSError:
        return None


def resolve_welcome_image(
    env: Mapping[str, object] | None = None,
    base_dir: str | Path | None = None,
) -> WelcomeImageSource | None:
    """Prefer Telegram/configured images, then the bundled welcome asset."""

    values = os.environ if env is None else env
    file_id = str(values.get("WELCOME_IMAGE_FILE_ID") or "").strip()
    if file_id:
        return WelcomeImageSource("file_id", file_id)

    raw_path = (
        values.get("WELCOME_IMAGE_PATH")
        or values.get("WELCOME_IMAGE_LOCAL_PATH")
        or values.get("WELCOME_IMAGE_FILE")
    )
    local_path = resolve_welcome_image_path(raw_path, base_dir=base_dir)
    if local_path:
        return WelcomeImageSource("local_path", str(local_path))
    bundled_path = resolve_welcome_image_path(
        DEFAULT_WELCOME_IMAGE_PATH,
        base_dir=base_dir,
    )
    if bundled_path:
        return WelcomeImageSource("local_path", str(bundled_path))
    return None


WELCOME_IMAGE_FILE_ID = os.getenv("WELCOME_IMAGE_FILE_ID", "").strip()
WELCOME_IMAGE_PATH = os.getenv("WELCOME_IMAGE_PATH", "").strip()
SERVICE_PACKAGES = get_service_packages()


__all__ = [
    "AUTO_CATEGORIES",
    "AUTO_CATEGORY_BY_CODE",
    "CategoryConfig",
    "CategoryDecision",
    "DEFAULT_WELCOME_IMAGE_PATH",
    "DEEP_SELECTION",
    "LINK_COMPARISON",
    "PRICE_UNSET_LABEL",
    "PROJECT_ROOT",
    "QUICK_SELECTION",
    "SERVICE_PACKAGES",
    "SERVICE_PACKAGE_TEMPLATES",
    "SUPPORTED_AUTO_SEARCH",
    "SearchMode",
    "ServicePackage",
    "WELCOME_IMAGE_FILE_ID",
    "WELCOME_IMAGE_PATH",
    "WelcomeImageSource",
    "classify_request",
    "detect_auto_category",
    "get_service_package",
    "get_service_packages",
    "is_supported_auto_category",
    "resolve_welcome_image",
    "resolve_welcome_image_path",
]
