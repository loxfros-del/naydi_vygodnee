# Исправить evidence-routing Avito без ослабления строгой выдачи

Этот ExecPlan — живой документ и поддерживается по правилам `.agent/PLANS.md`. Он продолжает завершённый план `docs/execplans/avito_cost_latency_optimization_2026-09-27.md`, но содержит достаточный контекст для самостоятельной работы. Разделы `Progress`, `Surprises & Discoveries`, `Decision Log` и `Outcomes & Retrospective` обновляются по мере реализации.

## Purpose / Big Picture

После изменений отсутствие данных перестанет ошибочно считаться доказанным нарушением. Объявление с неизвестным, но проверяемым по фотографии состоянием сможет дойти до ограниченного photo AI shortlist; подтверждённый дефект, условная цена или несовпадение запроса по-прежнему остановят его раньше. Семейный запрос `ps5` будет допускать варианты PS5, а точный `ps5 slim` — отклонять обычную PS5. Неполный сбор будет явно отличаться от проверенного пустого результата. Всё это доказывается offline replay сохранённых данных и synthetic tests без платных API.

## Progress

- [x] (2026-09-28) Прочитаны поручение, `.agent/PLANS.md`, `docs/product_selection_knowledge.md` и предыдущий ExecPlan.
- [x] (2026-09-28) Проведён repository audit matching, evidence state, photo gate, caches, terminal result и cancel telemetry.
- [x] (2026-09-28) Добавлена явная семантика `PASS` / `FAIL` / `NEEDS_EVIDENCE` и безопасный photo routing.
- [x] (2026-09-28) Исправлены family/exact matching diagnostics с полями model/variant/condition/price/city/pickup/other.
- [x] (2026-09-28) Подтверждена request-specific изоляция AI cache; исправлены market cache reuse и miss reason.
- [x] (2026-09-28) Разделены `EMPTY_VERIFIED`, `SEARCH_INCOMPLETE`, `SPEND_LIMIT`, `ERROR` и `CANCELLED` в результате/trace.
- [x] (2026-09-28) Исправлен cancel ordering и устранён telemetry false positive для POST, начатого до cancel.
- [x] (2026-09-28) Добавлен offline decision-chain helper и выполнен replay 18 PS5 survivors.
- [x] (2026-09-28) Добавлены regression tests; 512 Avito tests, compileall и Alice parser прошли.

## Surprises & Discoveries

- Observation: live `ps5` с `price_max=45000` получил market cache miss и после двух market Actor runs завершился публичным empty, хотя candidate collection был заблокирован `APIFY_SPEND_LIMIT`.
  Evidence: trace `runtime/avito_pilot_traces/-jfR06Rd1ToXxlm9.json` содержит `market_cache.hit=false`, два успешных `market_collection` и pre-POST error `APIFY_SPEND_LIMIT` для `candidate_collection`.
- Observation: cold PS5 дошёл до 18 hard survivors, но photo gate не выбрал ни одного кандидата; причины включают `CONDITION_EVIDENCE_INCOMPLETE` и `PHOTO_NOT_ANALYZED`.
  Evidence: trace `runtime/avito_pilot_traces/XkI1qlxw1BjuBfFJ.json`.
- Observation: cancel abort был подтверждён, но trace пометил `expensive_stage_started_after_cancel=true`.
  Evidence: trace `runtime/avito_pilot_traces/dBB3jqBU7i8VSEEr.json`.
- Observation: photo shortlist требовал `listing.completeness` до вызова photo AI, хотя именно фотографии должны были получить часть этого evidence.
  Evidence: `_photo_candidate_ids` в `avito_service/service.py` одновременно требовал неполный review и заранее заполненный `listing.completeness`.
- Observation: сохранённый PS5 snapshot содержит 27 объявлений: 9 deterministic mismatch и 18 survivors исходного hard routing. Offline decision replay после явного учёта blocking price risks даёт 8 text-valid/photo-eligible, 5 definite reject (три `PAYMENT_SURCHARGE`, один AI price condition, один defect) и 5, которым ещё нужен text AI.
  Evidence: offline inspection `runtime/avito_market_cache/334d00182be4e02498ed6f9f1cc203c9dbc9cddd823e9ca499fe1b72d0aa42eb.json` production-функциями routing.
