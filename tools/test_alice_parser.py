"""Тест парсера ответа Алисы — воспроизводит реальные форматы ответов."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.alice_service import parse_alice_response, build_client_draft, _score_item
from app.db import Request
from app.request_parser import full_parse, parse_budget

# ── Тестовый бюджет ──
BUDGET = 45000

# ── Формат 1: Поля с маркерами (наиболее вероятный) ──
FORMAT1 = """Название: Samsung UE43AU7100U
Цена: 43 000 ₽
Магазин: DNS
Ссылка: https://www.dns-shop.ru/product/samsung-ue43au7100u
Почему подходит: 4K, Smart TV, HDMI 2.0, 43 дюйма
Риски: только 60 Гц

Название: LG 43UP75006LF
Цена: 39 990 ₽
Магазин: М.Видео
Ссылка: https://www.mvideo.ru/lg-43up75006lf
Почему подходит: 4K, Smart TV, 43 дюйма, хорошая цена
Риски: слабый звук, нет HDMI 2.1

Название: TCL 50C645
Цена: 47 999 ₽
Магазин: Ozon
Ссылка: https://www.ozon.ru/product/tcl-50c645
Почему подходит: 50 дюймов, 4K, QLED, Google TV
Риски: чуть выше бюджета, 60 Гц

Название: Xiaomi TV A Pro 43
Цена: 41 500 ₽
Магазин: Яндекс Маркет
Ссылка: https://market.yandex.ru/product--xiaomi-tv-a-pro-43
Почему подходит: 4K, Android TV, 43 дюйма, цена
Риски: мало отзывов, 60 Гц

Название: Haier 50 Smart TV S3
Цена: 44 900 ₽
Магазин: Ситилинк
Ссылка: https://www.citilink.ru/product/haier-50-smart-tv-s3
Почему подходит: 50 дюймов, 4K, Smart TV, в бюджете
Риски: 60 Гц, неигровая модель"""

# ── Формат 2: Нумерованные (тоже распространён) ──
FORMAT2 = """1. Samsung UE43AU7100U
Цена: 43 000 ₽
Магазин: DNS
Ссылка: https://www.dns-shop.ru/product/samsung-ue43au7100u
Почему: 4K, Smart TV
Риски: 60 Гц

2. LG 43UP75006LF
Цена: 39 990 ₽
Магазин: М.Видео
Ссылка: https://www.mvideo.ru/lg-43up75006lf
Почему: 4K, Smart TV
Риски: слабый звук

3. TCL 50C645
Цена: 47 999 ₽
Магазин: Ozon
Ссылка: https://www.ozon.ru/product/tcl-50c645
Почему: 50", QLED
Риски: выше бюджета

4. Xiaomi TV A Pro 43
Цена: 41 500 ₽
Магазин: Яндекс Маркет
Ссылка: https://market.yandex.ru/product--xiaomi-tv-a-pro-43
Почему: 4K, цена
Риски: мало отзывов

5. Haier 50 Smart TV S3
Цена: 44 900 ₽
Магазин: Ситилинк
Ссылка: https://www.citilink.ru/product/haier-50-smart-tv-s3
Почему: в бюджете
Риски: 60 Гц"""

# ── Формат 3: Маркированный список ──
FORMAT3 = """- Samsung UE43AU7100U
  Цена: 43 000 ₽
  Магазин: DNS
  Ссылка: https://www.dns-shop.ru/
  Почему: 4K, Smart TV
  Риски: 60 Гц

- LG 43UP75006LF
  Цена: 39 990 ₽
  Магазин: М.Видео
  Ссылка: https://www.mvideo.ru/
  Почему: 4K, Smart TV
  Риски: слабый звук

- TCL 50C645
  Цена: 47 999 ₽
  Магазин: Ozon
  Ссылка: https://www.ozon.ru/
  Почему: QLED
  Риски: выше бюджета

