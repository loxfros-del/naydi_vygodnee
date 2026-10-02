# Добавить безопасную телеметрию live pilot для Avito Review

Этот ExecPlan — живой документ. Разделы `Progress`, `Surprises & Discoveries`, `Decision Log` и `Outcomes & Retrospective` поддерживаются по правилам `.agent/PLANS.md`.

## Purpose / Big Picture

После изменения владелец сможет включить `AVITO_LIVE_PILOT=1`, вручную выполнить ограниченное число реальных поисков и получить по каждому job атомарный JSON trace без секретов. Trace покажет длительность фактических стадий, воронку объявлений, известные и оценочные расходы Apify/AI, cache hit/miss, dedup и детали отмены. Локальная команда `tools/summarize_avito_pilot.py` агрегирует сохранённые traces в таблицу и медианы, не создавая платных запросов.

Бизнес-логика выдачи, бюджеты, hard filters, ranking, AI prompts и отсутствие retry платного POST остаются без изменений. Проверка выполняется бесплатными unit/synthetic тестами; live Apify/AI поиск автоматически не запускается.

## Progress

- [x] (2026-09-27) Прочитано новое ТЗ и полностью перечитан `.agent/PLANS.md`.
- [x] (2026-09-27) Найдены job lifecycle, реальные stages, Apify receipt/cost accounting, AI cost accounting, spending guard, market/AI cache, dedup и final revalidation.
- [x] (2026-09-27) Targeted baseline: 104 Python tests и query-normalization test прошли.
- [x] (2026-09-27) Добавлены отказоустойчивые `SearchTrace` и atomic `PilotTraceStore`.
- [x] (2026-09-27) Trace подключён к job lifecycle, parse timing, dedup и cancel.
- [x] (2026-09-27) Добавлена Apify run telemetry без retry и без секретов.
- [x] (2026-09-27) Добавлены AI timing/usage и market/AI cache telemetry.
- [x] (2026-09-27) Добавлены CLI summarizer и 7 synthetic tests.
- [x] (2026-09-27) README и pilot guide дополнены без изменения `.env`.
- [x] (2026-09-27) Финальная регрессия: 256 Python и 3 Node теста, `compileall` и Alice parser test прошли.

## Surprises & Discoveries

- Observation: проект уже содержит значительную cost/funnel основу, которую не нужно дублировать.
  Evidence: `CostSummary` хранит Apify/AI стоимость и признак оценки, `PipelineAudit` — raw/unique/filter/AI/final counts, а `usageTotalUsd` и charged events читаются в `avito_service/apify.py`.
- Observation: фактическая стоимость Apify может быть неизвестна после `SUCCEEDED`, поэтому текущий pipeline использует консервативный accounted cost.
  Evidence: `_complete_event_cost` и `apify_cost_estimated` различают завершённый receipt и оценку; новый trace обязан хранить unknown actual как `null`, не как ноль.
- Observation: AI reviewer уже получает token usage, но сохраняет только стоимость и признак оценки.
  Evidence: `_normalize_usage` читает `prompt_tokens`/`completion_tokens`, а `AIReview` пока не переносит их в отчёт.
- Observation: market cache TTL — 15 минут, но публичный `get` не возвращает возраст.
  Evidence: cache entry хранит expiry; возраст можно получить как `ttl - (expires - now)` без смены формата.
- Observation: исторический expanded pilot не имел единого trace и не позволяет точно выделить slowest stage.
  Evidence: artifact `20260923T104021Z-expanded-gpt/audit.json` фиксирует 259,9 с, 200 collected, 48 text checks и 8 photo checks, но не per-stage durations; Apify cost $1,603 был estimated, AI cost 12,52 руб. actual.

## Decision Log

- Decision: traces создаются в памяти для каждого job, но сохраняются на диск только при `AVITO_LIVE_PILOT=1`.
  Rationale: это даёт единый lifecycle и тестируемость без постоянного накопления пользовательских запросов вне пилота; pilot mode включает только дополнительное логирование.
  Date/Author: 2026-09-27 / Codex
- Decision: один JSON-файл на job записывается через временный файл и atomic replace.
  Rationale: jobs могут завершаться параллельно; отдельные файлы исключают конкурирующий append и упрощают повторное чтение.
  Date/Author: 2026-09-27 / Codex
- Decision: actual и estimated cost хранятся раздельно; неизвестный actual всегда `null`.
  Rationale: ноль означал бы бесплатный вызов и исказил бы пилот.
  Date/Author: 2026-09-27 / Codex
- Decision: provider telemetry передаётся только Zen Studio через optional callback и capability flag.
  Rationale: тестовые и сторонние providers сохраняют старые сигнатуры; изменение остаётся обратно совместимым.
  Date/Author: 2026-09-27 / Codex

## Outcomes & Retrospective

Реализован additive pilot mode: один atomic JSON trace на job, per-stage timings, funnel, Apify runs, AI/cache/cost/cancel/dedup и offline summary. Бизнес-логика, prompts, ranking, budgets и paid POST retry не менялись. Ошибки хранилища telemetry остаются вторичными. Платные live-прогоны не выполнялись; поэтому фактический текущий bottleneck должен быть определён по новым traces.

## Context and Orientation

