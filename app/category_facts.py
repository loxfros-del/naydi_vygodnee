"""Извлечение category-specific facts с evidence, без сети и DB."""
from __future__ import annotations

import re
from typing import Any, Callable

from app.category_registry import known_brand, normalize_text
from app.request_parser import parse_request_details


def _match(
    pattern: str,
    identity: str,
    supplemental: str,
    *,
    flags: int = re.IGNORECASE,
) -> tuple[re.Match[str] | None, str, str]:
    found = re.search(pattern, identity, flags)
    if found:
        return found, "title", "high"
    found = re.search(pattern, supplemental, flags)
    if found:
        return found, "page_text", "medium"
    return None, "", "none"


def _marker(
    markers: tuple[str, ...],
    identity: str,
    supplemental: str,
) -> tuple[bool, str, str]:
    identity_normalized = normalize_text(identity)
    supplemental_normalized = normalize_text(supplemental)
    for marker in markers:
        if normalize_text(marker) in identity_normalized:
            return True, "title", "high"
    for marker in markers:
        if normalize_text(marker) in supplemental_normalized:
            return True, "page_text", "medium"
    return False, "", "none"


def _put(
    facts: dict[str, Any],
    evidence: dict[str, dict[str, str]],
    key: str,
    value: Any,
    source: str,
    confidence: str,
) -> None:
    if value in (None, "", [], False):
        return
    facts[key] = value
    evidence[key] = {"confidence": confidence, "evidence": source}


def _first_marker_value(
    options: tuple[tuple[tuple[str, ...], str], ...],
    identity: str,
    supplemental: str,
) -> tuple[str, str, str]:
    for markers, value in options:
        found, source, confidence = _marker(markers, identity, supplemental)
        if found:
            return value, source, confidence
    return "", "", "none"


def _brand_model(
    category: str,
    identity: str,
    supplemental: str,
    facts: dict[str, Any],
    evidence: dict[str, dict[str, str]],
) -> None:
    brand = known_brand(category, identity)
    source = "title"
    confidence = "high"
    if not brand:
        brand = known_brand(category, supplemental)
        source = "page_text"
        confidence = "medium"
    _put(facts, evidence, "brand", brand, source, confidence)

    parsed = parse_request_details(identity)
    model = str(parsed.get("model") or "")
    if not model and brand:
        match, source, confidence = _match(
            rf"(?<![a-zа-я0-9]){re.escape(brand)}\s+([a-z0-9][a-z0-9-]*\d[a-z0-9-]*(?:\s+[a-z0-9-]+)?)",
            identity,
            supplemental,
        )
        if match:
            model = f"{brand} {match.group(1).upper()}"
    _put(facts, evidence, "model", model, "title" if parsed.get("model") else source if model else "", "high" if parsed.get("model") else confidence if model else "none")


def _common_capacities(
    identity: str,
    supplemental: str,
    facts: dict[str, Any],
    evidence: dict[str, dict[str, str]],
) -> None:
    ram, source, confidence = _match(
        r"(?:ram|озу|оператив(?:ная)?\s+память)\s*[:/-]?\s*(\d{1,3})\s*(?:гб|gb)|"
        r"(\d{1,3})\s*(?:гб|gb)\s*(?:ram|озу)",
        identity,
        supplemental,
    )
    if ram:
        _put(facts, evidence, "ram", f"{ram.group(1) or ram.group(2)} ГБ", source, confidence)
    ssd, source, confidence = _match(
        r"(?:ssd|накопитель)\s*[:/-]?\s*(\d{3,4}|[12]\s*(?:тб|tb))\s*(?:гб|gb|тб|tb)?|"
        r"(\d{3,4})\s*(?:гб|gb)\s*ssd",
        identity,
        supplemental,
    )
    if ssd:
        _put(facts, evidence, "ssd", (ssd.group(1) or ssd.group(2)).upper().replace(" ", " ") + (" ГБ" if (ssd.group(1) or ssd.group(2)).isdigit() else ""), source, confidence)