- Xiaomi TV A Pro 43
  Цена: 41 500 ₽
  Магазин: Яндекс Маркет
  Ссылка: https://market.yandex.ru/
  Почему: 4K
  Риски: мало отзывов

- Haier 50 Smart TV S3
  Цена: 44 900 ₽
  Магазин: Ситилинк
  Ссылка: https://www.citilink.ru/
  Почему: бюджет
  Риски: 60 Гц"""

# ── Формат 4: Эмодзи + bold ──
FORMAT4 = """✅ **Samsung UE43AU7100U**
Цена: 43 000 ₽
Магазин: DNS
Ссылка: https://www.dns-shop.ru/
Почему подходит: 4K, Smart TV
Риски: 60 Гц

✅ **LG 43UP75006LF**
Цена: 39 990 ₽
Магазин: М.Видео
Ссылка: https://www.mvideo.ru/
Почему подходит: 4K, доступная цена
Риски: слабый звук

⚠️ **TCL 50C645**
Цена: 47 999 ₽
Магазин: Ozon
Ссылка: https://www.ozon.ru/
Почему подходит: QLED
Риски: выше бюджета

✅ **Xiaomi TV A Pro 43**
Цена: 41 500 ₽
Магазин: Яндекс Маркет
Ссылка: https://market.yandex.ru/
Почему подходит: 4K
Риски: мало отзывов

⚠️ **Haier 50 Smart TV S3**
Цена: 44 900 ₽
Магазин: Ситилинк
Ссылка: https://www.citilink.ru/
Почему подходит: бюджет
Риски: 60 Гц"""

# ── Формат 5: Алиса склеила один вариант в одну строку ──
FORMAT5_INLINE = (
    "Вариант 1 Название модели: iPhone 15 128 ГБ Цена: около 71 000 рублей "
    "Магазин: rebro-store.ru Прямая ссылка: ссылку нужно искать вручную "
    "Почему подходит: новый смартфон с гарантией Риски: проверить наличие"
)

# ── Формат 6 (НОВЫЙ ТЕСТ): Реальный ответ Алисы с 5 телевизорами ──
FORMAT6_REAL_ALICE = """Hyundai H-LED43BU7006
Цена: около 27 000 ₽
Магазин: Ситилинк
Ссылка: нужно искать вручную в каталоге Ситилинк по названию модели
Почему подходит: диагональ 43", разрешение 4K Ultra HD, есть Smart TV (Android TV) — можно смотреть стриминговые сервисы и выходить в интернет.
Риски: в отзывах иногда отмечают не самую высокую яркость экрана, для очень светлой комнаты может потребоваться настройка.

TCL 43V6C
Цена: около 22 000 ₽
Магазин: Wildberries
Ссылка: нужно искать вручную в карточке товара на Wildberries
Почему подходит: 43 дюйма, 4K с поддержкой HDR, Smart TV на Google TV. Хороший баланс цены и функционала.
Риски: в некоторых партиях пользователи отмечают чуть менее насыщенную цветопередачу «из коробки» — обычно решается калибровкой.

TCL 43P7L-RU (2026)
Цена: около 26 000 ₽
Магазин: Ситилинк
Ссылка: нужно искать вручную в каталоге Ситилинк
Почему подходит: 43", 4K, QLED, Smart TV на Google TV.
Риски: из-за QLED-матрицы цена чуть выше базовых LED-моделей, но в рамках бюджета.

Sber SDX-43F3111
Цена: около 18 000 ₽
Магазин: ОНЛАЙН ТРЕЙД.РУ
Ссылка: нужно искать вручную в каталоге магазина
Почему подходит: строго в рамках бюджета, 43", 4K, Smart TV на платформе «Салют ТВ». Хороший вариант, если приоритет — максимальная экономия.
Риски: экосистема «Салют» может быть менее привычной для тех, кто активно использует сервисы других платформ.

