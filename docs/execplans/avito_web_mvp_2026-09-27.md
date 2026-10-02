# Завершить MVP веб-поиска Avito без изменения правил выдачи

Этот ExecPlan — живой документ. Разделы `Progress`, `Surprises & Discoveries`, `Decision Log` и `Outcomes & Retrospective` обновляются по мере работы. Документ ведётся по правилам `.agent/PLANS.md`.

## Purpose / Big Picture

После изменений пользователь сможет написать товар обычным русским языком, увидеть распознанные параметры, при необходимости поправить их, безопасно запустить единственную платную задачу, наблюдать фактические этапы проверки, отменить выполняющийся поиск и вернуться к нему после перезагрузки по идентификатору в URL. Завершённая выдача по-прежнему будет формироваться существующим Python-конвейером: frontend не станет ослаблять совпадение модели, проверку полной цены, рынка, фотографий, активности страницы или ролей рекомендаций.

Работа считается видимой в браузере на локальном Avito Review: запросы `ps5`, `iphone 16 pro идеал состояние` и `айфон 16 про 256 мск до 100к` должны показывать понятую модель и параметры до отправки. Во время фикстурного поиска должен быть виден номер задачи, прошедшее время, текущий реальный этап и кнопка отмены; URL должен содержать `?job=...`, а повторная загрузка должна выполнить только GET уже существующей задачи.

## Progress

- [x] (2026-09-27 12:00Z) Прочитаны пользовательское ТЗ, `.agent/PLANS.md`, структура репозитория и состояние рабочей копии.
- [x] (2026-09-27 12:15Z) Найдены фактические frontend, backend, API-контракт, job registry, progress stages, дедупликация, лимиты, Apify и тесты.
- [x] (2026-09-27) Зафиксирован зелёный targeted baseline Python/Node до изменения.
- [x] (2026-09-27) Добавлена изолированная deterministic-нормализация русского запроса и тесты сценариев PS5/iPhone.
- [x] (2026-09-27) Добавлены UI распознанных параметров, необязательные PS5-уточнения и восстановление последнего запроса без автоматического запуска.
- [x] (2026-09-27) Добавлены URL/localStorage-восстановление job, компактный job ID, единичный adaptive polling и точный elapsed time.
- [x] (2026-09-27) Реализована идемпотентная отмена job с кооперативной остановкой следующих стадий и abort активного Apify run, когда run ID уже известен.
- [x] (2026-09-27) Улучшены empty/error states, мобильная компоновка и доступность без изменения фильтров безопасности карточек.
- [x] (2026-09-27) Выполнены Python/Node тесты, compileall и обязательный Alice parser test.
- [x] (2026-09-27) Локальный сервер и фикстурный job flow визуально проверены через браузер на desktop и 360/390/430/768/1280/1440 px.

## Surprises & Discoveries

- Observation: существующий интерфейс уже содержит строгий клиентский safety-filter и не показывает `CAUTION`, `REJECTED`, неактивные, просроченные или неполные результаты.
  Evidence: `avito_web/app.js:isVerifiedRecommendation` проверяет роль, URL Avito, полную цену, AI verdict, отсутствие конфликтов и свежую серверную верификацию.
- Observation: backend уже дедуплицирует одинаковые активные запросы до нового платного запуска.
  Evidence: `avito_service/jobs.py:SearchJobRegistry._start` возвращает существующую незавершённую задачу по ключу `(SearchRequest, source_key)`.
- Observation: job хранится только в памяти 15 минут и уже восстанавливается из localStorage, но URL-параметр, последний успешный запрос и отмена отсутствуют.
  Evidence: `ACTIVE_JOB_KEY` и `resumeSavedJob` существуют, а маршрут `/cancel`, `history.pushState` и отдельный last-search storage key отсутствуют.
- Observation: Apify provider умеет завершать известный run через `_abort_run`, но эта возможность вызывается только при timeout.
  Evidence: `avito_service/apify.py` содержит POST `/actor-runs/{id}/abort?gracefully=true`.
