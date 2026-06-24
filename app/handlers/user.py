"""Обработчики пользователя — приём и парсинг заявок."""
import re
import json
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.db import (
    create_request, get_request, update_request,
    get_user_active_request, Request
)
from app.keyboards import kb_start, kb_confirm_cancel
from app.search_links import build_search_query, generate_search_links
from app.states import UserStates

router = Router()


# ──────────────────────────────────────────────
#  Парсинг исходного запроса пользователя
# ──────────────────────────────────────────────

def parse_budget(text: str) -> str:
    """
    Извлекает бюджет из текста.
    Возвращает число строкой (в рублях) или пустую строку.
    
    Поддерживает:
    - "до 45к" → "45000"
    - "45к" → "45000"
    - "32к" → "32000"
    - "до 50000" → "50000"
    - "5 тыс" → "5000"
    - "5 тысяч" → "5000"
    """
    t = text.lower()

    # Паттерн: число + "к" (тысячи)
    m = re.search(r'(\d+)\s*[кК]\b', t)
    if m:
        return str(int(m.group(1)) * 1000)

    # Паттерн: число + "тыс" / "тысяч"
    m = re.search(r'(\d+)\s*тыс(?:яч|\.|и)?\.?\b', t)
    if m:
        return str(int(m.group(1)) * 1000)

    # Паттерн: просто число (4-6 цифр)
    m = re.search(r'\b(\d{4,6})\b', t)
    if m:
        val = int(m.group(1))
        if 1000 <= val <= 999999:
            return str(val)

    return ""


def parse_city(text: str) -> str:
    """
    Извлекает город из текста.
    Ищет по паттерну "в <город>" или "<город>".
    """
    city_forms = {
        "ярославле": "Ярославль", "москве": "Москва", "санкт-петербурге": "Санкт-Петербург",
        "петербурге": "Санкт-Петербург", "казани": "Казань", "самаре": "Самара",
        "омске": "Омск", "перми": "Пермь", "тюмени": "Тюмень", "туле": "Тула",
        "воронеже": "Воронеж", "екатеринбурге": "Екатеринбург", "новосибирске": "Новосибирск",
        "нижнем новгороде": "Нижний Новгород", "ростове-на-дону": "Ростов-на-Дону",
    }

    # Паттерн: "в <город>" (предложный падеж)
    m = re.search(r'в\s+([А-Яа-яЁё]+(?:[-\s][А-Яа-яЁё]+)*)\b', text)
    if m:
        city = m.group(1).strip()
        # Фильтруем частые ложные срабатывания
        stop_words = {"видео", "случае", "итоге", "результате", "плане", "магазине",
                      "интернете", "городе", "районе", "центре", "смысле", "отличие",
                      "моменте", "процессе", "конце", "начале", "итоге", "примере"}
        if city.lower() not in stop_words and len(city) >= 3:
            return city_forms.get(city.lower(), city.title())

    return ""


def parse_use_case(text: str) -> str:
    """
    Извлекает цель покупки (use_case).
    Примеры: "для PS5", "для игр", "для работы", "для кухни".
    """
    t = text.lower()

    # Паттерн: "для <цель>"
    m = re.search(r'для\s+([А-Яа-яЁёA-Za-z0-9\s]+?)(?:\s+до\s|\s+в\s|\s+купить|$)', text)
    if m:
        use_case = m.group(1).strip()
        if len(use_case) >= 2 and len(use_case) <= 50:
            return use_case

    # Специфичные ключевые слова
    ps5_keywords = ["ps5", "плейстейшен", "playstation", "приставк"]
    for kw in ps5_keywords:
        if kw in t:
            return "для PS5"

    if "для игр" in t:
        return "для игр"
    if "для работы" in t:
        return "для работы"
    if "для кухни" in t:
        return "для кухни"
    if "для дома" in t:
        return "для дома"
    if "для офиса" in t:
        return "для офиса"

    return ""


