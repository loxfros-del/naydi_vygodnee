"""Клиентский продуктовый UX поверх совместимого legacy free-text flow."""
from __future__ import annotations

import html
import json
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from app import db
from app.config import settings
from app.handlers import user_legacy as _legacy
from app.keyboards import kb_admin_request
from app.link_checks import LinkCheckStatus
from app.product_config import (
    LINK_COMPARISON,
    QUICK_SELECTION,
    SearchMode,
    get_service_package,
    resolve_welcome_image,
)
from app.search_links import build_search_query, generate_search_links
from app.services.analytics import record_feedback, track_event
from app.services.ai_review import is_ai_card_client_approved
from app.services.link_comparison import prepare_comparison_links, save_comparison_links
from app.services.recommendations import RecommendationService, normalize_recommendation_role
from app.services.request_wizard import RequestWizard, WizardStep, validate_link_comparison_urls
from app.states import UserStates
from app.ui_formatters import (
    chunk_html,
    escape_html,
    format_client_card,
    format_price,
    format_pricing,
    format_progress,
    format_request_summary,
    format_unsupported_category,
    source_display_name,
)
from app.ui_keyboards import (
    CB_CATEGORY_PREFIX,
    CB_COMPARE_LINKS,
    CB_CONDITION_PREFIX,
    CB_FAQ,
    CB_FEEDBACK_PREFIX,
    CB_HOME,
    CB_HOW_IT_WORKS,
    CB_LINKS_CANCEL,
    CB_LINKS_CONFIRM,
    CB_MY_REQUESTS,
    CB_PACKAGE_PREFIX,
    CB_PRICING,
    CB_PRIORITY_PREFIX,
    CB_QUICK_REQUEST,
    CB_RESULT_COMPARE_PREFIX,
    CB_RESULT_DETAIL_PREFIX,
    CB_RESULT_SAVE_PREFIX,
    CB_SELECT_PRODUCT,
    CB_SUPPORT,
    CB_WIZARD_BACK,
    CB_WIZARD_CANCEL,
    CB_WIZARD_CONFIRM,
    CB_WIZARD_EDIT,
    CB_WIZARD_EDIT_PREFIX,
    category_keyboard,
    condition_keyboard,
    feedback_keyboard,
    link_comparison_keyboard,
    main_menu_keyboard,
    priority_keyboard,
    request_confirmation_keyboard,
    request_edit_keyboard,
    service_packages_keyboard,
    wizard_navigation_keyboard,
)
from app.ui_texts import (
    BTN_COMPARE_LINKS,
    BTN_FAQ,
    BTN_HOW_IT_WORKS,
    BTN_MY_REQUESTS,
    BTN_PRICING,
    BTN_SELECT_PRODUCT,
    BTN_SUPPORT,
    FAQ_TEXT,
    HOW_IT_WORKS_TEXT,
    LINK_COMPARISON_PROMPT,
    SUPPORT_TEXT,
    WELCOME_TEXT,
)


router = Router()
logger = logging.getLogger(__name__)


def _track(event_type: str, **kwargs) -> None:
    try:
        track_event(event_type, **kwargs)
    except Exception:
        # Аналитика не должна ломать клиентский сценарий.
        pass


