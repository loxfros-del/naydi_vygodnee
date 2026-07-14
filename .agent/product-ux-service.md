# Превратить Telegram-бот в цельный сервис подбора товаров

Этот ExecPlan — живой документ. Разделы `Progress`, `Surprises & Discoveries`, `Decision Log` и `Outcomes & Retrospective` обновляются по ходу работы. План ведётся по правилам `.agent/PLANS.md`.

## Purpose / Big Picture

После изменений клиент за первые секунды понимает ценность сервиса, выбирает услугу из компактного меню, оформляет структурированную заявку или сравнение ссылок, видит понятный прогресс и получает не более трёх проверенных рекомендаций. Администратор работает с очередью, видит требуемое действие, утверждает карточки и не может отправить сырой результат. Существующие поиск, ручное подтверждение оплаты, кэш, правила качества и SQLite-данные сохраняются.

## Progress

- [x] (2026-07-12) Зафиксированы `git status`, `git diff --stat`, `git diff` и успешный `python -m compileall -q .`.
- [x] (2026-07-12) Проверены существующие пользовательские и админские handlers, FSM, SQLite-модели, оплата, отчёты, AI-карточки, callbacks, лимиты Telegram и отсутствие продуктовых изображений в репозитории.
- [x] (2026-07-12) Успешно выполнены исходные тесты `tools/test_alice_parser.py`, `tools/test_ai_cards.py`, `tools/test_report_readiness.py` и `tools/test_db_migration.py`.
- [x] (2026-07-13) Добавлен единый продуктовый слой: конфиг пакетов и категорий, русские тексты, клавиатуры, безопасные форматтеры.
- [x] (2026-07-13) Реализованы onboarding, мастер заявки, свободный режим, сравнение ссылок, список заявок, поддержка и feedback.
- [x] (2026-07-13) Добавлены совместимые расширения SQLite, журнал событий, один progress message и state machine.
- [x] (2026-07-13) Обновлены админская очередь, review, AI-card approval и защищённая отправка результата.
- [x] (2026-07-13) Клиентский результат ограничен тремя ролями и очищен от технических полей.
- [x] (2026-07-13) Созданы `docs/product_flow.md`, пять deterministic UX-test runners и offline smoke.
- [x] (2026-07-13) Выполнены финальные compileall, обязательный Alice parser, offline-регрессии, smoke и `git diff --check`.

## Surprises & Discoveries

- Observation: рабочее дерево уже содержит крупный незавершённый набор изменений поиска, включая `app/product_search.py`, `app/config.py`, `app/handlers/admin.py` и новые quality/cache-модули.
  Evidence: исходный `git status --short --branch` показывает 16 изменённых и более 20 новых файлов; текущий diff поиска превышает две тысячи добавленных строк.
- Observation: существующая оплата — ручной Telegram-flow `предпросмотр -> подтверждение администратором -> полный отчёт`, а не Telegram invoice.
  Evidence: callbacks `readytopay_`, `markpaid_` и `sendreport_` в `app/handlers/admin.py`.
- Observation: клиентский тизер содержит захардкоженную цену `149–299 ₽`, хотя цены должны быть конфигурируемыми.
  Evidence: `app/report_builder.py`, функция `_build_client_teaser`.
- Observation: отдельного lifecycle AI-карточек нет; поле `SearchResult.status` одновременно используется как роль рекомендации.
  Evidence: `replace_alice_results` записывает `BEST/BACKUP/BUDGET/DO_NOT_BUY` прямо в `search_results.status`.
- Observation: в проекте нет собственного welcome asset.
  Evidence: поиск изображений нашёл только файлы внутри `.venv`.

## Decision Log

- Decision: внедрять продуктовый UX добавочными модулями и небольшими адаптерами, не переписывая поиск и не меняя `main.py`.
  Rationale: router-модули уже подключены в `main.py`, поэтому новые handlers можно зарегистрировать внутри существующих routers; это сохраняет текущий запуск и снижает риск для незавершённого search diff.
  Date/Author: 2026-07-12 / Codex.
