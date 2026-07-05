"""Сервис для сценария «Проверить через Алису» — промпт, парсинг, черновик."""
import re
from app.db import Request
from app.ai_cards_service import parse_ai_cards_json
from app.price_extractor import format_price

# ──────────────────────────────────────────────
#  Известные магазины (для приоритета)
# ──────────────────────────────────────────────
_KNOWN_STORES = [
    "Wildberries", "Ozon", "Яндекс.Маркет", "Яндекс Маркет", "DNS", "М.Видео",
    "Ситилинк", "Эльдорадо", "Авито", "Мегамаркет", "СберМегаМаркет", "AliExpress",
    "Lamoda", "KazanExpress", "Холодильник.ру", "ВсеИнструменты", "Петрович",
    "Леруа Мерлен", "OBI", "Castorama", "220 Вольт", "ОнлайнТрейд",
    "Регард", "Никс", "Oldi",
]


# ──────────────────────────────────────────────
#  Промпт для Алисы
# ──────────────────────────────────────────────

def build_alice_prompt(req: Request) -> str:
    """Формирует простой и рабочий промпт для Алисы / GigaChat."""
    product = req.product_name or req.product or "товар"
    budget_num = int(req.budget) if req.budget and req.budget.isdigit() else None
    budget_str = format_price(budget_num) if budget_num else req.budget

    city = req.city or ""
    use_case = req.use_case or req.purpose or ""
    criteria = req.important_criteria or req.criteria or ""
    is_used = req.is_used_allowed

    lines = [f"Найди выгодные варианты: {product}"]

    if use_case:
        lines[0] += f" {use_case}"

    if budget_str:
        lines[0] += f" до {budget_str}"

    if city:
        lines[0] += f" в {city}"

    lines[0] += "."

    lines.append("")
    lines.append("Дай только конкретные модели, не категории и не подборки.")
    lines.append("")
    lines.append("Ответь строгим JSON без Markdown и без текста вокруг:")
    lines.append('{"cards":[{"role":"BEST","title":"","price":0,"store":"","url":"","why":"","risks":[],"manual_check":[],"confidence":"medium"}]}')
    lines.append("")
    lines.append("Допустимые role: BEST, BACKUP, BUDGET, CAUTION, REJECTED.")
    lines.append("")
    lines.append("Требования:")
    lines.append("• Дай 3–5 вариантов для проверки админом.")
    if not is_used:
        lines.append("• Не предлагай б/у, если клиент просит новое.")
    lines.append("• Не предлагай товары сильно выше бюджета и не ставь BEST выше бюджета.")
    if budget_num:
        lower_bound = int(budget_num * 0.8)
        lines.append(
            f"• Если бюджет высокий, добавь варианты ближе к верхней границе бюджета "
            f"(примерно {format_price(lower_bound)}–{format_price(budget_num)})."
        )
    lines.append("• Если точной ссылки нет — оставь url пустым, но не выдумывай её.")
    lines.append("• Не выдумывай цены.")
    lines.append("• Не пиши длинный вывод в конце.")
    if criteria:
        lines.append(f"• Учти критерии клиента: {criteria}")

    if req.original_query and len(req.original_query) > 5:
        lines.append(f"\nКонтекст от клиента: {req.original_query[:300]}")

    return "\n".join(lines)


# ──────────────────────────────────────────────
#  Парсер ответа Алисы
# ──────────────────────────────────────────────

