# Реализовать Search Engine V2 рядом с legacy

Этот ExecPlan является живым документом и поддерживается по правилам `.agent/PLANS.md`. Он описывает только безопасный архитектурный переход: старый поиск остаётся fallback, production по умолчанию не переключается.

## Purpose / Big Picture

После изменения проект получит отдельный поисковый слой `app/search_v2`, который понимает запрос как набор жёстких требований, собирает единообразные предложения, исключает несовпадающие товары до ранжирования, группирует одинаковые конфигурации и возвращает не более трёх честных рекомендаций. Поведение доказывается детерминированным end-to-end тестом и режимом `shadow`, который сравнивает V2 с legacy, не меняя клиентский ответ.

## Progress

- [x] (2026-07-13 18:00+03:00) Выполнены `git status`, `git diff --stat`, `git diff`, исходный `python -m compileall -q .`; сохранён `backup_before_search_v2.patch`.
- [x] (2026-07-14) Созданы сериализуемые domain-модели, request adapter и query planner с сохранением hard constraints.
- [x] (2026-07-14) Реализованы adapter contract, source registry/policy, рабочие source adapters и ограниченный orchestrator.
- [x] (2026-07-14) Реализованы normalization, exact match, grouping, market median, risks, automatic/manual/final verification и recommendation roles.
- [x] (2026-07-14) Реализованы `SearchServiceV2`, namespaced cache/snapshots, metrics, shadow comparison и feature-flag bridge без изменения `main.py`.
- [x] (2026-07-14) Добавлены документация и deterministic tests; прошли 97 V2-тестов, 309 общих тестов, 58 stabilization/UI regression-тестов, parser и codex smoke.
- [x] (2026-07-14) Выполнен ровно один ограниченный V2 live smoke; 6/6 кейсов сохранены в `data/search_v2_live_smoke.json`, повторный запуск не выполнялся.

## Surprises & Discoveries

- Observation: рабочее дерево уже содержит незакоммиченный stabilization-pass с `Offer`, market analysis и automatic/manual/final verification.
  Evidence: исходный `git status` показывает новые `app/sources/offer.py`, `app/market_analysis.py`, `app/verification_state.py`; V2 должен переиспользовать их через узкие адаптеры, не импортировать Telegram.
- Observation: точная команда `python -m unittest discover` не видела `tools` как импортируемый test package.
  Evidence: первый запуск нашёл 0 тестов; после добавления `tools/__init__.py` та же команда выполнила 309 тестов.
- Observation: live-источники дают частичную, но безопасно изолированную выдачу.
  Evidence: 6/6 кейсов завершились без process error; DNS во всех кейсах вернул `UNAUTHORIZED`, Avito для кресла — контролируемый timeout 6 секунд, остальные результаты и partial snapshots сохранились.

## Decision Log

- Decision: строить V2 как additive strangler package и не переписывать `app/product_search.py`.
  Rationale: это сохраняет legacy fallback и снижает риск для оплаты, SQLite и Telegram flow.
  Date/Author: 2026-07-13 / Codex.
- Decision: feature flag читать программно с безопасным default `legacy`; `.env` не менять.
  Rationale: production не должен переключиться из-за появления нового кода.
  Date/Author: 2026-07-13 / Codex.
- Decision: реальные источники первой версии подключить через единый async contract и существующие безопасные loaders; блокировки сохранять как partial/manual, без обхода anti-bot.
  Rationale: вертикальный срез важнее семи дублирующих scraper-реализаций.
  Date/Author: 2026-07-13 / Codex.

## Outcomes & Retrospective

Search Engine V2 реализован как additive strangler рядом с legacy. Default остаётся `legacy`; `shadow` не изменяет клиентский payload и сохраняет сравнение, `v2` использует legacy fallback при системной ошибке. Жёсткие ограничения отсекаются до ranking, одинаковые конфигурации группируются, роли не дублируются и не выдумываются, ручное подтверждение подавляет устаревшие automatic warnings.

Offline acceptance выполнен: compileall, 97 V2-тестов, 309 общих тестов, 58 stabilization/UI regression-тестов, Alice parser и codex smoke прошли. Один live smoke завершил 6 кейсов и сохранил диагностический JSON. Production на V2 не переключался; перед переключением нужен период shadow-наблюдения и решение по авторизации DNS/качеству live-source выдачи. Commit и push не выполнялись.