- Decision: сохранить действующий ручной payment flow и заменить только его presentation/copy; при отсутствии цены выводить «Стоимость уточняется».
  Rationale: пользователь прямо запретил ломать оплату и выдумывать суммы.
  Date/Author: 2026-07-12 / Codex.
- Decision: расширять SQLite только через `CREATE TABLE IF NOT EXISTS` и `_ensure_column`, без удаления или переименования существующих полей.
  Rationale: так текущая база открывается как раньше, а новые события, feedback и lifecycle можно хранить без разрушительной миграции.
  Date/Author: 2026-07-12 / Codex.
- Decision: разделить роль рекомендации и lifecycle AI-карточки.
  Rationale: `BEST/BUDGET/...` отвечает за место карточки в отчёте, а `DRAFT/GENERATED/APPROVED/...` — за право показа клиенту.
  Date/Author: 2026-07-12 / Codex.
- Decision: автоматический поиск разрешать только для phone, laptop, tv, headphones, monitor и chair; всё остальное направлять в ручной подбор или сравнение ссылок до вызова `run_product_search`.
  Rationale: это исключает мусорную выдачу неподготовленных категорий и не требует изменения search engine.
  Date/Author: 2026-07-12 / Codex.

## Outcomes & Retrospective

Продуктовый UX реализован без изменения `main.py`, search engine, `.env` и действующего ручного подтверждения оплаты. `/start`, wizard, category gate, progress, link comparison, feedback, очередь администратора, canonical statuses и AI approval подключены к runtime handlers. Клиентская доставка требует `READY`, показывает максимум три рекомендации и только `APPROVED` AI-карточки. Все новые тесты, offline-регрессии, smoke, compileall, Alice parser и `git diff --check` завершились успешно. Commit и push не выполнялись по прямому запрету пользователя.

## Context and Orientation

`main.py` создаёт aiogram Dispatcher и подключает `app.handlers.user.router` и `app.handlers.admin.router`; менять этот файл не требуется. `app/handlers/user.py` сейчас содержит свободный ввод и короткое подтверждение. `app/handlers/admin.py` содержит очередь, запуск `run_product_search`, ручные и AI-карточки, предпросмотр, подтверждение оплаты и delivery. `app/db.py` владеет SQLite-моделями и совместимыми миграциями. `app/report_builder.py` формирует клиентский тизер и полный отчёт. `app/ai_cards_service.py` вызывает OpenAI-compatible API и парсит карточки. `app/keyboards.py` содержит текущие клавиатуры, преимущественно административные.

Новый «продуктовый слой» означает функции и сервисы, которые решают, какой пакет показать, поддерживается ли категория, какой переход статуса разрешён и какие рекомендации можно доставить. Telegram handler только принимает событие, вызывает этот слой и отправляет уже подготовленный текст.

## Plan of Work

Сначала создать `app/product_config.py`, `app/ui_texts.py`, `app/ui_keyboards.py` и `app/ui_formatters.py`. В них разместить пакеты из переменных окружения, шесть автоматических категорий, единые названия кнопок, безопасный HTML, chunking, progress и клиентские карточки. Настройка приветствия читает `WELCOME_IMAGE_FILE_ID` или путь локального файла; отсутствие или ошибка изображения приводит к текстовому fallback.

Затем создать пакет `app/services`. `RequestService` собирает структурированный draft и сохраняет заявку. `PaymentPresentationService` показывает состав услуги без придуманной цены. `SearchOrchestrationService` блокирует неподдерживаемые категории до запуска search engine. `RequestStateMachine` проверяет и журналирует canonical-переходы. `AdminReviewService` управляет approval карточек. `RecommendationService` выбирает не более трёх клиентских ролей. `DeliveryService` разрешает отправку только готового результата. Сервисы аналитики и сравнения ссылок используют лёгкие таблицы SQLite.

После этого расширить `UserStates` и `app/handlers/user.py`: красивый `/start`, главное меню, category-first wizard с back/home/cancel, подтверждение, свободный режим, pricing, how-it-works, my requests, link comparison, support, FAQ и feedback. В `app/handlers/admin.py` добавить счётчики и фильтры, компактную карточку заявки, отдельный debug, gate автопоиска, progress updates, AI-card lifecycle и защищённую delivery. Прямые изменения статуса заменить вызовами state machine.

