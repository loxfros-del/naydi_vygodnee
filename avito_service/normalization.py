"""Convert verbose Zen Studio records into listing-specific evidence."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import math
import re
import unicodedata
from typing import Any
from urllib.parse import unquote, urlparse

from .errors import InvalidDatasetError
from .models import NormalizedListing, SellerSummary


_INVISIBLE = str.maketrans({"\u200b": "", "\u200c": "", "\u200d": "", "\u2060": "", "\ufeff": ""})


def _value(raw: Mapping[str, Any], *names: str) -> Any:
    """Read actor fields tolerantly because Zen has used several ID/URL spellings."""
    for name in names:
        if name in raw and raw[name] not in (None, ""):
            return raw[name]
    folded = {str(key).casefold(): value for key, value in raw.items()}
    for name in names:
        value = folded.get(name.casefold())
        if value not in (None, ""):
            return value
    return None


def _text(value: Any, *, limit: int | None = None) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_INVISIBLE).strip()
    return text if limit is None else text[:limit]


def _integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(" ", "").replace("\u00a0", "")))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _safe_listing_url(value: Any) -> str:
    url = _text(value, limit=1000)
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return url if parsed.scheme == "https" and (host == "avito.ru" or host.endswith(".avito.ru")) else ""


def _listing_id(raw: Mapping[str, Any], listing_url: str) -> str:
    direct = _text(
        _value(
            raw,
            "id",
            "avitoid",
            "avitoId",
            "avito_id",
            "itemId",
            "item_id",
            "listingId",
            "listing_id",
            "adId",
            "ad_id",
        ),
        limit=80,
    )
    if direct:
        return direct
    if not listing_url:
        return ""
    path = urlparse(listing_url).path
    matches = re.findall(r"(?:^|[_/-])(\d{6,20})(?=$|[_/?#-])", path)
    if matches:
        return matches[-1]
    # Avito category and search pages are also valid avito.ru URLs. Without a
    # numeric item id we cannot prove that this is a direct orderable listing.
    return ""


def _safe_image_url(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("url") or value.get("src") or value.get("image")
    url = _text(value, limit=2000)
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    return url if parsed.scheme == "https" and host.endswith(".avito.st") else ""


def _images(raw: Mapping[str, Any]) -> tuple[str, ...]:
    values = raw.get("images") or raw.get("photoUrls") or []
    if not isinstance(values, list):
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        url = _safe_image_url(value)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return tuple(result)


def _parameters(raw: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, int]]:
    values = raw.get("parameters") or raw.get("attributes") or []
    if isinstance(values, Mapping):
        return ({_text(k, limit=120): _text(v, limit=500) for k, v in values.items() if _text(k)}, {})
    if not isinstance(values, list):
        return {}, {}
    parameters: dict[str, str] = {}
    identifiers: dict[str, int] = {}
    for item in values:
        if not isinstance(item, Mapping):
            continue
        name = _text(item.get("name"), limit=120)
        value = _text(item.get("value"), limit=500)
        if not name or not value:
            continue
        parameters[name] = value
        attribute_id = _integer(item.get("attributeId"))
        if attribute_id is not None:
            identifiers[name] = attribute_id
    return parameters, identifiers


def _badges(raw: Mapping[str, Any]) -> tuple[str, ...]:
    values = raw.get("badges") or []
    if not isinstance(values, list):
        return ()
    result: list[str] = []
    for item in values:
        title = _text(item.get("title") if isinstance(item, Mapping) else item, limit=160)
        if title and title not in result:
            result.append(title)
    return tuple(result)


def _seller(raw: Mapping[str, Any]) -> SellerSummary:
    value = raw.get("seller")
    seller = value if isinstance(value, Mapping) else {}
    identity = _value(seller, "id", "sellerId", "userId", "seller_id", "user_id", "userKey", "profileId")
    if identity is None:
        identity = _value(raw, "sellerId", "userId", "seller_id", "user_id")
    identity_text = _text(identity) if isinstance(identity, (str, int)) and not isinstance(identity, bool) else ""
    identity_source = f"id:{identity_text}" if identity_text else ""
    identity_kind = "seller_id" if identity_text else ""
    profile = _safe_listing_url(_value(seller, "url", "profileUrl", "sellerUrl", "profile_url"))
    if not profile:
        profile = _safe_listing_url(_value(raw, "sellerUrl", "sellerProfileUrl"))
    if not identity_source:
        if profile:
            parsed = urlparse(profile)
            if parsed.path.strip("/"):
                identity_source = f"profile:avito.ru{parsed.path.rstrip('/')}"
                identity_kind = "profile_url"
    identity_hash = hashlib.sha256(f"avito-seller-v1:{identity_source}".encode("utf-8")).hexdigest() if identity_source else ""
    return SellerSummary(
        name=_text(seller.get("name"), limit=160),
        seller_type=_text(seller.get("sellerType") or raw.get("userType"), limit=40),
        rating=_float(seller.get("ratingScore")),
        review_count=_integer(seller.get("reviewCount")),
        member_since=_text(seller.get("memberSince"), limit=120),
        is_shop=bool(seller.get("isShop")),
        identity_hash=identity_hash,
        identity_kind=identity_kind,
        profile_url=profile,
    )


def _address(raw: Mapping[str, Any], url: str) -> str:
    value = _value(raw, "address", "location", "city", "geo")
    if isinstance(value, Mapping):
        value = _value(value, "city", "name", "title", "region")
        if isinstance(value, Mapping):
            value = _value(value, "name", "title")
    result = _text(value, limit=160)
    if result:
        return result
    # The first direct-listing path component is the published locality.
    parts = urlparse(url).path.strip("/").split("/")
    return parts[0] if len(parts) >= 3 else ""


def _city_key(value: str) -> str:
    """Unify a structured Russian city with its Avito URL locality slug."""
    alphabet = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", (
        "a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n",
        "o", "p", "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya",
    )))
    value = unquote(value).casefold().strip()
    value = re.sub(r"^(?:г\.?|город)\s+", "", value)
    return re.sub(r"[^a-z0-9]+", "", "".join(alphabet.get(character, character) for character in value))


def _location(raw: Mapping[str, Any], url: str) -> str:
    # An explicitly named city is more precise than an address string. Never
    # compare street addresses as localities or assume the first comma segment
    # is a city (it can be a region).
    city = _value(raw, "city")
    if not city:
        for name in ("location", "geo", "address"):
            value = raw.get(name)
            if isinstance(value, Mapping):
                city = _value(value, "city")
                if city:
                    break
    if isinstance(city, Mapping):
        city = _value(city, "name", "title")
    if city and isinstance(city, str):
        return _city_key(city)
    # Some datasets already put a plain city in location, without address parts.
    value = raw.get("location")
    if isinstance(value, str) and not re.search(r"[,\d]|\b(?:ул\.?|улица|проспект|обл\.?|область)\b", value.casefold()):
        return _city_key(value)
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] not in {"rossiya", "all"}:
        return _city_key(parts[0])
    return ""


def _delivery(raw: Mapping[str, Any], parameters: dict[str, str]) -> str:
    value = _value(raw, "delivery", "deliveryAvailable", "delivery_enabled", "isDeliveryAvailable")
    if isinstance(value, Mapping):
        value = _value(value, "available", "enabled", "isAvailable", "status")
    if value is True:
        return "available"
    if value is False:
        return "unavailable"
    text = _text(value or parameters.get("Доставка")).casefold()
    if text in {"true", "yes", "да", "available", "доступна", "авито доставка", "есть"}:
        return "available"
    if text in {"false", "no", "нет", "unavailable", "недоступна", "самовывоз", "только самовывоз"}:
        return "unavailable"
    return ""


def _fold_kit_text(value: str) -> str:
    lookalikes = str.maketrans({"a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х",
                               "y": "у", "k": "к", "m": "м", "t": "т", "b": "в", "h": "н"})
    return unicodedata.normalize("NFKC", value).casefold().replace("ё", "е").translate(lookalikes)


def canonical_kit_evidence(value: str) -> str:
    """Canonicalize only fully understood component lists; retain opaque text."""
    original = re.sub(r"\s+", " ", value.casefold().replace("ё", "е")).strip()
    if original in {"", "full", "partial", "device_only", "unknown", "неизвестно", "не указано"}:
        return original
    prefix = "описанный комплект:"
    if not original.startswith(prefix):
        return "literal:" + original
    # No free-form qualifier is discarded: an unparsed piece preserves the
    # complete original list, including additional devices or subscriptions.
    pieces = re.split(r"\s*[,;/]\s*", _fold_kit_text(value[len(prefix):]))
    components = []
    for piece in pieces:
        piece = re.sub(r"\s+", " ", piece).strip(" .")
        if not piece:
            continue
        aliases = {
            "консоль": "console", "сама консоль": "console", "приставка": "console",
            "сама приставка": "console", "игровая приставка": "console", "тушка": "console",
            "коробка": "box", "документы": "documents", "документация": "documents",
            "подставка": "stand", "наушники": "headphones",
            "кабель питания": "power_cable", "провод питания": "power_cable",
            "кабель hdmi": "hdmi_cable", "hdmi-кабель": "hdmi_cable", "провод hdmi": "hdmi_cable",
            "кабель зарядки геймпада": "controller_charge_cable",
            "зарядный кабель для геймпада": "controller_charge_cable",
            "кабель для зарядки геймпада": "controller_charge_cable",
            "док станция для зарядки геймпадов": "controller_charging_dock",
            "док-станция для зарядки геймпадов": "controller_charging_dock",
            "станция зарядки геймпадов": "controller_charging_dock",
        }
        known = {_fold_kit_text(key): item for key, item in aliases.items()}.get(piece)
        if known:
            components.append(known)
            continue
        controller = re.fullmatch(
            r"(?:(один|одна|два|две|три|\d+)\s+)?"
            r"(геймпад|джойстик|контроллер)(а|ов|ы)?(?:\s+(\d+)\s*шт)?", piece,
        )
        if controller:
            count, _, plural, suffix_count = controller.groups()
            numbers = {"один": "1", "одна": "1", "два": "2", "две": "2", "три": "3"}
            count = numbers.get(count, count) or suffix_count or "unknown"
            if suffix_count and suffix_count != count:
                return "literal:" + original
            components.append("controller:" + count)
            continue
        cables = re.fullmatch(r"(?:(\d+)\s+)?(?:провод|провода|проводов|кабель|кабеля|кабели|кабелей)", piece)
        if cables:
            # Generic wiring never becomes HDMI + power + USB by assumption.
            components.append("unspecified_cables:" + (cables.group(1) or "unknown"))
            continue
        return "literal:" + original
    return "components:" + "|".join(sorted(components)) if components else "literal:" + original


def _kit_evidence(kit_text: str, description: str) -> str:
    """Keep literal kit statements; lists are described kits, not proven full sets."""
    folded = _fold_kit_text
    optional = re.compile(r"за\s+(?:доплату|(?:отдельную|дополнительную)\s+плату)|доплат[а-я]*|опциональн[а-я]*|"
                          r"можно\s+докуп[а-я]*|продается\s+отдельно|не\s+вход[а-я]*")
    unknown = re.compile(r"уточня[а-я]*|неизвест[а-я]*|не\s+указан[а-я]*|нет\s+данных|по\s+запросу")
    incomplete = re.compile(r"неполный\s+комплект|не\s+полный\s+комплект|без\s*коробк[а-я]*|"
                            r"коробк[а-я]*\s+(?:нет|отсутствует|потерял[а-я]*|утерян[а-я]*)")
    full = re.compile(r"полный\s+(?:(?:заводской|оригинальный)\s+)?комплект|комплект\s+полный")
    def with_included_extras(base: str, included_items: list[str] | None = None) -> str:
        # A scalar 'full' must not merge a console + TV or two-controller
        # bundle into an ordinary full kit. Keep the actual inclusion wording.
        extras = []
        extra_item = re.compile(
            r"\b(?:телевизор[а-я]*|тв|тv|наушник[а-я]*|гарнитур[а-я]*|геймпады|джойстики|контроллеры|"
            r"(?:два|две|двумя|двух|три|тремя|трех|[2-9]\d*|второй|вторым)\s+"
            r"(?:оригинальн[а-я]*\s+)?(?:геймпад|джойстик|контроллер)[а-я]*)\b"
        )
        for statement in re.split(r"[.!?;\n]", kit_text + "\n" + description):
            text = folded(statement)
            if not extra_item.search(text) or optional.search(text):
                continue
            if re.search(r"отдельно|можно\s+(?:купить|приобрести)|также\s+в\s+продаже|так\s+же\s+в\s+продаже", text):
                continue
            included = re.search(r"комплект|продаю|продам|продается|в\s+подарок|входит", text)
            bare_item = re.fullmatch(r"[\s•—–-]*(?:два|две|три|[2-9]\d*)\s+(?:геймпада|джойстика|контроллера)[\s,]*", text)
            if included or bare_item:
                extras.append(re.sub(r"\s+", " ", statement).strip())
        for statement in included_items or []:
            text = folded(statement)
            if (extra_item.search(text) and not optional.search(text)
                    and not re.search(r"в\s+(?:наличии|продаже)|ремонт|чистк|выкуп|обмен|можно\s+(?:купить|приобрести)", text)):
                extras.append(re.sub(r"\s+", " ", statement).strip())
        return base + "; явно включено: " + "; ".join(dict.fromkeys(extras)) if extras else base
    combined = folded(kit_text + "\n" + description)
    if incomplete.search(combined):
        return with_included_extras("partial")
    # Do not turn an optional kit advertised separately into the standard set.
    if kit_text and kit_text.casefold() not in {"неизвестно", "не указано", "уточняйте", "нет данных"}:
        if optional.search(folded(kit_text)) or unknown.search(folded(kit_text)):
            return ""
        if full.search(folded(kit_text)):
            return with_included_extras("full")
        if re.search(r"только\s+(?:телефон|смартфон|аппарат|ноутбук|консоль|устройство)", folded(kit_text)):
            return "device_only"
        return re.sub(r"\s+", " ", kit_text)[:800]

    lines = [unicodedata.normalize("NFKC", line).strip() for line in description.splitlines()]
    bullet = re.compile(r"^(?:[-*•▪●✅🟢—–]|\d+[.)])\s*(.+)$")
    item_start = re.compile(r"^(?:сама\s+)?(?:консол|приставк|геймпад|джойстик|контроллер|кабел|провод|"
                            r"наушник|коробк|документ|заряд|подставк|блок\s+питания)")
    heading = re.compile(r"\b(?:комплектация|комплект(?:\s+поставки)?)(?:\s+состоящий\s+из)?\s*:|"
                         r"\bв\s+комплекте\s+(?:(?:входит|входят)\s*)?:?\s*")
    for index, line in enumerate(lines):
        text = folded(line)
        label = heading.search(text)
        if not label and not full.search(text):
            continue
        following = []
        for next_line in lines[index + 1:]:
            if not next_line:
                continue
            marked = bullet.match(next_line)
            if marked:
                following.append(marked.group(1).strip())
            elif item_start.search(folded(next_line)):
                following.append(next_line)
            else:
                break
        full_statement = next((part for part in re.split(r"[.!?]", line) if full.search(folded(part))), line)
        context = folded(" ".join((full_statement, *following)))
        if (full.search(text) or any(full.search(folded(piece)) for piece in following)) and not optional.search(context):
            return with_included_extras("full", following)
        # A separate subscription/accessory sentence must not erase the kit
        # explicitly included in the preceding sentence.
        if not label:
            continue
        # The kit heading may start in the second sentence of a long line.
        statement = re.split(r"[.!?]", line[label.start():], maxsplit=1)[0]
        if optional.search(folded(statement)) or unknown.search(folded(statement)):
            continue
        described = statement[label.end() - label.start():].strip(" :;-.")
        # An inline inclusion statement ends with its own sentence.
        if label.group().startswith("в "):
            described = re.split(r"[.!?]", described, maxsplit=1)[0]
        pieces = [piece for piece in (described, *following)
                  if piece and not optional.search(folded(piece)) and not unknown.search(folded(piece))]
        if pieces:
            return "описанный комплект: " + "; ".join(re.sub(r"\s+", " ", piece) for piece in pieces)[:800]
    if re.search(r"только\s+(?:телефон|смартфон|аппарат|ноутбук|консоль|устройство)", combined):
        return "device_only"
    # Literal lists without the word 'комплект' are still seller evidence.
    # Only complete recognized slash-lists and explicit bundle-sale clauses
    # enter here; store inventory and optional accessories remain excluded.
    for line in lines:
        text = folded(line)
        if optional.search(text) or unknown.search(text):
            continue
        if "/" in line and canonical_kit_evidence("описанный комплект: " + line).startswith("components:"):
            return "описанный комплект: " + line[:800]
        clause = re.search(r"\b(?:продается\s+комплект|продам\s+приставку)\s*[, ]\s*", text)
        if clause:
            stated = re.split(r"[!?]|\.(?!\d)", line[clause.end():], maxsplit=1)[0].strip()
            if re.search(r"геймпад|джойстик|контроллер|наушник|подставк|станци", folded(stated)):
                # Keep box statements attached to the same explicit bundle.
                boxes = re.search(r"все\s+коробки\s+имеются", text)
                return "описанный комплект: " + stated[:700] + ("; все коробки имеются" if boxes else "")
    return ""


def _condition_facts(parameters: dict[str, str], description: str, title: str = "") -> dict[str, Any]:
    """Extract explicit seller evidence; omission never means original/healthy."""
    folded = {name.casefold().replace("ё", "е"): value for name, value in parameters.items()}
    def parameter(*names: str) -> str:
        return next((folded[name] for name in names if folded.get(name)), "")
    text = description.casefold().replace("ё", "е")
    repair_evidence = text
    accessory_subject = re.compile(
        r"^\s*(?:в\s+комплекте\s+)?(?:(?:все|оба|два|\d+)\s+)?"
        r"(?:оригинальн[а-я]*\s+)?(?:геймпад|джойстик|контроллер|кабель|зарядное|наушник)[а-я]*\b"
    )
    identity = f"{title} {parameter('модель')}".casefold()
    if (re.search(r"playstation|\bps\s*[45]\b|консол|приставк|iphone|смартфон|телефон|ноутбук|macbook", identity)
            and not accessory_subject.search(title.casefold())):
        # A bundled controller's repair history says nothing about the console.
        repair_evidence = "\n".join(
            statement for statement in re.split(r"[.!?;\n]", text)
            if not accessory_subject.search(statement)
        )
    battery_text = parameter("состояние аккумулятора", "емкость аккумулятора", "здоровье аккумулятора", "battery health")
    battery_match = re.search(r"(?<!\d)(100|[1-9]?\d)\s*%", battery_text)
    if battery_match is None:
        battery_match = re.search(r"(?:акб|аккумулятор[а-я]*|батаре[а-я]*|battery\s*health)[^\n.!?%]{0,35}?(?<!\d)(100|[1-9]?\d)\s*%", text)
    battery_health = int(battery_match.group(1)) if battery_match else None
    repair_text = parameter("ремонт", "история ремонта", "ремонтировался").casefold()
    no_repair = repair_text in {"нет", "не было", "не ремонтировался", "без ремонта", "не ремонтировался и не вскрывался"}
    no_repair_match = re.search(r"(?:не\s+ремонтировал[а-я]*|ремонтов\s+не\s+было|без\s+ремонт[а-я]*|не\s+был[а-я]*\s+в\s+ремонте)", repair_evidence)
    repair_without_negation = re.sub(r"не\s+ремонтировал[а-я]*|ремонтов\s+не\s+было|без\s+ремонт[а-я]*|не\s+был[а-я]*\s+в\s+ремонте", "", repair_evidence)
    had_repair = repair_text in {"да", "был", "ремонтировался", "после ремонта"} or bool(re.search(r"после\s+ремонта|ремонтировал[а-я]*|замен(?:ен[а-я]*|а|или)\s+(?:экран|аккумулятор|батаре|плат|камер)", repair_without_negation))
    repair = "conflicting" if (no_repair or no_repair_match) and had_repair else "repaired" if had_repair else "never_repaired" if no_repair or no_repair_match else ""
    parts_text = parameter("оригинальность деталей", "детали", "запчасти").casefold()
    # Evaluate whole statements: a matching positive substring inside a denial
    # or a guess is not evidence that the device's internal parts are original.
    original = replaced = False
    uncertain_parts = False
    for statement in re.findall(r"[^.!?;\n]+[.!?;\n]?", f"{parts_text}\n{text}"):
        relevant = bool(re.search(r"оригинальн|детал|комплектующ|китайский\s+экран|аналог\s+(?:экрана|аккумулятора)", statement))
        doubtful = "?" in statement or bool(re.search(
            r"\b(?:возможно|вероятно|наверное|предположительно|вроде|кажется)|"
            r"не\s+(?:уверен[а-я]*|знаю|проверял[а-я]*|известн[а-я]*|подтвержден[а-я]*|факт|могу\s+подтвердить)", statement))
        if relevant and doubtful:
            uncertain_parts = True
            continue
        denied = bool(re.search(
            r"неоригинальн[а-я]*|не\s+оригинальн[а-я]*|"
            r"не\s+все\s+(?:(?:детали|комплектующие)\s+)?оригинальн[а-я]*|"
            r"китайский\s+экран|аналог\s+(?:экрана|аккумулятора)", statement))
        replaced = replaced or denied
        if not denied:
            original = original or statement.strip(" .;\n") in {"оригинальные", "все оригинальные", "все оригинальное"} or bool(re.search(
                r"\b(?:все\s+)?(?:детали|комплектующие)\s+оригинальн[а-я]*\b|"
                r"^\s*(?:все|вся)\s+оригинальн[а-я]*\s*(?=$|[.!?;,\n])", statement))
    if uncertain_parts:
        original = False
    parts = "conflicting" if original and replaced else "non_original" if replaced else "original" if original else ""
    kit_text = parameter("комплектация", "комплект поставки", "комплект").casefold().strip()
    completeness = _kit_evidence(kit_text, description)
    return {
        "battery_health_percent": battery_health,
        "repair_status": repair,
        "parts_status": parts,
        "completeness": completeness,
    }


def _purchase_costs(raw: Mapping[str, Any], description: str) -> dict[str, Any]:
    """Only explicit mandatory amounts count; optional shipping stays separate."""
    folded = {str(name).casefold(): value for name, value in raw.items()}
    def field(*names: str) -> tuple[bool, Any]:
        for name in names:
            if name.casefold() in folded:
                return True, folded[name.casefold()]
        return False, None
    def amount(value: Any) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        text = _text(value).casefold().replace("\u00a0", "").replace(" ", "").replace(",", ".")
        text = re.sub(r"(?:₽|руб\.?|rub)$", "", text)
        if not re.fullmatch(r"\d+(?:\.\d{1,2})?", text):
            return None
        number = float(text)
        return math.ceil(number) if math.isfinite(number) else None
    fee_present, fee_value = field("mandatoryFeeRub", "mandatoryFee", "requiredFeeRub", "requiredFee", "mandatory_fee_rub")
    delivery_present, delivery_value = field("deliveryCostRub", "deliveryCost", "shippingCostRub", "shippingCost", "delivery_cost_rub")
    mandatory_fee = amount(fee_value) if fee_present else 0
    # A percentage, unspecified surcharge or conditional price cannot be made
    # final merely because the provider omitted a numeric fee field.
    text = description.casefold().replace("ё", "е")
    fee_text = re.sub(r"без\s+комисси[а-я]*|комисси[а-я]*\s+нет|без\s+доплат", "", text)
    if not fee_present and re.search(r"комисси[а-я]*|обязательн[а-я]*\s+(?:сбор|доплат)|дополнительно\s+оплачива", fee_text):
        mandatory_fee = None
    def is_true(value: Any) -> bool:
        return value is True or (isinstance(value, (str, int)) and _text(value).casefold() in {"true", "yes", "да", "1"})
    delivery_required = any(is_true(field(name)[1]) for name in ("requiredDelivery", "onlyShipping", "deliveryRequired", "delivery_required"))
    # "Не только доставка, но и самовывоз" offers both options. Removing the
    # negated qualifier prevents it being mistaken for mandatory shipping.
    shipping_text = re.sub(r"\bне\s+только\b", " ", text)
    delivery_required = delivery_required or bool(re.search(
        r"\bтолько\s+(?:(?:через|с|по)\s+)?(?:авито\s*[- ]?\s*)?(?:доставк[а-я]*|отправк[а-я]*)|"
        r"самовывоз[а-я]*\s*[:—-]?\s*(?:нет|невозможен|недоступен|исключен|не\s+(?:предусмотрен|доступен|возможен|делаю|рассматриваю))|"
        r"без\s+самовывоза", shipping_text))
    return {
        "mandatory_fee_rub": mandatory_fee,
        "delivery_cost_rub": amount(delivery_value) if delivery_present else None,
        "delivery_required": delivery_required,
    }


def normalize_listing(raw: Mapping[str, Any]) -> NormalizedListing:
    if not isinstance(raw, Mapping):
        raise InvalidDatasetError("Каждая запись Zen должна быть JSON-объектом.")
    parameters, parameter_ids = _parameters(raw)
    listing_url = _safe_listing_url(
        _value(
            raw,
            "url",
            "listingUrl",
            "listing_url",
            "itemUrl",
            "item_url",
            "canonicalUrl",
            "canonical_url",
            "link",
        )
    )
    listing_id = _listing_id(raw, listing_url)
    if not listing_id:
        raise InvalidDatasetError("Запись Zen не содержит ни идентификатора, ни прямой ссылки объявления.")
    images = _images(raw)
    description = _text(raw.get("description"))
    return NormalizedListing(
        listing_id=listing_id,
        title=_text(raw.get("title"), limit=500),
        url=listing_url,
        status=_text(raw.get("status"), limit=40).casefold(),
        price=_integer(raw.get("price")),
        currency="RUB" if _text(raw.get("currency") or "RUB").casefold() in {"rub", "rur", "₽", "руб", "руб."} else _text(raw.get("currency"), limit=8).upper(),
        # This is intentionally untruncated: the complete seller description is mandatory evidence.
        description=description,
        images=images,
        image_count_claimed=_integer(raw.get("imageCount") or raw.get("photoCount")) or len(images),
        parameters=parameters,
        parameter_ids=parameter_ids,
        badges=_badges(raw),
        stock=_text(raw.get("stock"), limit=160),
        seller=_seller(raw),
        collected_at=_text(raw.get("scrapedAt") or raw.get("collectedAt"), limit=80),
        location=_location(raw, listing_url),
        address=_address(raw, listing_url),
        delivery=_delivery(raw, parameters),
        # verifiedAt/status from imported JSON are intentionally never trusted.
        **_condition_facts(parameters, description, _text(raw.get("title"), limit=500)),
        **_purchase_costs(raw, description),
    )


def normalize_dataset(raw_items: Any) -> tuple[NormalizedListing, ...]:
    if not isinstance(raw_items, (list, tuple)):
        raise InvalidDatasetError("Ожидается JSON-массив объявлений Zen.")
    result: list[NormalizedListing] = []
    seen: set[str] = set()
    for raw in raw_items:
        try:
            listing = normalize_listing(raw)
        except InvalidDatasetError:
            # Actor datasets can contain a diagnostic/search metadata row next
            # to real listings. One malformed row must not abort the whole run.
            continue
        if listing.listing_id not in seen:
            seen.add(listing.listing_id)
            result.append(listing)
    return tuple(result)
