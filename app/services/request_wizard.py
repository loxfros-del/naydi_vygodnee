"""Pure request-wizard model suitable for aiogram FSM serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from app.product_config import (
    AUTO_CATEGORY_BY_CODE,
    SUPPORTED_AUTO_SEARCH,
    SearchMode,
    detect_auto_category,
)
from app.request_parser import build_request_search_query, normalize_request_data
from app.ui_texts import CONDITION_LABELS, PRIORITY_LABELS, WIZARD_QUESTION_TEXTS


class WizardStep(str, Enum):
    CATEGORY = "category"
    PRODUCT = "product"
    BUDGET = "budget"
    CITY = "city"
    CONDITION = "condition"
    CATEGORY_DETAILS = "category_details"
    REQUIREMENTS = "requirements"
    PRIORITY = "priority"
    CONFIRM = "confirm"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class WizardQuestion:
    key: str
    step: WizardStep
    prompt: str
    required: bool = True
    choices: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationResult:
    accepted: bool
    value: Any = None
    error: str = ""
    next_key: str | None = None

    def __bool__(self) -> bool:
        return self.accepted


def _question(
    key: str,
    step: WizardStep,
    *,
    required: bool = True,
    choices: tuple[str, ...] = (),
) -> WizardQuestion:
    return WizardQuestion(
        key=key,
        step=step,
        prompt=WIZARD_QUESTION_TEXTS[key],
        required=required,
        choices=choices,
    )


_CATEGORY = _question("category", WizardStep.CATEGORY)
_PRODUCT = _question("product", WizardStep.PRODUCT)
_BUDGET = _question("budget", WizardStep.BUDGET)
_CITY = _question("city", WizardStep.CITY)
_CONDITION = _question(
    "condition",
    WizardStep.CONDITION,
    choices=tuple(CONDITION_LABELS),
)
_REQUIREMENTS = _question("requirements", WizardStep.REQUIREMENTS, required=False)
_PRIORITY = _question(
    "priority",
    WizardStep.PRIORITY,
    choices=tuple(PRIORITY_LABELS),
)


CATEGORY_QUESTIONS: dict[str, tuple[WizardQuestion, ...]] = {
    "smartphones": (
        _question("phone_memory", WizardStep.CATEGORY_DETAILS, required=False),
        _question("phone_color", WizardStep.CATEGORY_DETAILS, required=False),
        _question("phone_sim_region", WizardStep.CATEGORY_DETAILS, required=False),
    ),
    "laptops": (
        _question("laptop_tasks", WizardStep.CATEGORY_DETAILS, required=False),
        _question("laptop_screen", WizardStep.CATEGORY_DETAILS, required=False),
        _question("laptop_memory", WizardStep.CATEGORY_DETAILS, required=False),
        _question("laptop_type", WizardStep.CATEGORY_DETAILS, required=False),
    ),
    "televisions": (
        _question("tv_diagonal", WizardStep.CATEGORY_DETAILS, required=False),
        _question("tv_4k", WizardStep.CATEGORY_DETAILS, required=False),
        _question("tv_console", WizardStep.CATEGORY_DETAILS, required=False),
        _question("tv_refresh", WizardStep.CATEGORY_DETAILS, required=False),
    ),
    "headphones": (
        _question("headphones_form", WizardStep.CATEGORY_DETAILS, required=False),
        _question("headphones_connection", WizardStep.CATEGORY_DETAILS, required=False),
        _question("headphones_anc", WizardStep.CATEGORY_DETAILS, required=False),
    ),
    "monitors": (
        _question("monitor_diagonal", WizardStep.CATEGORY_DETAILS, required=False),
        _question("monitor_resolution", WizardStep.CATEGORY_DETAILS, required=False),
        _question("monitor_refresh", WizardStep.CATEGORY_DETAILS, required=False),
        _question("monitor_usage", WizardStep.CATEGORY_DETAILS, required=False),
    ),
    "office_chairs": (
        _question("chair_body", WizardStep.CATEGORY_DETAILS, required=False),
        _question("chair_usage", WizardStep.CATEGORY_DETAILS, required=False),
        _question("chair_lumbar", WizardStep.CATEGORY_DETAILS, required=False),
        _question("chair_headrest", WizardStep.CATEGORY_DETAILS, required=False),
    ),
}

if set(CATEGORY_QUESTIONS) != set(SUPPORTED_AUTO_SEARCH):
    raise RuntimeError("Wizard category questions must cover exactly the supported auto categories")


_SKIP_VALUES = {"", "-", "нет", "не важно", "неважно", "пропустить", "любой", "любая"}
_CONDITION_ALIASES = {
    "new": "new",
    "новое": "new",
    "новый": "new",
    "только новое": "new",
    "used": "used",
    "б/у": "used",
    "бу": "used",
    "можно б/у": "used",
    "any": "any",
    "не важно": "any",
    "неважно": "any",
    "любое": "any",
}
_PRIORITY_ALIASES = {
    "price": "price",
    "цена": "price",
    "минимальная цена": "price",
    "reliability": "reliability",
    "надежность": "reliability",
    "надёжность": "reliability",
    "balance": "balance",
    "баланс": "balance",
    "лучший баланс": "balance",
    "delivery": "delivery",
    "доставка": "delivery",
    "быстрая доставка": "delivery",
}
_BUDGET_SUFFIX_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(к|k|тыс(?:\.|яч(?:а|и)?)?)\b",
    flags=re.IGNORECASE,
)
_BUDGET_NUMBER_RE = re.compile(r"\d{1,3}(?:[\s\u00a0]\d{3})+|\d{3,9}")
_URL_RE = re.compile(r"https?://[^\s<>\[\]{}]+", flags=re.IGNORECASE)


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def parse_budget(value: object) -> int | None:
    """Parse a realistic rouble budget; reject tiny, negative and huge values."""

    text = _clean_text(value).lower()
    suffix_match = _BUDGET_SUFFIX_RE.search(text)
    if suffix_match:
        amount = float(suffix_match.group(1).replace(",", ".")) * 1000
        budget = int(amount)
    else:
        number_match = _BUDGET_NUMBER_RE.search(text)
        if not number_match:
            return None
        budget = int(re.sub(r"[\s\u00a0]", "", number_match.group(0)))
    return budget if 500 <= budget <= 100_000_000 else None


def _validate_text(value: object, *, required: bool, max_length: int = 300) -> ValidationResult:
    text = _clean_text(value)
    if text.lower() in _SKIP_VALUES:
        if required:
            return ValidationResult(False, error="Поле нельзя пропустить.")
        return ValidationResult(True, value="")
    if len(text) < 2:
        return ValidationResult(False, error="Ответ слишком короткий.")
    if len(text) > max_length:
        return ValidationResult(False, error=f"Ответ должен быть короче {max_length + 1} символа.")
    return ValidationResult(True, value=text)


def _normalize_category(value: object) -> ValidationResult:
    normalized = _clean_text(value).lower().replace("-", "_").replace(" ", "_")
    if normalized in SUPPORTED_AUTO_SEARCH:
        return ValidationResult(True, value=normalized)
    if normalized in {"manual", "ручной", "ручной_подбор", "другое", "другая"}:
        return ValidationResult(True, value="manual")
    detected = detect_auto_category(value)
    if detected:
        return ValidationResult(True, value=detected.code)
    return ValidationResult(False, error="Выберите одну из доступных категорий или ручной подбор.")


def _normalize_choice(value: object, aliases: Mapping[str, str], error: str) -> ValidationResult:
    normalized = _clean_text(value).lower().replace("ё", "е")
    normalized_aliases = {key.replace("ё", "е"): result for key, result in aliases.items()}
    result = normalized_aliases.get(normalized)
    if result is None:
        return ValidationResult(False, error=error)
    return ValidationResult(True, value=result)


_DETAIL_LABELS = {
    key: question.prompt.rstrip("?.")
    for questions in CATEGORY_QUESTIONS.values()
    for question in questions
    for key in (question.key,)
}


@dataclass(slots=True)
class RequestWizard:
    """Mutable, serializable wizard with deterministic back/edit behaviour."""

    answers: dict[str, Any] = field(default_factory=dict)
    position: int = 0
    cancelled: bool = False

    @property
    def questions(self) -> tuple[WizardQuestion, ...]:
        category = str(self.answers.get("category") or "")
        details = CATEGORY_QUESTIONS.get(category, ())
        return (
            _CATEGORY,
            _PRODUCT,
            _BUDGET,
            _CITY,
            _CONDITION,
            *details,
            _REQUIREMENTS,
            _PRIORITY,
        )

    @property
    def current_question(self) -> WizardQuestion | None:
        if self.cancelled or self.position >= len(self.questions):
            return None
        return self.questions[max(0, self.position)]

    @property
    def current_step(self) -> WizardStep:
        if self.cancelled:
            return WizardStep.CANCELLED
        question = self.current_question
        return question.step if question else WizardStep.CONFIRM

    @property
    def is_complete(self) -> bool:
        return not self.cancelled and self.position >= len(self.questions)

    def _validate(self, question: WizardQuestion, value: object) -> ValidationResult:
        if question.key == "category":
            return _normalize_category(value)
        if question.key == "budget":
            budget = parse_budget(value)
            if budget is None:
                return ValidationResult(False, error="Укажите бюджет в рублях, например: 30 000 или 30к.")
            return ValidationResult(True, value=budget)
        if question.key == "condition":
            return _normalize_choice(value, _CONDITION_ALIASES, "Выберите состояние кнопкой ниже.")
        if question.key == "priority":
            return _normalize_choice(value, _PRIORITY_ALIASES, "Выберите главный приоритет кнопкой ниже.")
        if question.key == "city":
            result = _validate_text(value, required=True, max_length=80)
            if result and ("http://" in result.value.lower() or "https://" in result.value.lower()):
                return ValidationResult(False, error="Укажите название города без ссылки.")
            return result
        return _validate_text(value, required=question.required)

    def answer(self, value: object) -> ValidationResult:
        if self.cancelled:
            return ValidationResult(False, error="Заявка отменена. Начните новый подбор.")
        question = self.current_question
        if question is None:
            return ValidationResult(False, error="Все ответы уже заполнены.")

        result = self._validate(question, value)
        if not result:
            return result

        if question.key == "category":
            old_category = self.answers.get("category")
            if old_category != result.value:
                detail_keys = {
                    item.key
                    for category_questions in CATEGORY_QUESTIONS.values()
                    for item in category_questions
                }
                for key in detail_keys:
                    self.answers.pop(key, None)

        self.answers[question.key] = result.value
        self.position += 1
        next_question = self.current_question
        return ValidationResult(
            True,
            value=result.value,
            next_key=next_question.key if next_question else None,
        )

    def back(self) -> bool:
        if self.cancelled or self.position <= 0:
            return False
        self.position -= 1
        return True

    def edit(self, field_name: str | WizardStep) -> bool:
        if self.cancelled:
            return False
        target = field_name.value if isinstance(field_name, WizardStep) else str(field_name)
        questions = self.questions
        if target == WizardStep.CATEGORY_DETAILS.value:
            index = next((i for i, item in enumerate(questions) if item.step is WizardStep.CATEGORY_DETAILS), None)
        else:
            index = next(
                (i for i, item in enumerate(questions) if item.key == target or item.step.value == target),
                None,
            )
        if index is None:
            return False
        self.position = index
        return True

    def cancel(self) -> None:
        self.cancelled = True

    def reset(self) -> None:
        self.answers.clear()
        self.position = 0
        self.cancelled = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "answers": dict(self.answers),
            "position": self.position,
            "cancelled": self.cancelled,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "RequestWizard":
        values = data or {}
        answers = dict(values.get("answers") or {})
        wizard = cls(
            answers=answers,
            position=max(0, int(values.get("position") or 0)),
            cancelled=bool(values.get("cancelled", False)),
        )
        wizard.position = min(wizard.position, len(wizard.questions))
        return wizard

    def to_request_payload(self) -> dict[str, Any]:
        if not self.is_complete:
            raise ValueError("Wizard is not complete")

        category_code = str(self.answers.get("category") or "manual")
        category = AUTO_CATEGORY_BY_CODE.get(category_code)
        requirements = _clean_text(self.answers.get("requirements"))
        category_details: dict[str, str] = {}
        for question in CATEGORY_QUESTIONS.get(category_code, ()):
            value = _clean_text(self.answers.get(question.key))
            if value:
                category_details[question.key] = value

        condition = str(self.answers.get("condition") or "new")
        product = _clean_text(self.answers.get("product"))
        payload = {
            "product": product,
            "product_name": product,
            "budget": str(int(self.answers["budget"])),
            "city": _clean_text(self.answers.get("city")),
            "important_criteria": requirements,
            "is_used_allowed": condition in {"used", "any"},
            "original_query": product,
            "category": category_code,
            "category_title": category.title if category else "Ручной подбор",
            "condition": condition,
            "priority": str(self.answers.get("priority") or "balance"),
            "requirements": requirements,
            "category_details": category_details,
            "request_mode": SearchMode.AUTO.value if category else SearchMode.MANUAL.value,
        }
        canonical = normalize_request_data(payload)
        payload.update({
            "brand": canonical["brand"],
            "model": canonical["model"],
            "model_modifiers": canonical["model_modifiers"],
            "storage_gb": canonical["storage_gb"],
            "required_features": canonical["required_features"],
            "optional_features": canonical["optional_features"],
            "required_criteria": canonical["required_criteria"],
            "desired_criteria": canonical["desired_criteria"],
        })
        payload["important_criteria"] = ", ".join(
            [
                f"{canonical['storage_gb']} ГБ" if canonical.get("storage_gb") else "",
                *canonical.get("required_features", []),
            ]
        ).strip(" ,")
        payload["clean_search_query"] = build_request_search_query(payload)
        return payload


def _normalize_url(value: str) -> str | None:
    cleaned = value.rstrip(".,;:!?)]}'\"")
    try:
        parsed = urlsplit(cleaned)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if "." not in parsed.hostname and parsed.hostname.lower() != "localhost":
        return None
    netloc = parsed.netloc.lower()
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def validate_link_comparison_urls(text: object) -> ValidationResult:
    """Validate and deduplicate 2–10 HTTP(S) product links."""

    found = _URL_RE.findall(str(text or ""))
    urls: list[str] = []
    for raw_url in found:
        normalized = _normalize_url(raw_url)
        if normalized and normalized not in urls:
            urls.append(normalized)

    if len(urls) < 2:
        return ValidationResult(False, value=tuple(urls), error="Пришлите минимум две корректные ссылки.")
    if len(urls) > 10:
        return ValidationResult(False, value=tuple(urls), error="Можно сравнить не больше 10 ссылок за один раз.")
    return ValidationResult(True, value=tuple(urls))


__all__ = [
    "CATEGORY_QUESTIONS",
    "RequestWizard",
    "ValidationResult",
    "WizardQuestion",
    "WizardStep",
    "parse_budget",
    "validate_link_comparison_urls",
]