## Context and Orientation

Legacy поиск находится в `app/product_search.py`; разбор запроса — в `app/request_parser.py`; HTTP, cache и проверки — в `app/net_client.py`, `app/search_cache.py`, `app/candidate_verifier.py`; существующие source loaders — в `app/sources`; итоговые карточки и ручная проверка — в `app/ai_cards_service.py`, `app/verification_state.py`, `app/ui_formatters.py`. Новый пакет `app/search_v2` не зависит от aiogram, Telegram Message, CallbackQuery или FSM. Термин offer означает конкретное объявление одного продавца; product group означает одинаковую конфигурацию товара у разных продавцов; shadow означает запуск V2 только для сравнения без влияния на клиентский результат.

## Plan of Work

Сначала создать `models.py`, `serialization.py`, `request_normalizer.py` и `query_planner.py`. Затем определить async `SourceAdapter`, `SourceResult`, registry и orchestrator с максимум тремя source calls и двумя page verifications. После этого пропустить все `RawOffer` через единый normalization/exact/grouping/market/risk pipeline и выбрать уникальные роли. В конце собрать `SearchServiceV2`, cache namespace, metrics и `SearchOrchestrationService`, где `legacy` возвращает legacy, `shadow` возвращает legacy и сохраняет сравнение, а `v2` возвращает V2 с legacy fallback только при системной ошибке.

Telegram handlers не должны импортировать adapters. Интеграция выполняется через сервисный bridge и безопасный config getter; `main.py`, `.env`, schema оплаты и кредиты не меняются.

## Concrete Steps

Рабочая директория: `C:\Users\Пользователь\Documents\naydi_vygodnee`.

После каждого milestone запускать:

    python -m compileall -q app/search_v2 tools/test_search_v2_*.py
    python -m unittest discover -s tools -p "test_search_v2_*.py"

После реализации запускать обязательные regressions:

    python -m compileall -q .
    python -m unittest discover
    python tools/test_alice_parser.py
    python tools/codex_smoke.py
    git diff --check

Один live run выполняется только после всех offline проверок и обязан писать JSON snapshot до завершения процесса.

## Validation and Acceptance

Тестовый запрос `iPhone 16 Pro 256 ГБ новый до 80 000 ₽ в Ярославле` должен сохранять `Pro`, `256`, состояние и город во всех отправленных запросах. Broad query без `Pro` отклоняется до adapter call. Обычный iPhone 16, Pro Max и 128 ГБ попадают только в rejected debug. Partial failure одного adapter сохраняет предложения остальных. Shadow mode возвращает байт-в-байт тот же client payload, что legacy, и отдельно создаёт сравнимый snapshot. Все domain objects и `SearchResultV2` сериализуются в JSON.

## Idempotence and Recovery

Все изменения additive. Повторный offline запуск безопасен; cache keys versioned. Legacy остаётся доступным независимо от состояния V2. Исходный diff сохранён в `backup_before_search_v2.patch`. Запрещены reset, restore, checkout изменённых файлов, commit и push.

## Artifacts and Notes

Baseline: compileall завершился с exit code 0. В исходном tracked diff 38 файлов, 3999 добавлений и 932 удаления; эти пользовательские изменения сохраняются. Live snapshot: `data/search_v2_live_smoke.json`, 6 кейсов, 0 process errors. Статусы: phone и chair — `PARTIAL_SUCCESS`; laptop, tv, headphones и monitor — `NO_EXACT_MATCH`, то есть несовпадающие предложения не попали в рекомендации.

## Interfaces and Dependencies

`SearchServiceV2.search(request) -> SearchResultV2` является единственным публичным API V2. `SourceAdapter.search(request, source_query, context) -> SourceResult` является async contract источника. Модели строятся стандартными dataclass/Enum и JSON-safe serializer без новых зависимостей. Orchestrator принимает adapters через constructor, поэтому deterministic tests используют fake adapters без сети. `SearchOrchestrationService` получает legacy callable и V2 service через dependency injection; mode принимает только `legacy`, `shadow`, `v2`, неизвестное значение нормализуется в `legacy`.

Изменение 2026-07-13: создан исходный план после обязательного safety snapshot; выбран additive strangler и безопасный default legacy.

Изменение 2026-07-14: реализация, deterministic/regression validation и единственный limited live smoke завершены; production default оставлен legacy.
