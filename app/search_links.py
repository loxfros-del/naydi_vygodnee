"""Генерация поисковых ссылок по маркетплейсам и поисковикам.

Строит чистый поисковый запрос из данных заявки, а не вставляет
весь исходный текст пользователя.
"""
from urllib.parse import quote_plus


def _q(text: str) -> str:
    """URL-кодирование запроса."""
    return quote_plus(text)


def build_search_query(
    product_name: str,
    use_case: str = "",
    budget: str = "",
    city: str = "",
    important_criteria: str = "",
) -> str:
    """
    Строит чистый поисковый запрос из данных заявки.
    
    Пример:
    product_name="телевизор", use_case="PS5", budget="45000", city="Ярославль"
    → "телевизор 4K для PS5 до 45000 Ярославль"
    """
    parts = [product_name.strip()]

    # Добавляем ключевые слова из use_case
    is_ps5 = False
    if use_case:
        uc = use_case.strip().lower()
        # Для PS5/приставки добавляем специфичные термины
        if any(kw in uc for kw in ["ps5", "плейстейшен", "playstation", "приставк", "игры"]):
            is_ps5 = True
            parts.append("4K")
            parts.append("для PS5")
        else:
            parts.append(use_case.strip())

    # Добавляем важные критерии (коротко)
    # Для PS5 4K уже добавлен выше; длинный профиль критериев хранится в
    # заявке и применяется в scoring, но не превращает чистый запрос в кашу.
    if important_criteria and not is_ps5:
        crit = important_criteria.strip()
        # Берём первые 50 символов, чтобы не раздувать запрос
        if len(crit) > 50:
            crit = crit[:50]
        parts.append(crit)

    # Бюджет
    if budget:
        # Нормализуем бюджет: "45000" → "до 45000"
        budget_clean = budget.strip()
        if budget_clean.isdigit():
            parts.append(f"до {budget_clean}")
        else:
            parts.append(budget_clean)

    # Город
    if city:
        parts.append(city.strip())

    return " ".join(p for p in parts if p)


def generate_search_links(
    product_name: str,
    city: str = "",
    use_case: str = "",
    budget: str = "",
    important_criteria: str = "",
    clean_search_query: str = "",
) -> list[dict]:
    """
    Генерирует поисковые ссылки для товара.
    Возвращает список: [{"site": "Название", "url": "https://..."}]
    """

    # Если парсер уже собрал нормальный чистый запрос — используем его
    if clean_search_query and clean_search_query.strip():
        search_query = clean_search_query.strip()
    else:
        search_query = build_search_query(
            product_name=product_name,
            use_case=use_case,
            budget=budget,
            city=city,
            important_criteria=important_criteria,
        )

    # Для маркетплейсов лучше без города
    if clean_search_query and clean_search_query.strip():
        marketplace_query = clean_search_query.strip()
        if city:
            marketplace_query = marketplace_query.replace(city, "").strip()
    else:
        marketplace_query = build_search_query(
            product_name=product_name,
            use_case=use_case,
            budget=budget,
            important_criteria=important_criteria,
        )

    links = [
        {
            "site": "Яндекс",
            "url": f"https://yandex.ru/search/?text={_q(search_query + ' купить')}"
        },
        {
            "site": "Google",
            "url": f"https://www.google.com/search?q={_q(search_query + ' купить')}"
        },
        {
            "site": "Яндекс Маркет",
            "url": f"https://market.yandex.ru/search?text={_q(marketplace_query)}"
        },
        {
            "site": "Ozon",
            "url": f"https://www.ozon.ru/search/?text={_q(marketplace_query)}"
        },
        {
            "site": "Wildberries",
            "url": f"https://www.wildberries.ru/catalog/0/search.aspx?search={_q(marketplace_query)}"
        },
        {
            "site": "DNS",
            "url": f"https://www.dns-shop.ru/search/?q={_q(marketplace_query)}"
        },
        {
            "site": "М.Видео",
            "url": f"https://www.mvideo.ru/internal/search.jsp?q={_q(marketplace_query)}"
        },
        {
            "site": "Авито",
            "url": f"https://www.avito.ru/rossiya?q={_q(marketplace_query)}"
        },
    ]

    return links


def generate_product_search_links(title: str, city: str = "") -> list[dict]:
    """Быстрые поисковые ссылки для одной конкретной модели.

    Это именно поиск, а не подстановка выдуманной карточки товара: админ
    открывает нужный магазин и вставляет проверенную прямую ссылку обратно.
    """
    query = " ".join(part for part in (title.strip(), "купить", city.strip()) if part)
    encoded = _q(query)
    marketplace_query = _q(title.strip())
    return [
        {"site": "Яндекс Маркет", "url": f"https://market.yandex.ru/search?text={marketplace_query}"},
        {"site": "Google", "url": f"https://www.google.com/search?q={encoded}"},
        {"site": "Ozon", "url": f"https://www.ozon.ru/search/?text={marketplace_query}"},
        {"site": "DNS", "url": f"https://www.dns-shop.ru/search/?q={marketplace_query}"},
        {"site": "М.Видео", "url": f"https://www.mvideo.ru/internal/search.jsp?q={marketplace_query}"},
        {"site": "Wildberries", "url": f"https://www.wildberries.ru/catalog/0/search.aspx?search={marketplace_query}"},
        {"site": "Авито", "url": f"https://www.avito.ru/rossiya?q={encoded}"},
    ]