async def _show_home(
    message: Message,
    state: FSMContext,
    *,
    track_start: bool = False,
    show_image: bool = True,
) -> None:
    await state.clear()
    image = resolve_welcome_image()
    if image and show_image:
        try:
            photo = image.value if image.is_file_id else FSInputFile(Path(image.value))
            await message.answer_photo(photo)
        except Exception as exc:
            # Настройка изображения опциональна; текстовое меню всегда доступно.
            logger.warning("Welcome image was not sent (%s): %s", image.kind, exc)
    elif show_image:
        logger.debug("Welcome image was not sent: file_id is empty and local asset is missing or invalid")
    await message.answer(WELCOME_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    if track_start:
        _track("start", user_id=message.from_user.id)


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await _show_home(message, state, track_start=True)


@router.callback_query(F.data == CB_HOME)
async def callback_home(callback: CallbackQuery, state: FSMContext):
    await _show_home(callback.message, state, show_image=False)
    await callback.answer()


def _wizard_from_data(data: dict) -> RequestWizard:
    return RequestWizard.from_dict(data.get("request_wizard"))


async def _save_wizard(state: FSMContext, wizard: RequestWizard, **extra) -> None:
    await state.update_data(request_wizard=wizard.to_dict(), **extra)


def _wizard_markup(wizard: RequestWizard):
    if wizard.current_step is WizardStep.CATEGORY:
        return category_keyboard()
    if wizard.current_step is WizardStep.CONDITION:
        return condition_keyboard()
    if wizard.current_step is WizardStep.PRIORITY:
        return priority_keyboard()
    return wizard_navigation_keyboard(show_back=wizard.position > 0)


async def _show_wizard_step(message: Message, state: FSMContext, wizard: RequestWizard) -> None:
    if wizard.is_complete:
        payload = wizard.to_request_payload()
        await _save_wizard(state, wizard)
        await state.set_state(UserStates.wizard_input)
        await message.answer(
            format_request_summary(payload),
            reply_markup=request_confirmation_keyboard(),
            parse_mode="HTML",
        )
        return
    question = wizard.current_question
    await state.set_state(UserStates.wizard_input)
    await message.answer(
        question.prompt,
        reply_markup=_wizard_markup(wizard),
        parse_mode="HTML",
    )


async def _start_wizard(
    message: Message,
    state: FSMContext,
    package_code: str = "",
    *,
    user_id: int | None = None,
) -> None:
    wizard = RequestWizard()
    await state.clear()
    await _save_wizard(
        state,
        wizard,
        selected_package=package_code or QUICK_SELECTION.code,
        wizard_edit_return=False,
    )
    _track("request_started", user_id=user_id or message.from_user.id, metadata={"mode": "wizard"})
    await _show_wizard_step(message, state, wizard)


@router.message(F.text == BTN_SELECT_PRODUCT)
async def start_wizard_message(message: Message, state: FSMContext):
    await _start_wizard(message, state)


@router.callback_query(F.data == CB_SELECT_PRODUCT)
async def start_wizard_callback(callback: CallbackQuery, state: FSMContext):
    await _start_wizard(callback.message, state, user_id=callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == CB_QUICK_REQUEST)
async def start_quick_request(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(UserStates.waiting_for_request)
    await callback.message.answer(
        "Опишите товар, бюджет и город одним сообщением.\n\n"
        "Например: <i>телевизор для PS5 до 45 000 ₽ в Ярославле</i>",
        parse_mode="HTML",
        reply_markup=wizard_navigation_keyboard(show_back=False),
    )
    _track("request_started", user_id=callback.from_user.id, metadata={"mode": "free_text"})
    await callback.answer()


async def _apply_wizard_answer(message: Message, state: FSMContext, value: object) -> None:
    data = await state.get_data()
    wizard = _wizard_from_data(data)
    result = wizard.answer(value)
    if not result:
        await message.answer(result.error, reply_markup=_wizard_markup(wizard))
        return
    if data.get("wizard_edit_return"):
        wizard.position = len(wizard.questions)
        await _save_wizard(state, wizard, wizard_edit_return=False)
    else:
        await _save_wizard(state, wizard)
    await _show_wizard_step(message, state, wizard)


@router.message(UserStates.wizard_input, F.text)
async def wizard_text_answer(message: Message, state: FSMContext):
    data = await state.get_data()
    wizard = _wizard_from_data(data)
    if wizard.current_step in {WizardStep.CATEGORY, WizardStep.CONDITION, WizardStep.PRIORITY}:
        await message.answer("Выберите вариант кнопкой ниже.", reply_markup=_wizard_markup(wizard))
        return
    await _apply_wizard_answer(message, state, message.text)


@router.callback_query(F.data.startswith(CB_CATEGORY_PREFIX))
async def wizard_category(callback: CallbackQuery, state: FSMContext):
    value = callback.data.removeprefix(CB_CATEGORY_PREFIX)
    if value == "manual":
        await callback.message.answer(format_unsupported_category(), parse_mode="HTML")
    await _apply_wizard_answer(callback.message, state, value)
    await callback.answer()


@router.callback_query(F.data.startswith(CB_CONDITION_PREFIX))
async def wizard_condition(callback: CallbackQuery, state: FSMContext):
    await _apply_wizard_answer(callback.message, state, callback.data.removeprefix(CB_CONDITION_PREFIX))
    await callback.answer()


@router.callback_query(F.data.startswith(CB_PRIORITY_PREFIX))
async def wizard_priority(callback: CallbackQuery, state: FSMContext):
    await _apply_wizard_answer(callback.message, state, callback.data.removeprefix(CB_PRIORITY_PREFIX))
    await callback.answer()


@router.callback_query(F.data == CB_WIZARD_BACK)
async def wizard_back(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if data.get("comparison_urls"):
        await state.set_state(UserStates.waiting_for_links)
        await callback.message.answer(LINK_COMPARISON_PROMPT, parse_mode="HTML")
        await callback.answer()
        return
    wizard = _wizard_from_data(data)
    if not wizard.back():
        await _show_home(callback.message, state, show_image=False)
    else:
        await _save_wizard(state, wizard, wizard_edit_return=False)
        await _show_wizard_step(callback.message, state, wizard)
    await callback.answer()


@router.callback_query(F.data == CB_WIZARD_EDIT)
async def wizard_edit_menu(callback: CallbackQuery):
    await callback.message.answer("Что изменить?", reply_markup=request_edit_keyboard())
    await callback.answer()


@router.callback_query(F.data.startswith(CB_WIZARD_EDIT_PREFIX))
async def wizard_edit_field(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    wizard = _wizard_from_data(data)
    field = callback.data.removeprefix(CB_WIZARD_EDIT_PREFIX)
    if not wizard.edit(field):
        await callback.answer("Это поле нельзя изменить", show_alert=True)
        return
    await _save_wizard(state, wizard, wizard_edit_return=True)
    await _show_wizard_step(callback.message, state, wizard)
    await callback.answer()


@router.callback_query(F.data == CB_WIZARD_CANCEL)
async def wizard_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    _track("request_cancelled", user_id=callback.from_user.id)
    await callback.message.answer("Заявка отменена.", reply_markup=main_menu_keyboard())
    await callback.answer()


async def _notify_admins(bot, request_id: int, product: str, username: str, mode: str) -> None:
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"📥 <b>Новая заявка #{request_id}</b>\n"
                f"Клиент: {escape_html('@' + username if username else 'ID скрыт')}\n"
                f"Товар: <b>{escape_html(product)}</b>\n"
                f"Режим: {escape_html(mode)}",
                reply_markup=kb_admin_request(request_id, "NEW"),
                parse_mode="HTML",
            )
        except Exception:
            pass


async def _finish_request_message(callback: CallbackQuery, state: FSMContext, request_id: int) -> None:
    text = format_progress("accepted", request_id)
    try:
        await callback.message.edit_text(text, parse_mode="HTML")
        db.update_request(request_id, progress_message_id=callback.message.message_id)
    except Exception:
        sent = await callback.message.answer(text, parse_mode="HTML")
        db.update_request(request_id, progress_message_id=sent.message_id)
    await state.clear()


@router.callback_query(F.data == CB_WIZARD_CONFIRM)
async def wizard_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    wizard = _wizard_from_data(data)
    if not wizard.is_complete:
        await callback.answer("Сначала заполните все шаги", show_alert=True)
        return
    payload = wizard.to_request_payload()
    product = payload["product_name"]
    clean_query = payload["clean_search_query"]
    request_id = db.create_request(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        product=product,
        product_name=product,
        budget=payload["budget"],
        city=payload["city"],
        important_criteria=payload["important_criteria"],
        clean_search_query=clean_query,
        is_used_allowed=payload["is_used_allowed"],
        original_query=payload["original_query"],
        category=payload["category"],
        request_mode=payload["request_mode"],
        requirements_json=json.dumps(payload, ensure_ascii=False),
        priority=payload["priority"],
        condition=payload["condition"],
        package_code=data.get("selected_package") or QUICK_SELECTION.code,
        progress_message_id=callback.message.message_id,
    )
    links = []
    if payload["request_mode"] == SearchMode.AUTO.value:
        links = generate_search_links(
            product_name=product,
            city=payload["city"],
            budget=payload["budget"],
            important_criteria=payload["important_criteria"],
            clean_search_query=clean_query,
        )
    db.update_request(request_id, search_links=json.dumps(links, ensure_ascii=False))
    _track("request_completed", user_id=callback.from_user.id, request_id=request_id, metadata={"mode": payload["request_mode"], "category": payload["category"]})
    if payload["request_mode"] == SearchMode.MANUAL.value:
        _track("unsupported_category", user_id=callback.from_user.id, request_id=request_id, metadata={"category": payload["category"]})
    await _finish_request_message(callback, state, request_id)
    await _notify_admins(callback.bot, request_id, product, callback.from_user.username or "", payload["request_mode"])
    await callback.answer("Заявка принята")


@router.message(F.text == BTN_PRICING)
async def pricing_message(message: Message, state: FSMContext):
    await state.clear()
    _track("pricing_opened", user_id=message.from_user.id)
    await message.answer(format_pricing(), reply_markup=service_packages_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == CB_PRICING)
async def pricing_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    _track("pricing_opened", user_id=callback.from_user.id)
    await callback.message.answer(format_pricing(), reply_markup=service_packages_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith(CB_PACKAGE_PREFIX))
async def choose_package(callback: CallbackQuery, state: FSMContext):
    code = callback.data.removeprefix(CB_PACKAGE_PREFIX)
    package = get_service_package(code)
    if not package or not package.available:
        await callback.answer("Пакет временно недоступен", show_alert=True)
        return
    if package.code == LINK_COMPARISON.code:
        await _start_link_comparison(callback.message, state)
    else:
        await _start_wizard(callback.message, state, package.code, user_id=callback.from_user.id)
    await callback.answer(f"Выбран пакет: {package.title}")


@router.message(F.text == BTN_HOW_IT_WORKS)
async def how_message(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HOW_IT_WORKS_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == CB_HOW_IT_WORKS)
async def how_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(HOW_IT_WORKS_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.message(F.text == BTN_FAQ)
async def faq_message(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(FAQ_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")


@router.callback_query(F.data == CB_FAQ)
async def faq_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(FAQ_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML")
    await callback.answer()


def _request_status_label(status: str) -> str:
    from app.services.state_machine import STATUS_LABELS, normalize_request_status
    try:
        return STATUS_LABELS[normalize_request_status(status)]
    except Exception:
        return str(status or "Неизвестно")


async def _render_my_requests(message: Message, state: FSMContext, user_id: int) -> None:
    await state.clear()
    requests = db.get_user_requests(user_id, limit=10)
    if not requests:
        await message.answer("У вас пока нет заявок.", reply_markup=main_menu_keyboard())
        return
    lines = ["<b>Мои заявки</b>", ""]
    for request in requests:
        lines.append(
            f"#{request.id} · {escape_html(request.product_name or request.product or 'товар')}\n"
            f"{escape_html(_request_status_label(request.status))}"
        )
    await message.answer("\n\n".join(lines), reply_markup=main_menu_keyboard(), parse_mode="HTML")


@router.message(F.text == BTN_MY_REQUESTS)
async def my_requests_message(message: Message, state: FSMContext):
    await _render_my_requests(message, state, message.from_user.id)


@router.callback_query(F.data == CB_MY_REQUESTS)
async def my_requests_callback(callback: CallbackQuery, state: FSMContext):
    await _render_my_requests(callback.message, state, callback.from_user.id)
    await callback.answer()


async def _start_link_comparison(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(UserStates.waiting_for_links)
    _track("request_started", user_id=message.chat.id, metadata={"mode": SearchMode.LINK_COMPARISON.value})
    await message.answer(LINK_COMPARISON_PROMPT, reply_markup=wizard_navigation_keyboard(show_back=False), parse_mode="HTML")


@router.message(F.text == BTN_COMPARE_LINKS)
async def compare_links_message(message: Message, state: FSMContext):
    await _start_link_comparison(message, state)


@router.callback_query(F.data == CB_COMPARE_LINKS)
async def compare_links_callback(callback: CallbackQuery, state: FSMContext):
    await _start_link_comparison(callback.message, state)
    await callback.answer()


@router.message(UserStates.waiting_for_links, F.text)
async def receive_comparison_links(message: Message, state: FSMContext):
    result = validate_link_comparison_urls(message.text)
    if not result:
        await message.answer(result.error, reply_markup=wizard_navigation_keyboard(show_back=False))
        return
    urls = list(result.value)
    await state.update_data(comparison_urls=urls)
    lines = ["<b>Проверьте ссылки</b>", ""]
    lines.extend(f"{index}. {escape_html(url)}" for index, url in enumerate(urls, 1))
    await message.answer("\n".join(lines), reply_markup=link_comparison_keyboard(), parse_mode="HTML", disable_web_page_preview=True)


@router.callback_query(F.data == CB_LINKS_CONFIRM)
async def confirm_comparison_links(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    urls = list(data.get("comparison_urls") or [])
    result = validate_link_comparison_urls("\n".join(urls))
    if not result:
        await callback.answer(result.error, show_alert=True)
        return
    items = prepare_comparison_links(result.value, blocked_urls=result.value)
    request_id = db.create_request(
        user_id=callback.from_user.id,
        username=callback.from_user.username or "",
        product=f"Сравнение {len(items)} предложений",
        product_name=f"Сравнение {len(items)} предложений",
        original_query="\n".join(result.value),
        category="link_comparison",
        request_mode=SearchMode.LINK_COMPARISON.value,
        package_code=LINK_COMPARISON.code,
        requirements_json=json.dumps({"urls": list(result.value)}, ensure_ascii=False),
        progress_message_id=callback.message.message_id,
    )
    save_comparison_links(user_id=callback.from_user.id, request_id=request_id, items=items)
    for item in items:
        db.create_search_result(
            request_id=request_id,
            title=item.title or f"Предложение {item.sort_order}",
            price=item.price,
            source=item.source,
            url=item.url,
            snippet="Требуется ручная проверка модели, цены, наличия и продавца.",
            risk_flags=json.dumps(item.risks, ensure_ascii=False),
            status="CANDIDATE",
            origin="link_comparison",
            sort_order=item.sort_order,
            facts_json=json.dumps(item.facts, ensure_ascii=False),
            link_check_status=LinkCheckStatus.FOUND_UNVERIFIED.value,
        )
    _track("request_completed", user_id=callback.from_user.id, request_id=request_id, metadata={"mode": SearchMode.LINK_COMPARISON.value, "links": len(items)})
    await _finish_request_message(callback, state, request_id)
    await _notify_admins(callback.bot, request_id, f"Сравнение {len(items)} ссылок", callback.from_user.username or "", SearchMode.LINK_COMPARISON.value)
    await callback.answer("Ссылки приняты")


@router.callback_query(F.data == CB_LINKS_CANCEL)
async def cancel_link_comparison(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    _track("request_cancelled", user_id=callback.from_user.id, metadata={"mode": SearchMode.LINK_COMPARISON.value})
    await callback.message.answer("Сравнение отменено.", reply_markup=main_menu_keyboard())
    await callback.answer()


async def _start_support(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(UserStates.waiting_for_support)
    await message.answer(SUPPORT_TEXT, reply_markup=wizard_navigation_keyboard(show_back=False), parse_mode="HTML")


@router.message(F.text == BTN_SUPPORT)
async def support_message(message: Message, state: FSMContext):
    await _start_support(message, state)


@router.callback_query(F.data == CB_SUPPORT)
async def support_callback(callback: CallbackQuery, state: FSMContext):
    await _start_support(callback.message, state)
    await callback.answer()


@router.message(UserStates.waiting_for_support, F.text)
async def receive_support(message: Message, state: FSMContext):
    text = " ".join(message.text.split())[:1500]
    if len(text) < 3:
        await message.answer("Опишите вопрос чуть подробнее.")
        return
    _track("support_requested", user_id=message.from_user.id, metadata={"length": len(text)})
    for admin_id in settings.ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                f"🆘 <b>Обращение в поддержку</b>\nКлиент: {message.from_user.id}\n\n{escape_html(text)}",
                parse_mode="HTML",
            )
        except Exception:
            pass
    await state.clear()
    await message.answer("Сообщение передано специалисту.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data.startswith(CB_FEEDBACK_PREFIX))
async def feedback_callback(callback: CallbackQuery, state: FSMContext):
    try:
        _prefix, request_id_raw, rating_raw = callback.data.split(":", 2)
        request_id, rating = int(request_id_raw), int(rating_raw)
    except (ValueError, TypeError):
        await callback.answer("Некорректная оценка", show_alert=True)
        return
    request = db.get_request(request_id)
    if request is None or request.user_id != callback.from_user.id:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    existing = next((item for item in db.get_request_feedback(request_id) if item.user_id == callback.from_user.id), None)
    if existing:
        db.update_feedback(existing.id, rating=rating)
    else:
        record_feedback(user_id=callback.from_user.id, request_id=request_id, rating=rating)
    await callback.answer("Спасибо за оценку!")
    if rating <= 2:
        await state.update_data(problem_request_id=request_id)
        await state.set_state(UserStates.waiting_for_problem)
        await callback.message.answer("Расскажите одним сообщением, что пошло не так — специалист увидит отзыв.")


@router.message(UserStates.waiting_for_problem, F.text)
async def feedback_problem(message: Message, state: FSMContext):
    data = await state.get_data()
    request_id = int(data.get("problem_request_id") or 0)
    comment = " ".join(message.text.split())[:1500]
    existing = next((item for item in db.get_request_feedback(request_id) if item.user_id == message.from_user.id), None)
    if existing:
        db.update_feedback(existing.id, feedback_type="problem", comment=comment)
    else:
        record_feedback(user_id=message.from_user.id, request_id=request_id or None, rating=1, feedback_type="problem", comment=comment)
    for admin_id in settings.ADMIN_IDS:
        try:
            await message.bot.send_message(admin_id, f"⚠️ Низкая оценка по заявке #{request_id}\n{escape_html(comment)}", parse_mode="HTML")
        except Exception:
            pass
    await state.clear()
    await message.answer("Спасибо. Передали проблему специалисту.", reply_markup=main_menu_keyboard())


def _owned_result(user_id: int, result_id: int):
    result = db.get_search_result(result_id)
    request = db.get_request(result.request_id) if result else None
    if result is None or request is None or request.user_id != user_id:
        return None, None
    if result.status in {"REJECTED", "REJECTED_AUTO", "DO_NOT_BUY", "CAUTION"}:
        return None, None
    if result.origin == "alice" and not is_ai_card_client_approved(result):
        return None, None
    return request, result


@router.callback_query(F.data.startswith(CB_RESULT_DETAIL_PREFIX))
async def result_detail(callback: CallbackQuery):
    result_id = int(callback.data.removeprefix(CB_RESULT_DETAIL_PREFIX))
    _request, result = _owned_result(callback.from_user.id, result_id)
    if not result:
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    role = normalize_recommendation_role(result.status) or "BACKUP"
    await callback.message.answer(format_client_card(result, role), parse_mode="HTML", disable_web_page_preview=True)
    await callback.answer()


@router.callback_query(F.data.startswith(CB_RESULT_COMPARE_PREFIX))
async def result_compare(callback: CallbackQuery):
    result_id = int(callback.data.removeprefix(CB_RESULT_COMPARE_PREFIX))
    request, _result = _owned_result(callback.from_user.id, result_id)
    if not request:
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    recommendations = RecommendationService().for_request(request.id)
    cards = [item.card for item in recommendations]
    if not cards:
        cards = [item for item in db.get_search_results(request.id) if item.status in {"BEST", "CHEAP", "RELIABLE", "APPROVED"}][:3]
    lines = ["<b>Короткое сравнение</b>", ""]
    for index, card in enumerate(cards, 1):
        lines.append(
            f"{index}. <b>{escape_html(card.title)}</b> — {format_price(card.price)}\n"
            f"{escape_html(source_display_name(card.source))}"
        )
    await callback.message.answer("\n\n".join(lines), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith(CB_RESULT_SAVE_PREFIX))
async def result_save(callback: CallbackQuery):
    result_id = int(callback.data.removeprefix(CB_RESULT_SAVE_PREFIX))
    request, result = _owned_result(callback.from_user.id, result_id)
    if not result:
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    db.save_product(
        user_id=callback.from_user.id,
        request_id=request.id,
        search_result_id=result.id,
        title=result.title,
        price=result.price,
        source=result.source,
        url=result.url,
    )
    await callback.answer("Сохранено ❤️")


# Legacy подключён последним: старые callback_data, свободный текст и parser API
# продолжают работать, но не перехватывают новый продуктовый сценарий.
router.include_router(_legacy.router)


def __getattr__(name: str):
    return getattr(_legacy, name)


__all__ = ["router", "cmd_start", "feedback_keyboard", "chunk_html"]
