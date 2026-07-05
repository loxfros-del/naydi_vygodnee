# Автоматические ИИ-карточки из автопоиска

This ExecPlan is a living document. It follows `.agent/PLANS.md`.

## Purpose / Big Picture

После этой доработки админ сможет запустить автопоиск, затем нажать кнопку `🤖 Сделать ИИ-карточки из автопоиска`, и бот сам превратит найденные ссылки в 3-5 практичных карточек для проверки. Старый ручной сценарий с вставкой ответа Алисы или GigaChat остаётся запасным путём.

## Progress

- [x] (2026-07-03) Прочитаны требования и текущие точки интеграции: `app/alice_service.py`, `app/db.py`, `app/handlers/admin.py`, `app/keyboards.py`.
- [x] (2026-07-03) Создан `app/ai_cards_service.py` с prompt, API-вызовом, JSON-парсером и общей функцией генерации.
- [x] (2026-07-03) Добавлены env-настройки AI-карточек в `app/config.py`.
- [x] (2026-07-03) JSON-формат подключён к старому `parse_alice_response`.
- [x] (2026-07-03) Обновлены `replace_alice_results`, кнопка и handler `aicards_{req_id}`.
- [x] (2026-07-03) Улучшены отображение карточки и prompt старой Алисы/GigaChat.
- [x] (2026-07-03) Добавлен `tools/test_ai_cards.py`; проверки пройдены.

## Surprises & Discoveries

- Observation: В проекте уже сохранён `origin="alice"` как основной контракт для ИИ-карточек, отчётов и readiness.
  Evidence: `get_alice_results()` выбирает `origin = 'alice'`, поэтому новый автоматический путь должен сохранять тот же origin.
- Observation: `admin_note` уже доступен в `search_results` и не участвует в клиентском отчёте как обязательное поле.
  Evidence: метаданные `role`, `confidence`, `manual_check` сохранены в JSON внутри `admin_note`, без миграции схемы.

## Decision Log

- Decision: Оставить `origin="alice"` для новых автоматических карточек.
  Rationale: Это минимальный риск: существующие кнопки, предпросмотр, полный отчёт и readiness уже работают через `get_alice_results()`.
  Date/Author: 2026-07-03 / Codex.
- Decision: Хранить `confidence` и ручной чек-лист в `admin_note` как JSON.
  Rationale: Это даёт нужное отображение админу без новой миграции БД и без изменения клиентского отчёта.
  Date/Author: 2026-07-03 / Codex.

## Outcomes & Retrospective

Реализован автоматический путь: `aicards_{req_id}` берёт результаты автопоиска, вызывает AI в отдельном thread, сохраняет 3-5 карточек через `replace_alice_results()` и показывает их админу. Старый ручной сценарий сохранён, а при выключенном AI или ошибке API админ получает понятное сообщение.

## Context and Orientation

`app/product_search.py` складывает найденных кандидатов в таблицу `search_results`. `app/db.py` содержит модель `SearchResult` и функцию `replace_alice_results()`, которая заменяет текущие ИИ-карточки. `app/alice_service.py` строит ручной промт и парсит ответ Алисы/GigaChat. `app/handlers/admin.py` показывает кнопки и карточки админу. Новая автоматизация должна взять уже найденные `SearchResult`, отправить их в внешний AI API при включённых настройках, распарсить строгий JSON и сохранить результат через существующий механизм карточек.

## Plan of Work

Сначала добавляется новый сервис `app/ai_cards_service.py`. Он нормализует кандидатов, строит строгий JSON-промт, вызывает API только при `AI_CARDS_ENABLED=true` и наличии ключа, и возвращает понятную ошибку без падения бота. Затем `app/alice_service.py` получает поддержку JSON с `cards`, а `replace_alice_results()` начинает учитывать роли `BEST`, `BACKUP`, `BUDGET`, `CAUTION`, `REJECTED`. После этого добавляется кнопка и handler `aicards_{req_id}` в админке. В конце обновляются отображение карточек, ручной промт и тестовый скрипт.

## Concrete Steps

Рабочая папка: `C:\Users\Пользователь\Documents\naydi_vygodnee`.

Команды проверки:

    python -m compileall .
    python tools/test_alice_parser.py
    python tools/test_ai_cards.py
    python tools/test_db_migration.py
    python tools/test_report_readiness.py

## Validation and Acceptance

Успех: `python tools/test_ai_cards.py` показывает `test_ai_cards: OK`, старый `python tools/test_alice_parser.py` проходит, а в админке появляется кнопка `🤖 Сделать ИИ-карточки из автопоиска` для заявки в статусе `SEARCHING`. Если AI API выключен или ключ не задан, handler отвечает админу понятной ошибкой и не ломает ручной сценарий.

## Idempotence and Recovery

Все изменения добавочные. Повторный запуск тестов безопасен. Если AI API недоступен, состояние заявки и старые карточки не удаляются, потому что сохранение происходит только после успешного получения и парсинга карточек.

## Artifacts and Notes

Будет обновлено после проверки.

Проверки:

    python -m compileall .                         # OK
    python tools/test_alice_parser.py              # OK
    python tools/test_ai_cards.py                  # test_ai_cards: OK
    python tools/test_db_migration.py              # test_db_migration: OK
    python tools/test_report_readiness.py          # test_report_readiness: OK
    python tools/codex_smoke.py                    # codex_smoke: OK

Revision note 2026-07-03: план обновлён после реализации, чтобы отразить фактические файлы, решения и результаты проверок.

## Interfaces and Dependencies

В `app/ai_cards_service.py` должны существовать функции `build_ai_cards_prompt(req, candidates) -> str`, `call_ai_cards_model(prompt: str) -> str`, `parse_ai_cards_json(text: str, budget: int | None = None) -> list[dict]`, `generate_ai_cards_from_candidates(req, candidates) -> dict`. Для HTTP используется уже установленный `requests`. Ключи и модель читаются только из `app.config.settings`.
