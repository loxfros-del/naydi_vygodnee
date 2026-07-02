from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

from app.link_checks import LinkCheckStatus, normalize_link_check_status


def kb_start() -> ReplyKeyboardMarkup:
    """Главная клавиатура пользователя."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔍 Найти товар")]],
        resize_keyboard=True
    )


def kb_confirm_cancel() -> InlineKeyboardMarkup:
    """Подтверждение или отмена заявки."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Отправить заявку", callback_data="confirm_req"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_req"),
        ]
    ])


def kb_admin_menu() -> InlineKeyboardMarkup:
    """Главное меню админа."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Все заявки", callback_data="admin_all")],
        [InlineKeyboardButton(text="🆕 Новые", callback_data="admin_new")],
        [InlineKeyboardButton(text="🔎 В поиске", callback_data="admin_searching")],
        [InlineKeyboardButton(text="⏳ Ожидают оплаты", callback_data="admin_waiting")],
    ])


def kb_admin_request(req_id: int, status: str) -> InlineKeyboardMarkup:
    """Кнопки действий с заявкой."""
    buttons = []

    if status == "NEW":
        buttons.append([InlineKeyboardButton(text="📂 Взять в работу", callback_data=f"take_{req_id}")])
    elif status == "SEARCHING":
        # Автопоиск и работа с вариантами
        buttons.append([InlineKeyboardButton(text="🔎 Запустить автопоиск", callback_data=f"autosearch_{req_id}")])
        buttons.append([InlineKeyboardButton(text="📦 Показать найденные варианты", callback_data=f"showresults_{req_id}")])
        buttons.append([InlineKeyboardButton(text="🧪 Debug поиска", callback_data=f"debugsearch_{req_id}")])
        buttons.append([InlineKeyboardButton(text="➕ Добавить вариант вручную", callback_data=f"addprod_{req_id}")])
        buttons.append([InlineKeyboardButton(text="🧩 Карточки ИИ", callback_data=f"alicecards_{req_id}")])
        buttons.append([InlineKeyboardButton(text="👁 Полный предпросмотр для админа", callback_data=f"adminpreview_{req_id}")])
        buttons.append([InlineKeyboardButton(text="👀 Клиентский предпросмотр до оплаты", callback_data=f"preview_{req_id}")])
    elif status == "PREVIEW_SENT":
        buttons.append([InlineKeyboardButton(text="👁 Полный предпросмотр для админа", callback_data=f"adminpreview_{req_id}")])
        buttons.append([InlineKeyboardButton(text="✅ Оплата подтверждена", callback_data=f"paid_{req_id}")])
    elif status == "PAID":
        buttons.append([InlineKeyboardButton(text="👁 Полный предпросмотр для админа", callback_data=f"adminpreview_{req_id}")])
        buttons.append([InlineKeyboardButton(text="📩 Отправить полный отчёт", callback_data=f"sendreport_{req_id}")])

    if status in ("SEARCHING", "PREVIEW_SENT", "PAID"):
        buttons.append([InlineKeyboardButton(text="📊 Проверить готовность", callback_data=f"readiness_{req_id}")])

    if status in ("NEW", "SEARCHING", "PREVIEW_SENT", "PAID"):
        buttons.append([InlineKeyboardButton(text="🟡 Проверить через Алису", callback_data=f"alice_{req_id}")])

    if status in ("SEARCHING", "PREVIEW_SENT", "PAID"):
        buttons.append([InlineKeyboardButton(text="🔍 Проверка рынка выполнена", callback_data=f"mcstart_{req_id}")])

    buttons.append([InlineKeyboardButton(text="🔙 Назад к списку", callback_data="admin_all")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_alice_product(result_id: int, status: str, link_check_status: str = LinkCheckStatus.NEEDED.value, price_verified: bool = False) -> InlineKeyboardMarkup:
    """Действия под одной карточкой, полученной из ответа Алисы."""
    top_label = "🏆 ТОП-1 выбран" if status == "BEST" else "🏆 ТОП-1"
    keep_label = "✅ Оставлен" if status in ("APPROVED", "BEST") else "✅ Оставить"
    remove_label = "❌ Убран" if status == "REJECTED" else "❌ Убрать"

    # Статус проверки ссылки
    link_check_status = normalize_link_check_status(link_check_status)
    if link_check_status == LinkCheckStatus.VERIFIED.value:
        check_label = "✅ Ссылка проверена ✓"
    elif link_check_status == LinkCheckStatus.FOUND_UNVERIFIED.value:
        check_label = "✅ Проверил ссылку и цену"
    elif link_check_status == LinkCheckStatus.UNSUITABLE.value:
        check_label = "✅ Проверил ссылку и цену"
    else:
        check_label = "✅ Проверил ссылку и цену"

    # Цена
    if price_verified:
        price_label = "✏️ Цена подтверждена ✓"
    else:
        price_label = "✏️ Изменить цену"

    reserve_label = "✅ Запасной выбран" if status == "RESERVE" else "✅ Запасной"
    budget_label = "💰 Бюджетный выбран" if status == "BUDGET" else "💰 Бюджетный"
    caution_label = "⚠️ Осторожно выбрано" if status == "DO_NOT_BUY" else "⚠️ Осторожно"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=top_label, callback_data=f"alicetop_{result_id}"),
        ],
        [
            InlineKeyboardButton(text=reserve_label, callback_data=f"alicereserve_{result_id}"),
            InlineKeyboardButton(text=budget_label, callback_data=f"alicebudget_{result_id}"),
        ],
        [
            InlineKeyboardButton(text=keep_label, callback_data=f"alicekeep_{result_id}"),
            InlineKeyboardButton(text=remove_label, callback_data=f"aliceremove_{result_id}"),
            InlineKeyboardButton(text=caution_label, callback_data=f"alicecaution_{result_id}"),
        ],
        [
            InlineKeyboardButton(text="✏️ Изменить ссылку", callback_data=f"alicelink_{result_id}"),
            InlineKeyboardButton(text=price_label, callback_data=f"aliceprice_{result_id}"),
        ],
        [
            InlineKeyboardButton(text=check_label, callback_data=f"alicechecklink_{result_id}"),
            InlineKeyboardButton(text="❌ Ссылка не подходит", callback_data=f"alicebadlink_{result_id}"),
        ],
        [
            InlineKeyboardButton(text="⬆️ Предыдущая карточка", callback_data=f"aliceup_{result_id}"),
            InlineKeyboardButton(text="⬇️ Следующая карточка", callback_data=f"alicedown_{result_id}"),
        ],
        [InlineKeyboardButton(text="🔎 Найти ссылку", callback_data=f"alicesearch_{result_id}")],
        [
            InlineKeyboardButton(text="✏️ Изменить магазин", callback_data=f"alicestore_{result_id}"),
            InlineKeyboardButton(text="❌ Нет в наличии", callback_data=f"aliceoutofstock_{result_id}"),
        ],
    ])


def kb_alice_search_links(links: list[dict], req_id: int) -> InlineKeyboardMarkup:
    """Быстрые внешние поиски для конкретной модели."""
    buttons = [[InlineKeyboardButton(text=link["site"], url=link["url"])] for link in links]
    buttons.append([InlineKeyboardButton(text="🔙 К карточкам ИИ", callback_data=f"alicecards_{req_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_admin_product(result_id: int, status: str) -> InlineKeyboardMarkup:
    """Кнопки действий с найденным товаром."""
    buttons = []

    row1 = []
    if status != "BEST":
        row1.append(InlineKeyboardButton(text="⭐ Лучший", callback_data=f"markbest_{result_id}"))
    if status != "CHEAP":
        row1.append(InlineKeyboardButton(text="💸 Дешёвый норм", callback_data=f"markcheap_{result_id}"))
    if row1:
        buttons.append(row1)

    row2 = []
    if status != "RELIABLE":
        row2.append(InlineKeyboardButton(text="🛡 Надёжный", callback_data=f"markreliable_{result_id}"))
    if status != "APPROVED":
        row2.append(InlineKeyboardButton(text="✅ Норм", callback_data=f"markapproved_{result_id}"))
    if row2:
        buttons.append(row2)

    row3 = []
    if status != "DO_NOT_BUY":
        row3.append(InlineKeyboardButton(text="❌ Не брать", callback_data=f"markreject_{result_id}"))
    row3.append(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"deleteresult_{result_id}"))
    buttons.append(row3)

    # Цена и заметка админа
    buttons.append([
        InlineKeyboardButton(text="💰 Изменить цену", callback_data=f"editprice_{result_id}"),
        InlineKeyboardButton(text="📝 Заметка", callback_data=f"editnote_{result_id}"),
    ])

    buttons.append([InlineKeyboardButton(text="➡️ Следующий", callback_data=f"nextresult_{result_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к вариантам", callback_data=f"showresults_back_{result_id}")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_admin_results_list(req_id: int, page: int = 0) -> InlineKeyboardMarkup:
    """Кнопки списка найденных вариантов + навигация."""
    buttons = [
        [InlineKeyboardButton(text="🔎 Запустить автопоиск", callback_data=f"autosearch_{req_id}")],
        [InlineKeyboardButton(text="🧪 Debug поиска", callback_data=f"debugsearch_{req_id}")],
        [InlineKeyboardButton(text="➕ Добавить вручную", callback_data=f"addprod_{req_id}")],
        [InlineKeyboardButton(text="🧩 Карточки ИИ", callback_data=f"alicecards_{req_id}")],
        [InlineKeyboardButton(text="👁 Полный предпросмотр для админа", callback_data=f"adminpreview_{req_id}")],
        [InlineKeyboardButton(text="👀 Клиентский предпросмотр до оплаты", callback_data=f"preview_{req_id}")],
        [InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_admin_back() -> InlineKeyboardMarkup:
    """Кнопка возврата к списку заявок."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="admin_all")]
    ])