`avito_service/http_api.py` разбирает JSON в `SearchRequest` и вызывает `SearchJobRegistry`. `avito_service/jobs.py` создаёт background thread, дедуплицирует одинаковые активные запросы, публикует progress и обрабатывает cancel. `avito_service/service.py` выполняет market collection, deterministic filtering, text AI, photo AI, final URL refresh и ranking. `avito_service/apify.py` запускает Zen Actor одним POST, затем использует только GET polling; `maxTotalChargeUsd` и `SpendingGuard` ограничивают расходы. `avito_service/ai.py` получает provider usage и вычисляет actual/estimated AI cost. `avito_service/market_cache.py` хранит рыночный снимок до 15 минут; `_review_cache` в service хранит полные AI reviews.

Trace — это структурированный JSON жизненного цикла одной job. Actual cost — стоимость, подтверждённая provider receipt. Accounted/estimated cost — консервативная сумма, используемая защитой бюджета при задержке биллинга. Эти значения нельзя смешивать.

## Plan of Work

Создать `avito_service/telemetry.py` с thread-safe `SearchTrace` и `PilotTraceStore`. Recorder будет принимать безопасную нормализованную форму `SearchRequest`, stage transitions, Apify/AI/cache events и финальный `AnalysisReport`. Любая ошибка telemetry поглощается и не меняет результат поиска.

В `jobs.py` создавать recorder вместе с job, отмечать dedup hit, cancel request, worker stop, error и response serialization. В `http_api.py` измерять только parse/normalization и передавать миллисекунды registry; при включённом `AVITO_LIVE_PILOT` создавать store в `runtime/avito_pilot_traces`.

В `service.py` использовать recorder, прикреплённый к progress callback: передавать Zen optional callback/purpose, фиксировать market-cache hit/age, AI cache hit/miss, text/photo calls, duration, counts и cost. `MarketSnapshotCache` получит additive lookup с metadata. `AIReview` получит необязательные token counts; старые constructors и cache records останутся совместимыми.

В `apify.py` записывать один run record на каждый фактический POST: purpose, run id, timestamps, duration, requested cap, actual cost или null, accounted cost, items, status, cancel/abort и error category. POST retry останется равным одному.

Создать `tools/summarize_avito_pilot.py` на стандартной библиотеке и `tools/test_avito_pilot_telemetry.py` с synthetic traces: success, empty, error, cancel, unknown cost, storage failure и summary. README и `docs/avito_pilot.md` получат безопасные команды ручного включения пилота и создания чистого окружения на пути без кириллицы.

## Concrete Steps

Рабочая директория: `C:\Users\Пользователь\Documents\naydi_vygodnee`.

Targeted baseline и финальная регрессия:

    python -B -m unittest tools.test_avito_service tools.test_avito_spending tools.test_avito_ai_budget tools.test_avito_market_cache tools.test_avito_web_mvp
    node tools/test_avito_query_normalization.js

Новые тесты:

    python -B -m unittest tools.test_avito_pilot_telemetry
    python tools/summarize_avito_pilot.py --trace-dir <synthetic-dir>

Обязательные финальные команды:

    python -m compileall .
    python tools/test_alice_parser.py

Из-за известной поломки launcher старой `.venv` разрешено использовать bundled Python и добавить существующий `.venv/Lib/site-packages` в конец `sys.path`; зависимости или `.venv` при этом не изменяются.

## Validation and Acceptance

Synthetic success trace должен содержать ненулевые реальные timings из тестового clock, funnel из `PipelineAudit`, actual/estimated costs без подмены unknown нулём, AI model/calls/tokens и cache status. Cancel test должен показать stage на момент запроса, active Apify run, abort result, worker stop latency, отсутствие опубликованного результата и отсутствие дорогого этапа после cancel. Storage failure не должен изменить успешный job.

Summary tool должен прочитать несколько synthetic JSON-файлов, вывести строки поисков, status counts, median/p95 latency, median известных costs, cache-hit count, average funnel values, slowest stage и most expensive component. Пустая директория должна завершаться понятным сообщением без traceback.

Ни один test не должен обращаться в Apify или AI. Счётчик POST в existing provider tests должен остаться равен одному.

## Idempotence and Recovery

Временный trace записывается рядом с целевым файлом и заменяет только JSON того же job id. Повторная запись безопасна. Некорректные или частично записанные traces summarizer пропускает с предупреждением. Отключение `AVITO_LIVE_PILOT` прекращает disk persistence, но не меняет поиск. Удалять `.env`, `.venv`, базы или spending ledger нельзя.

## Artifacts and Notes

Существующие источники истины: `CollectionBatch.apify_cost_usd`, `CostSummary`, `PipelineAudit`, `AIReview.request_count`, `AIReview.cost_rub`, Apify `usageTotalUsd`, charged events и `runtime/avito_spend.json`. Новый trace агрегирует их, но не заменяет budget enforcement.

## Interfaces and Dependencies

Новые внешние зависимости не добавляются. `SearchTrace` предоставляет безопасные методы `stage`, `record_cache`, `record_ai`, `record_apify`, `request_cancel`, `finish_report`, `finish_error` и `worker_stopped`. `PilotTraceStore.write(trace_dict)` выполняет atomic JSON write и не выбрасывает ошибки наружу. `MarketSnapshotCache.lookup(key)` возвращает `(items, {hit, age_seconds})`, а существующий `get(key)` остаётся совместимым.

Change note (2026-09-27): создан первоначальный ExecPlan после audit текущего pipeline; выбран additive trace recorder и atomic per-job storage без изменения бизнес-логики.

Change note (2026-09-27): план завершён; добавлены итоги реализации, сведения об историческом pilot и результаты всех бесплатных проверок.