def parse_product_name(text: str) -> str:
    """
    Извлекает название товара из текста.
    Убирает мусор: "нужен", "хочу", "ищу", "посоветуйте" и т.д.
    """
    t = text.lower().strip()

    # Убираем вводные слова
    noise = [
        r'^нужен\s+', r'^хочу\s+', r'^ищу\s+', r'^посоветуйте\s+',
        r'^подскажите\s+', r'^порекомендуйте\s+', r'^мне\s+нужен\s+',
        r'^мне\s+хочется\s+', r'^дайте\s+', r'^найдите\s+',
        r'^какой\s+', r'^какую\s+', r'^какие\s+',
    ]
    for pattern in noise:
        t = re.sub(pattern, '', t)

    # Убираем "до <бюджет>"
    t = re.sub(r'\s*до\s+\d+\s*[кК]?\s*', ' ', t)
    t = re.sub(r'\s*до\s+\d+\s*тыс.*?\s*', ' ', t)

    # Убираем "в <город>"
    t = re.sub(r'\s+в\s+[А-Яа-яЁё]+\s*$', ' ', t)

    # Убираем "для <цель>"
    t = re.sub(r'\s+для\s+[^\s]+', ' ', t)

    # Убираем "купить"
    t = re.sub(r'\s*купить\s*', ' ', t)

    # Убираем "б/у" / "новый"
    t = re.sub(r'\s*б/?у\s*', ' ', t)
    t = re.sub(r'\s*новый?\s*', ' ', t)

    # Чистим
    t = re.sub(r'\s+', ' ', t).strip()

    # Берём первые 2-3 слова как название товара
    words = t.split()
    if words:
        # Берём до 3 слов, но не больше 40 символов
        name_parts = []
        total_len = 0
        for w in words[:4]:
            if total_len + len(w) > 40:
                break
            name_parts.append(w)
            total_len += len(w) + 1
        return " ".join(name_parts)

    return text[:40]


def parse_is_used_allowed(text: str) -> bool:
    """Определяет, разрешены ли б/у товары."""
    t = text.lower()
    return any(kw in t for kw in ["б/у", "бу ", "б/у ", "второй рук", "с рук", "подержан"])


def parse_important_criteria(text: str) -> str:
    """Извлекает важные критерии из текста."""
    t = text.lower()
    criteria = []

    # Размер экрана
    m = re.search(r'(\d{2})\s*(?:дюйм|")', t)
    if m:
        criteria.append(f"диагональ {m.group(1)}\"")

    # 4K / Ultra HD
    if "4k" in t or "ultra hd" in t or "ультра hd" in t:
        criteria.append("4K")

    # Full HD
    if "full hd" in t or "фулл hd" in t:
        criteria.append("Full HD")

    # 120 Гц
    if "120 гц" in t or "120hz" in t:
        criteria.append("120 Гц")

    # HDMI
    if "hdmi" in t:
        criteria.append("HDMI")

    # Бренд
    brands = ["samsung", "lg", "sony", "xiaomi", "tcl", "hisense", "haier"]
    for brand in brands:
        if brand in t:
            criteria.append(f"бренд {brand.title()}")

    return ", ".join(criteria) if criteria else ""


def is_ps5_tv(product_name: str, use_case: str, original_query: str) -> bool:
    product = product_name.lower()
    combined = f"{use_case} {original_query}".lower()
    return (
        "телевизор" in product
        and any(word in combined for word in ("ps5", "ps 5", "playstation", "плейстейшен", "приставк", "игр"))
    )


def full_parse(text: str) -> dict:
    """
    Полный парсинг исходного текста пользователя.
    Возвращает dict с полями заявки.
    """
    product_name = parse_product_name(text)
    use_case = parse_use_case(text)
    budget = parse_budget(text)
    city = parse_city(text)
    criteria = parse_important_criteria(text)

    # Для PS5 это не пожелание пользователя, а полезный профиль поиска:
    # он не запрещает другие ТВ, но делает критерии и запросы осмысленными.
    if is_ps5_tv(product_name, use_case, text):
        required = ["4K", "HDMI", "43–55 дюймов", "игровой режим/низкая задержка", "120 Гц желательно"]
        existing = {item.strip().lower() for item in criteria.split(",") if item.strip()}
        criteria = ", ".join([item for item in required if item.lower() not in existing] + ([criteria] if criteria else []))

    clean_search_query = build_search_query(product_name, use_case, budget, city, criteria)
    return {
        "original_query": text.strip(),
        "product_name": product_name,
        "use_case": use_case,
        "budget": budget,
        "city": city,
        "important_criteria": criteria,
        "clean_search_query": clean_search_query,
        "is_used_allowed": parse_is_used_allowed(text),
    }


# Консольный тест и бот используют один источник истины для правил разбора;
# оставшиеся функции выше сохранены для обратной совместимости импортов.
from app.request_parser import full_parse as full_parse


# ──────────────────────────────────────────────
#  Обработчики
# ──────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Я бот «Найди выгоднее».\n\n"
        "Напиши, что тебе нужно найти, и я подберу лучшие варианты.\n"
        "Пример: <i>«Нужен телевизор для PS5 до 45к в Ярославле»</i>",
        reply_markup=kb_start(),
        parse_mode="HTML"
    )


