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


def kb_admin_menu(counts: dict[str, int] | None = None) -> InlineKeyboardMarkup:
    """Главное меню админа со счётчиками очередей."""
    counts = counts or {}

    def label(text: str, key: str) -> str:
        return f"{text} · {counts.get(key, 0)}"

    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=label("📥 Новые", "new"), callback_data="admin_new"),
            InlineKeyboardButton(text=label("🔎 В поиске", "searching"), callback_data="admin_searching"),
        ],
        [InlineKeyboardButton(text=label("🧑‍💻 Требуют проверки", "review"), callback_data="admin_review")],
        [
            InlineKeyboardButton(text=label("✅ Готовые", "ready"), callback_data="admin_ready"),
            InlineKeyboardButton(text=label("💳 Ожидают оплаты", "waiting"), callback_data="admin_waiting"),
        ],
        [
            InlineKeyboardButton(text=label("📤 Отправленные", "delivered"), callback_data="admin_delivered"),
            InlineKeyboardButton(text=label("⚠️ Проблемные", "problem"), callback_data="admin_problem"),
        ],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📋 Все заявки", callback_data="admin_all")],
    ])


def kb_admin_request(req_id: int, status: str) -> InlineKeyboardMarkup:
    """Кнопки действий с заявкой."""
    buttons = []
    status = (status or "NEW").upper()

    if status == "NEW":
        buttons.append([InlineKeyboardButton(text="📂 Взять в работу", callback_data=f"take_{req_id}")])
    elif status in ("SEARCHING", "ADMIN_REVIEW", "AI_CARDS_DRAFT"):
        # Автопоиск и работа с вариантами
        buttons.append([InlineKeyboardButton(text="🔎 Запустить / повторить поиск", callback_data=f"autosearch_{req_id}")])
        buttons.append([InlineKeyboardButton(text="📦 Показать найденные варианты", callback_data=f"showresults_{req_id}")])
        buttons.append([InlineKeyboardButton(text="🤖 Создать карточки", callback_data=f"aicards_{req_id}")])
        buttons.append([InlineKeyboardButton(text="➕ Добавить вариант вручную", callback_data=f"addprod_{req_id}")])
        buttons.append([InlineKeyboardButton(text="🧩 Проверить карточки", callback_data=f"alicecards_{req_id}")])
        buttons.append([InlineKeyboardButton(text="👁 Клиентский предпросмотр", callback_data=f"adminpreview_{req_id}")])
        buttons.append([InlineKeyboardButton(text="💳 Отправить предпросмотр и запросить оплату", callback_data=f"readytopay_{req_id}")])
    elif status in ("PREVIEW_SENT", "WAITING_PAYMENT"):
        buttons.append([InlineKeyboardButton(text="👁 Клиентский предпросмотр", callback_data=f"adminpreview_{req_id}")])
        buttons.append([InlineKeyboardButton(text="✅ Оплата подтверждена", callback_data=f"paid_{req_id}")])
    elif status == "PAID":
        buttons.append([InlineKeyboardButton(text="👁 Клиентский предпросмотр", callback_data=f"adminpreview_{req_id}")])
        buttons.append([InlineKeyboardButton(text="✅ Утвердить результат", callback_data=f"markready_{req_id}")])
    elif status == "READY":
        buttons.append([InlineKeyboardButton(text="👁 Клиентский предпросмотр", callback_data=f"adminpreview_{req_id}")])
        buttons.append([InlineKeyboardButton(text="📩 Отправить полный отчёт", callback_data=f"sendreport_{req_id}")])

    if status in ("SEARCHING", "ADMIN_REVIEW", "AI_CARDS_DRAFT", "PREVIEW_SENT", "WAITING_PAYMENT", "PAID", "READY"):
        buttons.append([InlineKeyboardButton(text="📊 Проверить готовность", callback_data=f"readiness_{req_id}")])

    if status in ("NEW", "SEARCHING", "ADMIN_REVIEW", "AI_CARDS_DRAFT", "PREVIEW_SENT", "WAITING_PAYMENT", "PAID"):
        buttons.append([InlineKeyboardButton(text="🟡 Проверить через Алису", callback_data=f"alice_{req_id}")])

    if status in ("SEARCHING", "ADMIN_REVIEW", "AI_CARDS_DRAFT", "PREVIEW_SENT", "WAITING_PAYMENT", "PAID"):
        buttons.append([InlineKeyboardButton(text="🔍 Проверка рынка выполнена", callback_data=f"mcstart_{req_id}")])

    buttons.append([InlineKeyboardButton(text="📝 Заметка по заявке", callback_data=f"reqnote_{req_id}")])
    buttons.append([InlineKeyboardButton(text="⚖️ Ссылки сравнения", callback_data=f"complinks_{req_id}")])
    buttons.append([InlineKeyboardButton(text="🌓 Сравнить Legacy и V2", callback_data=f"v2compare_{req_id}")])
    if status not in ("DELIVERED", "REPORT_SENT", "CANCELLED"):
        buttons.append([InlineKeyboardButton(text="❌ Отменить заявку", callback_data=f"admincancel_{req_id}")])
    buttons.append([InlineKeyboardButton(text="🛠 Debug", callback_data=f"debugsearch_{req_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к списку", callback_data="admin_all")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_alice_product(
    result_id: int,
    status: str,
    link_check_status: str = LinkCheckStatus.NEEDED.value,
    price_verified: bool = False,
    ai_card_status: str = "",
    manual_verification: dict | None = None,
) -> InlineKeyboardMarkup:
    """Действия под одной карточкой, полученной из ответа Алисы."""
    manual = manual_verification if isinstance(manual_verification, dict) else {}
    model_ok = bool(manual.get("manual_model_verified"))
    link_ok = bool(manual.get("manual_link_verified"))
    manual_price_ok = bool(manual.get("manual_price_verified"))
    availability_ok = bool(manual.get("manual_availability_verified"))
    seller_state = str(manual.get("manual_seller_state") or "UNSET").upper()
    top_label = "🏆 ТОП-1 выбран" if status == "BEST" else "🏆 ТОП-1"
    keep_label = "✅ Оставлен" if status in ("APPROVED", "BEST") else "✅ Оставить"
    remove_label = "❌ Убран" if status == "REJECTED" else "❌ Убрать"

    # Статус проверки ссылки
    link_check_status = normalize_link_check_status(link_check_status)
    if link_ok or link_check_status == LinkCheckStatus.VERIFIED.value:
        check_label = "✅ Ссылка проверена ✓"
    else:
        check_label = "☑️ Подтвердить ссылку"

    # Цена
    if price_verified:
        price_label = "✏️ Цена подтверждена ✓"
    else:
        price_label = "✏️ Изменить цену"

    reserve_label = "✅ Запасной выбран" if status == "RESERVE" else "✅ Запасной"
    budget_label = "💰 Бюджетный выбран" if status == "BUDGET" else "💰 Бюджетный"
    caution_label = "⚠️ Осторожно выбрано" if status == "DO_NOT_BUY" else "⚠️ Осторожно"

    lifecycle_buttons = []
    if (ai_card_status or "").upper() != "APPROVED":
        lifecycle_buttons.append([
            InlineKeyboardButton(text="✅ Утвердить карточку", callback_data=f"aiapprove_{result_id}"),
            InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"aiedit_{result_id}"),
        ])
    lifecycle_buttons.append([
        InlineKeyboardButton(text="🖼 Заменить изображение", callback_data=f"aiimage_{result_id}"),
        InlineKeyboardButton(text="🔄 Перегенерировать", callback_data=f"airegen_{result_id}"),
    ])

    checklist_buttons = [
        [
            InlineKeyboardButton(
                text="✅ Модель ✓" if model_ok else "☑️ Подтвердить модель",
                callback_data=f"manualmodel_{result_id}",
            ),
            InlineKeyboardButton(
                text="✅ Наличие ✓" if availability_ok else "☑️ Подтвердить наличие",
                callback_data=f"manualavailable_{result_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="✅ Цена ✓" if manual_price_ok else "☑️ Подтвердить текущую цену",
                callback_data=f"manualprice_{result_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="✅ Продавец ✓" if seller_state == "VERIFIED" else "☑️ Продавец подтверждён",
                callback_data=f"manualsellerok_{result_id}",
            ),
            InlineKeyboardButton(
                text="🟡 Продавца проверить ✓" if seller_state == "REQUIRES_CHECK" else "🟡 Продавца проверить",
                callback_data=f"manualsellercheck_{result_id}",
            ),
        ],
    ]

    return InlineKeyboardMarkup(inline_keyboard=lifecycle_buttons + checklist_buttons + [
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


def kb_admin_product(
    result_id: int,
    status: str,
    manual_verification: dict | None = None,
) -> InlineKeyboardMarkup:
    """Кнопки действий с найденным товаром."""
    manual = manual_verification if isinstance(manual_verification, dict) else {}
    seller_state = str(manual.get("manual_seller_state") or "UNSET").upper()
    buttons = [
        [
            InlineKeyboardButton(
                text="✅ Модель ✓" if manual.get("manual_model_verified") else "☑️ Подтвердить модель",
                callback_data=f"manualmodel_{result_id}",
            ),
            InlineKeyboardButton(
                text="✅ Ссылка ✓" if manual.get("manual_link_verified") else "☑️ Подтвердить ссылку",
                callback_data=f"manuallink_{result_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="✅ Цена ✓" if manual.get("manual_price_verified") else "☑️ Подтвердить цену",
                callback_data=f"manualprice_{result_id}",
            ),
            InlineKeyboardButton(
                text="✅ Наличие ✓" if manual.get("manual_availability_verified") else "☑️ Подтвердить наличие",
                callback_data=f"manualavailable_{result_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="✅ Продавец ✓" if seller_state == "VERIFIED" else "☑️ Продавец подтверждён",
                callback_data=f"manualsellerok_{result_id}",
            ),
            InlineKeyboardButton(
                text="🟡 Проверить ✓" if seller_state == "REQUIRES_CHECK" else "🟡 Продавца проверить",
                callback_data=f"manualsellercheck_{result_id}",
            ),
        ],
    ]

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
    buttons.append([InlineKeyboardButton(text="🛠 Debug варианта", callback_data=f"debugresult_{result_id}")])

    buttons.append([InlineKeyboardButton(text="➡️ Следующий", callback_data=f"nextresult_{result_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к вариантам", callback_data=f"showresults_back_{result_id}")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_admin_results_list(req_id: int, page: int = 0) -> InlineKeyboardMarkup:
    """Кнопки списка найденных вариантов + навигация."""
    buttons = [
        [InlineKeyboardButton(text="🔎 Запустить автопоиск", callback_data=f"autosearch_{req_id}")],
        [InlineKeyboardButton(text="🧪 Debug поиска", callback_data=f"debugsearch_{req_id}")],
        [InlineKeyboardButton(text="➕ Добавить вручную", callback_data=f"addprod_{req_id}")],
        [InlineKeyboardButton(text="🧩 Карточки рекомендаций", callback_data=f"alicecards_{req_id}")],
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
    buttons = [[InlineKeyboardButton(text=r, callback_data=f"mcreason_set_{index}")] for index, r in enumerate(reasons)]
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
    buttons.append([InlineKeyboardButton(text="🔙 К заявке", callback_data=f"view_{req_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
