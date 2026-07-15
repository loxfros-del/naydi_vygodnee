"""Подготовка 2–10 пользовательских ссылок к сравнению без сетевых вызовов."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app import db


MIN_COMPARISON_LINKS = 2
MAX_COMPARISON_LINKS = 10

_TRACKING_QUERY_KEYS = {
    "yclid", "gclid", "fbclid", "from", "ref", "referrer", "source",
}
_SOURCE_DOMAINS = (
    ("market.yandex.ru", "Яндекс Маркет"),
    ("dns-shop.ru", "DNS"),
    ("wildberries.ru", "Wildberries"),
    ("megamarket.ru", "Мегамаркет"),
    ("onlinetrade.ru", "ОнлайнТрейд"),
    ("citilink.ru", "Ситилинк"),
    ("mvideo.ru", "М.Видео"),
    ("ozon.ru", "Ozon"),
    ("avito.ru", "Авито"),
)


class ComparisonValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedComparisonLink:
    url: str
    normalized_url: str
    source: str
    title: str = ""
    model: str = ""
    price: int | None = None
    availability: str = ""
    seller: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    risks: list[str] = field(default_factory=list)
    status: str = "PENDING"
    is_blocked: bool = False
    manual_check_required: bool = False
    manual_note: str = ""
    sort_order: int = 0

    def to_db_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["facts_json"] = value.pop("facts")
        value["risk_flags"] = value.pop("risks")
        return value


def normalize_url(url: str) -> str:
    """Проверяет URL и удаляет fragment/типовые tracking-параметры."""
    raw = str(url or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError as exc:
        raise ComparisonValidationError(f"Некорректная ссылка: {raw!r}") from exc
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ComparisonValidationError("Нужна полная ссылка http:// или https://")
    if parsed.username or parsed.password:
        raise ComparisonValidationError("Ссылки с логином или паролем не поддерживаются")

    host = parsed.hostname.lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    try:
        port = parsed.port
    except ValueError as exc:
        raise ComparisonValidationError("Некорректный порт в ссылке") from exc
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        query_items.append((key, value))
    query = urlencode(sorted(query_items))
    return urlunsplit((scheme, netloc, path, query, ""))


def recognize_source(url: str) -> str:
    normalized = normalize_url(url)
    host = urlsplit(normalized).hostname or ""
    for domain, display_name in _SOURCE_DOMAINS:
        if host == domain or host.endswith(f".{domain}"):
            return display_name
    return "Другой магазин"


def validate_comparison_urls(
    urls: Iterable[str],
    *,
    min_links: int = MIN_COMPARISON_LINKS,
    max_links: int = MAX_COMPARISON_LINKS,
) -> list[str]:
    values = [urls] if isinstance(urls, str) else list(urls)
    if len(values) > max_links:
        raise ComparisonValidationError(f"Можно сравнить не больше {max_links} ссылок")

    normalized: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values, 1):
        try:
            clean = normalize_url(value)
        except ComparisonValidationError as exc:
            raise ComparisonValidationError(f"Ссылка #{index}: {exc}") from exc
        if clean in seen:
            continue
        seen.add(clean)
        normalized.append(clean)

    if len(normalized) < min_links:
        raise ComparisonValidationError(
            f"Нужно от {min_links} до {max_links} разных ссылок"
        )
    return normalized


def _normalise_fact_mapping(
    values: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for url, facts in (values or {}).items():
        try:
            result[normalize_url(url)] = facts
        except ComparisonValidationError:
            continue
    return result


def _merge_facts(
    base: Mapping[str, Any],
    manual: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(base.get("facts") or {})
    result.update(dict(manual.get("facts") or {}))
    return result


def _risk_list(*values) -> list[str]:
    result: list[str] = []
    for value in values:
        items = [value] if isinstance(value, str) else list(value or [])
        for item in items:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
    return result


def prepare_comparison_links(
    urls: Iterable[str],
    *,
    extracted_facts: Mapping[str, Mapping[str, Any]] | None = None,
    blocked_urls: Iterable[str] | None = None,
    manual_facts: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[PreparedComparisonLink]:
    """Объединяет только переданные structured/manual facts, ничего не выдумывая."""
    normalized_urls = validate_comparison_urls(urls)
    extracted_by_url = _normalise_fact_mapping(extracted_facts)
    manual_by_url = _normalise_fact_mapping(manual_facts)
    blocked: set[str] = set()
    for value in blocked_urls or ():
        try:
            blocked.add(normalize_url(value))
        except ComparisonValidationError:
            continue

    result: list[PreparedComparisonLink] = []
    for index, url in enumerate(normalized_urls, 1):
        extracted = dict(extracted_by_url.get(url) or {})
        manual = dict(manual_by_url.get(url) or {})
        is_blocked = bool(extracted.get("blocked")) or url in blocked
        has_extracted = any(
            extracted.get(key) not in (None, "", {}, [])
            for key in ("title", "model", "price", "availability", "seller", "facts")
        )
        has_manual = any(
            manual.get(key) not in (None, "", {}, [])
            for key in ("title", "model", "price", "availability", "seller", "facts")
        )

        def chosen(key: str, default=""):
            value = manual.get(key)
            return value if value not in (None, "") else extracted.get(key, default)

        risks = _risk_list(extracted.get("risks"), manual.get("risks"))
        if is_blocked and not has_manual:
            risks = _risk_list(risks, "страница недоступна для автоматической проверки")
            status = "NEEDS_MANUAL_CHECK"
            manual_required = True
        elif has_manual:
            status = "MANUAL_CHECKED"
            manual_required = False
        elif has_extracted:
            status = "EXTRACTED"
            manual_required = False
        else:
            status = "PENDING"
            manual_required = False

        result.append(PreparedComparisonLink(
            url=url,
            normalized_url=url,
            source=recognize_source(url),
            title=str(chosen("title") or "").strip(),
            model=str(chosen("model") or "").strip(),
            price=db.to_int_price(chosen("price", None)),
            availability=str(chosen("availability") or "").strip(),
            seller=str(chosen("seller") or "").strip(),
            facts=_merge_facts(extracted, manual),
            risks=risks,
            status=status,
            is_blocked=is_blocked,
            manual_check_required=manual_required,
            manual_note=str(manual.get("manual_note") or extracted.get("manual_note") or "").strip(),
            sort_order=index,
        ))
    return result


def save_comparison_links(
    *,
    user_id: int,
    request_id: int,
    items: Iterable[PreparedComparisonLink | Mapping[str, Any]],
) -> list[db.ComparisonLink]:
    payload: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, PreparedComparisonLink):
            payload.append(item.to_db_dict())
        else:
            payload.append(dict(item))
    if not MIN_COMPARISON_LINKS <= len(payload) <= MAX_COMPARISON_LINKS:
        raise ComparisonValidationError(
            f"Нужно от {MIN_COMPARISON_LINKS} до {MAX_COMPARISON_LINKS} ссылок"
        )
    return db.replace_comparison_links(
        user_id=user_id,
        request_id=request_id,
        items=payload,
    )
