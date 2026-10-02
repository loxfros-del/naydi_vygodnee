"""Deterministic fail-closed checks run before multimodal AI review."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

from .models import NormalizedListing, RiskFinding, Severity


_LATIN_LOOKALIKES = str.maketrans({
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х",
    "y": "у", "k": "к", "m": "м", "t": "т", "b": "в", "h": "н",
})


def _is_direct_listing_url(value: str, listing_id: str) -> bool:
    parsed = urlparse(value or "")
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (host == "avito.ru" or host.endswith(".avito.ru")):
        return False
    if listing_id.isdigit():
        return bool(re.search(
            rf"(?:^|[_/-]){re.escape(listing_id)}(?:$|[_/-])",
            parsed.path,
        ))
    return False


def _search_text(value: str) -> str:
    raw = unicodedata.normalize("NFKC", value or "").casefold()
    return raw.translate(_LATIN_LOOKALIKES)


def _evidence(text: str, match: re.Match[str], radius: int = 100) -> str:
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return " ".join(text[start:end].split())


def _find(pattern: str, text: str) -> re.Match[str] | None:
    return re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)


def _positive_activation(text: str) -> re.Match[str] | None:
    """Find activation claims while excluding `неактивирован` with optional spacing."""
    for match in re.finditer(r"активирован[а-я]*", text, flags=re.IGNORECASE):
        prefix = text[max(0, match.start() - 8):match.start()]
        if re.search(r"не\s*$", prefix, flags=re.IGNORECASE):
            continue
        return match
    return None


def _affirmed_defect(pattern: str, text: str) -> re.Match[str] | None:
    """Ignore local, explicit denials without hiding a later positive defect.

    Negation never crosses a sentence or an adversative conjunction. A list
    after «без» may contain defect nouns only; «без трещин, камера не работает»
    must still reject the camera failure.
    """
    defect_noun = r"(?:трещин[а-я]*|скол[а-я]*|царапин[а-я]*|дефект[а-я]*|неисправност[а-я]*)"
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        prefix = re.split(r"[.!?;\n]|\b(?:но|однако|зато)\b", text[:match.start()])[-1]
        suffix = text[match.end():]
        # «не разбитый», «нет трещин», «никаких неисправностей нет».
        if re.search(r"\b(?:не|без|нет|никаких|никакой)\s*$", prefix) and not re.search(r"\bне\s+без\s*$", prefix):
            continue
        if re.fullmatch(defect_noun, match.group()) and re.search(
            rf"\b(?:без|нет|никаких)\s+(?:{defect_noun}\s*(?:,|и|или)\s*)+$", prefix
        ):
            continue
        if re.fullmatch(defect_noun, match.group()) and re.match(
            rf"(?:\s*(?:,|и|или)\s*{defect_noun})+\s+(?:нет|отсутству[а-я]*)\b", suffix
        ):
            continue
        if re.match(r"\s*(?:нет|не\s+(?:обнаружен[а-я]*|выявлен[а-я]*)|отсутству[а-я]*)\b", suffix):
            continue
        return match
    return None


def _nand_failure(text: str) -> re.Match[str] | None:
    for sentence in re.finditer(r"[^.!?;,\n]+", text):
        if not re.search(r"\b(?:nand|нанд)\b|чип[а-я ]{0,12}памят", sentence.group()):
            continue
        failure = _affirmed_defect(
            r"(?:неисправ(?:ен|на|но|ны)|неисправност[а-я]*|ошибк[а-я]*|"
            r"выш[а-я ]{0,10}из\s+строя|выход\s+из\s+строя)", sentence.group(),
        )
        if failure:
            # Return a span in the original text for the customer evidence quote.
            return re.compile(re.escape(failure.group())).search(text, sentence.start() + failure.start())
    return None


def evaluate_rules(listing: NormalizedListing) -> tuple[RiskFinding, ...]:
    description = _search_text(listing.description)
    raw = unicodedata.normalize("NFKC", listing.description or "").casefold()
    findings: list[RiskFinding] = []

    def add(code: str, severity: Severity, message: str, match: re.Match[str] | None = None) -> None:
        if any(item.code == code for item in findings):
            return
        findings.append(RiskFinding(code, severity, message, _evidence(description, match) if match else ""))

    status = listing.status.casefold().strip()
    if not status or status in {"unknown", "неизвестно", "unconfirmed"}:
        add("LISTING_STATUS_UNCONFIRMED", Severity.CRITICAL, "Активность объявления не подтверждена.")
    elif status != "active":
        add("LISTING_INACTIVE", Severity.CRITICAL, "Объявление не отмечено активным.")
    if not listing.url or not _is_direct_listing_url(listing.url, listing.listing_id):
        add("INVALID_DIRECT_URL", Severity.WARNING, "Нет безопасной прямой ссылки Avito.")
    if not listing.description:
        add("MISSING_DESCRIPTION", Severity.WARNING, "Полное описание отсутствует: рекомендация запрещена.")
    if not listing.images:
        add("MISSING_PHOTOS", Severity.WARNING, "Фотографии отсутствуют: рекомендация запрещена.")

    nand = _nand_failure(raw)
    if nand:
        add("NAND_DEFECT", Severity.CRITICAL, "В описании указан дефект памяти NAND.", nand)

    hardware = _affirmed_defect(
        r"(?:не\s*работа(?:ет|ют)|не\s*включа(?:ется|ют)|выход\s+из\s+строя|"
        r"разбит[а-я]*|трещин[а-я]*|на\s+запчасти|неисправност[а-я]*|неисправ(?:ен|на|но|ны)|"
        r"отклонени[яй]\s+в\s+работе\s+(?:цепи\s+заряда|контроллера\s+питания)|"
        r"дефект\s+(?:экрана|камеры|платы|аккумулятора|корпуса))",
        description,
    )
    if hardware:
        add("HARDWARE_DEFECT", Severity.CRITICAL, "В описании есть явный технический дефект.", hardware)

    variable_price = _find(
        r"(?:итогов[а-я ]{0,12}цен[а-я ]{0,20}(?:индивидуаль|уточня)|"
        r"цена\s+(?:указана\s+)?(?:при|только\s+при)\s+(?:обмене|трейд|сдаче)|"
        r"цена\s+(?:\d[\d\s]*(?:₽|руб\.?|рублей)?\s*)?(?:указана\s+)?(?:только\s+)?"
        r"при\s+оформлени[а-я]*\s+(?:кредит[а-я]*|рассрочк[а-я]*)|"
        r"цена\s+[^.!?;\n]{0,260}и\s+потому\s+считается\s+индивидуаль[а-я]*|"
        r"стоимост[а-я ]{0,30}рассчитыва[а-я ]{0,15}индивидуаль)",
        description,
    )
    if variable_price:
        add("NON_FINAL_PRICE", Severity.WARNING, "Цена может зависеть от дополнительных условий.", variable_price)

    surcharge = _find(r"(?:комисси[а-я ]{0,8}|\+\s*)\d{1,2}\s*%", description)
    if surcharge:
        add("PAYMENT_SURCHARGE", Severity.WARNING, "Для части способов оплаты указана комиссия.", surcharge)

    if "trade-in" in raw or "трейд-ин" in description or "в зачёт" in description or "в зачет" in description:
        add("TRADE_IN_MENTIONED", Severity.INFO, "В объявлении упоминается Trade-in; базовую цену нужно подтвердить.")

    if "PRICE_VARIANT_MISMATCH" in listing.price_truth.conflicts:
        add(
            "PRICE_VARIANT_MISMATCH",
            Severity.WARNING,
            "Цена карточки не подтверждает цену заявленного варианта товара.",
        )

    short_warranty = _find(r"гаранти[а-я ]{0,10}(?:30|45|60|90)\s*(?:дн|дней|дня)", description)
    if short_warranty:
        add("SHORT_WARRANTY", Severity.WARNING, "Гарантия продавца короче года.", short_warranty)

    structured_activation = _search_text(listing.activation)
    says_activated = _positive_activation(description)
    if "неактивирован" in structured_activation and says_activated:
        add(
            "ACTIVATION_CONFLICT",
            Severity.WARNING,
            "Характеристики говорят «неактивированный», а описание сообщает об активации.",
            says_activated,
        )
    if (
        "нов" in _search_text(listing.condition)
        and "активирован" in structured_activation
        and "неактивирован" not in structured_activation
    ):
        add("ACTIVATED_AS_NEW", Severity.WARNING, "Товар заявлен новым, но уже активирован.")

    used_or_repaired = _find(
        r"(?:(?:данн[а-я]*\s+)?(?:телефон|аппарат|устройство|товар)[а-я ]{0,35}"
        r"(?:б\s*/\s*у|бывш[а-я ]{0,12}эксплуатац|после\s+ремонта|восстановлен[а-я]*|рефаб)|"
        r"(?:восстановленн[а-я]*|рефаб)\s+(?:телефон|аппарат|устройство|товар))",
        description,
    )
    if "нов" in _search_text(listing.condition) and used_or_repaired:
        add("CONDITION_CONFLICT", Severity.CRITICAL, "Описание противоречит заявленному новому состоянию.", used_or_repaired)
    elif listing.price_truth.condition.startswith("conflict:new_vs_"):
        add("CONDITION_CONFLICT", Severity.CRITICAL, "Описание противоречит заявленному новому состоянию.")

    if listing.image_count_claimed > len(listing.images):
        add("PHOTO_SET_INCOMPLETE", Severity.WARNING, "Получены не все фотографии, заявленные в карточке.")
    return tuple(findings)
