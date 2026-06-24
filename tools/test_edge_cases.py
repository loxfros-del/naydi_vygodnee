"""Edge-case тесты для парсера Алисы."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.alice_service import parse_alice_response

BUDGET = 45000

# ── Case 1: Без пустых строк между товарами (РЕАЛЬНЫЙ БАГ) ──
NO_BLANK_LINES = """Название: Samsung UE43AU7100U
Цена: 43 000 ₽
Магазин: DNS
Ссылка: https://www.dns-shop.ru/
Почему подходит: 4K, Smart TV
Риски: 60 Гц
Название: LG 43UP75006LF
Цена: 39 990 ₽
Магазин: М.Видео
Ссылка: https://www.mvideo.ru/
Почему подходит: 4K, цена
Риски: слабый звук
Название: TCL 50C645
Цена: 47 999 ₽
Магазин: Ozon
Ссылка: https://www.ozon.ru/
Почему подходит: QLED
Риски: выше бюджета"""

# ── Case 2: Смесь с текстом до/после ──
WITH_PREAMBLE = """Вот что я нашла:

Название: Samsung UE43AU7100U
Цена: 43 000 ₽
Магазин: DNS
Ссылка: https://www.dns-shop.ru/
Почему подходит: 4K, Smart TV
Риски: 60 Гц

Название: LG 43UP75006LF
Цена: 39 990 ₽
Магазин: М.Видео
Ссылка: https://www.mvideo.ru/
Почему подходит: 4K, цена
Риски: слабый звук

Надеюсь, это поможет! Если нужны уточнения — пиши."""

# ── Case 3: Нет ссылок ──
NO_LINKS = """Название: Samsung UE43AU7100U
Цена: 43 000 ₽
Магазин: DNS
Ссылка: 
Почему подходит: 4K, Smart TV
Риски: 60 Гц

Название: LG 43UP75006LF
Цена: 39 990 ₽
Магазин: М.Видео
Ссылку нужно искать вручную
Почему подходит: 4K, цена
Риски: слабый звук"""

# ── Case 4: Жирным шрифтом ──
BOLD_FORMAT = """**Samsung UE43AU7100U**
Цена: 43 000 ₽
Магазин: DNS
Ссылка: https://www.dns-shop.ru/
Почему: 4K, Smart TV
Риски: 60 Гц

**LG 43UP75006LF**
Цена: 39 990 ₽
Магазин: М.Видео
Ссылка: https://www.mvideo.ru/
Почему: 4K, цена
Риски: слабый звук"""


def test_case(name: str, text: str, expected_min: int):
    items = parse_alice_response(text, budget=BUDGET)
    status = "✅" if len(items) >= expected_min else "❌"
    print(f"\n{status} {name}")
    print(f"   Найдено: {len(items)} (ожидалось ≥{expected_min})")
    for i, item in enumerate(items, 1):
        print(f"   {i}. {item.get('name', '?')[:60]} | {item.get('price')} | link={'✓' if item.get('link') else '✗'}")
    if len(items) < expected_min:
        print(f"   ⚠️ ПРОБЛЕМА: недобор товаров!")
    return len(items) >= expected_min


results = []
results.append(test_case("1. Без пустых строк между товарами (полевой формат)", NO_BLANK_LINES, 3))
results.append(test_case("2. С преамбулой и постскриптумом", WITH_PREAMBLE, 2))
results.append(test_case("3. Пустые/отсутствующие ссылки", NO_LINKS, 2))
results.append(test_case("4. Жирный шрифт (Markdown)", BOLD_FORMAT, 2))

print(f"\n{'='*50}")
print(f"ИТОГО: {sum(results)}/{len(results)} тестов пройдено")
if all(results):
    print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ ✅")
else:
    print("ЕСТЬ ПРОВАЛЕННЫЕ ТЕСТЫ ❌")