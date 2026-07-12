"""Пользовательский onboarding поверх сохранённого рабочего сценария заявок."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.handlers import user_legacy as _legacy
from app.states import UserStates
from app.ui_keyboards import main_menu_keyboard
from app.ui_texts import (
    BTN_HOW_IT_WORKS,
    BTN_SELECT_PRODUCT,
    BTN_SUPPORT,
    HOW_IT_WORKS_TEXT,
    SUPPORT_TEXT,
    WELCOME_TEXT,
)


router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Показывает новый стартовый экран и очищает незавершённый сценарий."""

    await state.clear()
    await message.answer(
        WELCOME_TEXT,
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == BTN_SELECT_PRODUCT)
async def btn_create_request(message: Message, state: FSMContext):
    """Запускает существующий сценарий создания заявки."""

    await state.clear()
    await state.set_state(UserStates.waiting_for_request)
    await message.answer(
        "Напиши, что нужно найти:\n\n"
        "Пример: <i>«Нужен телевизор для PS5 до 45к в Ярославле»</i>\n\n"
        "Чем подробнее опишешь — тем точнее подбор.",
        parse_mode="HTML",
    )


@router.message(F.text == BTN_HOW_IT_WORKS)
async def show_how_it_works(message: Message, state: FSMContext):
    """Объясняет путь от заявки до результата."""

    await state.clear()
    await message.answer(
        HOW_IT_WORKS_TEXT,
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


@router.message(F.text == BTN_SUPPORT)
async def show_help(message: Message, state: FSMContext):
    """Показывает короткую инструкцию для пользователя."""

    await state.clear()
    await message.answer(
        SUPPORT_TEXT,
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


# Старые обработчики подключаются после новых. Так сохраняются свободный ввод,
# подтверждение заявки, уведомления администраторов и обратная совместимость.
router.include_router(_legacy.router)


def __getattr__(name: str):
    """Сохраняет совместимость с импортами функций из прежнего user.py."""

    return getattr(_legacy, name)