def _legacy_parse_alice_response(text: str, budget: int | None = None) -> list[dict]:
    """
    Разбирает текстовый ответ Алисы и возвращает список товаров.

    Поддерживает форматы:
      - Нумерованные / маркированные списки (1., -, *, +, эмодзи)
      - Блоки c полевыми маркерами: Название:, Цена:, Магазин:, Ссылка:,
        Почему подходит:, Риски:
      - Блоки, разделённые двойным переводом строки

    Каждый товар — dict с полями:
        name, price, price_num, store, link, pluses, risks,
        within_budget, notes
    """
    if not text or not text.strip():
        return []

    items: list[dict] = []

    # ── Шаг 1: разбиваем текст на блоки-кандидаты ──
    # Двойной перевод строки — почти всегда разделитель товаров
    raw_blocks = re.split(r'\n\s*\n', text.strip())

    # Дополнительно разбиваем очень длинные блоки по нумерованным строкам
    blocks: list[str] = []
    for block in raw_blocks:
        sub_blocks = re.split(r'\n(?=(?:\d{1,2}[.)]\s|[-*+]\s|[✅⭐💸🛡🏆]\s))', block)
        blocks.extend(b for b in sub_blocks if b.strip())

    # ── Шаг 2: регулярки для извлечения полей ──
    # Маркеры полей (ключ: значение)
    name_marker_re = re.compile(
        r'^\s*(?:название|товар|модель|вариант)\s*[:\-–—]\s*(.+)',
        re.IGNORECASE,
    )
    numbered_start_re = re.compile(
        r'^\s*(?:\d{1,2}[.)]\s*|[-*+]\s*|[✅⭐💸🛡🏆]\s*)\s*(.+)',
    )
    bold_name_re = re.compile(r'^\s*\*\*(.+?)\*\*\s*$')

    # Цена: 45 000 ₽, 45000 руб и т.д.
    price_marker_re = re.compile(
        r'(?:цена|стоит|стоимость|прайс|ценник)\s*[:\-–—]?\s*'
        r'([\d\s,.]+)\s*(?:₽|руб|р\.|RUB|P)',
        re.IGNORECASE,
    )
    price_generic_re = re.compile(
        r'(?:≈|~|около\s*)?([\d\s,.]+)\s*(?:₽|руб|р\.|RUB|P)\b',
    )

    # Магазин
    store_marker_re = re.compile(
        r'(?:магазин|продавец|где\s+(?:купить|найти|брать)|'
        r'источник|маркетплейс|платформа|на)\s*[:\-–—]?\s*(.+)',
        re.IGNORECASE,
    )
    store_bare_re = re.compile(
        r'\b(' + '|'.join(re.escape(s) for s in _KNOWN_STORES) + r')\b',
        re.IGNORECASE,
    )

    # Ссылка
    link_marker_re = re.compile(
        r'(?:ссылка|url|линк|ссыль|перейти)\s*[:\-–—]?\s*'
        r'(https?://[^\s\n]+|[\w.-]+\.[a-z]{2,}/[^\s\n]*)',
        re.IGNORECASE,
    )
    link_bare_re = re.compile(r'(https?://[^\s\n]+)')

    # Почему подходит / плюсы
    plus_marker_re = re.compile(
        r'(?:почему\s+(?:подходит|брать|стоит\s+брать|хорош)|'
        r'плюсы?|преимуществ[ао]|достоинств[ао]|за)\s*[:\-–—]?\s*(.+)',
        re.IGNORECASE,
    )
    plus_emoji_re = re.compile(r'[✅➕]\s*(.+)')

    # Риски
    risk_marker_re = re.compile(
        r'(?:риски?|минусы?|недостат(?:ки|ок)|слабы[её]\s+места?|'
        r'осторожно|что\s+(?:может\s+)?пойти\s+не\s+так|нюансы?|'
        r'подводные\s+камни)\s*[:\-–—]?\s*(.+)',
        re.IGNORECASE,
    )
    risk_emoji_re = re.compile(r'[❌⚠️🚫⛔]\s*(.+)')


    def _extract_price_value(txt: str) -> int | None:
        """Извлекает числовую цену из строки."""
        m = price_marker_re.search(txt)
        if not m:
            m = price_generic_re.search(txt)
        if m:
            raw = m.group(1).replace(" ", "").replace(",", "").replace(".", "")
            try:
                val = int(raw)
                if 100 <= val <= 10_000_000:
                    return val
            except ValueError:
                pass
        return None


    def _clean_name(raw: str) -> str:
        """Очищает название от эмодзи и лишних символов."""
        raw = re.sub(r'[✅⭐💸🛡❌⚠️🏆📦🔗➕]', '', raw).strip()
        raw = re.sub(r'^\*+\s*|\s*\*+$', '', raw)  # bold markers
        return raw[:200]


    # ── Шаг 3: разбор каждого блока ──
    for block in blocks:
        block_lines = block.strip().split('\n')

        item: dict = {
            "name": "",
            "price": "",
            "price_num": None,
            "store": "",
            "link": "",
            "pluses": [],
            "risks": [],
            "within_budget": None,
            "notes": [],
        }

        first_line = block_lines[0].strip() if block_lines else ""

        # Пытаемся найти название
        name_from_marker = name_marker_re.match(first_line)
        if name_from_marker:
            item["name"] = _clean_name(name_from_marker.group(1))
        else:
            num_match = numbered_start_re.match(first_line)
            if num_match:
                item["name"] = _clean_name(num_match.group(1))
            else:
                bold_match = bold_name_re.match(first_line)
                if bold_match:
                    item["name"] = _clean_name(bold_match.group(1))
                else:
                    item["name"] = _clean_name(first_line)

        # Проходим по всем строкам блока
        for line in block_lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Цена
            price_val = _extract_price_value(stripped)
            if price_val is not None and item["price_num"] is None:
                item["price_num"] = price_val
                item["price"] = format_price(price_val)
                if budget is not None:
                    item["within_budget"] = price_val <= budget
                continue

            # Ссылка (маркер)
            link_m = link_marker_re.search(stripped)
            if link_m and not item["link"]:
                item["link"] = link_m.group(1).strip()
                continue
            # Голая ссылка
            link_bare = link_bare_re.search(stripped)
            if link_bare and not item["link"]:
                item["link"] = link_bare.group(1).strip()
                continue

            # Магазин (маркер)
            store_m = store_marker_re.search(stripped)
            if store_m and not item["store"]:
                store_name = store_m.group(1).strip().rstrip('.')
                if not store_name.startswith('http'):
                    item["store"] = store_name[:100]
                continue
            # Явное название магазина
            store_bare_m = store_bare_re.search(stripped)
            if store_bare_m and not item["store"]:
                item["store"] = store_bare_m.group(1).strip()
                continue

            # Почему подходит / плюсы
            plus_m = plus_marker_re.search(stripped)
            if plus_m:
                plus_text = plus_m.group(1).strip()[:200]
                if plus_text and plus_text not in item["pluses"]:
                    item["pluses"].append(plus_text)
                continue
            plus_emoji_m = plus_emoji_re.match(stripped)
            if plus_emoji_m:
                plus_text = plus_emoji_m.group(1).strip()[:200]
                if plus_text and plus_text not in item["pluses"]:
                    item["pluses"].append(plus_text)
                continue

            # Риски (маркер)
            risk_m = risk_marker_re.search(stripped)
            if risk_m:
                risk_text = risk_m.group(1).strip()[:200]
                if risk_text and risk_text not in item["risks"]:
                    item["risks"].append(risk_text)
                continue
            risk_emoji_m = risk_emoji_re.match(stripped)
            if risk_emoji_m:
                risk_text = risk_emoji_m.group(1).strip()[:200]
                if risk_text and risk_text not in item["risks"]:
                    item["risks"].append(risk_text)
                continue

            # Всё остальное — заметки
            if len(stripped) > 3:
                item["notes"].append(stripped[:200])

        if item["name"] and len(item["name"]) >= 3:
            items.append(item)

    # ── Шаг 4: fallback-парсинг ──
    if not items:
        lines = text.strip().split('\n')
        current: dict | None = None
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if current and current.get("name"):
                    items.append(current)
                    current = None
                continue
            num_match = numbered_start_re.match(stripped)
            if num_match:
                if current and current.get("name"):
                    items.append(current)
                current = {
                    "name": _clean_name(num_match.group(1)),
                    "price": "", "price_num": None, "store": "", "link": "",
                    "pluses": [], "risks": [], "within_budget": None, "notes": [],
                }
                continue
            if current is None:
                current = {
                    "name": _clean_name(stripped),
                    "price": "", "price_num": None, "store": "", "link": "",
                    "pluses": [], "risks": [], "within_budget": None, "notes": [],
                }
                continue
            pv = _extract_price_value(stripped)
            if pv is not None and current["price_num"] is None:
                current["price_num"] = pv
                current["price"] = format_price(pv)
                if budget is not None:
                    current["within_budget"] = pv <= budget
                continue
            sm = store_bare_re.search(stripped)
            if sm and not current["store"]:
                current["store"] = sm.group(1).strip()
                continue
            lb = link_bare_re.search(stripped)
            if lb and not current["link"]:
                current["link"] = lb.group(1).strip()
                continue
        if current and current.get("name"):
            items.append(current)

    # Совсем fallback
    if not items:
        items.append({
            "name": text.strip()[:200],
            "price": "",
            "price_num": None,
            "store": "",
            "link": "",
            "pluses": [],
            "risks": [],
            "within_budget": None,
            "notes": [text.strip()],
        })

    # Пересчитываем within_budget
    if budget is not None:
        for item in items:
            if item["price_num"] is not None:
                item["within_budget"] = item["price_num"] <= budget

    return items