def _display_facts(
    identity: str,
    supplemental: str,
    facts: dict[str, Any],
    evidence: dict[str, dict[str, str]],
) -> None:
    diagonal, source, confidence = _match(
        r"(?<!\d)(\d{2,3}(?:[.,]\d)?)\s*(?:\"|”|″|дюйм(?:а|ов)?)",
        identity,
        supplemental,
    )
    if diagonal:
        _put(facts, evidence, "diagonal", diagonal.group(1).replace(",", "."), source, confidence)
    resolution, source, confidence = _first_marker_value((
        (("4k", "4к", "uhd", "ultra hd", "3840x2160", "3840 x 2160"), "4K"),
        (("qhd", "wqhd", "2560x1440", "2560 x 1440", "2k"), "QHD"),
        (("full hd", "fhd", "1080p", "1920x1080", "1920 x 1080"), "Full HD"),
    ), identity, supplemental)
    _put(facts, evidence, "resolution", resolution, source, confidence)
    refresh, source, confidence = _match(
        r"(?<!\d)(50|60|75|90|100|120|144|165|180|240|360)\s*(?:гц|hz)\b",
        identity,
        supplemental,
    )
    if refresh:
        _put(facts, evidence, "refresh_rate", f"{refresh.group(1)} Гц", source, confidence)
    panel, source, confidence = _first_marker_value((
        (("mini led", "mini-led"), "Mini LED"), (("oled",), "OLED"),
        (("qled",), "QLED"), (("ips",), "IPS"), (("va",), "VA"), (("tn",), "TN"),
    ), identity, supplemental)
    _put(facts, evidence, "panel", panel, source, confidence)