- Observation: первоначальный cancel handler не считывал JSON-тело `{}`, поэтому следующий запрос на том же HTTP/1.1 keep-alive соединении разбирался как `{}GET` и получал 501.
  Evidence: браузерный fixture smoke воспроизвёл дефект; handler теперь всегда читает тело, а `tools/test_avito_web_mvp.py` выполняет cancel и GET через один `HTTPConnection`.
- Observation: проектная `.venv` не запускается по пути с кириллицей в текущей среде, а isolated Python не видит `pydantic_settings` без site-packages проекта.
  Evidence: обязательный Alice parser test успешно выполнен bundled Python с добавленным в конец `sys.path` `.venv/Lib/site-packages`; зависимости и `.venv` не изменялись.

## Decision Log

- Decision: не переписывать `avito_web/app.js` на framework и не менять публичную модель отчёта.
  Rationale: текущая vanilla-архитектура и строгая фильтрация уже покрыты тестами; задача требует совместимости и минимального риска.
  Date/Author: 2026-09-27 / Codex
- Decision: вынести только чистую нормализацию в `avito_web/query_normalization.js`, а DOM/state оставить в существующем `app.js`.
  Rationale: так функции тестируются Node без браузерного framework, а aliases/category hints не смешиваются с DOM-кодом.
  Date/Author: 2026-09-27 / Codex
- Decision: отмену сделать кооперативной, а не принудительно завершать Python thread.
  Rationale: принудительная остановка thread небезопасна. Job сразу получает состояние `cancelled`; callback останавливает следующие стадии, а Zen provider отменяет активный Apify run после получения его ID.
  Date/Author: 2026-09-27 / Codex
- Decision: POST создания job и POST отмены никогда не получают автоматический retry; только GET polling может повторяться управляемым циклом.
  Rationale: создание Apify run может быть платным, а неоднозначный повтор POST нарушит существующую защиту расходов.
  Date/Author: 2026-09-27 / Codex

## Outcomes & Retrospective

MVP завершён без смены стека и без ослабления правил выдачи. Натуральный запрос теперь явно нормализуется до существующего `SearchRequest`; PS5 остаётся семейным запросом с необязательными уточнениями. Job виден в URL и localStorage, восстанавливается только через GET, показывает серверные стадии и elapsed, а cancel идемпотентно останавливает публикацию результата и abort-ит известный Apify run.

Проверки: 172 backend-теста, 3 Node-набора frontend/normalization, `python -m compileall .` и `tools/test_alice_parser.py` прошли. В браузере проверены три обязательных запроса, progress/reload/cancel, отсутствие ошибок консоли и horizontal overflow на ширинах 360–1440 px. Платный live Apify запуск намеренно не выполнялся: взаимодействие с ним покрыто unit-тестом.

Оставшиеся ограничения осознанны: registry хранится в памяти и не переживает рестарт процесса; Python thread не прерывается насильно, поэтому уже выполняющийся сторонний HTTP/AI вызов может завершиться до следующей проверки cancel. Для следующего production-шага нужен durable JobStore (Redis/Postgres) и наблюдаемость долгих внешних вызовов.

## Context and Orientation

`avito_web/index.html`, `avito_web/styles.css` и `avito_web/app.js` — отдельный vanilla frontend Avito Review. Его обслуживает `avito_service/http_api.py` на localhost, по умолчанию порт 8091. `POST /api/avito/jobs` принимает параметры поиска и возвращает snapshot фоновой задачи; `GET /api/avito/jobs/{jobId}` возвращает её состояние. Старый `POST /api/avito/search` является совместимым алиасом создания job. `GET /health`, `GET /api/session`, `POST /api/avito/analyze-dataset` и `POST /api/support` должны остаться совместимыми.

`avito_service/models.py:SearchRequest` поддерживает query, location, category, max_results, mode, priority, desired_results, price_min, price_max, required_storage, required_sim, required_condition, attributes и pickup_only. `avito_service/http_api.py:parse_search_request` отображает camelCase JSON frontend на эти поля. Режимы называются `bargain` и `find`; frontend уже показывает их как «Найти выгоднее» и «Найти точно».