Xiaomi TV A 43 2026
Цена: около 32 000 ₽
Магазин: DNS
Ссылка: нужно искать вручную в каталоге DNS
Почему подходит: 43", 4K, современный дизайн, Smart TV с хорошей экосистемой Xiaomi.
Риски: ближе к верхней границе бюджета, но всё ещё в пределах лимита."""

# ── Формат 7: Вариант N с маркером названия ──
FORMAT7_VARIANT_MARKER = """Вариант 1 Название модели: Samsung UE43AU7100U Цена: 43 000 ₽ Магазин: DNS Ссылка: https://www.dns-shop.ru/ Почему подходит: 4K Риски: 60 Гц
Вариант 2 Название модели: LG 43UP75006LF Цена: 39 990 ₽ Магазин: М.Видео Ссылка: https://www.mvideo.ru/ Почему подходит: доступная цена Риски: слабый звук
Вариант 3 Название модели: TCL 50C645 Цена: 47 999 ₽ Магазин: Ozon Ссылка: https://www.ozon.ru/ Почему подходит: QLED Риски: выше бюджета"""


def test_format(name: str, text: str, budget: int | None = BUDGET):
    print(f"\n{'='*60}")
    print(f"ФОРМАТ: {name}")
    print(f"{'='*60}")
    items = parse_alice_response(text, budget=budget)
    print(f"Найдено товаров: {len(items)}")
    for i, item in enumerate(items, 1):
        print(f"  {i}. {item.get('name', '?')[:80]}")
        print(f"     Цена: {item.get('price', '?')} (num={item.get('price_num')}) | within_budget: {item.get('within_budget')}")
        print(f"     Магазин: {item.get('store', '?')}")
        print(f"     Ссылка: {'есть' if item.get('link') else 'нет'}")
        print(f"     Плюсы: {item.get('pluses', [])}")
        print(f"     Риски: {item.get('risks', [])}")
        print(f"     Score: {_score_item(item, budget)}")

    if not items:
        print("  ⚠️ ПАРСЕР НЕ НАШЁЛ НИ ОДНОГО ТОВАРА!")
    return items


# ── Тестируем все форматы ──
for name, text in [
    ("1. Поля с маркерами (Название:, Цена:, ...)", FORMAT1),
    ("2. Нумерованный (1., 2., ...)", FORMAT2),
    ("3. Маркированный список (-)", FORMAT3),
    ("4. Эмодзи + bold", FORMAT4),
]:
    test_format(name, text)

# ── Регрессия: поля из однострочного ответа не должны быть пустыми ──
inline_items = parse_alice_response(FORMAT5_INLINE, budget=80_000)
assert len(inline_items) == 1, inline_items
inline_item = inline_items[0]
assert inline_item["name"] == "iPhone 15 128 ГБ", inline_item
assert inline_item["price_num"] == 71_000, inline_item
assert inline_item["store"] == "rebro-store.ru", inline_item
assert inline_item["link"] == "", inline_item
assert inline_item["pluses"] and inline_item["risks"], inline_item
print("\n✅ Однострочный формат Алисы: карточка заполнена корректно.")

# ── Регрессия: Telegram склеил несколько карточек в одну строку ──
INLINE_THREE_CARDS = (
    "Название модели: Google Pixel 6a Цена: ~17 500 – 19 500 ₽ Магазин: Ozon "
    "Прямая ссылка на товар: https://www.ozon.ru/product/pixel-6a "
    "Почему подходит: компактный Pixel с хорошей камерой и чистым Android без лишних оболочек. "
    "Риски: проверить региональную версию, гарантию, состояние аккумулятора и наличие NFC; "
    "этот длинный риск должен сохраниться полностью до самого конца фразы без жёсткой обрезки. "
    "Название модели: Google Pixel 7a Цена: ~20 000 ₽ Магазин: Wildberries "
    "Прямая ссылка на товар: ссылку нужно искать вручную "
    "Почему подходит: свежее поколение, хороший баланс камеры и цены. "
    "Риски: проверить продавца, отзывы и комплектацию. "
    "Название модели: Google Pixel 8a Цена: цена не указана Магазин: DNS "
    "Ссылка: https://www.dns-shop.ru/product/pixel-8a "
    "Почему: актуальная модель с долгими обновлениями. "
    "Риск: цена может быть выше бюджета."
)

