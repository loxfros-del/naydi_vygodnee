from aiogram.fsm.state import State, StatesGroup


class UserStates(StatesGroup):
    """Состояния пользователя."""
    waiting_for_request = State()       # Ожидание описания товара
    answering_questions = State()       # Ответы на уточняющие вопросы
    waiting_for_confirm = State()       # Подтверждение заявки
    wizard_input = State()              # Текстовый шаг мастера заявки
    waiting_for_links = State()         # Ссылки для отдельного сравнения
    waiting_for_support = State()       # Сообщение специалисту
    waiting_for_problem = State()       # Проблема после получения результата


class AdminStates(StatesGroup):
    """Состояния админа."""
    viewing_request = State()           # Просмотр конкретной заявки
    adding_product = State()            # Добавление найденного варианта
    editing_preview = State()           # Редактирование предпросмотра
    viewing_product = State()           # Просмотр конкретного найденного товара
    editing_price = State()             # Редактирование цены найденного товара
    editing_link = State()              # Редактирование ссылки карточки Алисы
    editing_note = State()              # Добавление заметки к найденному товару
    waiting_alice_response = State()    # Ожидание ответа Алисы от админа
    waiting_market_check = State()      # Ожидание ввода проверки рынка
    editing_market_reason = State()     # Редактирование причины проверки рынка
    editing_market_price = State()      # Редактирование цены проверки рынка
    editing_market_link = State()       # Редактирование ссылки проверки рынка
    editing_store = State()             # Редактирование названия магазина в карточке ИИ
    editing_ai_text = State()           # Редактирование текста AI-карточки
    editing_ai_image = State()          # Замена изображения AI-карточки
    editing_request_note = State()       # Внутренняя заметка по заявке
    editing_comparison = State()         # Ручная корректировка фактов ссылки