`avito_service/jobs.py` хранит задачи в памяти. Snapshot содержит jobId, state, stage, message, percent, elapsedSeconds, remainingSeconds, softTargetExceeded, alive и workerAlive, а после завершения result либо публичную ошибку. Фактические стадии из `avito_service/service.py` — queued, collect, market, prepare, text, photo, refresh, ranking и complete. Проценты формирует backend; frontend не должен вычислять ложный процент. Job не переживает рестарт процесса, поэтому 404 после перезапуска должен стать понятным состоянием «задача недоступна».

`avito_service/apify.py:ZenStudioProvider` выполняет платный POST ровно один раз и повторяет только GET. Его private-метод `_abort_run` безопасно завершает известный run. `avito_service/jobs.py:SearchJobRegistry` возвращает один и тот же активный job для одинакового SearchRequest, поэтому frontend должен использовать возвращённый jobId и не пытаться сам повторять POST.

Основная тестовая инфраструктура — стандартный `unittest` в `tools/test_avito_service.py` и небольшие Node VM-тесты `tools/test_avito_card_presentation.js` и `tools/test_avito_results_presentation.js`. Обязательная проектная проверка после любой правки парсинга — `python tools/test_alice_parser.py`; хотя Alice parser не меняется, команда всё равно будет выполнена по AGENTS.md.

## Plan of Work

Сначала в `avito_web/query_normalization.js` будет создана чистая конфигурация aliases и recognizers. Функция `normalizeSearchQuery(text)` вернёт исходный текст, канонический backend query, категорию, семейство товара и набор распознанных полей. Она распознает минимум семейство PS5 без обязательной версии, iPhone 16 Pro, MacBook Air M2, состояния «идеал/идеальное» и «хорошее», город «мск», цену `до 100к/100 тыс/100000` и память `256гб/256 gb`. Отдельный Node-тест докажет три обязательных сценария.

Затем `avito_web/index.html` получит компактную область «Поняли запрос», необязательные PS5 controls, блок последнего запроса и более информативную progress card с job ID, подсказкой восстановления и кнопкой отмены. `avito_web/app.js` применит только очевидные распознанные поля, покажет их до поиска и позволит изменить через обычные form controls. Последний успешный SearchRequest сохранится отдельно и по кнопке только заполнит форму.

Job state будет упорядочен вокруг одного текущего поколения поиска. Сохранение job обновит `?job=JOB_ID` через History API. При загрузке URL имеет приоритет над localStorage; выполняется только GET. Polling будет последовательно ожидать 750–2000 мс в зависимости от длительности, прекратится на complete/error/cancel, а устаревший цикл перестанет обновлять DOM. Прошедшее время будет форматироваться как минуты и секунды из server `elapsedSeconds`.

Для отмены `avito_service/jobs.py` получит идемпотентный `cancel(job_id)`. Состояние `cancelled` блокирует запись результата и заставляет progress callback выбросить публичную отмену. В production callback будет доступен `cancel_requested`, а `AvitoAnalysisService` передаст его только provider, который объявляет поддержку отмены. `ZenStudioProvider` проверит сигнал до платного POST, сразу после ответа старта и между GET polls; если run ID известен, вызовет существующий `_abort_run`. `avito_service/http_api.py` добавит `POST /api/avito/jobs/{jobId}/cancel` с теми же access/owner checks. Endpoint не запускает новый поиск и не повторяет provider POST.

Наконец будут уточнены пользовательские сообщения об empty, network, timeout, busy, budget, cancelled и job-not-found, CSS tokens и mobile rules для 360–430 px. Существующая функция `isVerifiedRecommendation` и public/admin разграничение останутся без ослаблений.

## Concrete Steps

Все команды выполняются из `C:\Users\Пользователь\Documents\naydi_vygodnee`.

До изменения выполнить targeted baseline:

    python -B -m unittest tools.test_avito_service
    node tools/test_avito_card_presentation.js
    node tools/test_avito_results_presentation.js

После каждого логического блока повторять соответствующий тест. В конце выполнить:

    python -m compileall .
    python tools/test_alice_parser.py
    python -B -m unittest tools.test_avito_service tools.test_avito_evidence tools.test_avito_market_quality tools.test_avito_collection_quality tools.test_avito_market_cache tools.test_avito_ops_quality tools.test_avito_web_mvp
    node tools/test_avito_query_normalization.js
    node tools/test_avito_card_presentation.js
    node tools/test_avito_results_presentation.js