def _phone(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    storage, source, confidence = _match(r"(?<!\d)(64|128|256|512|1024)\s*(?:гб|gb|тб|tb)\b", identity, supplemental)
    if storage:
        value = int(storage.group(1))
        _put(facts, evidence, "storage_gb", value, source, confidence)
        _put(facts, evidence, "storage", f"{value} ГБ", source, confidence)
        facts["memory"] = facts["storage"]
    _common_capacities(identity, supplemental, facts, evidence)
    color, source, confidence = _first_marker_value((
        (("черный", "чёрный", "black", "midnight"), "черный"),
        (("белый", "white", "starlight"), "белый"), (("синий", "blue"), "синий"),
        (("зеленый", "зелёный", "green"), "зеленый"), (("красный", "red"), "красный"),
    ), identity, supplemental)
    _put(facts, evidence, "color", color, source, confidence)
    condition, source, confidence = _first_marker_value((
        (("б/у", "бу ", "used", "подержан", "восстанов"), "б/у"),
        (("новый", "new"), "новый"),
    ), identity, supplemental)
    _put(facts, evidence, "condition", condition, source, confidence)
    sim, source, confidence = _first_marker_value((
        (("dual sim", "2 sim", "две sim"), "Dual SIM"), (("esim",), "eSIM"),
    ), identity, supplemental)
    _put(facts, evidence, "sim_variant", sim, source, confidence)


def _laptop(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    cpu, source, confidence = _match(
        r"\b((?:intel\s+)?(?:core\s+)?i[3579](?:[- ]?\d{3,5}[a-z]*)?|"
        r"(?:amd\s+)?ryzen\s*[3579](?:\s+\d{3,5}[a-z]*)?|n95|n100|n150|n5095|celeron|pentium\s+silver)\b",
        identity,
        supplemental,
    )
    if cpu:
        _put(facts, evidence, "cpu", re.sub(r"\s+", " ", cpu.group(1)).upper(), source, confidence)
    _common_capacities(identity, supplemental, facts, evidence)
    screen, source, confidence = _match(r"(?<!\d)(13|14|15[.,]6|16|17[.,]3)\s*(?:\"|дюйм|inch)?", identity, supplemental)
    if screen:
        _put(facts, evidence, "screen", screen.group(1).replace(",", "."), source, confidence)
    _display_facts(identity, supplemental, facts, evidence)
    os_name, source, confidence = _first_marker_value((
        (("windows 11",), "Windows 11"), (("windows 10",), "Windows 10"),
        (("macos", "mac os"), "macOS"), (("без ос", "no os", "dos"), "без ОС"),
    ), identity, supplemental)
    _put(facts, evidence, "os", os_name, source, confidence)
    gpu, source, confidence = _match(r"\b((?:rtx|gtx)\s*\d{3,4}|radeon\s+[a-z0-9 -]+|iris\s+xe)\b", identity, supplemental)
    if gpu:
        _put(facts, evidence, "gpu", re.sub(r"\s+", " ", gpu.group(1)).upper(), source, confidence)


def _tv(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    _display_facts(identity, supplemental, facts, evidence)
    hdmi, source, confidence = _first_marker_value(((("hdmi 2.1", "hdmi2.1"), "HDMI 2.1"), (("hdmi",), "HDMI")), identity, supplemental)
    _put(facts, evidence, "hdmi", hdmi, source, confidence)
    platform, source, confidence = _first_marker_value((
        (("google tv",), "Google TV"), (("android tv",), "Android TV"),
        (("tizen",), "Tizen"), (("webos", "web os"), "webOS"),
        (("салют",), "Салют ТВ"), (("yaos", "яндекс тв"), "YaOS"),
    ), identity, supplemental)
    _put(facts, evidence, "smart_platform", platform, source, confidence)
    if "panel" in facts:
        facts["matrix_type"] = facts["panel"]


def _monitor(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    _display_facts(identity, supplemental, facts, evidence)
    response, source, confidence = _match(r"(?<!\d)([1-9])\s*(?:мс|ms)\b", identity, supplemental)
    if response:
        _put(facts, evidence, "response_time", f"{response.group(1)} мс", source, confidence)
    sync, source, confidence = _first_marker_value((
        (("g-sync", "gsync"), "G-Sync"), (("freesync",), "FreeSync"), (("adaptive sync",), "Adaptive Sync"),
    ), identity, supplemental)
    _put(facts, evidence, "adaptive_sync", sync, source, confidence)


def _headphones(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    connection, source, confidence = _first_marker_value((
        (("tws",), "TWS"), (("беспровод", "bluetooth", "wireless"), "wireless"),
        (("проводн", "wired", "3.5", "3,5"), "wired"),
    ), identity, supplemental)
    _put(facts, evidence, "connection", connection, source, confidence)
    if connection:
        facts["headphone_type"] = connection
    anc, source, confidence = _marker(("anc", "шумоподав", "noise cancelling"), identity, supplemental)
    _put(facts, evidence, "anc", anc, source, confidence)
    form, source, confidence = _first_marker_value((
        (("полноразмер", "over-ear"), "over-ear"), (("накладн", "on-ear"), "on-ear"),
        (("внутриканаль", "in-ear"), "in-ear"), (("вкладыши", "earbuds"), "earbuds"),
    ), identity, supplemental)
    _put(facts, evidence, "form_factor", form, source, confidence)


def _chair(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    lumbar, source, confidence = _marker(("пояснич", "lumbar"), identity, supplemental)
    _put(facts, evidence, "lumbar_support", lumbar, source, confidence)
    headrest, source, confidence = _marker(("подголовник", "headrest"), identity, supplemental)
    _put(facts, evidence, "headrest", headrest, source, confidence)
    adjustments: list[str] = []
    for markers, label in ((("регулировка высоты", "регулируемая высота"), "высота"), (("регулируемые подлокотники", "регулировка подлокотников"), "подлокотники"), (("регулировка наклона",), "наклон")):
        found, source, confidence = _marker(markers, identity, supplemental)
        if found:
            adjustments.append(label)
            evidence["adjustments"] = {"confidence": confidence, "evidence": source}
    if adjustments:
        facts["adjustments"] = adjustments
    material, source, confidence = _first_marker_value(((("сетка", "сетчат"), "сетка"), (("экокожа",), "экокожа"), (("ткань",), "ткань")), identity, supplemental)
    _put(facts, evidence, "material", material, source, confidence)
    mechanism, source, confidence = _first_marker_value(((("мультиблок",), "мультиблок"), (("топ-ган", "top gun"), "топ-ган"), (("механизм качания",), "качание")), identity, supplemental)
    _put(facts, evidence, "mechanism", mechanism, source, confidence)
    load, source, confidence = _match(r"(?<!\d)(\d{2,3})\s*(?:кг|kg)\b", identity, supplemental)
    if load:
        _put(facts, evidence, "load_capacity", f"{load.group(1)} кг", source, confidence)


def _robot(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    wet, source, confidence = _marker(("влажн", "моющ"), identity, supplemental)
    _put(facts, evidence, "wet_cleaning", wet, source, confidence)
    lidar, source, confidence = _marker(("лидар", "lidar", "laser navigation"), identity, supplemental)
    _put(facts, evidence, "lidar", lidar, source, confidence)
    if lidar:
        _put(facts, evidence, "navigation", "lidar", source, confidence)
    suction, source, confidence = _match(r"(?<!\d)(\d{3,5})\s*(?:па|pa)\b", identity, supplemental)
    if suction:
        _put(facts, evidence, "suction", f"{suction.group(1)} Па", source, confidence)
    station, source, confidence = _marker(("самоочист", "self-empty", "станция самоочистки"), identity, supplemental)
    _put(facts, evidence, "self_empty_station", station, source, confidence)
    mapping, source, confidence = _marker(("карта помещения", "mapping", "построение карты"), identity, supplemental)
    _put(facts, evidence, "mapping", mapping, source, confidence)


def _vacuum(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    vacuum_type, source, confidence = _first_marker_value((
        (("вертикальн",), "вертикальный"), (("ручной",), "ручной"), (("моющий",), "моющий"), (("контейнерный",), "контейнерный"),
    ), identity, supplemental)
    _put(facts, evidence, "type", vacuum_type, source, confidence)
    power, source, confidence = _match(r"(?<!\d)(\d{2,4})\s*(?:вт|w)\b", identity, supplemental)
    if power:
        _put(facts, evidence, "power", f"{power.group(1)} Вт", source, confidence)
    suction, source, confidence = _match(r"(?<!\d)(\d{3,5})\s*(?:па|pa)\b", identity, supplemental)
    if suction:
        _put(facts, evidence, "suction", f"{suction.group(1)} Па", source, confidence)
    battery, source, confidence = _match(r"(?<!\d)(\d{2,3})\s*(?:мин|minutes?)\b", identity, supplemental)
    if battery:
        _put(facts, evidence, "battery", f"{battery.group(1)} мин", source, confidence)
    wet, source, confidence = _marker(("влажн", "моющ"), identity, supplemental)
    _put(facts, evidence, "wet_cleaning", wet, source, confidence)
    attachments: list[str] = []
    for marker, label in (("турбощет", "турбощётка"), ("щелевая", "щелевая"), ("для мебели", "для мебели")):
        found, source, confidence = _marker((marker,), identity, supplemental)
        if found:
            attachments.append(label)
            evidence["attachments"] = {"confidence": confidence, "evidence": source}
    if attachments:
        facts["attachments"] = attachments


def _microwave(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    volume, source, confidence = _match(r"(?<!\d)(\d{1,2})\s*(?:л|литр(?:а|ов)?)\b", identity, supplemental)
    if volume:
        _put(facts, evidence, "volume", f"{volume.group(1)} л", source, confidence)
    power, source, confidence = _match(r"(?<!\d)(\d{3,4})\s*(?:вт|w)\b", identity, supplemental)
    if power:
        _put(facts, evidence, "power", f"{power.group(1)} Вт", source, confidence)
    for key, markers in (("grill", ("гриль", "grill")), ("inverter", ("инвертор", "inverter"))):
        found, source, confidence = _marker(markers, identity, supplemental)
        _put(facts, evidence, key, found, source, confidence)
    controls, source, confidence = _first_marker_value(((("сенсор",), "сенсорное"), (("поворотные", "механическое"), "механическое"), (("кнопочное",), "кнопочное")), identity, supplemental)
    _put(facts, evidence, "controls", controls, source, confidence)


def _coffee(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    machine_type, source, confidence = _first_marker_value((
        (("автоматическ",), "automatic"), (("рожков", "espresso"), "espresso"), (("капсульн",), "capsule"),
    ), identity, supplemental)
    _put(facts, evidence, "machine_type", machine_type, source, confidence)
    capp, source, confidence = _marker(("капучинатор", "milk system", "молочн"), identity, supplemental)
    _put(facts, evidence, "cappuccinator", capp, source, confidence)
    pressure, source, confidence = _match(r"(?<!\d)(\d{1,2})\s*(?:бар|bar)\b", identity, supplemental)
    if pressure:
        _put(facts, evidence, "pressure", f"{pressure.group(1)} бар", source, confidence)
    grinder, source, confidence = _marker(("встроенная кофемолка", "встроенной кофемолкой", "grinder"), identity, supplemental)
    _put(facts, evidence, "grinder", grinder, source, confidence)
    milk, source, confidence = _marker(("молочная система", "milk system", "lattego"), identity, supplemental)
    _put(facts, evidence, "milk_system", milk, source, confidence)


def _mattress(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    size, source, confidence = _match(r"(?<!\d)(\d{2,3})\s*[xх×]\s*(\d{2,3})(?!\d)", identity, supplemental)
    if size:
        _put(facts, evidence, "size", f"{size.group(1)}x{size.group(2)}", source, confidence)
    firmness, source, confidence = _first_marker_value((
        (("средней жесткости", "средней жёсткости", "среднежест"), "средняя"),
        (("жесткий", "жёсткий"), "жёсткий"), (("мягкий",), "мягкий"),
    ), identity, supplemental)
    _put(facts, evidence, "firmness", firmness, source, confidence)
    spring, source, confidence = _first_marker_value(((("независимые пружины", "pocket spring"), "независимые пружины"), (("боннель",), "боннель"), (("беспружин",), "беспружинный")), identity, supplemental)
    _put(facts, evidence, "spring_type", spring, source, confidence)
    height, source, confidence = _match(r"(?:высота|толщина)\s*(\d{1,2})\s*см", identity, supplemental)
    if height:
        _put(facts, evidence, "height", f"{height.group(1)} см", source, confidence)
    load, source, confidence = _match(r"(?:нагрузка|на\s+место)\D{0,10}(\d{2,3})\s*кг", identity, supplemental)
    if load:
        _put(facts, evidence, "load_per_bed", f"{load.group(1)} кг", source, confidence)
    materials: list[str] = []
    for marker, label in (("кокос", "кокос"), ("латекс", "латекс"), ("пена", "пена"), ("memory", "memory foam")):
        found, source, confidence = _marker((marker,), identity, supplemental)
        if found:
            materials.append(label)
            evidence["materials"] = {"confidence": confidence, "evidence": source}
    if materials:
        facts["materials"] = materials


def _bed(identity: str, supplemental: str, facts: dict[str, Any], evidence: dict[str, dict[str, str]]) -> None:
    size, source, confidence = _match(r"(?<!\d)(\d{2,3})\s*[xх×]\s*(\d{2,3})(?!\d)", identity, supplemental)
    if size:
        _put(facts, evidence, "size", f"{size.group(1)}x{size.group(2)}", source, confidence)
    material, source, confidence = _first_marker_value(((("массив дерева", "деревян"), "дерево"), (("металл",), "металл"), (("лдсп",), "ЛДСП"), (("мдф",), "МДФ")), identity, supplemental)
    _put(facts, evidence, "material", material, source, confidence)
    lift, source, confidence = _marker(("подъемн", "подъёмн"), identity, supplemental)
    _put(facts, evidence, "lift_mechanism", lift, source, confidence)
    base, source, confidence = _first_marker_value(((("ортопедическое основание",), "ортопедическое"), (("ламели", "реечное основание"), "ламели"), (("основание в комплекте",), "в комплекте")), identity, supplemental)
    _put(facts, evidence, "base", base, source, confidence)
    included, source, confidence = _marker(("матрас в комплекте", "с матрасом"), identity, supplemental)
    _put(facts, evidence, "mattress_included", included, source, confidence)
    storage, source, confidence = _marker(("ящик для белья", "ящики для хранения", "ниша для хранения"), identity, supplemental)
    _put(facts, evidence, "storage", storage, source, confidence)


_EXTRACTORS: dict[str, Callable[[str, str, dict[str, Any], dict[str, dict[str, str]]], None]] = {
    "phone": _phone,
    "laptop": _laptop,
    "tv": _tv,
    "headphones": _headphones,
    "chair": _chair,
    "monitor": _monitor,
    "robot_vacuum": _robot,
    "vacuum": _vacuum,
    "microwave": _microwave,
    "coffee_machine": _coffee,
    "mattress": _mattress,
    "bed": _bed,
}


def extract_category_facts(
    category: str,
    identity_text: str,
    supplemental_text: str = "",
) -> dict[str, Any]:
    """Возвращает только поля выбранной категории и per-fact evidence."""
    facts: dict[str, Any] = {"category": category}
    evidence: dict[str, dict[str, str]] = {}
    identity = re.sub(r"\s+", " ", identity_text or "").strip()
    supplemental = re.sub(r"\s+", " ", supplemental_text or "").strip()
    _brand_model(category, identity, supplemental, facts, evidence)
    extractor = _EXTRACTORS.get(category)
    if extractor:
        extractor(identity, supplemental, facts, evidence)
    facts["fact_evidence"] = evidence
    return facts