# ──────────────────────────────────────────────
#  Скоринг товара для выбора ТОП-1
# ──────────────────────────────────────────────

def parse_alice_response(text: str, budget: int | None = None) -> list[dict]:
    """Разбирает ответ Алисы на отдельные карточки товаров.

    Поддерживает строгий формат с полями и обычные списки: с нумерацией,
    маркерами, Markdown-заголовками или строками с моделью, ценой и магазином.
    Пустая или явно отсутствующая ссылка не подменяется поисковой ссылкой.
    """
    if not text or not text.strip():
        return []

    normalized = text.replace("\r", "").replace("\u00a0", " ").strip()
    if '"cards"' in normalized or "'cards'" in normalized:
        try:
            json_items = parse_ai_cards_json(normalized, budget=budget)
            if json_items:
                return json_items
        except Exception:
            pass

    # Очистка мусора Алисы: +1, повторные домены в конце строк
    normalized = re.sub(r'\s*\+1\b', '', normalized)
    normalized = re.sub(r'\s+\b[\w.-]+\.[a-z]{2,}\b(?:\s*\+\s*1)?\s*$', '', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'\s+\b(?:руб\.|руб|₽|р\.)\s*$', '', normalized, flags=re.IGNORECASE)  # не цена в конце строки без цифр
    known_store_pattern = "|".join(re.escape(store) for store in _KNOWN_STORES)
    known_brand_pattern = (
        "hisense|tcl|samsung|lg|sony|xiaomi|haier|philips|"
        "skyworth|hyundai|dexp|huawei|honor|apple|iphone|ipad|macbook|asus|lenovo|"
        "acer|hp|dell|realme|poco|redmi|dyson|bosch|karcher|sber|google|pixel"
    )
    field_re = re.compile(
        r"^\s*(?P<label>название(?:\s+(?:модели|товара))?|модель|товар|вариант|"
        r"цена|стоимость|стоит|магазин|продавец|где\s+(?:купить|брать)|"
        r"источник|(?:прямая\s+)?ссылка(?:\s+на\s+товар)?|url|линк|почему(?:\s+подходит|\s+брать)?|"
        r"плюсы?|преимущества|риски?|минусы?|недостатки|нюансы)\s*"
        r"[:—–-]\s*(?P<value>.*)$",
        re.IGNORECASE,
    )
    # Алиса иногда склеивает весь вариант в одну строку. Разворачиваем
    # известные маркеры в отдельные логические строки до основного разбора.
    inline_marker_re = re.compile(
        r"\s+(?=(?:вариант\s*\d{1,2}\b|"
        r"(?:название(?:\s+(?:модели|товара))?|модель|"
        r"цена|стоимость|стоит|магазин|продавец|"
        r"(?:прямая\s+)?ссылка(?:\s+на\s+товар)?|ссылка|url|линк|"
        r"почему(?:\s+подходит|\s+брать)?|почему|плюсы?|преимущества|"
        r"риски?|риск|минусы?|недостатки|нюансы)\s*:))",
        re.IGNORECASE,
    )
    normalized = inline_marker_re.sub("\n", normalized)
    variant_header_re = re.compile(r"^\s*вариант\s*\d{1,2}\s*$", re.IGNORECASE)
    summary_header_re = re.compile(
        r"^\s*(?:самый\s+деш[её]вый\s+найденный\s+вариант|"
        r"лучший\s+вариант\s+ближе\s+к\s+верхней\s+границе\s+бюджета)\s*$",
        re.IGNORECASE,
    )
    numbered_re = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*{0,2})?(?:вариант\s*)?\d{1,2}[.)]\s*(.+)$", re.IGNORECASE)
    bullet_re = re.compile(r"^\s*(?:[-•*✅⭐🏆])\s+(.+)$")
    url_re = re.compile(r"(?:https?://|www\.)[^\s<>]+", re.IGNORECASE)
    price_range_re = re.compile(
        r"(?<!\d)(\d{1,3}(?:[\s,.]\d{3})+|\d{4,6})\s*[–—-]\s*"
        r"(?:\d{1,3}(?:[\s,.]\d{3})+|\d{4,6})\s*(?:₽|руб(?:\.|лей)?|р\.)",
        re.IGNORECASE,
    )
    price_re = re.compile(
        r"(?<!\d)(\d{1,3}(?:[\s,.]\d{3})+|\d{4,6}|\d{2,3}\s*[кk])\s*(?:₽|руб(?:\.|лей)?|р\.)",
        re.IGNORECASE,
    )
    labeled_price_re = re.compile(r"(?<!\d)(\d{1,3}(?:[\s,.]\d{3})+|\d{4,6}|\d{2,3}\s*(?:[кk]|тыс\.?))", re.IGNORECASE)
    store_re = re.compile(r"\b(" + known_store_pattern + r")\b", re.IGNORECASE)
    brand_re = re.compile(r"^\s*(?:\*{0,2})(?:" + known_brand_pattern + r")\b", re.IGNORECASE)

    def clean(value: str) -> str:
        value = re.sub(r"^\s*(?:#{1,6}\s*)?(?:\d{1,2}[.)]|[-•*✅⭐🏆])?\s*", "", value)
        value = value.replace("**", "").replace("__", "")
        return value.strip(" \t:—–-*")

    def number_from(value: str, allow_plain: bool = False) -> int | None:
        match = price_range_re.search(value)
        if not match:
            match = price_re.search(value)
        if not match and allow_plain:
            match = labeled_price_re.search(value)
        if not match:
            return None
        raw = match.group(1).lower().replace(" ", "").replace(",", "").replace(".", "")
        if raw.endswith("тыс"):
            raw = raw[:-3]
            multiplier = 1000
        elif raw.endswith("к"):
            raw = raw[:-1]
            multiplier = 1000
        else:
            multiplier = 1
        try:
            amount = int(raw) * multiplier
        except ValueError:
            return None
        return amount if 100 <= amount <= 10_000_000 else None

    def is_plain_price_line(value: str) -> bool:
        return bool(re.fullmatch(
            r"\s*(?:около\s*)?\d{1,3}(?:[\s,.]\d{3})?|\d{4,6}\s*",
            value,
            re.IGNORECASE,
        ))

    def url_from(value: str) -> str:
        value_lower = value.lower()
        if any(phrase in value_lower for phrase in (
            "искать вручную", "точной ссылки нет", "ссылка не найдена",
            "нужно искать", "не указана", "не найдена",
        )):
            return ""
        # Если в строке явно говорится "нужно искать вручную в каталоге" — не ссылка
        if "вручную" in value_lower and "http" not in value_lower:
            return ""
        match = url_re.search(value)
        if not match:
            return ""
        url = match.group(0).rstrip(".,;:!?)、】【")
        return f"https://{url}" if url.lower().startswith("www.") else url

    def parse_pipe_item(value: str) -> dict | None:
        """Разбирает ручной/табличный формат: name | price | store | link."""
        if "|" not in value:
            return None
        parts = [clean(part.strip().strip("[]()")) for part in value.split("|")]
        parts = [part for part in parts if part]
        if len(parts) < 2 or is_bad_title(parts[0]):
            return None
        item = {
            "name": parts[0][:200],
            "price": "",
            "price_num": None,
            "store": "магазин нужно уточнить",
            "link": "",
            "pluses": [],
            "risks": [],
            "within_budget": None,
            "notes": [],
        }
        amount = number_from(parts[1], allow_plain=True)
        if amount is not None:
            item["price_num"] = amount
            item["price"] = format_price(amount)
        if len(parts) > 2:
            store = url_re.sub("", parts[2]).strip(" —–-.,")
            if store:
                item["store"] = store[:100]
        if len(parts) > 3:
            item["link"] = url_from(parts[3])
            if not item["link"]:
                item["notes"].append("ссылку нужно искать вручную")
        if len(parts) > 4:
            item["pluses"].append(parts[4])
        if len(parts) > 5:
            item["risks"].append(parts[5])
        if budget is not None and item["price_num"] is not None:
            item["within_budget"] = item["price_num"] <= budget
        return item

    def is_bad_title(value: str) -> bool:
        value = clean(value).strip()
        lower = value.lower()

        if not value:
            return True

        if url_from(value):
            return True

        if lower.startswith(("http://", "https://", "www.", "[www](")):
            return True

        if "ссылку нужно искать" in lower or "искать вручную" in lower:
            return True

        if re.fullmatch(r"вариант\s*\d{1,2}", lower):
            return True

        return False
    
    def is_new_item_line(line: str, current_lines: list[str]) -> bool:
        if not current_lines:
            return False
        stripped = line.strip()
        if not stripped or field_re.match(stripped):
            return False
        if variant_header_re.match(stripped) or numbered_re.match(stripped) or bullet_re.match(stripped):
            return True
        # Свободный формат: новая строка начинается с бренда → новый товар
        has_current_details = any(field_re.match(existing.strip()) for existing in current_lines)
        if brand_re.match(stripped):
            if has_current_details:
                return True
            # Если строка содержит бренд + ещё слова (модель) — вероятно новый товар
            if len(stripped.split()) >= 2:
                return True
        return False

    blocks: list[list[str]] = []
    current: list[str] = []
    for raw_line in normalized.split("\n"):
        if not raw_line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        if is_new_item_line(raw_line, current):
            blocks.append(current)
            current = [raw_line]
        else:
            current.append(raw_line)
    if current:
        blocks.append(current)

    # Ответы, где все поля идут подряд без пустых строк и нумерации.
    expanded_blocks: list[list[str]] = []
    for block in blocks:
        part: list[str] = []
        for line in block:
            field = field_re.match(line.strip())
            label = field.group("label").lower() if field else ""
            if label in {"название", "название модели", "название товара", "модель", "товар", "вариант"} and part:
                expanded_blocks.append(part)
                part = [line]
            else:
                part.append(line)
        if part:
            expanded_blocks.append(part)

    items: list[dict] = []
    seen_names: set[str] = set()
    for block in expanded_blocks:
        # Финальные два блока промпта — это сводки, а не новые товары.
        # Не создаём из них дублирующие карточки.
        if block and summary_header_re.match(block[0]):
            continue
        item = {
            "name": "", "price": "", "price_num": None, "store": "", "link": "",
            "pluses": [], "risks": [], "within_budget": None, "notes": [],
        }
        plain_lines: list[str] = []
        for raw_line in block:
            line = raw_line.strip()
            if not line:
                continue
            pipe_item = parse_pipe_item(line)
            if pipe_item:
                item.update(pipe_item)
                continue
            field = field_re.match(line)
            label = field.group("label").lower() if field else ""
            value = clean(field.group("value")) if field else clean(line)

            if label in {"название", "название модели", "название товара", "модель", "товар", "вариант"}:
                if is_bad_title(value):
                    link = url_from(value)
                    if link and not item["link"]:
                        item["link"] = link
                    elif "искать" in value.lower() or "вручную" in value.lower():
                        item["notes"].append("ссылку нужно искать вручную")
                    continue

                item["name"] = value[:200]
                continue
            if label in {"цена", "стоимость", "стоит"}:
                amount = number_from(value, allow_plain=True)
                if amount is not None:
                    item["price_num"] = amount
                    item["price"] = format_price(amount)
                continue
            if label in {"магазин", "продавец", "где купить", "где брать", "источник"}:
                item["store"] = url_re.sub("", value).strip(" —–-.,")[:100]
                continue
            if label in {
                "ссылка", "ссылка на товар", "прямая ссылка",
                "прямая ссылка на товар", "url", "линк",
            }:
                item["link"] = url_from(value)
                if not item["link"]:
                    item["notes"].append("ссылку нужно искать вручную")
                continue
            if label in {"почему", "почему подходит", "почему брать", "плюс", "плюсы", "преимущества"}:
                if value:
                    item["pluses"].append(value)
                continue
            if label in {"риск", "риски", "минус", "минусы", "недостатки", "нюансы"}:
                if value:
                    item["risks"].append(value)
                continue

            if not item["link"]:
                item["link"] = url_from(value)
            if item["price_num"] is None:
                amount = number_from(value, allow_plain=is_plain_price_line(value))
                if amount is not None:
                    item["price_num"] = amount
                    item["price"] = format_price(amount)
                    continue
            if not item["store"]:
                store_match = store_re.search(value)
                if store_match:
                    item["store"] = store_match.group(1)[:100]
                elif item["name"] and not url_from(value) and len(value) <= 80:
                    item["store"] = value[:100]
                    continue
            if re.search(r"(?:почему|подходит|плюс|преимущ)", value, re.IGNORECASE):
                item["pluses"].append(value)
            elif re.search(r"(?:риск|минус|недостат|осторож|нюанс)", value, re.IGNORECASE):
                item["risks"].append(value)
            else:
                plain_lines.append(value)

        if not item["name"]:
            for value in plain_lines:
                candidate = numbered_re.sub(r"\1", value)
                candidate = bullet_re.sub(r"\1", candidate)
                candidate = clean(candidate.split(" — ")[0].split(" - ")[0])
                if candidate and not is_bad_title(candidate) and not re.match(r"^(вот|ниже|подборк|вариант[ы:]|итог)", candidate, re.IGNORECASE):
                    item["name"] = candidate[:200]
                    break

        if item["name"] and not item["store"]:
            for value in plain_lines:
                candidate = clean(value)
                if (
                    candidate
                    and candidate != item["name"]
                    and len(candidate) <= 80
                    and not is_bad_title(candidate)
                    and number_from(candidate, allow_plain=is_plain_price_line(candidate)) is None
                    and not re.search(r"^(вот|ниже|подборк|вариант[ы:]|итог)", candidate, re.IGNORECASE)
                ):
                    item["store"] = candidate[:100]
                    break

        item["pluses"] = list(dict.fromkeys(item["pluses"]))
        item["risks"] = list(dict.fromkeys(item["risks"]))
        if not item["store"]:
            item["store"] = "магазин нужно уточнить"
        if budget is not None and item["price_num"] is not None:
            item["within_budget"] = item["price_num"] <= budget

        name_key = re.sub(r"[^a-zа-яё0-9]+", "", item["name"].lower())
        has_details = bool(item["price_num"] or item["store"] or item["link"] or item["pluses"] or item["risks"])
        if len(item["name"]) >= 3 and has_details and name_key and name_key not in seen_names:
            seen_names.add(name_key)
            items.append(item)

    # Редкий неструктурированный ответ обрабатываем предыдущим, более мягким
    # разбором, но только если новый парсер не смог выделить ни одной карточки.
    if items:
        return items

    # ── Fallback 2: разбиваем по строкам с ценой ──
    # Ищем строки, содержащие цену (число + ₽/руб), и группируем текст между ними
    price_line_re = re.compile(r'[\d\s,.]+?\s*(?:₽|руб|р\.)', re.IGNORECASE)
    lines = normalized.split('\n')
    price_indices = [i for i, line in enumerate(lines) if price_line_re.search(line)]
    if len(price_indices) >= 2:
        fallback_items: list[dict] = []
        for idx, pi in enumerate(price_indices):
            start = price_indices[idx - 1] + 1 if idx > 0 else 0
            end = price_indices[idx + 1] if idx + 1 < len(price_indices) else len(lines)
            block_lines = lines[start:end]
            item = _parse_fallback_block(block_lines, budget, known_store_pattern, url_re, price_re, store_re)
            if item and item.get("name") and len(item["name"]) >= 3:
                fallback_items.append(item)
        if fallback_items:
            return fallback_items

    # ── Fallback 3: разбиваем по строкам, похожим на названия моделей ──
    model_line_re = re.compile(
        r'(?:samsung|lg|sony|xiaomi|haier|tcl|philips|hisense|skyworth|hyundai|dexp|'
        r'huawei|honor|apple|asus|lenovo|acer|hp|dell|realme|poco|redmi|dyson|bosch|karcher|sber)',
        re.IGNORECASE,
    )
    model_indices = [
    i for i, line in enumerate(lines)
    if model_line_re.search(line)
    and not field_re.match(line.strip())
    and not url_re.search(line)
]
    if len(model_indices) >= 2:
        fallback_items = []
        for idx, mi in enumerate(model_indices):
            start = mi
            end = model_indices[idx + 1] if idx + 1 < len(model_indices) else len(lines)
            block_lines = lines[start:end]
            item = _parse_fallback_block(block_lines, budget, known_store_pattern, url_re, price_re, store_re)
            if item and item.get("name") and len(item["name"]) >= 3:
                fallback_items.append(item)
        if fallback_items:
            return fallback_items

    return _legacy_parse_alice_response(normalized, budget)