Локальный smoke test запускается командой:

    python -m avito_service --host 127.0.0.1 --port 8091

После запуска открыть `http://127.0.0.1:8091/`, проверить ширины 390 и 1440 пикселей, а также отсутствие horizontal overflow. Live платный поиск не запускать без необходимости; job flow и cancel проверять через фикстурный HTTP-тест.

## Validation and Acceptance

Node normalization test должен подтвердить: `ps5` возвращает семейство `ps5`, категорию gaming и не требует конкретную версию; `iphone 16 pro идеал состояние` возвращает модель iPhone 16 Pro и состояние Отличное; `айфон 16 про 256 мск до 100к` возвращает iPhone 16 Pro, 256 ГБ, Москву и priceMax 100000.

Python HTTP tests должны подтвердить: два одинаковых активных POST возвращают один job; GET показывает реальные стадии и результат; cancel endpoint возвращает cancelled, повторный cancel остаётся cancelled, worker не публикует частичный result; неизвестный ID даёт публичный 404; malformed request остаётся 400; POST создания job не повторяется.

В браузере запрос должен немедленно показывать распознанные параметры и не блокировать `ps5` обязательным выбором. После создания job должен появиться номер, URL `?job=...`, реальный stage/message и elapsed. Reload не создаёт новый POST. Cancel показывает «Поиск остановлен» и не запускает новый поиск. Завершённый пустой отчёт не называется ошибкой и объясняет, что непроверенные объявления скрыты. На 390 px controls, chips, progress и cards не имеют горизонтального overflow.

## Idempotence and Recovery

Все новые storage записи версионируются отдельными ключами и могут быть безопасно удалены браузером. URL меняется через `history.replaceState`, поэтому перезагрузка не добавляет записи истории. Cancel идемпотентен. Если процесс сервера перезапущен и job исчез, frontend удаляет только свой active-job handle и сохраняет заполненную форму. Никакие `.env`, базы данных, Telegram token, `main.py`, схема SQLite, оплата или кредиты не изменяются.

Рабочая копия до начала уже содержит пользовательские изменения и множество untracked Avito-файлов. Работа не должна откатывать, перемещать или форматировать несвязанные файлы. Если тест выявит прежнюю ошибку вне этого объёма, она будет зафиксирована отдельно, а не скрыта массовым refactor.

## Artifacts and Notes

Текущий API создания задачи:

    POST /api/avito/jobs
    202 {"jobId":"...","state":"running","stage":"queued",...}

Текущий безопасный duplicate behavior:

    SearchJobRegistry._start -> existing unfinished SearchJob

Планируемый cancel contract:

    POST /api/avito/jobs/{jobId}/cancel
    200 {"jobId":"...","state":"cancelled","stage":"cancelled","errorCode":"SEARCH_CANCELLED",...}

## Interfaces and Dependencies

Новые внешние зависимости не добавляются. `avito_web/query_normalization.js` должен работать как browser global `window.NaydiQueryNormalization` и как CommonJS export для Node-теста. Его основной интерфейс:

    normalizeSearchQuery(text) -> {
        originalQuery, canonicalQuery, family, category,
        recognized: [{key, label, value}],
        fields: {location?, priceMax?, requiredCondition?, requiredStorage?},
        attributes: {storage?, edition?, details?}
    }

`SearchJobRegistry.cancel(job_id)` возвращает `SearchJob | None`. `SearchJob.cancel_requested()` возвращает bool. Production progress callable получает attribute `cancel_requested`, который `AvitoAnalysisService.search` использует только при `provider.supports_cancellation`. `ZenStudioProvider.collect`, `collect_market`, `collect_to_target` и `refresh` принимают optional keyword `cancel_requested: Callable[[], bool] | None`.

Change note (2026-09-27): создан первоначальный ExecPlan после repository audit; зафиксированы существующие safety-фильтры, API и минимальный путь реализации без смены стека.

Change note (2026-09-27): план закрыт после реализации, automated/browser validation и keep-alive regression fix; добавлены фактические результаты и ограничения.