@router.message(F.text == "🔍 Найти товар")
async def btn_find(message: Message, state: FSMContext):
    await state.set_state(UserStates.waiting_for_request)
    await message.answer(
        "Напиши, что нужно найти:\n\n"
        "Пример: <i>«Нужен телевизор для PS5 до 45к в Ярославле»</i>\n\n"
        "Чем подробнее опишешь — тем точнее подбор.",
        parse_mode="HTML"
    )


@router.message(UserStates.waiting_for_request)
async def process_request(message: Message, state: FSMContext):
    """Получаем исходный запрос, парсим, задаём уточняющие вопросы."""
    text = message.text.strip()
    if len(text) < 3:
        await message.answer("Слишком коротко. Опиши подробнее, что нужно найти.")
        return

    # Парсим запрос
    parsed = full_parse(text)

    # Сохраняем в FSM
    await state.update_data(
        parsed=parsed,
        user_id=message.from_user.id,
        username=message.from_user.username or "",
    )

    # Если запрос слишком короткий и не содержит ни use_case, ни бюджета, ни города —
    # задаём один понятный вопрос вместо цепочки.
    if not parsed["use_case"] and not parsed["budget"] and not parsed["city"] and len(text.split()) <= 3:
        await message.answer(
            "🤔 Уточни, пожалуйста: для чего нужен, какой бюджет и город?\n\n"
            "Например: <i>«Нужен телевизор для PS5 до 45к в Ярославле»</i>",
            parse_mode="HTML"
        )
        return

    # Определяем, какие данные отсутствуют — по ним задаём вопросы
    missing = []
    if not parsed["city"]:
        missing.append("city")
    if not parsed["budget"]:
        missing.append("budget")

    # Задаём вопросы по очереди
    if missing:
        await state.update_data(missing_fields=missing, missing_idx=0)
        await state.set_state(UserStates.answering_questions)
        await _ask_next_question(message, missing, 0)
    else:
        # Все данные есть — показываем подтверждение
        await _show_confirm(message, state, parsed)


async def _ask_next_question(message: Message, missing: list[str], idx: int):
    """Задаёт следующий уточняющий вопрос."""
    if idx >= len(missing):
        return

    field = missing[idx]
    if field == "city":
        await message.answer("📍 В каком городе ищешь?")
    elif field == "budget":
        await message.answer("💰 Какой бюджет? (например: до 30к, 50000, 10 тыс)")


@router.message(UserStates.answering_questions)
async def process_answer(message: Message, state: FSMContext):
    """Обрабатываем ответ на уточняющий вопрос."""
    data = await state.get_data()
    parsed = data.get("parsed", {})
    missing = data.get("missing_fields", [])
    idx = data.get("missing_idx", 0)

    if idx >= len(missing):
        await _show_confirm(message, state, parsed)
        return

    field = missing[idx]
    answer = message.text.strip()

    if field == "city":
        parsed["city"] = answer.title()
    elif field == "budget":
        budget = parse_budget(answer)
        if budget:
            parsed["budget"] = budget
        else:
            # Попробуем взять как есть
            parsed["budget"] = answer

    # Пересобираем clean_search_query из обновлённых полей,
    # чтобы автопоиск не запускался по одному слову «телевизор».
    from app.search_links import build_search_query
    parsed["clean_search_query"] = build_search_query(
        parsed.get("product_name", ""),
        parsed.get("use_case", ""),
        parsed.get("budget", ""),
        parsed.get("city", ""),
        parsed.get("important_criteria", ""),
    )

    idx += 1
    await state.update_data(parsed=parsed, missing_idx=idx)

    if idx < len(missing):
        await _ask_next_question(message, missing, idx)
    else:
        await _show_confirm(message, state, parsed)