def _parse_fallback_block(
    block_lines: list[str],
    budget: int | None,
    known_store_pattern: str,
    url_re: re.Pattern,
    price_re: re.Pattern,
    store_re: re.Pattern,
) -> dict | None:
    """Разбирает один блок строк в словарь товара (fallback-парсер)."""
    item: dict = {
        "name": "", "price": "", "price_num": None, "store": "", "link": "",
        "pluses": [], "risks": [], "within_budget": None, "notes": [],
    }
    for line in block_lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Название — первая непустая строка, не содержащая цену/ссылку/магазин
        if not item["name"]:
            has_price = price_re.search(stripped)
            has_url = url_re.search(stripped)
            has_store = store_re.search(stripped)
            if not has_price and not has_url and not has_store:
                item["name"] = re.sub(r'^[✅⭐💸🛡❌⚠️🏆\s*#\d.)\-•]+', '', stripped).strip()[:200]
                continue
        # Цена
        if item["price_num"] is None:
            pm = price_re.search(stripped)
            if pm:
                raw = pm.group(0).lower().replace(" ", "").replace(",", "").replace(".", "").replace("₽", "").replace("руб", "").replace("р.", "")
                try:
                    val = int(raw)
                    if 100 <= val <= 10_000_000:
                        item["price_num"] = val
                        item["price"] = format_price(val)
                        if budget is not None:
                            item["within_budget"] = val <= budget
                except ValueError:
                    pass
        # Ссылка
        if not item["link"]:
            um = url_re.search(stripped)
            if um:
                item["link"] = um.group(0).rstrip(".,;:!?)、】【")
        # Магазин
        if not item["store"]:
            sm = store_re.search(stripped)
            if sm:
                item["store"] = sm.group(1)[:100]
        # Плюсы
        if re.search(r'(?:почему|подходит|плюс|преимущ|4k|120\s*гц|hdmi|smart|смарт)', stripped, re.IGNORECASE):
            if stripped not in item["pluses"]:
                item["pluses"].append(stripped)
        # Риски
        if re.search(r'(?:риск|минус|недостат|осторож|нюанс|60\s*гц|выше бюджета|мало отзыв)', stripped, re.IGNORECASE):
            if stripped not in item["risks"]:
                item["risks"].append(stripped)
    if not item["name"] and block_lines:
        item["name"] = re.sub(r'^[✅⭐💸🛡❌⚠️🏆\s*#\d.)\-•]+', '', block_lines[0].strip()).strip()[:200]
    if item["name"] and not item["store"]:
        item["store"] = "магазин нужно уточнить"
    return item if item["name"] and len(item["name"]) >= 3 else None