- Observation: ключ `MarketSnapshotCache` уже не включает `price_max`, но `AvitoService.search` добавлял разные version suffix для shared-discovery и обычного запроса, поэтому узкий price request не мог использовать broad snapshot.
  Evidence: `cache_version` в `avito_service/service.py`.
- Observation: AI verdict cache уже включает нормализованные query, mode, location, price, required fields и attributes; опасный cross-request reuse в локальном AI cache не найден.
  Evidence: `_cache_key` в `avito_service/service.py`.
- Observation: cancel trace может ложно считать Apify запущенным после cancel: provider сообщает `started` лишь после ответа POST, хотя сам POST начался до cancel.
  Evidence: порядок reserve/POST/`started` в `avito_service/apify.py` и обработка события в `avito_service/telemetry.py`.

## Decision Log

- Decision: неизвестность моделируется как `NEEDS_EVIDENCE`, но только если следующий конкретный stage способен получить это evidence; после последнего доступного stage она становится финальным reject.
  Rationale: это сохраняет fail-closed выдачу и устраняет circular photo gate.
  Date/Author: 2026-09-28 / Codex
- Decision: не запускать live Apify/AI и не менять provider/Actor.
  Rationale: поручение требует offline correctness по saved traces/cache и synthetic tests.
  Date/Author: 2026-09-28 / Codex
- Decision: photo stage получает два явных результата `photo_condition_evidence` и `photo_completeness_evidence` со значениями pass/fail/unknown; отсутствие видимого дефекта не считается pass.
  Rationale: фото может закрыть только реально наблюдаемую нехватку evidence, не доказывая скрытую исправность или оригинальность.
  Date/Author: 2026-09-28 / Codex
- Decision: стратегия collection (shared или separate) удалена из market cache identity, а price bounds по-прежнему применяются детерминированно после broad snapshot.
  Rationale: один совместимый широкий рынок должен переиспользоваться для более узкого `price_max` без повторного платного market run.
  Date/Author: 2026-09-28 / Codex
- Decision: событие Apify `starting` записывается непосредственно перед paid POST; поздний ответ `started` после cancel обновляет тот же run и не считается новым expensive stage.
  Rationale: trace должен отражать реальный порядок начала POST, а отмена перед новым POST всё равно проверяется последней проверкой cancel.
  Date/Author: 2026-09-28 / Codex

## Outcomes & Retrospective

Реализован fail-closed evidence pipeline: неизвестный, но проверяемый по фото комплект больше не отсекается до photo AI; после фото unresolved evidence остаётся блокером. Saved PS5 replay: 27 строк, 18 survivors, 8 text-valid/photo-eligible, 5 definite reject, 5 awaiting text AI, 9 pre-text mismatch. Broad market snapshot переиспользуется при изменении `price_max`; AI cache ключи остаются request-specific. Неполный сбор и spend limit больше не маскируются под verified empty. Cancel telemetry различает уже начатый POST и новый stage после cancel.

Проверки: `python -m unittest discover -b -s tools -p 'test_avito*.py'` — 512 OK; `python -m compileall .` — OK; `python tools/test_alice_parser.py` — OK; `python tools/diagnose_avito_decisions.py` — ожидаемые счётчики выше. Live Apify/AI не запускались.

## Context and Orientation

`avito_service/service.py` управляет сбором, text AI, photo AI, ranking и final revalidation. `avito_service/matching.py` и `avito_service/request_intent.py` определяют соответствие модели и ограничений. `avito_service/rejection.py` агрегирует причины отказа и ambiguity routing. `avito_service/ai_cache.py` хранит AI evidence. `avito_service/market_cache.py` хранит короткоживущий рыночный snapshot. `avito_service/jobs.py` владеет terminal status и cancel. `avito_service/telemetry.py` записывает trace. `runtime/avito_pilot_traces` и `runtime/avito_market_cache` используются только для чтения в offline replay.

Термин `NEEDS_EVIDENCE` означает, что публикация пока запрещена, но объявление не является доказанно плохим и может перейти на следующий разрешённый stage. Термин `verified empty` означает, что полный обязательный pipeline завершён и все кандидаты доказанно отклонены. `Search incomplete` означает, что обязательный сбор или проверка не завершились; это не утверждение об отсутствии подходящих объявлений.

