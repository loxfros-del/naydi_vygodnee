"""Cheap request-family parsing and listing SKU compatibility before paid AI."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .models import NormalizedListing, SearchRequest


_PS5 = re.compile(
    r"\b(?:ps\s*5|play\s*station\s*5|playstation\s*5|пс\s*5|плей\s*ст[еэ]йш[еэ]н\s*5)\b",
    re.I,
)
_PS4 = re.compile(
    r"\b(?:ps\s*4|play\s*station\s*4|playstation\s*4|пс\s*4|плей\s*ст[еэ]йш[еэ]н\s*4)\b",
    re.I,
)
_PRO = re.compile(r"\b(?:pro|про)\b", re.I)
_SLIM = re.compile(r"\b(?:slim|слим)\b", re.I)
_FAT = re.compile(r"\b(?:fat|фат)\b", re.I)
_DIGITAL = re.compile(
    r"\b(?:digital(?:\s+edition)?|диджитал|дигитал|цифров(?:ая|ой)(?:\s+версия)?)\b|"
    r"\bбез\s+(?:дисковода|привода)\b", re.I,
)
_DISC = re.compile(
    r"\b(?:disc|disk)(?:\s+edition)?\b|\b(?:с|со)\s+(?:дисководом|приводом)\b|"
    r"\bдисков(?:ая|ой)(?:\s+версия)?\b", re.I,
)
_ACCESSORY = re.compile(
    r"\b(?:геймпад\w*|джойстик\w*|контроллер\w*|dualsense|dualshock|"
    r"дисковод\w*|привод\w*|чехол\w*|кейс\w*|подставк\w*|заряд\w*|"
    r"кабел\w*|коробк\w*|запчаст\w*|ремонт\w*|игр[аы]\b|аккаунт\w*|"
    r"подписк\w*|ps\s*plus|vr\s*2|ps\s*vr)\b", re.I,
)
_ONLY_ACCESSORY = re.compile(
    r"\b(?:для|к)\s+(?:ps\s*5|play\s*station\s*5|playstation\s*5)\b|"
    r"\bбез\s+(?:консоли|приставки)\b|\bтолько\s+(?:коробка|дисковод|геймпад)\b", re.I,
)
_STORAGE = re.compile(r"(?<!\d)(\d+(?:[.,]\d+)?)\s*(tb|тб|gb|гб)\b", re.I)


@dataclass(frozen=True, slots=True)
class RequestSignature:
    requested_product: str
    requested_family: str
    requested_form_factor: str
    requested_edition: str
    requested_storage: str
    requested_condition: str
    allowed_variants: tuple[str, ...]
    excluded_variants: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ListingSku:
    product_type: str
    family: str
    form_factor: str
    edition: str
    storage: str
    condition: str
    conflicts: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return "|".join((self.product_type, self.family, self.form_factor,
                         self.edition, self.storage, self.condition))


@dataclass(frozen=True, slots=True)
class RequestCompatibility:
    matches: bool
    reason: str = ""
    signature: RequestSignature | None = None
    sku: ListingSku | None = None
    field: str = ""


def _text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
    value = re.sub(r"(?<=[a-zа-я])(?=\d)|(?<=\d)(?=[a-zа-я])", " ", value)
    return " ".join(re.findall(r"[a-zа-я0-9.,]+", value))


def _storage(value: str) -> str:
    match = _STORAGE.search(_text(value))
    if not match:
        return "any"
    number = float(match.group(1).replace(",", "."))
    if match.group(2).casefold() in {"tb", "тб"}:
        number *= 1000
    return f"{int(number) if number.is_integer() else number:g} gb"


def _condition(value: str) -> str:
    value = _text(value)
    if re.search(r"\b(?:б\s*у|бу|used|pre\s*owned|бывш\w*\s+в\s+употреблении)\b", value):
        return "used"
    if re.search(r"\b(?:как\s+нов\w*|идеал\w*|отличн\w*|excellent|like\s+new)\b", value):
        return "excellent"
    if re.search(
        r"\b(?:нов(?:ый|ая|ое|ые)|new|sealed|запечатан\w*|"
        r"не\s+использовал(?:ся|ась|ось|ись)|нов\w*\s+в\s+заводск\w*\s+упаковк\w*)\b",
        value,
    ):
        return "new"
    if re.search(r"\b(?:хорош\w*|good)\b", value):
        return "good"
    return "any"


def _form(value: str) -> str:
    value = _text(value)
    if _PRO.search(value):
        return "pro"
    if _SLIM.search(value):
        return "slim"
    if _FAT.search(value):
        return "fat"
    return "base"


def _edition(value: str) -> str:
    value = _text(value)
    digital, disc = bool(_DIGITAL.search(value)), bool(_DISC.search(value))
    if digital and disc:
        return "conflict"
    if digital:
        return "digital"
    if disc:
        return "disc"
    return "any"


def parse_request_signature(request: SearchRequest | str) -> RequestSignature:
    query = request.query if isinstance(request, SearchRequest) else str(request)
    text = _text(query)
    if not _PS5.search(text):
        return RequestSignature("unknown", "unknown", "any", "any", "any",
                                _condition(request.required_condition) if isinstance(request, SearchRequest) else "any",
                                (), ())
    form = _form(text)
    edition = _edition(text)
    family = "ps5_pro" if form == "pro" else "ps5_standard"
    requested_form = form if form in {"pro", "slim", "fat"} else "any_standard"
    storage_source = request.required_storage if isinstance(request, SearchRequest) and request.required_storage else query
    condition_source = request.required_condition if isinstance(request, SearchRequest) else ""
    if family == "ps5_pro":
        allowed = ("ps5_pro",)
        excluded = ("base", "fat", "slim", "standard_digital", "standard_disc")
    elif requested_form == "slim" and edition in {"digital", "disc"}:
        allowed = (f"slim_{edition}",)
        excluded = ("base", "fat", "pro", "disc" if edition == "digital" else "digital")
    elif requested_form == "slim":
        allowed = ("slim", "slim_digital", "slim_disc")
        excluded = ("base", "fat", "pro")
    else:
        allowed = ("base", "fat", "slim", "standard_digital", "standard_disc", "slim_digital", "slim_disc")
        excluded = ("ps5_pro",)
    return RequestSignature(
        requested_product="console",
        requested_family=family,
        requested_form_factor=requested_form,
        requested_edition=edition,
        requested_storage=_storage(storage_source),
        requested_condition=_condition(condition_source),
        allowed_variants=allowed,
        excluded_variants=excluded,
    )


def _parameter(listing: NormalizedListing, *needles: str) -> str:
    for name, value in listing.parameters.items():
        folded = _text(name)
        if any(needle in folded for needle in needles):
            return value
    return ""


def _storage_parameter(listing: NormalizedListing) -> str:
    for name, value in listing.parameters.items():
        folded = _text(name)
        if not any(needle in folded for needle in ("встроенная память", "объем памяти", "накопитель")):
            continue
        if _storage(value) != "any":
            return value
        if re.fullmatch(r"\s*\d+(?:[.,]\d+)?\s*", str(value)):
            if re.search(r"\b(?:гб|gb)\b", folded):
                return f"{value} GB"
            if re.search(r"\b(?:тб|tb)\b", folded):
                return f"{value} TB"
        return value
    return ""


def normalize_listing_sku(listing: NormalizedListing) -> ListingSku:
    structured_model = _parameter(listing, "модель")
    structured_storage = _storage_parameter(listing)
    structured_condition = _parameter(listing, "состояние")
    all_sources = (structured_model, listing.title, listing.description)
    model_sources = [source for source in all_sources if _PS5.search(_text(source))]
    conflicts: list[str] = []
    explicit_forms = {value for value in (_form(source) for source in all_sources) if value != "base"}
    if len(explicit_forms) > 1:
        conflicts.append("SKU_CONFLICT")
    form = next(iter(explicit_forms), "base")
    # Edition wording often omits the console name in the description, e.g.
    # "Digital Edition" or "с дисководом". It still refines a generic title.
    explicit_editions = {value for value in (_edition(source) for source in all_sources) if value != "any"}
    if "conflict" in explicit_editions or len(explicit_editions - {"conflict"}) > 1:
        conflicts.append("SKU_CONFLICT")
    edition = next(iter(explicit_editions - {"conflict"}), "any")
    structured_ps5 = bool(_PS5.search(_text(structured_model)))
    structured_ps4 = bool(_PS4.search(_text(structured_model)))
    title_ps5 = bool(_PS5.search(_text(listing.title)))
    title_ps4 = bool(_PS4.search(_text(listing.title)))
    if (structured_ps5 and title_ps4 and not title_ps5) or (structured_ps4 and title_ps5):
        conflicts.append("SKU_CONFLICT")
    if (title_ps4 and not title_ps5) or (structured_ps4 and not structured_ps5):
        family = "ps4"
    elif form == "pro":
        family = "ps5_pro"
    elif model_sources:
        family = "ps5_standard"
    else:
        family = "unknown"
    title = _text(listing.title)
    product_type = "console"
    if family in {"unknown", "ps4"} or _ONLY_ACCESSORY.search(title):
        product_type = "other"
    elif _ACCESSORY.search(title):
        ps = _PS5.search(title)
        accessory = _ACCESSORY.search(title)
        if not ps or not accessory or accessory.start() < ps.start():
            product_type = "accessory"
    storage_values = [value for value in (
        _storage(structured_storage), _storage(listing.title), _storage(listing.description),
    ) if value != "any"]
    if len(set(storage_values)) > 1:
        conflicts.append("SKU_CONFLICT")
    storage = storage_values[0] if storage_values else "any"
    parameter_condition = _condition(structured_condition)
    text_condition = _condition(f"{listing.title} {listing.description}")
    if parameter_condition != "any" and text_condition != "any":
        parameter_used = parameter_condition in {"used", "good", "excellent"}
        text_used = text_condition in {"used", "good", "excellent"}
        if parameter_used != text_used:
            conflicts.append("CONDITION_CONFLICT")
    condition = text_condition if text_condition != "any" else parameter_condition
    return ListingSku(product_type, family, form, edition, storage, condition,
                      tuple(dict.fromkeys(conflicts)))


def check_request_compatibility(
    listing: NormalizedListing,
    request: SearchRequest | RequestSignature,
) -> RequestCompatibility:
    signature = request if isinstance(request, RequestSignature) else parse_request_signature(request)
    sku = normalize_listing_sku(listing)
    if signature.requested_family == "unknown":
        return RequestCompatibility(True, signature=signature, sku=sku)
    if sku.conflicts:
        field = "condition" if sku.conflicts[0] == "CONDITION_CONFLICT" else "variant"
        return RequestCompatibility(False, sku.conflicts[0], signature, sku, field)
    if sku.product_type != "console" or sku.family != signature.requested_family:
        return RequestCompatibility(False, "REQUEST_SKU_MISMATCH", signature, sku, "model")
    wanted_form = signature.requested_form_factor
    if wanted_form not in {"any", "any_standard"} and sku.form_factor != wanted_form:
        return RequestCompatibility(False, "REQUEST_SKU_MISMATCH", signature, sku, "variant")
    if signature.requested_edition in {"digital", "disc"} and sku.edition != signature.requested_edition:
        return RequestCompatibility(False, "REQUEST_SKU_MISMATCH", signature, sku, "variant")
    if signature.requested_storage != "any" and sku.storage != signature.requested_storage:
        return RequestCompatibility(False, "REQUEST_SKU_MISMATCH", signature, sku, "other")
    if signature.requested_condition != "any" and sku.condition != signature.requested_condition:
        return RequestCompatibility(False, "REQUEST_SKU_MISMATCH", signature, sku, "condition")
    return RequestCompatibility(True, signature=signature, sku=sku)


def search_query_variants(signature: RequestSignature) -> tuple[str, ...]:
    if signature.requested_family == "ps5_pro":
        base = ("PS5 Pro", "PlayStation 5 Pro", "PS 5 Pro")
    elif signature.requested_form_factor == "slim" and signature.requested_edition == "digital":
        base = ("PS5 Slim Digital", "PlayStation 5 Slim Digital", "PS 5 Slim без дисковода")
    elif signature.requested_form_factor == "slim" and signature.requested_edition == "disc":
        base = ("PS5 Slim Disc", "PlayStation 5 Slim с дисководом", "PS 5 Slim Disc Edition")
    elif signature.requested_form_factor == "slim":
        base = ("PS5 Slim", "PlayStation 5 Slim", "PS 5 Slim")
    elif signature.requested_family == "ps5_standard":
        base = ("PlayStation 5", "PS5", "Play Station 5", "PS 5", "PS5 Slim", "PS5 Digital", "PS5 с дисководом")
    else:
        return ()
    if signature.requested_storage == "any":
        return base
    amount = int(float(signature.requested_storage.split()[0]))
    if amount % 1000 == 0:
        tb = amount // 1000
        storage_aliases = (
            f"{tb}TB", f"{tb} TB", f"{amount}GB", f"{amount} GB",
            f"{tb}ТБ", f"{amount} ГБ",
        )
    else:
        storage_aliases = (f"{amount}GB", f"{amount} GB", f"{amount}ГБ", f"{amount} ГБ")
    count = max(len(base), len(storage_aliases))
    return tuple(dict.fromkeys(
        f"{base[index % len(base)]} {storage_aliases[index % len(storage_aliases)]}"
        for index in range(count)
    ))