Наконец обновить `app/report_builder.py`, чтобы убрать hardcoded price и технические поля, показывать максимум три роли, нормальные названия источников и дату проверки. Описать путь в `docs/product_flow.md` и покрыть чистую бизнес-логику детерминированными тестами в `tools/`.

## Concrete Steps

Рабочая директория для всех команд: `C:\Users\Пользователь\Documents\naydi_vygodnee`.

После каждого логического слоя выполнять:

    python -m compileall -q .
    python <релевантный deterministic test>

В финале выполнить:

    python -m compileall .
    python tools/test_alice_parser.py
    python tools/test_product_ui.py
    python tools/test_request_wizard.py
    python tools/test_request_state_machine.py
    python tools/test_product_admin.py
    python tools/test_link_comparison.py
    python tools/test_ai_cards.py
    python tools/test_report_readiness.py
    python tools/test_db_migration.py
    python tools/codex_smoke.py

Live benchmark и сетевой поиск не запускать.

## Validation and Acceptance

Тест onboarding должен доказать отправку фото при настроенном источнике и текстовый fallback при его отсутствии или ошибке, а также наличие всех основных кнопок. Wizard должен пройти happy path для каждой из шести категорий, отклонить неверный бюджет, поддержать back/cancel/home и сформировать безопасную сводку. Gate должен вернуть AUTO только для шести категорий и MANUAL/LINK_COMPARISON для остальных.

Тест state machine должен проверить не менее 25 разрешённых переходов, запрещённые переходы и единственное правило доставки `READY -> DELIVERED`. Тест AI-card flow должен доказать, что GENERATED/DRAFT не попадают клиенту, APPROVED попадает, а недоступный AI создаёт текстовый fallback, не меняющий цену, модель или ссылку кандидата. Rendering-тест должен доказать escaping, chunking, display names и отсутствие `score`, `verify_status`, cache/error-кодов в клиентском тексте. Link comparison должен принимать 2–10 уникальных HTTP(S) URL, отклонять дубли и неверный диапазон и помечать заблокированные данные для ручной проверки.

Локальный smoke создаёт временную SQLite-базу, оформляет заявку, переводит её через review/ready/delivered и сохраняет feedback без обращения к сети и без изменения `bot.db`.

## Idempotence and Recovery

Новые таблицы и колонки создаются идемпотентно. Тесты используют только временные базы. Существующие пользовательские изменения не откатываются и не перезаписываются целиком. При ошибке отдельного milestone исправляется только новый слой; `git reset`, `git restore`, checkout, commit, push и запись в `.env` запрещены.

## Artifacts and Notes

Исходная проверка:

    python -m compileall -q .
    exit code 0

    python tools/test_ai_cards.py
    test_ai_cards: OK

    python tools/test_report_readiness.py
    test_report_readiness: OK

    python tools/test_db_migration.py
    test_db_migration: OK

`tools/test_alice_parser.py` также завершился сообщением «ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО».

## Interfaces and Dependencies

Используются уже установленные aiogram, sqlite3, dataclasses и стандартная библиотека. Новые внешние зависимости не нужны. `ProductConfig` возвращает `ServicePackage` и `CategoryConfig`. `transition_request(request_id, target, actor, reason, extra_fields)` является единственным runtime-входом для смены статуса. `SearchOrchestrationService.route(request)` возвращает `AUTO`, `MANUAL` или `LINK_COMPARISON`. `RecommendationService.for_request(request_id)` возвращает максимум три карточки, исключая rejected и неутверждённые AI-карточки. `DeliveryService.prepare(request)` либо возвращает безопасный отчёт, либо объясняет, почему отправка заблокирована.

---

Изменение 2026-07-12: создан исходный план после аудита и baseline-тестов; зафиксированы ограничения грязного рабочего дерева, ручной оплаты и совместимой SQLite-миграции.

Изменение 2026-07-13: план завершён после runtime-интеграции и полного offline regression run; финальные compileall, Alice parser, smoke и diff-check прошли.