inline_cards = parse_alice_response(INLINE_THREE_CARDS, budget=45_000)
assert len(inline_cards) == 3, inline_cards
assert inline_cards[0]["name"] == "Google Pixel 6a", inline_cards
assert inline_cards[0]["price_num"] == 17500, inline_cards
assert inline_cards[1]["name"] == "Google Pixel 7a", inline_cards
assert inline_cards[1]["price_num"] == 20000, inline_cards
assert inline_cards[1]["link"] == "", inline_cards
assert inline_cards[2]["price_num"] is None, inline_cards
assert all(not item["name"].startswith(("http://", "https://", "www.")) for item in inline_cards), inline_cards
assert "без жёсткой обрезки" in " ".join(inline_cards[0]["risks"]), inline_cards
assert inline_cards[0]["price_num"] < 1_000_000, inline_cards
print("\n✅ Inline-формат Telegram: 3 карточки, диапазон цены и длинные риски разобраны корректно.")

# ── Регрессия: Pixel-варианты, ссылки и pipe-формат ──
PIXEL_VARIANTS = """Вариант 1
Название модели: Google Pixel 8a
Цена: 32 990 ₽
Магазин: Gix
Ссылка: https://gix.ru/pixel-8a
Почему подходит: свежая модель, хороший экран, долгие обновления, нормальная камера и компактный размер.
Риски: японская версия, нужно проверить eSIM и гарантию.

2) Google Pixel 7
Цена: 29 500 ₽
Магазин: Ozon
Ссылка: ссылку нужно искать вручную
Почему подходит: всё ещё сильная камера и чистый Android.
Риски: проверить продавца и региональную версию.

3. Название модели: Google Pixel 8
Цена: 43 000 ₽
Магазин: DNS
Ссылка: https://www.dns-shop.ru/product/pixel-8
Почему подходит: лучше камера и процессор, чем у 7a.
Риски: ближе к верхней границе бюджета.

Pixel 7a | 20500 | Wildberries | [https://www.wildberries.ru/catalog/pixel-7a]

Название модели: Google Pixel 6a
Цена: 18 900 ₽
Магазин: магазин нужно уточнить
Ссылка: точной ссылки нет
Почему подходит: самый бюджетный вариант.
Риски: старая модель, проверить батарею.
"""

pixel_items = parse_alice_response(PIXEL_VARIANTS, budget=45_000)
assert len(pixel_items) >= 5, pixel_items
assert all(not item["name"].startswith(("http://", "https://", "www.")) for item in pixel_items), pixel_items
assert any(item["name"] == "Pixel 7a" and item["price_num"] == 20500 for item in pixel_items), pixel_items
assert any(item["link"].startswith("https://gix.ru/") for item in pixel_items), pixel_items
assert any(item["name"] == "Google Pixel 7" and item["link"] == "" for item in pixel_items), pixel_items
print("\n✅ Pixel-регрессия: карточки, pipe-формат и ссылки разобраны корректно.")

# ── Регрессия: бюджет не раздувается до миллионов ──
for raw_budget in ("45к", "45 к", "45 тысяч", "45 000", "45000"):
    assert parse_budget(raw_budget) == "45000", raw_budget
parsed_budget = full_parse("Нужен телевизор для PS5 до 45к в Ярославле")
assert parsed_budget["budget"] == "45000", parsed_budget
assert "до 45000" in parsed_budget["clean_search_query"], parsed_budget
assert "45000000" not in parsed_budget["clean_search_query"], parsed_budget
print("\n✅ Бюджет 45к нормализуется в 45000.")