# ──────────────────────────────────────────────
#  Клавиатуры проверки рынка
# ──────────────────────────────────────────────

def kb_market_check_actions(check_id: int, verdict: str = "") -> InlineKeyboardMarkup:
    """Действия с вариантом проверки рынка."""
    buttons = [
        [InlineKeyboardButton(text="✅ Добавить в подборку", callback_data=f"mcpromote_{check_id}")],
        [
            InlineKeyboardButton(
                text="💸 Самый дешёвый ✓" if verdict == "BUY" else "💸 Самый дешёвый",
                callback_data=f"mcverdict_BUY_{check_id}",
            ),
            InlineKeyboardButton(
                text="🛡️ Надёжнее, но дороже ✓" if verdict == "RELIABLE" else "🛡️ Надёжнее, но дороже",
                callback_data=f"mcverdict_RELIABLE_{check_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="⚠️ Не брать ✓" if verdict == "DO_NOT_BUY" else "⚠️ Не брать",
                callback_data=f"mcverdict_DO_NOT_BUY_{check_id}",
            ),
        ],
        [
            InlineKeyboardButton(text="✏️ Изменить причину", callback_data=f"mcreason_{check_id}"),
            InlineKeyboardButton(text="✏️ Изменить цену", callback_data=f"mcprice_{check_id}"),
        ],
        [
            InlineKeyboardButton(text="✏️ Изменить ссылку", callback_data=f"mclink_{check_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_market_check_reasons() -> InlineKeyboardMarkup:
    """Быстрые причины почему не брать."""
    reasons = [
        "слабый рейтинг магазина",
        "мало отзывов",
        "не тот город",
        "б/у вместо нового",
        "другая память/цвет/модель",
        "серый товар/сомнительная гарантия",
        "цена ниже рынка и выглядит подозрительно",
        "нет нормальной доставки/возврата",
    ]
    buttons = [[InlineKeyboardButton(text=r, callback_data=f"mcreason_set_{r}")] for r in reasons]
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="mcreason_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_market_check_skip(req_id: int) -> InlineKeyboardMarkup:
    """Диалог: отправить без проверки рынка или проверить."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить без проверки рынка", callback_data=f"mcskip_{req_id}")],
        [InlineKeyboardButton(text="🔍 Сначала проверить рынок", callback_data=f"mcstart_{req_id}")],
    ])


# ──────────────────────────────────────────────
#  Кнопки клиентской воронки
# ──────────────────────────────────────────────

def kb_ready_for_payment(req_id: int, readiness_percent: int = 0) -> InlineKeyboardMarkup:
    """Кнопка: ✅ Подборка готова / запросить оплату."""
    if readiness_percent < 75:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="⚠️ Заявка слабая, отправить предпросмотр всё равно?",
                callback_data=f"forcepreview_{req_id}",
            )],
            [InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")],
        ])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Подборка готова / запросить оплату",
            callback_data=f"readyforpay_{req_id}",
        )],
        [InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")],
    ])


def kb_payment_confirmed(req_id: int) -> InlineKeyboardMarkup:
    """Кнопка: 💰 Оплата подтверждена."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💰 Оплата подтверждена",
            callback_data=f"markpaid_{req_id}",
        )],
        [InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")],
    ])