async def _show_confirm(message: Message, state: FSMContext, parsed: dict):
    """Показывает сводку заявки и просит подтвердить."""
    lines = ["📋 <b>Проверь заявку:</b>\n"]
    lines.append(f"📦 Товар: <b>{parsed['product_name']}</b>")
    if parsed["use_case"]:
        lines.append(f"🎯 Цель: {parsed['use_case']}")
    if parsed["budget"]:
        budget_display = parsed["budget"]
        if budget_display.isdigit():
            budget_display = f"{int(budget_display):,}".replace(",", " ") + " ₽"
        lines.append(f"💰 Бюджет: {budget_display}")
    if parsed["city"]:
        lines.append(f"📍 Город: {parsed['city']}")
    if parsed["important_criteria"]:
        lines.append(f"📌 Критерии: {parsed['important_criteria']}")
    if parsed.get("clean_search_query"):
        lines.append(f"🔎 Поисковый запрос: <i>{parsed['clean_search_query']}</i>")
    if parsed["is_used_allowed"]:
        lines.append("🔄 Можно б/у")
    else:
        lines.append("🆕 Только новое")

    lines.append(f"\n<i>Исходный запрос: {parsed['original_query']}</i>")

    await message.answer(
        "\n".join(lines),
        reply_markup=kb_confirm_cancel(),
        parse_mode="HTML"
    )
    await state.set_state(UserStates.waiting_for_confirm)


@router.callback_query(F.data == "confirm_req")
async def confirm_request(callback: CallbackQuery, state: FSMContext):
    """Подтверждение заявки — создаём в БД."""
    data = await state.get_data()
    parsed = data.get("parsed", {})
    user_id = data.get("user_id", callback.from_user.id)
    username = data.get("username", callback.from_user.username or "")
    product_name = (
        parsed.get("product_name")
        or parsed.get("product")
        or parsed.get("clean_search_query")
        or parsed.get("original_query")
        or "товар"
    )
    parsed["product_name"] = product_name

    # Генерируем поисковые ссылки
    links = generate_search_links(
        product_name=product_name,
        city=parsed.get("city", ""),
        use_case=parsed.get("use_case", ""),
        budget=parsed.get("budget", ""),
        important_criteria=parsed.get("important_criteria", ""),
        clean_search_query=parsed.get("clean_search_query", ""),
    )

    # Создаём заявку
    req_id = create_request(
        user_id=user_id,
        username=username,
        product=product_name,
        budget=parsed.get("budget", ""),
        city=parsed.get("city", ""),
        original_query=parsed.get("original_query", ""),
        product_name=product_name,
        use_case=parsed.get("use_case", ""),
        important_criteria=parsed.get("important_criteria", ""),
        clean_search_query=parsed.get("clean_search_query", ""),
        is_used_allowed=parsed.get("is_used_allowed", False),
    )

    # Сохраняем поисковые ссылки
    update_request(req_id, search_links=json.dumps(links, ensure_ascii=False))

    await state.clear()

    budget_disp = ""
    if parsed.get("budget"):
        if parsed["budget"].isdigit():
            budget_disp = f"{int(parsed['budget']):,} ₽".replace(",", " ")
        else:
            budget_disp = parsed["budget"]

    confirm_lines = [
        f"✅ Заявка <b>#{req_id}</b> принята!\n",
        f"📦 Товар: <b>{parsed['product_name']}</b>",
    ]
    if parsed.get("use_case"):
        confirm_lines.append(f"🎯 Цель: {parsed['use_case']}")
    if budget_disp:
        confirm_lines.append(f"💰 Бюджет: {budget_disp}")
    if parsed.get("city"):
        confirm_lines.append(f"📍 Город: {parsed['city']}")
    confirm_lines.append("\nСкоро пришлю результаты.")

    await callback.message.edit_text(
        "\n".join(confirm_lines),
        parse_mode="HTML"
    )
    await callback.answer()

    # Уведомляем админов
    from app.config import settings
    from app.keyboards import kb_admin_request
    for admin_id in settings.ADMIN_IDS:
        try:
            lines = [
                f"🆕 Новая заявка <b>#{req_id}</b>",
                f"👤 @{username or user_id}",
                f"📦 {parsed['product_name']}",
            ]
            if parsed.get("use_case"):
                lines.append(f"🎯 Цель: {parsed['use_case']}")
            if parsed.get("budget"):
                budget_disp = parsed["budget"]
                if budget_disp.isdigit():
                    budget_disp = f"{int(budget_disp):,}".replace(",", " ") + " ₽"
                lines.append(f"💰 Бюджет: {budget_disp}")
            else:
                lines.append("💰 Бюджет: не указан")
            if parsed.get("city"):
                lines.append(f"📍 Город: {parsed['city']}")
            else:
                lines.append("📍 Город: не указан")
            await callback.bot.send_message(
                admin_id,
                "\n".join(lines),
                reply_markup=kb_admin_request(req_id, "NEW"),
                parse_mode="HTML"
            )
        except Exception:
            pass


@router.callback_query(F.data == "cancel_req")
async def cancel_request(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Отменено. Напиши, если нужно что-то найти.")
    await callback.answer()