def _score_item(item: dict, budget: int | None) -> int:
    """
    Вычисляет приоритет товара для ТОП-1.
    Больше баллов = лучше.

    Приоритеты:
      - цена в бюджете (+100, иначе -50)
      - 120 Гц (+30)
      - HDMI 2.1 (+15)
      - игровой режим (+10)
      - 4K (+8)
      - Smart TV (+5)
      - диагональ 43-55" (+10)
      - известный магазин (+10)
      - есть ссылка (+8)
      - меньше рисков (-5 за каждый риск)
      - есть плюсы (+3 за каждый)
    """
    score = 0
    name_lower = item.get("name", "").lower()
    plus_text = " ".join(item.get("pluses", [])).lower()
    risk_text = " ".join(item.get("risks", [])).lower()
    all_text = f"{name_lower} {plus_text} {risk_text}"

    # Бюджет
    if budget is not None and item.get("price_num"):
        if item["price_num"] <= budget:
            score += 100
            # Для высокого бюджета слишком дешёвый вариант часто означает
            # другой класс товара. Не поднимаем его автоматически выше
            # подходящих моделей из целевого диапазона 80–100% бюджета.
            if budget >= 60_000:
                ratio = item["price_num"] / budget
                if ratio < 0.6:
                    score -= 40
                elif ratio >= 0.8:
                    score += 25
        else:
            score -= 50

    # 120 Гц
    if re.search(r'120\s*гц|120\s*hz|120hz|120\s*герц', all_text):
        score += 30

    # HDMI 2.1
    if re.search(r'hdmi\s*2\.1|hdmi\s*2\s*1', all_text):
        score += 15

    # Игровой режим
    if re.search(r'игров|game\s*mode|gaming|game\s*optimizer|vrr|allm', all_text):
        score += 10

    # 4K
    if re.search(r'4k|4\s*к|uhd|ultra\s*hd', all_text):
        score += 8

    # Smart TV
    if re.search(r'smart\s*tv|смарт\s*тв|android\s*tv|tizen|webos|google\s*tv', all_text):
        score += 5

    # Диагональ 43-55 дюймов
    diag_match = re.search(r'(\d{2})\s*(?:дюйм|")', all_text)
    if diag_match:
        diag = int(diag_match.group(1))
        if 43 <= diag <= 55:
            score += 10
        elif 32 <= diag <= 42:
            score += 3
        elif 56 <= diag <= 65:
            score += 2

    # Известный магазин
    store_lower = item.get("store", "").lower()
    for ks in _KNOWN_STORES:
        if ks.lower() in store_lower:
            score += 10
            break

    # Есть ссылка
    if item.get("link"):
        score += 8

    # Меньше рисков
    num_risks = len(item.get("risks", []))
    score -= num_risks * 5

    # Есть плюсы
    num_pluses = len(item.get("pluses", []))
    score += num_pluses * 3

    return score


