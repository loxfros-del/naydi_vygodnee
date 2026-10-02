"""Deterministic request matching shared by selection and final ranking."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from .models import NormalizedListing, SearchRequest
from .normalization import _city_key
from .request_intent import check_request_compatibility


_PLAYSTATION_MODEL = re.compile(r"\bplaystation\s+(\d+)\b")
_PLAYSTATION_VARIANTS = {"slim", "pro", "fat", "digital", "disc"}
_PLAYSTATION_ALIAS = re.compile(r"\b(?:плей\s*ст[еэ]йш[еэ]н|play\s*station|пс|ps)\s*(?=\d)")
_CONSOLE_ACCESSORY = re.compile(
    r"\b(?:игр(?:а|ы|у|е|ами|ам|ой)|games?|аккаунт\w*|профил\w*|пополн\w*|подписк\w*|"
    r"ключ\w*|код\w*|vr\s*2|ps\s*vr|геймпад\w*|джойстик\w*|контроллер\w*|"
    r"dualsense|dualshock|коробк\w*|упаковк\w*|чехол|чехл\w*|кейс\w*|"
    r"подставк\w*|креплен\w*|док\s*станци\w*|заряд\w*|кабел\w*|"
    r"дисковод\w*|привод\w*|диск[иаоу]?|disc\s+drive|ssd|наушник\w*|"
    r"накладк\w*|панел\w*|крышк\w*|пульт\w*|запчаст\w*|материнск\w*)\b|ps\s*plus",
    re.IGNORECASE,
)
_CONSOLE_SERVICE = re.compile(r"\b(?:аренд\w*|прокат\w*|rental?|подписк\w*|аккаунт\w*|профил\w*)\b|ps\s*plus")
_CONSOLE_ONLY_ACCESSORY = re.compile(
    r"\b(?:только|пустая|пустую)\s+(?:оригинальн\w*\s+)?(?:коробк\w*|упаковк\w*)\b|"
    r"\bбез\s+(?:самой\s+)?(?:консол\w*|приставк\w*)\b|"
    r"\b(?:консол\w*|приставк\w*)\s+не\s+(?:прода\w*|входит)\b|"
    r"\b(?:продаю|продам|продается)\s+(?:только\s+)?(?:коробк\w*|дисковод\w*|геймпад\w*)\b"
)
_ATTRIBUTE_PARAMETERS = {
    "storage": ("Встроенная память", "Объём памяти", "Накопитель", "SSD"),
    "sim": ("SIM-карты", "Количество SIM-карт", "Версия"),
    "color": ("Цвет",),
    "ram": ("Оперативная память", "RAM"),
    "screen": ("Диагональ экрана", "Экран"),
    "appliance_type": ("Тип", "Вид техники", "Модель"),
    "dimensions": ("Размеры", "Габариты", "Ширина", "Глубина", "Высота"),
    "load": ("Максимальная загрузка", "Загрузка"),
    "material": ("Материал",),
    "edition": ("Версия", "Модель", "Тип"),
    "power": ("Мощность", "Напряжение аккумулятора"),
    "size": ("Размер", "Диаметр", "Размер одежды"),
    "compatibility": ("Марка", "Модель", "Применимость"),
    "year": ("Год выпуска", "Поколение"),
}
_STOP_WORDS = {
    "от", "до", "около", "примерно", "не", "менее", "более", "и", "или",
    "для", "с", "со", "на", "the", "with",
}
_UNKNOWN = re.compile(
    r"(?:^unknown$|^n\s*a$|неизвест|не\s+(?:указан|определ|видно|подтвержден)|"
    r"невозможно\s+определ|уточн|предполож|вероятно|возможно)", re.IGNORECASE,
)
_MODEL_WORDS = {
    "купить", "найти", "нужен", "нужна", "нужно", "ищу", "оригинал", "оригинальный",
    "телефон", "смартфон", "ноутбук", "планшет", "приставка", "консоль", "игровая",
    "новый", "новое", "новая", "бу", "б", "у", "состояние", "отличное", "хорошее",
    "apple", "эппл", "sony", "сони",
}
_VARIANTS = {"pro", "max", "mini", "plus", "ultra", "lite", "se", "slim", "digital", "disc", "air", "ti", "super", "fe", "edge"}


def canonical_text(value: str) -> str:
    """Normalize common marketplace units and spelling for stable comparisons."""
    text = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
    replacements = (
        (r"(?<![a-zа-я])(?:g\s*bytes?|g\s*b|г\s*байт(?:а|ов)?|г\s*б)\b", " gb "),
        (r"(?<![a-zа-я])(?:t\s*bytes?|t\s*b|т\s*байт(?:а|ов)?|т\s*б)\b", " tb "),
        (r"(?<![a-zа-я])(?:мбайт(?:а|ов)?|mb|мб)\b", " mb "),
        (r"\b(?:inch(?:es)?|дюйм(?:а|ов|ы)?)\b", " inch "),
        (r"\b(?:black|черн(?:ый|ая|ое|ые))\b", " black "),
        (r"\b(?:white|бел(?:ый|ая|ое|ые))\b", " white "),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return " ".join(re.findall(r"[a-zа-я0-9]+", text))


def _compact(value: str) -> str:
    return canonical_text(value).replace(" ", "")


def _parameter(listing: NormalizedListing, names: tuple[str, ...]) -> str:
    wanted = {name.casefold() for name in names}
    values = [value for name, value in listing.parameters.items() if name.casefold() in wanted]
    return " ".join(values)


def evidence_known(value: str) -> bool:
    return bool(value.strip() and not _UNKNOWN.search(canonical_text(value)))


def canonical_evidence(field: str, value: str) -> str:
    """Canonical facts used by deterministic matching and text/photo agreement."""
    if field == "storage":
        # Marketplace drive capacities use nominal decimal GB/TB. Keeping
        # decimal punctuation here also prevents 0.5 TB becoming 5 TB after
        # canonical_text removes punctuation. Explicit GiB/TiB stay distinct.
        capacities = re.findall(
            r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*"
            r"(g\s*bytes?|g\s*b|г\s*байт(?:а|ов)?|г\s*б|t\s*bytes?|t\s*b|т\s*байт(?:а|ов)?|т\s*б)\b",
            unicodedata.normalize("NFKC", value or "").casefold(),
        )
        if capacities:
            numbers = []
            for number, unit in capacities:
                gigabytes = Decimal(number.replace(",", ".")) * (1000 if unit.startswith(("t", "т")) else 1)
                numbers.append(format(gigabytes.normalize(), "f"))
            return " ".join(numbers) + " gb"
    if field == "model":
        # Legacy free-text laptop requests combine the model with RAM/storage
        # and screen size. Strip only explicitly marked specifications, keeping
        # M1/M2, S23/S24 and Pro/Max as model identity.
        value = re.sub(r"\b\d+\s*/\s*\d+\s*(?:gb|гб)\b", " ", value, flags=re.IGNORECASE)
        value = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:дюйм(?:а|ов|ы)?|inch(?:es)?|[\"″])", " ", value, flags=re.IGNORECASE)
    value = canonical_text(value)
    if field == "model":
        for pattern, replacement in (
            (r"\bайфон\b", "iphone"), (r"\b(?:плей\s*ст[еэ]йш[еэ]н|play\s*station|пс|ps)\s*(?=\d)", "playstation "),
            (r"\bсамсунг\b", "samsung"), (r"\bгалакси\b", "galaxy"),
            (r"\bпро\b", "pro"), (r"\bмакс\b", "max"), (r"\bплюс\b", "plus"),
            (r"\bмини\b", "mini"), (r"\bультра\b", "ultra"), (r"\bслим\b", "slim"), (r"\bфат\b", "fat"),
        ):
            value = re.sub(pattern, replacement, value)
        if re.search(r"\bgalaxy\b", value):
            value = re.sub(r"\bsamsung\b", " ", value)
        value = re.sub(r"(?<=[a-zа-я])(?=\d)|(?<=\d)(?=[a-zа-я])", " ", value)
        value = re.sub(r"\b\d+\s*(?:gb|tb|mb)\b", " ", value)
        if _PLAYSTATION_MODEL.search(value):
            # A detachable drive is a separate product. These aliases only
            # normalize a model/edition statement, never infer an included drive.
            for pattern, replacement in (
                (r"\b(?:digital\s+edition|digital|диджитал|дигитал|цифровая(?:\s+версия)?|без\s+дисковода|без\s+привода)\b", "digital"),
                (r"\b(?:disc\s+edition|disk\s+edition|disc|disk|дисковая(?:\s+версия)?|версия\s+с\s+дисководом|с\s+дисководом|с\s+приводом)\b", "disc"),
            ):
                value = re.sub(pattern, replacement, value)
            if re.search(r"\bplaystation\s+[45]\b", value):
                value = re.sub(r"\bblu\s*ray(?:\s+edition)?\b", "disc", value)
        return " ".join(token for token in value.split() if token not in _MODEL_WORDS | _STOP_WORDS)
    if field == "sim":
        if value in {"n a", "none", "null", "not applicable", "не применимо", "неприменимо", "нет"}:
            return ""
        value = re.sub(r"\be\s+sim\b", "esim", value)
        value = re.sub(r"\bphysical\b|\bфизическ[а-я]*\b", " ", value)
        return " ".join(sorted(value.split()))
    if field == "condition":
        grades = {
            "new": {
                "новый", "новое", "новая", "new", "новое запечатанное",
                "новый запечатанный", "новая запечатанная", "new sealed", "sealed new",
                "brand new", "factory sealed", "new in box", "новое в упаковке",
            },
            "excellent": {
                "отличное", "отличное состояние", "состояние отличное",
                "в отличном состоянии", "excellent", "excellent condition", "like new", "как новый",
            },
            "good": {
                "хорошее", "хорошее состояние", "состояние хорошее",
                "в хорошем состоянии", "good", "good condition", "very good", "very good condition",
            },
        }
        for grade, aliases in grades.items():
            if value in aliases:
                return grade
        # A used marker qualifies a grade, it does not replace the grade:
        # "Хорошее (Б/у)" is good, not a conflict with "Хорошее". Accept
        # only complete known phrases; "новый б/у", negations, uncertain
        # wording and multiple different grades must remain contradictions.
        without_used, used_count = re.subn(
            r"\b(?:б\s+у|бу|used|pre\s+owned|бывш(?:ий|ая|ее|ие)\s+в\s+употреблении)\b",
            " ", value,
        )
        without_used = " ".join(without_used.split())
        if used_count:
            if not without_used:
                return "used"
            for grade in ("good", "excellent"):
                if without_used in grades[grade]:
                    return grade
    return value


def _playstation_identity(value: str, *, title: bool = False) -> tuple[str, frozenset[str]] | None:
    canonical = canonical_evidence("model", value)
    primary = _PLAYSTATION_MODEL.search(canonical)
    if not primary:
        return None
    generation = primary.group(1)
    if not title and any(match.group(1) != generation for match in _PLAYSTATION_MODEL.finditer(canonical)):
        return "", frozenset()
    variants: set[str] = set()
    for token in canonical[primary.end():].split():
        if token not in _PLAYSTATION_VARIANTS:
            break
        variants.add(token)
    return generation, frozenset(variants)


def _compatible_playstation(left: tuple[str, frozenset[str]], right: tuple[str, frozenset[str]]) -> bool:
    if not left[0] or left[0] != right[0]:
        return False
    combined = left[1] | right[1]
    return not (len(combined & {"slim", "pro", "fat"}) > 1 or {"digital", "disc"} <= combined)


def _playstation_model_details(value: str) -> str:
    """Preserve explicit source/AI model identifiers such as CFI-2016A."""
    canonical = _PLAYSTATION_MODEL.sub(" ", canonical_evidence("model", value))
    return " ".join(token for token in canonical.split() if token not in _PLAYSTATION_VARIANTS)


def effective_model_identity(listing: NormalizedListing, identified_model: str = "") -> str:
    """Merge only explicit, consistent PS generation/edition facts for grouping.

    A generic PS5 has an unspecified edition. A Slim title refines a generic
    parameter/AI model, but two different explicit editions never merge.
    Description-wide mentions are excluded: shop catalogs list many products.
    """
    values = (
        _playstation_identity(listing.parameter("Модель")),
        _playstation_identity(identified_model),
        _playstation_identity(listing.title, title=True),
    )
    identities = [value for value in values if value is not None]
    if not identities:
        return canonical_evidence("model", identified_model or listing.model)
    generation, variants = identities[0]
    for identity in identities:
        if not _compatible_playstation((generation, variants), identity):
            return ""
        variants = variants | identity[1]
    details = {
        _playstation_model_details(value) for value in (listing.parameter("Модель"), identified_model)
        if _playstation_identity(value) is not None and _playstation_model_details(value)
    }
    if len(details) > 1:
        return ""
    return " ".join(("playstation", generation,
        *(value for value in ("slim", "pro", "fat", "digital", "disc") if value in variants), *sorted(details)))


def evidence_conflicts(field: str, left: str, right: str) -> bool:
    """Only compare explicit facts; a photo that cannot reveal memory adds no fact."""
    if not evidence_known(left) or not evidence_known(right):
        return False
    if field == "condition":
        # These exact observations describe what is visible, not a condition
        # grade. Keep canonical_evidence unchanged: they cannot prove a requested
        # grade or merge an unknown listing into a new/excellent market group.
        visual_observations = {
            "внешне без видимых дефектов", "без видимых дефектов",
            "видимых дефектов не обнаружено", "на фото видимых дефектов нет",
            "внешних повреждений не обнаружено", "без видимых повреждений",
        }
        if canonical_text(left) in visual_observations or canonical_text(right) in visual_observations:
            return False
    if field == "model":
        left_identity, right_identity = _playstation_identity(left), _playstation_identity(right)
        if left_identity is not None and right_identity is not None:
            left_details, right_details = _playstation_model_details(left), _playstation_model_details(right)
            return (not _compatible_playstation(left_identity, right_identity)
                    or bool(left_details and right_details and left_details != right_details))
    left_value = canonical_evidence(field, left)
    right_value = canonical_evidence(field, right)
    return left_value != right_value


def _model_matches(query: str, actual: str) -> bool:
    if not evidence_known(actual):
        return False
    wanted = canonical_evidence("model", query).split()
    available = canonical_evidence("model", actual).split()
    if not wanted or not available:
        # Generic category requests still require a model, but contain no exact
        # model constraint. AI request confirmation remains mandatory upstream.
        return bool(available)
    if not set(wanted).issubset(available):
        return False
    wanted_ps, available_ps = _playstation_identity(query), _playstation_identity(actual)
    if wanted_ps is not None and available_ps is not None:
        # Unspecified dimensions are open: PS5 includes Slim/Pro, and PS5 Slim
        # can be Digital or Disc. Explicit dimensions still must be present.
        return _compatible_playstation(wanted_ps, available_ps) and wanted_ps[1] <= available_ps[1]
    # A numbered model is exact: an iPhone 13 Pro is not an iPhone 13. The same
    # guard catches RTX 4070 Ti and PS5 Pro without hand-written model catalogs.
    if any(token.isdigit() for token in wanted):
        if set(wanted) & _VARIANTS != set(available) & _VARIANTS:
            return False
        if {token for token in wanted if token.isdigit()} != {token for token in available if token.isdigit()}:
            return False
    return True


def _title_model_conflicts(query: str, title: str) -> bool:
    """Reject an explicit different numbered model in the same title family.

    Descriptive words and controller counts are not model numbers. Compare only
    the model prefix and its immediate number/edition, not arbitrary title digits.
    Other families without a literal common prefix remain for the AI review.
    """
    wanted_ps, title_ps = _playstation_identity(query), _playstation_identity(title, title=True)
    if wanted_ps is not None and title_ps is not None:
        return not _compatible_playstation(wanted_ps, title_ps)
    wanted = canonical_evidence("model", query).split()
    available = canonical_evidence("model", title).split()
    number_index = next((index for index, token in enumerate(wanted) if token.isdigit()), None)
    if number_index is None or number_index == 0:
        return False
    prefix = wanted[:number_index]
    for index in range(len(available) - len(prefix)):
        if available[index:index + len(prefix)] != prefix:
            continue
        suffix = available[index + len(prefix):]
        if not suffix[0].isdigit():
            continue
        if suffix[0] != wanted[number_index]:
            return True
        title_variants = set(prefix) & _VARIANTS
        for token in suffix[1:]:
            if token not in _VARIANTS:
                break
            title_variants.add(token)
        # Missing edition text is not proof of a different edition. An explicit
        # additional Pro/Digital/Disc is a conflict, including when AI omits it.
        return bool(title_variants - (set(wanted) & _VARIANTS))
    return False


def _console_product_matches(listing: NormalizedListing) -> bool:
    """Separate the console being sold from compatible products and services."""
    text = _PLAYSTATION_ALIAS.sub("playstation ", canonical_text(listing.title.replace("+", " плюс ")))
    description = canonical_text(listing.description)
    primary = _PLAYSTATION_MODEL.search(text)
    if _CONSOLE_SERVICE.search(text) or _CONSOLE_ONLY_ACCESSORY.search(text + " " + description):
        return False
    if re.search(r"\b(?:сдаю|сдается|сдаем)\b.{0,60}\b(?:аренд\w*|прокат\w*)\b", description):
        return False
    if re.search(r"\bремонт\w*\b", text) and not re.search(r"\bбез\s+ремонт\w*\b", text):
        return False
    for accessory in _CONSOLE_ACCESSORY.finditer(text):
        # A console plus a controller/box/game is a bundle. A controller *for*
        # that console, or a title starting with a game name, is not a console.
        if primary and accessory.start() > primary.end():
            before_accessory = text[primary.end():accessory.start()]
            if re.search(r"\b(?:с|со|плюс|комплект\w*|подарок)\b", before_accessory):
                continue
        return False
    if primary:
        allowed_prefix = _MODEL_WORDS | _STOP_WORDS | {
            "продам", "продаю", "продажа", "срочно", "оригинальная", "запечатанная",
            "запечатанный", "новенькая", "идеальная", "идеальный", "original", "new",
        }
        if any(token not in allowed_prefix for token in text[:primary.start()].split()):
            return False
        for other in _PLAYSTATION_MODEL.finditer(text, primary.end()):
            if other.group(1) == primary.group(1):
                continue
            # Sony confirms PS5 can run many PS4 games; that compatibility
            # statement does not change the generation of the offered console.
            context = text[primary.end():other.start()]
            if not re.search(r"\b(?:игр\w*|совместим\w*|поддерж\w*)\b", context):
                return False
    return True


def _requirement_matches(expected: str, actual: str, *, final: bool = False, field: str = "") -> bool:
    if not expected:
        return True
    if not evidence_known(actual):
        # Missing structured data is resolved by the request-aware AI stage.
        return not final
    wanted = canonical_evidence(field, expected)
    available = canonical_evidence(field, actual)
    if not wanted or not available:
        return not final
    if wanted == available:
        return True
    if final and field in {"storage", "sim", "condition"}:
        return False
    wanted_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", wanted))
    available_numbers = set(re.findall(r"\d+(?:[.,]\d+)?", available))
    if wanted_numbers and not wanted_numbers.issubset(available_numbers):
        return False
    wanted_words = {
        token for token in wanted.split()
        if token not in _STOP_WORDS and not token.isdigit()
    }
    available_words = set(available.split())
    return not wanted_words or wanted_words.issubset(available_words)


def _literal_details_match(listing: NormalizedListing, expected: str) -> bool:
    """Accept only a literal source statement; never infer free-form requirements."""
    wanted = canonical_text(expected)
    if not evidence_known(expected):
        return False
    # This is deliberately conservative, not a semantic or diagnostic check.
    # AI request agreement remains mandatory in final ranking as well.
    anchors = {word[:5] for word in wanted.split() if len(word) >= 4 and word not in _STOP_WORDS}
    sources = [listing.description, *listing.analysis_parameters.values()]
    sentences = [canonical_text(sentence) for source in sources
                 for sentence in re.split(r"[.!?;]+", source) if sentence.strip()]
    doubtful = re.compile(r"\b(?:не|нет|без|отсутств\w*|неоригин\w*|неисправ\w*|поддель\w*|может|наверн\w*|кроме|исключени\w*)\b")
    confirmed = False
    for sentence in sentences:
        contains = f" {wanted} " in f" {sentence} "
        # Removing the requested phrase allows explicit negative requirements
        # such as 'без гнили', but not 'не без гнили' or uncertainty around it.
        context = sentence.replace(wanted, " ") if contains else sentence
        relevant = contains or bool(anchors & {word[:5] for word in sentence.split() if len(word) >= 4})
        if relevant and (doubtful.search(context) or not evidence_known(context or "known")):
            return False
        confirmed = confirmed or contains
    return confirmed


def storage_with_source_unit(listing: NormalizedListing, value: str) -> str:
    """Recover an omitted GB unit only when the source states that exact amount."""
    bare = value.strip().replace(",", ".")
    if not re.fullmatch(r"\d+(?:\.\d+)?", bare):
        return value
    source = canonical_evidence("storage", listing.storage)
    stated = re.fullmatch(r"(\d+(?:\.\d+)?) gb", source)
    if stated and Decimal(bare) == Decimal(stated.group(1)):
        return source
    return value


def matches_listing_request(
    listing: NormalizedListing,
    request: SearchRequest,
    *,
    identified_model: str = "",
    storage: str = "",
    sim_variant: str = "",
    condition: str = "",
    final: bool = False,
) -> bool:
    storage = storage_with_source_unit(listing, storage)
    if request.pickup_only and listing.delivery_required:
        return False
    if not matches_requested_city(listing, request, final=final):
        return False
    if not check_request_compatibility(listing, request).matches:
        return False
    total_price = listing.acquisition_price
    if total_price is None:
        return False
    if request.price_min is not None and total_price < request.price_min:
        return False
    if request.price_max is not None and total_price > request.price_max:
        return False

    model = (identified_model or listing.model).casefold()
    query = request.query.casefold()
    structured_model = listing.parameter("Модель")
    playstation_query = _playstation_identity(query)
    effective_model = identified_model or structured_model
    if playstation_query is not None:
        effective_model = effective_model_identity(listing, identified_model)
        if not effective_model and any(_playstation_identity(value) is not None for value in (
            structured_model, identified_model, listing.title,
        )):
            return False
        if evidence_known(structured_model) and evidence_conflicts("model", query, structured_model):
            return False
    elif evidence_known(structured_model) and not _model_matches(query, structured_model):
        return False
    if _title_model_conflicts(query, listing.title):
        return False
    if final and (not evidence_known(identified_model or structured_model) or not _model_matches(query, effective_model)):
        return False
    if final and any(
        evidence_conflicts(field, inferred, structured)
        for field, inferred, structured in (
            ("model", identified_model, listing.parameter("Модель")),
            ("storage", storage, listing.storage),
            ("sim", sim_variant, listing.sim_variant),
            ("condition", condition, listing.condition),
        )
    ):
        return False
    if "pro max" in query and "pro max" not in model:
        return False
    if " pro" in f" {query}" and "pro max" not in query and "pro max" in model:
        return False

    # All supported spellings use the same identity and product-type gate.
    # No description-wide generation regex: a real PS5 may mention PS4 games.
    if _PLAYSTATION_MODEL.search(canonical_evidence("model", query)):
        if not _console_product_matches(listing):
            return False
        if listing.price is not None and listing.price < 10_000:
            return False

    requirements = (
        (request.required_storage or " ".join(re.findall(r"\b\d+\s*(?:gb|tb)\b", canonical_text(query))), storage or listing.storage, "storage"),
        (request.required_sim, sim_variant or listing.sim_variant, "sim"),
        (request.required_condition, condition or listing.condition, "condition"),
    )
    if any(not _requirement_matches(expected, actual, final=final, field=field) for expected, actual, field in requirements):
        return False

    # Specifications removed from model identity remain actual requirements.
    paired_memory = re.search(r"\b(\d+)\s*/\s*(\d+)\s*(?:gb|гб)\b", query)
    if paired_memory and not _requirement_matches(
        paired_memory.group(1) + " GB", _parameter(listing, _ATTRIBUTE_PARAMETERS["ram"]),
        final=final, field="storage",
    ):
        return False
    screen = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(?:дюйм(?:а|ов|ы)?|inch(?:es)?|[\"″])", query)
    if screen and not _requirement_matches(
        screen.group(1) + " inch", _parameter(listing, _ATTRIBUTE_PARAMETERS["screen"]), final=final,
    ):
        return False

    for key, expected in request.attribute_map().items():
        if key == "details":
            if final and not _literal_details_match(listing, expected):
                return False
            continue
        names = _ATTRIBUTE_PARAMETERS.get(key)
        actual = _parameter(listing, names or (key,))
        if not _requirement_matches(expected, actual, final=final, field=key):
            return False
    return True


def matches_requested_city(
    listing: NormalizedListing, request: SearchRequest, *, final: bool = False
) -> bool:
    """Apply the locality gate independently so it can run before paid AI."""
    requested_location = request.location.casefold().strip()
    if requested_location in {"", "россия", "вся россия", "rossiya", "russia", "all"}:
        return True
    if not evidence_known(listing.location):
        return not final
    # Available delivery never broadens an explicitly local request.
    return _city_key(listing.location) == _city_key(request.location)


@dataclass(frozen=True, slots=True)
class RequestMismatchDiagnostic:
    matches: bool
    field: str = ""
    reason: str = ""


def diagnose_request_mismatch(
    listing: NormalizedListing,
    request: SearchRequest,
    *,
    identified_model: str = "",
    storage: str = "",
    sim_variant: str = "",
    condition: str = "",
    final: bool = False,
) -> RequestMismatchDiagnostic:
    """Explain the first stable request mismatch without returning seller text."""
    if matches_listing_request(
        listing, request, identified_model=identified_model, storage=storage,
        sim_variant=sim_variant, condition=condition, final=final,
    ):
        return RequestMismatchDiagnostic(True)
    if request.pickup_only and listing.delivery_required:
        return RequestMismatchDiagnostic(False, "pickup", "PICKUP_MISMATCH")
    if not matches_requested_city(listing, request, final=final):
        return RequestMismatchDiagnostic(False, "city", "LOCATION_MISMATCH")
    compatibility = check_request_compatibility(listing, request)
    if not compatibility.matches:
        return RequestMismatchDiagnostic(False, compatibility.field or "other",
                                         compatibility.reason or "REQUEST_FILTER_MISMATCH")
    total_price = listing.acquisition_price
    if (total_price is None
            or request.price_min is not None and total_price < request.price_min
            or request.price_max is not None and total_price > request.price_max):
        return RequestMismatchDiagnostic(False, "price", "PRICE_MISMATCH")
    for expected, actual, field in (
        (request.required_storage, storage or listing.storage, "storage"),
        (request.required_sim, sim_variant or listing.sim_variant, "other"),
        (request.required_condition, condition or listing.condition, "condition"),
    ):
        if expected and not _requirement_matches(expected, actual, final=final,
                                                 field="sim" if field == "other" else field):
            return RequestMismatchDiagnostic(False, field, "REQUEST_FILTER_MISMATCH")
    query = request.query.casefold()
    if _PLAYSTATION_MODEL.search(canonical_evidence("model", query)):
        if not _console_product_matches(listing):
            return RequestMismatchDiagnostic(False, "model", "REQUEST_FILTER_MISMATCH")
        return RequestMismatchDiagnostic(False, "variant", "REQUEST_FILTER_MISMATCH")
    return RequestMismatchDiagnostic(False, "model", "REQUEST_FILTER_MISMATCH")