## Plan of Work

Сначала проследить каждое условие от hard filter до photo eligibility и final publication, затем воспроизвести 18 PS5 survivors из сохранённого cache/trace. На основании реальных типов добавить небольшой модуль решения либо расширить `rejection.py`, чтобы одно и то же состояние управляло telemetry и gating.

Matching будет разделять семейный и точный запрос, а mismatch получит безопасную structured detail по полю: model, variant, condition, price, city, pickup или other. Детали не должны содержать сырой текст продавца.

AI cache будет проверен на наличие request-specific verdict. Если cache хранит такой verdict, ключ обязан включать нормализованные обязательные параметры запроса; несовместимые `ps5` и `ps5 slim`, а также разное требование состояния не должны делить решение.

Market cache будет хранить broad snapshot независимо от `price_max`, но reuse разрешается только при совместимой identity, region, configuration и достаточном coverage. Lookup будет возвращать miss reason, который попадёт в trace.

Ошибки обязательного collection/verification будут подниматься как incomplete/spend-limit outcome вместо обычного empty. Cancel path будет проверять флаг отмены непосредственно перед каждым новым expensive stage; telemetry будет сравнивать реальные timestamps в корректном порядке.

Offline helper примет сохранённые listing facts, AI evidence и `SearchRequest`, затем выдаст JSON-safe decision chain с `listing_id`, request match, text state, price conditions, defects, condition evidence, photo eligibility, first fatal rejection, secondary reasons и next required stage.

## Concrete Steps

Рабочая директория: `C:\Users\Пользователь\Documents\naydi_vygodnee`.

После audit выполнить узкие тесты matching/gating/cache/jobs. После реализации запустить все `tools/test_avito_*.py`, затем обязательные проверки проекта:

    python -m compileall .
    python tools/test_alice_parser.py

Использовать bundled Python, если launcher `.venv` не работает. Не устанавливать зависимости и не обращаться к сети.

## Validation and Acceptance

Synthetic tests должны доказать: generic PS5 принимает PS5 Slim; explicit PS5 Slim отклоняет regular PS5; неизвестное проверяемое состояние идёт в photo AI; подтверждённые defect/price/mismatch не идут; положительный photo evidence разрешает продолжение, а оставшаяся неизвестность блокирует финал. `find` не требует savings, `bargain` требует.

Cache tests должны доказать отсутствие reuse request-specific AI между несовместимыми запросами и reuse broad market snapshot для более узкого `price_max` при достаточном coverage. Job tests должны отличать `EMPTY_VERIFIED`, `SEARCH_INCOMPLETE`, `SPEND_LIMIT`, `ERROR`. Cancel test должен доказать отсутствие нового expensive stage и публикации после cancel.

Offline replay должен обработать 18 survivors без сети и вывести числа valid text, photo eligible и definitely rejected, а также цепочку причин для каждого объявления.

## Idempotence and Recovery

Тесты и replay читают saved artifacts, но не изменяют их. Новые cache formats должны безопасно игнорировать старые записи. Изменения не касаются `.env`, `.venv`, баз данных, токенов или `main.py`. Если полный прогон обнаружит unrelated failure, он фиксируется отдельно и не маскируется.

## Artifacts and Notes

Основные live traces: `XkI1qlxw1BjuBfFJ.json` — cold PS5; `g4bmHYF93vSH2HzV.json` — cached PS5; `-jfR06Rd1ToXxlm9.json` — price-filter miss/spend limit; `dBB3jqBU7i8VSEEr.json` — cancel race. Они не содержат достаточных listing facts сами по себе, поэтому replay также использует сохранённый market cache с совпадающим listing identity.

## Interfaces and Dependencies

Новые внешние зависимости не добавляются. Решение verification должно быть JSON-safe и использовать enum со значениями `PASS`, `FAIL`, `NEEDS_EVIDENCE`. Mismatch diagnostics и market miss reason должны использовать стабильные коды. Offline helper располагается в `tools/` и импортирует production decision functions вместо копирования business logic.

Change note (2026-09-28): создан follow-up ExecPlan после live pilot; цель — исправить evidence-routing, cache correctness и terminal outcomes без платных запусков.
