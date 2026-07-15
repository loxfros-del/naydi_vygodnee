"""One-shot upgrade for Search V2 structured requirement extraction."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "app" / "search_v2" / "normalization.py"
TRIGGER = ROOT / ".ci" / "apply-search-v2-normalization-upgrade"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"Normalization upgrade anchor not found: {label}")


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '''    VerificationAccess,
)


_SPACE_RE''',
        '''    VerificationAccess,
)
from .request_semantics import is_generic_request


_SPACE_RE''',
        "request semantics import",
    )

    text = replace_once(
        text,
        '''_SPACE_RE = re.compile(r"\\s+")
_STORAGE_RE = re.compile(r"(?<!\\d)(\\d+(?:[.,]\\d+)?)\\s*(тб|tb|гб|gb)(?!\\w)", re.I)
_DIAGONAL_RE = re.compile(r"(?<!\\d)(\\d{2}(?:[.,]\\d)?)\\s*(?:[\"″]|дюйм|inch)", re.I)
_REFRESH_RE = re.compile(r"(?<!\\d)(\\d{2,3})\\s*(?:гц|hz)(?!\\w)", re.I)
_RAM_RE = re.compile(r"(?<!\\d)(\\d{1,3})\\s*(?:гб|gb)\\s*(?:ram|озу)", re.I)
_SLASH_CONFIG_RE = re.compile(r"(?<!\\d)(\\d{1,3})\\s*/\\s*(\\d{3,4})(?:\\s*(?:гб|gb))?(?!\\d)", re.I)''',
        '''_SPACE_RE = re.compile(r"\\s+")
_STORAGE_RE = re.compile(r"(?<!\\d)(\\d+(?:[.,]\\d+)?)\\s*(тб|tb|гб|gb)(?!\\w)", re.I)
_DIAGONAL_RE = re.compile(r"(?<!\\d)(\\d{2}(?:[.,]\\d)?)\\s*(?:[\"″]|дюйм|inch)", re.I)
_REFRESH_RE = re.compile(r"(?<!\\d)(\\d{2,3})\\s*(?:гц|hz)(?!\\w)", re.I)
_RAM_RE = re.compile(r"(?<!\\d)(\\d{1,3})\\s*(?:гб|gb)\\s*(?:ram|озу)", re.I)
_RAM_PREFIX_RE = re.compile(r"(?:ram|озу)\\s*[-:]?\\s*(\\d{1,3})\\s*(?:гб|gb)?", re.I)
_SSD_PREFIX_RE = re.compile(r"\\bssd\\s*[-:]?\\s*(\\d{3,4})\\s*(?:гб|gb)?", re.I)
_SSD_SUFFIX_RE = re.compile(r"(?<!\\d)(\\d{3,4})\\s*(?:гб|gb)?\\s*ssd\\b", re.I)
_SLASH_CONFIG_RE = re.compile(r"(?<!\\d)(\\d{1,3})\\s*/\\s*(\\d{3,4})(?:\\s*(?:гб|gb))?(?!\\d)", re.I)''',
        "spec regexes",
    )

    text = replace_once(
        text,
        '''def extract_storage(value: Any) -> int | None:
    normalized = normalize_text(value)
    matches = _STORAGE_RE.findall(normalized)
    if not matches:
        slash = _SLASH_CONFIG_RE.search(normalized)
        return int(slash.group(2)) if slash else None
    values: list[int] = []
    for number, unit in matches:
        parsed = float(number.replace(",", "."))
        if unit.casefold() in {"тб", "tb"}:
            parsed *= 1024
        values.append(int(parsed))
    # Storage is normally the largest capacity mentioned; RAM is filtered below.
    ram = _RAM_RE.search(normalize_text(value))
    ram_value = int(ram.group(1)) if ram else None
    candidates = [item for item in values if item != ram_value or len(values) == 1]
    return max(candidates or values)
''',
        '''def extract_ram(value: Any) -> int | None:
    normalized = normalize_text(value)
    match = _RAM_RE.search(normalized) or _RAM_PREFIX_RE.search(normalized)
    if match:
        return int(match.group(1))
    slash = _SLASH_CONFIG_RE.search(normalized)
    return int(slash.group(1)) if slash else None


def extract_storage(value: Any) -> int | None:
    normalized = normalize_text(value)
    explicit = _SSD_PREFIX_RE.search(normalized) or _SSD_SUFFIX_RE.search(normalized)
    if explicit:
        return int(explicit.group(1))
    slash = _SLASH_CONFIG_RE.search(normalized)
    if slash:
        return int(slash.group(2))
    matches = _STORAGE_RE.findall(normalized)
    if not matches:
        return None
    values: list[int] = []
    for number, unit in matches:
        parsed = float(number.replace(",", "."))
        if unit.casefold() in {"тб", "tb"}:
            parsed *= 1024
        values.append(int(parsed))
    # Storage is normally the largest capacity mentioned; RAM is filtered below.
    ram_value = extract_ram(normalized)
    candidates = [item for item in values if item != ram_value or len(values) == 1]
    return max(candidates or values)


def extract_resolution(value: Any) -> str | None:
    text = normalize_key(value).replace(" ", "")
    if any(marker in text for marker in ("3840x2160", "2160p", "4k", "uhd")):
        return "4K"
    if any(marker in text for marker in ("2560x1440", "1440p", "qhd", "2k")):
        return "QHD"
    if any(marker in text for marker in ("1920x1080", "1080p", "fullhd", "fhd")):
        return "FHD"
    return None


def extract_features(value: Any) -> list[str]:
    text = normalize_key(value)
    features: list[str] = []
    if re.search(r"(?:\\banc\\b|активн\\w*\\s+шумоподав)", text, re.I):
        features.append("ANC")
    if re.search(r"\\btws\\b|true\\s+wireless", text, re.I):
        features.append("TWS")
    return features


def extract_cpu_family(value: Any) -> str | None:
    text = normalize_key(value)
    match = re.search(r"\\b(?:amd\\s+)?(ryzen\\s+[3579])\\b", text, re.I)
    if match:
        return " ".join(match.group(1).split()).title()
    match = re.search(r"\\b(?:intel\\s+)?(?:core\\s+)?(i[3579])\\b", text, re.I)
    return match.group(1).upper() if match else None


def _canonical_model_from_title(value: Any) -> str:
    title = normalize_text(value)
    candidate = re.split(
        r"\\s+(?:купить|отзывы?|характеристики|вопросы?|подробное\\s+описание)\\b",
        title,
        maxsplit=1,
        flags=re.I,
    )[0]
    return candidate.strip(" -–—")[:180] or title[:180]
''',
        "structured extractors",
    )

    text = replace_once(
        text,
        '''def build_product_identity(raw: RawOffer, request: SearchRequestV2) -> ProductIdentity:
    metadata = _metadata(raw)
    title = normalize_text(raw.title)
    lowered = title.casefold()
    requested_model = normalize_text(request.canonical_model)
    canonical_model = requested_model if requested_model and normalize_key(requested_model) in lowered else str(
        metadata.get("canonical_model") or metadata.get("model") or title
    )
    storage = metadata.get("storage_gb", metadata.get("storage"))
    storage = extract_storage(storage) if storage not in (None, "") else extract_storage(title)
    diagonal_match = _DIAGONAL_RE.search(title)
    refresh_match = _REFRESH_RE.search(title)
    diagonal = metadata.get("diagonal")
    if diagonal in (None, "") and diagonal_match:
        diagonal = float(diagonal_match.group(1).replace(",", "."))
    refresh = metadata.get("refresh_rate", metadata.get("hz"))
    if refresh in (None, "") and refresh_match:
        refresh = int(refresh_match.group(1))
    modifiers = extract_modifiers(title)
    condition = normalize_condition(raw.condition, title)
    key_configuration = dict(metadata.get("structured_facts") or {})
    for key in ("ram", "cpu", "gpu", "color", "size"):
        if key in metadata and key not in key_configuration:
            key_configuration[key] = metadata[key]
    model_matched = bool(requested_model and normalize_key(requested_model) in lowered)
    identity = ProductIdentity(
        category=request.category or str(metadata.get("category") or "unknown"),
        brand=request.brand if request.brand and (normalize_key(request.brand) in lowered or model_matched) else str(metadata.get("brand") or ""),
        canonical_model=canonical_model,
        modifiers=modifiers,
        storage=storage,
        size=str(metadata.get("size") or "") or None,
        diagonal=diagonal,
        refresh_rate=refresh,
        key_configuration=key_configuration,
        condition=condition,
        region_or_sim_variant=str(metadata.get("region_or_sim_variant") or metadata.get("sim_variant") or ""),
        identity_confidence=0.9 if requested_model and normalize_key(requested_model) in lowered else 0.55,
    )
    from .grouping import canonical_identity_key

    identity.canonical_key = canonical_identity_key(identity)
    return identity
''',
        '''def build_product_identity(raw: RawOffer, request: SearchRequestV2) -> ProductIdentity:
    metadata = _metadata(raw)
    title = normalize_text(raw.title)
    evidence = normalize_text(
        " ".join(
            str(item or "")
            for item in (
                raw.title,
                getattr(raw, "snippet", ""),
                metadata.get("body"),
                metadata.get("description"),
            )
        )
    )
    lowered = evidence.casefold()
    requested_model = normalize_text(request.canonical_model)
    generic_request = is_generic_request(request)
    model_matched = bool(
        requested_model
        and not generic_request
        and normalize_key(requested_model) in lowered
    )
    canonical_model = (
        requested_model
        if model_matched
        else str(metadata.get("canonical_model") or metadata.get("model") or _canonical_model_from_title(title))
    )
    storage = metadata.get("storage_gb", metadata.get("storage"))
    storage = extract_storage(storage) if storage not in (None, "") else extract_storage(evidence)
    diagonal_match = _DIAGONAL_RE.search(evidence)
    refresh_match = _REFRESH_RE.search(evidence)
    diagonal = metadata.get("diagonal")
    if diagonal in (None, "") and diagonal_match:
        diagonal = float(diagonal_match.group(1).replace(",", "."))
    refresh = metadata.get("refresh_rate", metadata.get("hz"))
    if refresh in (None, "") and refresh_match:
        refresh = int(refresh_match.group(1))
    modifiers = extract_modifiers(evidence)
    condition = normalize_condition(raw.condition, evidence)
    key_configuration = dict(metadata.get("structured_facts") or {})
    for key in ("ram", "ram_gb", "ssd_gb", "cpu", "cpu_family", "gpu", "color", "size", "resolution", "features"):
        if key in metadata and key not in key_configuration:
            key_configuration[key] = metadata[key]
    ram_gb = extract_ram(evidence)
    ssd_gb = extract_storage(evidence)
    resolution = extract_resolution(evidence)
    features = extract_features(evidence)
    cpu_family = extract_cpu_family(evidence)
    if ram_gb is not None:
        key_configuration.setdefault("ram_gb", ram_gb)
    if ssd_gb is not None:
        key_configuration.setdefault("ssd_gb", ssd_gb)
    if resolution:
        key_configuration.setdefault("resolution", resolution)
    if features:
        current_features = key_configuration.get("features") or []
        if not isinstance(current_features, (list, tuple, set, frozenset)):
            current_features = [current_features]
        key_configuration["features"] = list(dict.fromkeys([*current_features, *features]))
    if cpu_family:
        key_configuration.setdefault("cpu_family", cpu_family)
    identity_confidence = 0.9 if model_matched else (0.7 if metadata.get("model") or metadata.get("canonical_model") else 0.6)
    identity = ProductIdentity(
        category=request.category or str(metadata.get("category") or "unknown"),
        brand=request.brand if request.brand and (normalize_key(request.brand) in lowered or model_matched) else str(metadata.get("brand") or ""),
        canonical_model=canonical_model,
        modifiers=modifiers,
        storage=storage,
        size=str(metadata.get("size") or "") or None,
        diagonal=diagonal,
        refresh_rate=refresh,
        key_configuration=key_configuration,
        condition=condition,
        region_or_sim_variant=str(metadata.get("region_or_sim_variant") or metadata.get("sim_variant") or ""),
        identity_confidence=identity_confidence,
    )
    from .grouping import canonical_identity_key

    identity.canonical_key = canonical_identity_key(identity)
    return identity
''',
        "product identity",
    )

    TARGET.write_text(text, encoding="utf-8")
    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
