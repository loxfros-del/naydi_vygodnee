"""Deterministic price and condition truth extraction for marketplace listings."""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .matching import canonical_evidence, effective_model_identity


_LATIN_TO_CYRILLIC = str.maketrans({
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х",
    "y": "у", "k": "к", "m": "м", "t": "т", "b": "в", "h": "н",
})
_CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x",
    "у": "y", "к": "k", "м": "m", "т": "t", "в": "b", "н": "h",
})
_PRICE_NUMBER = re.compile(
    r"(?<![\w.,])(?P<number>\d{1,3}(?:[ .]\d{3})+|\d{2,6})(?!\d)"
    r"\s*(?P<unit>к\b|k\b|тыс(?:яч[аиу]?)?\b|₽|руб(?:\.|л(?:ей|я)?)?\b)?(?!\w)",
    re.IGNORECASE,
)
_NON_PRICE_IDENTIFIER = re.compile(
    r"\b[a-z]{1,4}[-‐‑‒–—−]\d+(?:[-‐‑‒–—−]\d+)+\b",
    re.IGNORECASE,
)
_WORD_TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}
_WORD_UNITS = {
    "одна": 1, "один": 1, "две": 2, "два": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
}


@dataclass(frozen=True, slots=True)
class PriceOffer:
    amount: int
    model: str = ""
    variant: str = ""
    storage: str = ""
    condition: str = ""
    is_from: bool = False
    evidence: str = ""

    def public_dict(self) -> dict[str, object]:
        return {
            "amount": self.amount,
            "model": self.model,
            "variant": self.variant,
            "storage": self.storage,
            "condition": self.condition,
            "isFrom": self.is_from,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class PriceTruth:
    advertised_price: int | None
    effective_price: int | None
    price_confidence: str
    model: str
    variant: str
    storage: str
    condition: str
    offers: tuple[PriceOffer, ...] = ()
    conflicts: tuple[str, ...] = ()
    payment_conditions: tuple[str, ...] = ()


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")


def _russian(value: str) -> str:
    return _normalized(value).translate(_LATIN_TO_CYRILLIC)


def _latin(value: str) -> str:
    return _normalized(value).translate(_CYRILLIC_TO_LATIN)


def _condition(value: str) -> str:
    raw, russian = _normalized(value), _russian(value)
    has_used = bool(re.search(r"\b(?:б\s*/?\s*у|бу|used|pre[- ]?owned)\b|бывш\w*\s+в\s+употреб", raw + " " + russian))
    has_new = bool(re.search(r"\b(?:нов(?:ый|ая|ое|ые)|new|brand\s+new)\b", raw + " " + russian))
    if has_used and has_new:
        return "ambiguous"
    if has_used:
        return "used"
    if has_new:
        return "new"
    canonical = canonical_evidence("condition", value)
    return canonical if canonical in {"new", "used", "excellent", "good"} else ""


def _identity(value: str) -> tuple[str, frozenset[str]]:
    raw, latin, russian = _normalized(value), _latin(value), _russian(value)
    combined = f"{raw} {latin} {russian}"
    generation = "5" if re.search(r"\b(?:play\s*station|ps|рс|пс)\s*5\b", combined) else ""
    if not generation and re.search(r"\b(?:play\s*station|ps|рс|пс)\s*[34]\b", combined):
        generation = "other"
    variants: set[str] = set()
    if re.search(r"\b(?:slim|слим)\b", combined):
        variants.add("slim")
    if re.search(r"\b(?:pro|про)\b", combined):
        variants.add("pro")
    if re.search(r"\b(?:fat|фат)\b", combined):
        variants.add("fat")
    if re.search(r"\b(?:digital|диджитал|дигитал|цифров\w*)\b|без\s+(?:дисковод|привод)", combined):
        variants.add("digital")
    if re.search(r"\b(?:disc|disk)\b|(?:с|with)\s+(?:дисковод\w*|привод\w*)|дисков\w*\s+верси", combined):
        variants.add("disc")
    return generation, frozenset(variants)


def _variant_label(variants: frozenset[str]) -> str:
    return " ".join(name for name in ("slim", "pro", "fat", "digital", "disc") if name in variants)


def _storage(value: str) -> str:
    canonical = canonical_evidence("storage", value)
    return canonical if re.fullmatch(r"\d+(?:\.\d+)?(?:\s+\d+(?:\.\d+)?)*\s+gb", canonical) else ""


def _word_prices(value: str) -> list[tuple[int, int, int]]:
    russian = _russian(value)
    results: list[tuple[int, int, int]] = []
    pattern = re.compile(
        r"(?P<tens>двадцать|тридцать|сорок|пятьдесят|шестьдесят|семьдесят|восемьдесят|девяносто)"
        r"(?:\s+(?P<unit>одна|один|две|два|три|четыре|пять|шесть|семь|восемь|девять))?"
        r"\s+тысяч\w*"
    )
    for match in pattern.finditer(russian):
        amount = (_WORD_TENS[match.group("tens")] + _WORD_UNITS.get(match.group("unit") or "", 0)) * 1000
        results.append((amount, match.start(), match.end()))
    return results


def _numeric_prices(value: str) -> list[tuple[int, int, int]]:
    results: list[tuple[int, int, int]] = []
    normalized = _normalized(value)
    identifier_spans = [match.span() for match in _NON_PRICE_IDENTIFIER.finditer(normalized)]
    for match in _PRICE_NUMBER.finditer(normalized):
        # A dash inside a compound error code is not the separator of a price
        # row: «PS5 (CE-108255-1)» must not override the advertised price.
        if any(start < match.end("number") and match.start("number") < end for start, end in identifier_spans):
            continue
        number = match.group("number")
        unit = (match.group("unit") or "").casefold()
        compact = number.replace(" ", "").replace(".", "")
        amount = int(compact)
        if unit in {"к", "k"} or unit.startswith("тыс"):
            amount *= 1000
        has_thousands_separator = "." in number or " " in number
        if not unit and not has_thousands_separator and amount < 10_000:
            continue
        if 10_000 <= amount <= 2_000_000:
            results.append((amount, match.start(), match.end()))
    return results


def _extract_offers(description: str) -> tuple[PriceOffer, ...]:
    lines = [" ".join(line.split()) for line in re.split(r"[\r\n]+", description or "") if line.strip()]
    offers: list[PriceOffer] = []
    seen: set[tuple[int, str]] = set()
    for index, line in enumerate(lines):
        prices = _numeric_prices(line) + _word_prices(line)
        if not prices:
            continue
        context = line
        if not _identity(line)[0] and not _condition(line) and index:
            previous = lines[index - 1]
            if _identity(previous)[0] or _condition(previous):
                context = f"{previous} {line}"
        generation, variants = _identity(context)
        storage = _storage(context)
        condition = _condition(context)
        # Numbers from prose (discounts, client counts, deposits, phone
        # numbers) are not a price table. A row must identify a SKU or be a
        # concise condition-specific row such as «Новая — от 65к».
        if (generation and len(context) > 220) or (not generation and not (condition and len(context) <= 100)):
            continue
        for amount, start, end in prices:
            # Skip storage/revision numbers accidentally adjacent to a real price.
            local = _normalized(line)[max(0, start - 16):min(len(line), end + 16)]
            if re.search(r"(?:gb|гб|tb|тб|rev|рев)\s*$", local[: max(0, 16)]):
                continue
            price_token = _normalized(line)[start:end]
            if not re.search(r"к\b|k\b|тыс|₽|руб", _russian(price_token)) and not re.search(
                r"(?:^|\s)от\s*[—–-]?\s*$|[—–-]\s*$", _russian(line[:start])
            ):
                continue
            is_from = bool(re.search(r"(?:^|\s)от\s*[—–-]?\s*$", _russian(line[:start])))
            key = (amount, context.casefold())
            if key in seen:
                continue
            seen.add(key)
            offers.append(PriceOffer(
                amount=amount,
                model=f"playstation {generation}" if generation else "",
                variant=_variant_label(variants),
                storage=storage,
                condition=condition,
                is_from=is_from,
                evidence=context[:300],
            ))
    return tuple(offers)


def _compatible(target: tuple[str, frozenset[str]], storage: str, condition: str, offer: PriceOffer) -> bool:
    offer_generation, offer_variants = _identity(f"{offer.model} {offer.variant}")
    if offer_generation and target[0] and offer_generation != target[0]:
        return False
    for family in ({"slim", "pro", "fat"}, {"digital", "disc"}):
        target_fact, offer_fact = target[1] & family, offer_variants & family
        if target_fact and offer_fact and target_fact != offer_fact:
            return False
    if storage and offer.storage and storage != offer.storage:
        return False
    if condition and offer.condition and condition != offer.condition:
        return False
    return True


def _description_condition(description: str) -> str:
    facts: set[str] = set()
    for part in re.split(r"[\r\n.!?;]+", description or ""):
        if not _identity(part)[0]:
            continue
        fact = _condition(part)
        if fact == "ambiguous":
            return "ambiguous"
        if fact:
            facts.add(fact)
    if "new" in facts and "used" in facts:
        return "ambiguous"
    return next(iter(facts), "")


def analyze_price_truth(listing: object) -> PriceTruth:
    """Resolve a listing card price to the advertised SKU without paid inference."""
    advertised = getattr(listing, "price", None)
    title = str(getattr(listing, "title", "") or "")
    model_value = str(getattr(listing, "model", "") or "")
    storage_value = str(getattr(listing, "storage", "") or "")
    condition_value = str(getattr(listing, "condition", "") or "")
    description = str(getattr(listing, "description", "") or "")

    model = effective_model_identity(listing) or canonical_evidence("model", f"{title} {model_value}")
    target = _identity(f"{title} {model_value}")
    storage = _storage(storage_value)
    structured_condition = _condition(condition_value)
    described_condition = _description_condition(description)
    condition = structured_condition or described_condition or "unknown"
    if structured_condition and described_condition and described_condition != structured_condition:
        condition = f"conflict:{structured_condition}_vs_{described_condition}"

    offers = _extract_offers(description)
    matching = [offer for offer in offers if _compatible(target, storage, structured_condition, offer)]
    conflicts: list[str] = []
    effective: int | None = None
    confidence = "invalid"

    distinct = {offer.amount for offer in matching}
    if len(distinct) == 1:
        chosen = max(matching, key=lambda item: sum(bool(value) for value in (
            item.model, item.variant, item.storage, item.condition,
        )))
        effective = chosen.amount
        _, offer_variants = _identity(f"{chosen.model} {chosen.variant}")
        missing_identity = bool(target[0] and not chosen.model) or bool(target[1] and not offer_variants)
        missing_identity = missing_identity or bool(storage and not chosen.storage) or bool(structured_condition and not chosen.condition)
        confidence = "likely" if chosen.is_from or missing_identity else "exact"
    elif len(distinct) > 1:
        confidence = "ambiguous"
    elif offers:
        confidence = "invalid"
    elif advertised is not None and advertised > 0:
        effective = advertised
        confidence = "exact"

    if offers and (effective is None or advertised != effective):
        conflicts.append("PRICE_VARIANT_MISMATCH")

    payments: list[str] = []
    payment_text = _russian(description)
    if re.search(r"\bза\s+наличн|\bналичн\w*\s+расчет", payment_text):
        payments.append("cash_price")
    if re.search(r"(?:карт|безналич)\w*", payment_text):
        payments.append("card_available")
    if re.search(r"кредит|рассроч", payment_text):
        payments.append("credit_or_installment")

    return PriceTruth(
        advertised_price=advertised,
        effective_price=effective,
        price_confidence=confidence,
        model=model,
        variant=_variant_label(target[1]),
        storage=storage,
        condition=condition,
        offers=offers,
        conflicts=tuple(conflicts),
        payment_conditions=tuple(payments),
    )