def kb_send_report_blocked(req_id: int) -> InlineKeyboardMarkup:
    """Отправка отчёта заблокирована: оплата не подтверждена."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💰 Оплата подтверждена",
            callback_data=f"markpaid_{req_id}",
        )],
        [InlineKeyboardButton(
            text="👀 Отправить предпросмотр до оплаты",
            callback_data=f"preview_{req_id}",
        )],
        [InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")],
    ])


def kb_send_without_payment_confirm(req_id: int) -> InlineKeyboardMarkup:
    """Диалог: точно отправить полный отчёт без оплаты?"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✅ Да, отправить",
            callback_data=f"sendwithoutpay_{req_id}",
        )],
        [InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"view_{req_id}",
        )],
    ])


def kb_admin_detail_bottom(req_id: int, readiness_percent: int, is_paid: bool, report_sent: bool, can_send_report: bool) -> InlineKeyboardMarkup:
    """Нижние кнопки детального просмотра заявки — с учётом этапа воронки."""
    buttons = []
    if report_sent:
        buttons.append([InlineKeyboardButton(text="📤 Отчёт уже отправлен", callback_data=f"view_{req_id}")])
    elif is_paid:
        if can_send_report:
            buttons.append([InlineKeyboardButton(
                text="📩 Отправить полный отчёт",
                callback_data=f"sendreport_{req_id}",
            )])
        else:
            buttons.append([InlineKeyboardButton(
                text="📩 Отправить полный отчёт",
                callback_data=f"sendreportblocked_{req_id}",
            )])
    else:
        buttons.append([InlineKeyboardButton(
            text="✅ Подборка готова / запросить оплату",
            callback_data=f"readytopay_{req_id}",
        )])
    if not report_sent:
        buttons.append([InlineKeyboardButton(
            text="👀 Отправить предпросмотр до оплаты",
            callback_data=f"preview_{req_id}",
        )])
        buttons.append([InlineKeyboardButton(
            text="⚠️ Отправить без оплаты",
            callback_data=f"sos_sendreport_{req_id}",
        )])
    buttons.append([InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