# ──────────────────────────────────────────────
#  Финальный черновик для клиента
# ──────────────────────────────────────────────

def build_client_draft(req: Request, alice_items: list[dict]) -> str:
    """
    Формирует короткий клиентский черновик на основе разбора ответа Алисы.

    Структура:
      ✅ Подборка проверена вручную

      🏆 ТОП-1 — лучше брать:
      Название: ...
      Цена: ...
      Где: ...
      Почему: ...
      Риск: ...

      Ещё варианты:
      2. ...
      3. ...
      4. ...

      ❌ Не брать / осторожно:
      ...

      Итог:
      Лучший вариант — ...
      Перед покупкой нужно уточнить цену и наличие.

      Проверено вручную по доступной информации.
    """
    product = req.product_name or req.product or "товар"

    budget_num: int | None = None
    if req.budget and req.budget.isdigit():
        budget_num = int(req.budget)

    if not alice_items:
        return (
            "✅ <b>Подборка проверена вручную</b>\n\n"
            f"По запросу «{product}» данных недостаточно.\n"
            "Нужен ручной подбор — напишу, как только проверю.\n\n"
            "<i>Проверено вручную по доступной информации.</i>"
        )

    # ── Категоризация ──
    within_budget: list[dict] = []
    over_budget: list[dict] = []
    no_price: list[dict] = []

    for item in alice_items:
        if item.get("within_budget") is True:
            within_budget.append(item)
        elif item.get("within_budget") is False:
            over_budget.append(item)
        else:
            no_price.append(item)

    # Скорим
    scored_within = [(item, _score_item(item, budget_num)) for item in within_budget]
    scored_within.sort(key=lambda x: x[1], reverse=True)

    scored_no_price = [(item, _score_item(item, budget_num)) for item in no_price]
    scored_no_price.sort(key=lambda x: x[1], reverse=True)

    scored_over = [(item, _score_item(item, budget_num)) for item in over_budget]
    scored_over.sort(key=lambda x: x[1], reverse=True)

    lines: list[str] = []

    # ── Заголовок ──
    lines.append("✅ <b>Подборка проверена вручную</b>")
    lines.append("")

    # ── ТОП-1 ──
    lines.append("🏆 <b>ТОП-1 — лучше брать:</b>")

    if scored_within:
        best, _best_score = scored_within[0]
        lines.append(_format_item_compact(best))
    elif scored_no_price:
        best, _best_score = scored_no_price[0]
        lines.append(_format_item_compact(best))
        lines.append("  ⚠️ Цену нужно уточнить перед рекомендацией.")
    elif scored_over:
        best, _best_score = scored_over[0]
        lines.append("  ⚠️ <i>Все варианты выше бюджета.</i>")
        lines.append(_format_item_compact(best))
        lines.append("  💡 Можно рассмотреть при скидке или б/у.")
    else:
        lines.append("  <i>Нет подходящих вариантов — нужен ручной подбор.</i>")

    lines.append("")

    # ── Ещё варианты ──
    remaining_within = scored_within[1:] if scored_within else []
    extra = remaining_within + scored_no_price
    if not scored_within and scored_no_price:
        extra = remaining_within + scored_no_price[1:]
    extra = extra[:4]

    if extra:
        lines.append("<b>Ещё варианты:</b>")
        idx = 2
        for item, _sc in extra:
            name = item.get("name", "?")[:100]
            price = item.get("price") or "цена не указана"
            store = item.get("store") or "магазин уточняется"
            lines.append(f"{idx}. {name}")
            lines.append(f"   {price} — {store}")
            if item.get("link"):
                lines.append(f"   Ссылка: {item['link']}")
            if item.get("pluses"):
                lines.append(f"   Почему: {', '.join(item['pluses'][:2])}")
            if item.get("risks"):
                lines.append(f"   Риск: {', '.join(item['risks'][:2])}")
            idx += 1
        lines.append("")

    # ── Не брать / осторожно ──
    if scored_over:
        lines.append("<b>❌ Не брать / осторожно:</b>")
        for item, _sc in scored_over[:4]:
            name = item.get("name", "?")[:80]
            price = item.get("price") or "цена не указана"
            note = "выше бюджета — можно рассмотреть при скидке"
            lines.append(f"  • {name} — {price} ({note})")
        lines.append("")

    # ── Итог ──
    lines.append("<b>Итог:</b>")
    if scored_within:
        best_item, _ = scored_within[0]
        best_name = best_item.get("name", "?")[:100]
        best_price = best_item.get("price") or "цена не указана"
        best_store = best_item.get("store") or "магазин"
        lines.append(f"Лучший вариант — {best_name}")
        lines.append(f"за {best_price} в {best_store}.")
    elif scored_no_price:
        best_item, _ = scored_no_price[0]
        lines.append(f"Самый перспективный — {best_item.get('name', '?')[:100]},")
        lines.append("но нужно сначала уточнить цену.")
    elif scored_over:
        lines.append("Все варианты выше бюджета.")
        lines.append("Нужно либо повысить бюджет, либо искать б/у.")
    else:
        lines.append("По запросу нужен ручной подбор — данных недостаточно.")

    lines.append("Перед покупкой нужно уточнить цену и наличие.")
    lines.append("")
    lines.append("<i>Проверено вручную по доступной информации.</i>")

    return "\n".join(lines)


# ──────────────────────────────────────────────
#  Вспомогательная: формат одного товара
# ──────────────────────────────────────────────

def _format_item_compact(item: dict) -> str:
    """Компактное представление одного товара для черновика."""
    parts: list[str] = []

    name = item.get("name", "?")[:120]
    parts.append(f"Название: {name}")

    price = item.get("price")
    if price:
        parts.append(f"Цена: {price}")
    else:
        parts.append("Цена: уточнить")

    store = item.get("store")
    if store:
        parts.append(f"Где: {store}")
    else:
        parts.append("Где: уточнить")

    link = item.get("link")
    if link:
        parts.append(f"Ссылка: {link}")
    else:
        parts.append("Ссылку нужно уточнить вручную")

    pluses = item.get("pluses", [])
    if pluses:
        parts.append(f"Почему: {', '.join(pluses[:3])}")

    risks = item.get("risks", [])
    if risks:
        parts.append(f"Риск: {', '.join(risks[:3])}")

    return "\n".join(f"  {p}" for p in parts)
