"""One-shot upgrade for category-specific hard-fact extraction and query tokens."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NORMALIZATION = ROOT / "app" / "search_v2" / "normalization.py"
REQUEST_NORMALIZER = ROOT / "app" / "search_v2" / "request_normalizer.py"
TRIGGER = ROOT / ".ci" / "apply-search-v2-category-facts"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise RuntimeError(f"category-facts anchor not found: {label}")


def patch_normalization() -> None:
    text = NORMALIZATION.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''_REFRESH_RE = re.compile(r"(?<!\\d)(\\d{2,3})\\s*(?:гц|hz)(?!\\w)", re.I)
_RAM_SUFFIX_RE''',
        '''_REFRESH_RE = re.compile(r"(?<!\\d)(\\d{2,3})\\s*(?:гц|hz)(?!\\w)", re.I)
_LOAD_RE = re.compile(r"(?:нагрузк\\w*|до)\\D{0,16}(\\d{2,3})\\s*(?:кг|kg)", re.I)
_SIZE_RE = re.compile(r"(?:размер|size)\\s*[-:]?\\s*([abc])\\b", re.I)
_RAM_SUFFIX_RE''',
        "category regexes",
    )
    text = replace_once(
        text,
        '''def extract_features(value: Any) -> list[str]:
    text = normalize_key(value)
    features: list[str] = []
    if re.search(r"(?:\\banc\\b|активн\\w*\\s+шумоподав)", text, re.I):
        features.append("ANC")
    if re.search(r"\\btws\\b|true\\s+wireless", text, re.I):
        features.append("TWS")
    return features


def extract_cpu_family''',
        '''def extract_features(value: Any) -> list[str]:
    text = normalize_key(value)
    features: list[str] = []
    if re.search(r"(?:\\banc\\b|активн\\w*\\s+шумоподав)", text, re.I):
        features.append("ANC")
    if re.search(r"\\btws\\b|true\\s+wireless", text, re.I):
        features.append("TWS")
    if re.search(r"\\bwireless\\b|беспроводн", text, re.I):
        features.append("wireless")
    return features


def extract_category_facts(value: Any, category: str = "") -> dict[str, Any]:
    text = normalize_key(value)
    compact = text.replace(" ", "")
    facts: dict[str, Any] = {}

    for marker, canonical in (
        ("mini led", "Mini LED"),
        ("miniled", "Mini LED"),
        ("oled", "OLED"),
        ("qled", "QLED"),
        ("ips", "IPS"),
        (" va ", "VA"),
        (" tn ", "TN"),
    ):
        haystack = f" {text} " if marker.startswith(" ") else text
        if marker in haystack:
            facts["matrix"] = canonical
            break

    if any(marker in compact for marker in ("usb-c", "usbc", "usbtype-c", "type-c")):
        facts["connector"] = "USB-C"
    elif "lightning" in text:
        facts["connector"] = "Lightning"

    if re.search(r"\\bwireless\\b|беспроводн", text, re.I):
        facts["wireless"] = True
    if re.search(r"\\btws\\b|true\\s+wireless", text, re.I):
        facts["form_factor"] = "TWS"
    if re.search(r"ultra\\s*wide|ультраширок", text, re.I):
        facts["ultrawide"] = True

    gpu = re.search(r"\\b(?:rtx|gtx|rx)\\s*[- ]?\\d{3,4}(?:\\s*ti)?\\b", text, re.I)
    if gpu:
        facts["gpu"] = " ".join(gpu.group(0).upper().replace("-", " ").split())

    if str(category).casefold() == "chair" or any(marker in text for marker in ("кресло", "chair")):
        if re.search(r"эргономич|ergonomic", text, re.I):
            facts["type"] = "ergonomic"
        elif re.search(r"игров|gaming", text, re.I):
            facts["type"] = "gaming"
        elif re.search(r"офисн|office|компьютерн", text, re.I):
            facts["type"] = "office"
        if re.search(r"сетчат|mesh|сетка", text, re.I):
            facts["material"] = "mesh"
        if re.search(r"подголовник|headrest", text, re.I):
            facts["headrest"] = True
        if re.search(r"пояснич|lumbar", text, re.I):
            facts["lumbar_support"] = True
        load = _LOAD_RE.search(text)
        if load:
            facts["load_capacity_kg"] = int(load.group(1))
        size = _SIZE_RE.search(text)
        if size:
            facts["size"] = size.group(1).upper()

    return facts


def extract_cpu_family''',
        "category fact extractor",
    )
    text = replace_once(
        text,
        '''        "resolution",
        "features",
    ):''',
        '''        "resolution",
        "features",
        "matrix",
        "connector",
        "wireless",
        "form_factor",
        "ultrawide",
        "type",
        "material",
        "headrest",
        "lumbar_support",
        "load_capacity_kg",
    ):''',
        "metadata fact keys",
    )
    text = replace_once(
        text,
        '''    features = extract_features(evidence)
    cpu_family = extract_cpu_family(evidence)
    if ram_gb is not None:''',
        '''    features = extract_features(evidence)
    cpu_family = extract_cpu_family(evidence)
    category_facts = extract_category_facts(evidence, request.category)
    if ram_gb is not None:''',
        "build extracted facts",
    )
    text = replace_once(
        text,
        '''    if cpu_family:
        key_configuration.setdefault("cpu_family", cpu_family)

    identity_confidence''',
        '''    if cpu_family:
        key_configuration.setdefault("cpu_family", cpu_family)
    for key, value in category_facts.items():
        key_configuration.setdefault(key, value)

    identity_confidence''',
        "merge category facts",
    )
    text = replace_once(
        text,
        '''        size=str(metadata.get("size") or "") or None,''',
        '''        size=str(metadata.get("size") or key_configuration.get("size") or "") or None,''',
        "identity size",
    )
    text = replace_once(
        text,
        '''    "extract_cpu_family",
    "extract_features",''',
        '''    "extract_category_facts",
    "extract_cpu_family",
    "extract_features",''',
        "exports",
    )
    NORMALIZATION.write_text(text, encoding="utf-8")


def patch_request_normalizer() -> None:
    text = REQUEST_NORMALIZER.read_text(encoding="utf-8")
    old = '''def spec_token(key: str, value: Any) -> str:
    """Canonical human token used by both normalizer and planner validation."""
    if value in (None, "", False):
        return ""
    if key == "storage_gb":
        return f"{value} ГБ"
    if key == "ram_gb":
        return f"RAM {value} ГБ"
    if key == "ssd_gb":
        return f"SSD {value} ГБ"
    if key == "diagonal":
        return f"{value} дюймов"
    if key == "refresh_rate":
        return f"{value} Гц"
    if key == "size":
        return str(value).replace("х", "x").replace("×", "x")
    if isinstance(value, bool):
        return key.replace("_", " ") if value else ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_clean(item) for item in value if _clean(item))
    return _clean(value)
'''
    new = '''def spec_token(key: str, value: Any) -> str:
    """Canonical searchable token used by planner validation and source queries."""
    if value in (None, "", False):
        return ""
    key = str(key).casefold()
    if key == "storage_gb":
        return f"{value} ГБ"
    if key == "ram_gb":
        return f"RAM {value} ГБ"
    if key == "ssd_gb":
        return f"SSD {value} ГБ"
    if key == "diagonal":
        return f"{value} дюймов"
    if key == "refresh_rate":
        return f"{value} Гц"
    if key == "load_capacity_kg":
        return f"нагрузка {value} кг"
    if key == "size":
        return f"размер {str(value).replace('х', 'x').replace('×', 'x')}"
    if key == "type":
        return {"office": "офисное", "ergonomic": "эргономичное", "gaming": "игровое"}.get(str(value).casefold(), _clean(value))
    if key == "material":
        return {"mesh": "сетчатое"}.get(str(value).casefold(), _clean(value))
    if key in {"matrix", "resolution", "cpu", "cpu_family", "gpu", "connector", "form_factor"}:
        return _clean(value)
    if isinstance(value, bool):
        translations = {
            "anc": "ANC",
            "wireless": "беспроводные",
            "ultrawide": "ультраширокий",
            "headrest": "подголовник",
            "lumbar_support": "поясничная поддержка",
        }
        return translations.get(key, key.replace("_", " ")) if value else ""
    if isinstance(value, (list, tuple, set)):
        return " ".join(_clean(item) for item in value if _clean(item))
    return _clean(value)
'''
    text = replace_once(text, old, new, "spec_token")
    REQUEST_NORMALIZER.write_text(text, encoding="utf-8")


def main() -> int:
    patch_normalization()
    patch_request_normalizer()
    TRIGGER.unlink(missing_ok=True)
    SELF.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