# ── НОВЫЙ ТЕСТ: Реальный ответ с 5 телевизорами ──
print(f"\n{'='*60}")
print("ТЕСТ: Реальный ответ Алисы с 5 телевизорами (Формат В)")
print(f"{'='*60}")
items6 = test_format("6. Реальные 5 телевизоров (Формат В)", FORMAT6_REAL_ALICE, budget=BUDGET)

print(f"\n--- ПРОВЕРКИ ---")
assert len(items6) == 5, f"Ожидалось 5 карточек, найдено {len(items6)}"

names_found = [item["name"] for item in items6]
assert any("Hyundai H-LED43BU7006" in n for n in names_found), f"Не найден Hyundai! Найдено: {names_found}"
assert any("TCL 43V6C" in n for n in names_found), f"Не найден TCL 43V6C! Найдено: {names_found}"
assert any("TCL 43P7L-RU" in n for n in names_found), f"Не найден TCL 43P7L-RU! Найдено: {names_found}"
assert any("Sber SDX-43F3111" in n for n in names_found), f"Не найден Sber! Найдено: {names_found}"
assert any("Xiaomi TV A 43 2026" in n for n in names_found), f"Не найден Xiaomi! Найдено: {names_found}"

# Проверка: у Sber описание не попадает в TCL
sber_item = next((item for item in items6 if "Sber SDX-43F3111" in item.get("name", "")), None)
assert sber_item is not None, "Sber не найден среди карточек!"
tcl_43v6c_item = next((item for item in items6 if "TCL 43V6C" in item.get("name", "")), None)
assert tcl_43v6c_item is not None, "TCL 43V6C не найден среди карточек!"

# У Sber должны быть свои плюсы (про Салют ТВ)
sber_pluses = " ".join(sber_item.get("pluses", [])).lower()
assert "салют" in sber_pluses or "экономия" in sber_pluses.lower(), f"Плюсы Sber: {sber_item.get('pluses')}"
# У TCL 43V6C не должно быть описания Sber
tcl_pluses = " ".join(tcl_43v6c_item.get("pluses", [])).lower()
assert "салют" not in tcl_pluses, f"TCL 43V6C содержит описание Sber: {tcl_pluses}"

# Проверка цен — все должны быть int
for item in items6:
    assert isinstance(item.get("price_num"), int), f"Цена не int у {item.get('name')}: {item.get('price_num')}"
    assert item["price_num"] > 0, f"Цена = 0 у {item.get('name')}"

# Проверка: Hyundai цена ~27000
hyundai_item = next((item for item in items6 if "Hyundai" in item.get("name", "")), None)
assert hyundai_item is not None, "Hyundai не найден!"
assert hyundai_item["price_num"] == 27000, f"Цена Hyundai: {hyundai_item['price_num']} (ожидалось 27000)"

print("\n✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ!")
print(f"   - Найдено 5 карточек: {len(items6)}")
print(f"   - Модели: {names_found}")
print(f"   - Sber описание не попало в TCL")
print(f"   - Цены распознаны числами (int)")

# ── Регрессия: формат «Вариант N Название модели: ...» ──
items7 = test_format("7. Вариант N с маркером названия", FORMAT7_VARIANT_MARKER, budget=BUDGET)
assert len(items7) == 3, f"Формат 7: ожидалось 3, найдено {len(items7)}"

# ── Тест build_client_draft ──
print(f"\n{'='*60}")
print("ТЕСТ build_client_draft с форматом 1")
print(f"{'='*60}")
items = parse_alice_response(FORMAT1, budget=BUDGET)
req = Request(
    id=1, user_id=1, product="телевизор", product_name="телевизор 43-50 дюймов 4K",
    budget="45000", city="Москва", use_case="для PS5",
    important_criteria="4K, 120 Гц желательно, HDMI 2.1"
)
draft = build_client_draft(req, items)
print(draft)

print(f"\n{'='*60}")
print("ТЕСТ build_client_draft с 5 реальными телевизорами")
print(f"{'='*60}")
draft6 = build_client_draft(req, items6)
print(draft6)

print(f"\n{'='*60}")
print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО")
print(f"{'='*60}")
