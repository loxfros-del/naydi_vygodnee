# Довести автопоиск до измеримого качества 9/10

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

Этот документ ведётся по правилам `.agent/PLANS.md`. Пользователь явно запретил commit и push, поэтому требование PLANS.md о частых коммитах в этой задаче не применяется. Все изменения делаются поверх существующего незакоммиченного worktree без reset, restore и checkout.

## Purpose / Big Picture

После изменений автопоиск должен разбирать запросы, планировать ограниченный набор запросов, отличать точную модель от варианта или аксессуара, извлекать доказуемую цену и характеристики, объяснимо ранжировать товары и компактно показывать результат администратору. Качество проверяется offline-наборами и cached benchmark, а затем одним live suite из 12 новых запросов. Оценка 9/10 считается достигнутой только по формальным порогам; при недостаточных cached/live данных отчёт обязан показать реальный меньший балл и конкретные провалы.

## Progress

- [x] (2026-07-11) Выполнены обязательные `git status --short`, `git diff --stat`, `git diff` и `python -m compileall -q .`; компиляция успешна, существующие изменения сохранены.
- [x] (2026-07-11) Зафиксирован baseline: search invariants 21/21, cache tests 37/37, Alice parser успешно.
- [x] (2026-07-11) Начат read-only аудит parser, query planning, verification, facts, policies, ranking, dedupe, cache и Telegram rendering.
- [ ] Завершить аудит конфликтов и записать точные наблюдения в этот план.
- [ ] Добавить декларативный category registry и совместимый структурированный parser/query planner.
- [ ] Добавить единые exact-match, product-card, price-evidence, facts-evidence и source-trust модели.
- [ ] Свести final policy, ranking, score caps и dedupe в один последовательный путь без параллельного третьего gate.
- [ ] Расширить golden dataset до 40+ запросов и добавить требуемые deterministic suites.
- [ ] Добавить cached quality evaluator с оценками по направлениям и доказуемым weighted score.
- [ ] Улучшить admin/Telegram diagnostics без изменения DB-схемы и основного Telegram flow.
- [ ] Выполнить полный offline validation и cached audit; исправить максимум три системные причины провалов.
- [ ] Выполнить один live `quality_v3` suite, затем cached audit и записать итоговые метрики.
- [ ] Обновить Outcomes & Retrospective и финальный отчёт; не коммитить и не пушить.

## Surprises & Discoveries

- Observation: worktree уже содержит существенные незакоммиченные изменения в cache/benchmark и source adapters.
  Evidence: `git status --short` показал 7 изменённых tracked-файлов и новые планы, adapters и tests; `git diff --stat` показал 869 добавлений и 76 удалений только в tracked-файлах.
- Observation: текущий request parser распознаёт в критериях только ограниченный TV-профиль, а category detector знает лишь phone, laptop, TV, headphones и chair.
  Evidence: `app/request_parser.py::parse_important_criteria` и `app/candidate_verifier.py::_category_from_text` не содержат monitor, vacuum, robot_vacuum, microwave, coffee_machine, mattress и bed.
- Observation: score формируется до verification в `score_result`, затем заново вычисляется в `_candidate_rank_score`, после чего ограничивается несколькими независимыми caps в `_apply_ranking_sanity`.
  Evidence: `app/product_search.py` строки около 1095, 1148 и 1191.
- Observation: status и presentation status различаются намеренно, но не оформлены как один контракт: `verify_status` хранит смысловой статус, тогда как `candidate.status` принимает `CANDIDATE`, `WEAK_CANDIDATE` или `REJECTED_AUTO`.
  Evidence: `app/product_search.py::_apply_final_search_policy`.
- Observation: facts извлекаются из title, snippet и первых 6000 символов всей страницы одним regex-проходом, поэтому identity-поля теоретически могут прийти из блока похожих товаров.
  Evidence: `app/candidate_verifier.py::extract_product_facts`.
- Observation: current golden dataset содержит только 6 запросов и не покрывает большинство требуемых категорий.
  Evidence: PowerShell JSON audit: `query_count=6`.

## Decision Log

- Decision: сохранить публичный `full_parse(text) -> dict` и строковый `generate_search_queries(req) -> list[str]`, добавив структурированные поля и typed planner под ними.
  Rationale: Telegram flow, QA tools и benchmark уже используют эти API; совместимость важнее полной замены.
  Date/Author: 2026-07-11 / Codex.
- Decision: не менять `Request` и SQLite-схему; расширенные parser/facts/score diagnostics хранить в runtime-структурах и существующем `facts_json`.
  Rationale: пользователь запретил DB/migrations, а `facts_json` уже является совместимым расширяемым контейнером.
  Date/Author: 2026-07-11 / Codex.
- Decision: category registry будет декларативным и не будет импортировать network/source adapters.
  Rationale: registry должен тестироваться без сети и не создавать циклические зависимости.
  Date/Author: 2026-07-11 / Codex.
- Decision: semantic status остаётся в `verify_status`, workflow status остаётся в `status`, но единственная final policy обязана синхронно выставлять оба и `keep_for_admin`.
  Rationale: это сохраняет DB/Telegram совместимость и устраняет скрытое расхождение ролей полей.
  Date/Author: 2026-07-11 / Codex.
- Decision: score использует шкалу 0–100, breakdown по именованным компонентам и один final cap после всех компонентов.
  Rationale: шкала объяснима, не превышает 200 и позволяет доказать инварианты ranking.
  Date/Author: 2026-07-11 / Codex.
- Decision: blocked page снижает verification confidence, но не product quality; direct source повышает source confidence только при structured/product-card evidence и не делает товар GOOD автоматически.
  Rationale: сетевой доступ и качество товара — независимые измерения.
  Date/Author: 2026-07-11 / Codex.
- Decision: live benchmark запускается ровно один раз после зелёных deterministic и cached проверок.
  Rationale: ТЗ запрещает расходовать сеть на итеративную отладку и просит учитывать rate limits как внешнее ограничение.
  Date/Author: 2026-07-11 / Codex.

## Outcomes & Retrospective

На старте подтверждены только baseline-компиляция и прежние 21/37 offline-тестов. Итоговые оценки, cached/live evidence и оставшиеся ограничения будут записаны после завершения реализации.

## Context and Orientation

`app/request_parser.py` преобразует свободный текст в поля существующей `app.db.Request`. `app/product_search.py` планирует запросы, вызывает adapters, нормализует кандидатов, запускает verification, final policy, ranking и dedupe. `app/candidate_verifier.py` загружает карточки, извлекает цену/наличие/facts и создаёт `VerifiedCandidate`. `app/product_quality.py` содержит часть category и exact-model правил, а `app/search_policy.py` решает semantic status и сохранение администратору. `app/handlers/admin.py` рендерит сохранённые `SearchResult`; расширяемые диагностики передаются через существующее поле `facts_json`. `tools/search_benchmark.py` пишет отдельные cache snapshots через `app/search_cache.py`; это не основная `bot.db`.

В этой работе semantic status означает реальное состояние кандидата: например `VERIFIED_GOOD`, `PRICE_MISSING` или `WRONG_PRODUCT`. Workflow status означает способ хранения и показа: `CANDIDATE`, `WEAK_CANDIDATE` или `REJECTED_AUTO`. Confidence означает уровень доказательности (`high`, `medium`, `low`, `none`), а не качество самого товара. Score cap — верхняя граница итогового score, применяемая последней из-за manual, blocked, missing-price, wrong-model или другого риска.

Целевой порядок единственного полного pipeline: parse; query planning; collect; normalize; verify; category analysis; exact-match policy; universal policy; ranking; dedupe; admin-save; rendering. Compare-search может переиспользовать те же pure-функции для одного кандидата, но не получает отдельную политику.

## Plan of Work

Milestone 1 завершает аудит и фиксирует контракты. Нужно перечислить все места записи `score`, `status`, `verify_status`, `quality`, `keep_for_admin` и `product_facts`; затем определить единственного владельца каждого финального поля. Наблюдаемый результат — этот план содержит подтверждённые конфликты, а baseline остаётся зелёным.

Milestone 2 добавляет `app/category_registry.py` с immutable `CategorySpec` для 13 категорий и `app/query_planner.py` с `QueryPlanItem(text, priority, reason, kind, direct_eligible)`. `app/request_parser.py` получает structured parsing category, brand, model, modifiers, condition, budget, city, use case, dimensions и major criteria, сохраняя старые ключи. `app/product_search.py::generate_search_queries` становится совместимой оболочкой над planner. Один case получает один main query, до трёх category variants, до трёх feature/brand variants и ограниченные site queries; direct retail использует только помеченные полезные варианты. Acceptance — 30+ parser cases и query budget tests проходят без сети.

Milestone 3 добавляет evidence engines. `app/exact_match.py` возвращает один из `EXACT`, `COMPATIBLE_VARIANT`, `GENERIC_MATCH`, `MODEL_MISMATCH`, `ACCESSORY`, `UNKNOWN` с конкретными различиями. `app/price_extractor.py` возвращает typed evidence с confidence и source, сохраняя старый `extract_price`. Category facts извлекаются отдельными pure-функциями: brand/model только из identity-текста title/page title, supplemental page text лишь дополняет признаки. Product-card assessment различает high/medium/low и исключает article/search/category/PDF/review pages. Source trust и verification confidence вычисляются независимо. Acceptance — 30+ price, 40+ exact-match и 30+ facts/brand cases проходят.

Milestone 4 консолидирует решения. Existing verification остаётся владельцем сетевых фактов, category/exact analysis добавляет evidence, а `app/search_policy.py` единожды определяет semantic status, presentation status и admin-save. Ranking формирует `score_breakdown` на шкале 0–100 и применяет final cap последним. Dedupe использует normalized URL, source product id, exact identity key и осторожный title fallback, сохраняя различия size/storage/variant/condition. Sort сначала исключает wrong/accessory/unavailable, затем гарантирует GOOD/OK выше manual при сопоставимых кандидатах и поддерживает source diversity. Acceptance — 25+ ranking cases и расширенные invariants проходят.

Milestone 5 добавляет доказательства качества. `tools/golden_cases.json` расширяется минимум до 40 query cases и отдельных synthetic candidates. `tools/search_quality_audit.py` считает parsing, links, price, exact match, facts/quality, ranking, admin/Telegram и performance/reliability, печатает failing case IDs и применяет заданные веса и пороги. `tools/search_benchmark.py` получает suite `quality_v3` из 12 заданных запросов. Cached mode не вызывает loaders и честно печатает `CACHE_MISS`. Admin detail показывает breakdown/evidence/confidences/caps, summary остаётся компактным и HTML-safe. Acceptance — evaluator сам запрещает 9/10 при нарушении любого порога.

Milestone 6 валидирует систему. Сначала выполняются все offline-команды и cached audit. Исправляются максимум три системные причины, найденные evaluator. Затем один раз запускается live `quality_v3 --verification fast`, после чего тот же suite оценивается только из cache. Outcomes фиксирует числа, длительности, top-1, ошибки и честный weighted score.

## Concrete Steps

Рабочая директория для всех команд: `C:\Users\Пользователь\Documents\naydi_vygodnee`.

После каждой логической группы правок выполнять:

    python -m compileall -q .
    python -X utf8 tools/test_search_invariants.py

Полная offline-проверка:

    python -m compileall -q .
    python -X utf8 tools/test_search_invariants.py
    python -X utf8 tools/test_search_cache.py
    python -X utf8 tools/test_request_parser_cases.py
    python -X utf8 tools/test_price_cases.py
    python -X utf8 tools/test_exact_match_cases.py
    python -X utf8 tools/test_category_facts.py
    python -X utf8 tools/test_ranking_cases.py
    python tools/test_alice_parser.py
    git diff --check

Cached evaluation до live:

    python -u -X utf8 tools/search_quality_audit.py --cached --suite extended

Единственный разрешённый новый live suite после зелёного offline baseline:

    python -u -X utf8 tools/search_benchmark.py --suite quality_v3 --live --verification fast
    python -u -X utf8 tools/search_quality_audit.py --cached --suite quality_v3

## Validation and Acceptance

Parser suite должен содержать минимум 30 случаев и давать не менее 90% полной точности по category, budget, city и major criteria. Price suite должен содержать минимум 30 случаев и иметь ноль false positives на specs, budget, years, model/article/review numbers и furniture sizes. Exact suite должен содержать минимум 40 случаев и давать не менее 95% accuracy, а accessory rejection — не менее 98%. Facts/brand suite должен содержать минимум 30 случаев и доказать word-boundary brands и отсутствие чужих category fields. Ranking suite должен содержать минимум 25 случаев и доказать заявленные инварианты.

Cached evaluator обязан сообщить `network_calls=0`; cache misses не превращаются в successes. Общий балл 9/10 разрешён только при weighted score не ниже 9.0, exact и price не ниже 9.0, ranking не ниже 8.5 и каждой группе не ниже 8.0. Live acceptance требует не менее 11/12 `SUCCESS` или `PARTIAL_SUCCESS` с сохранённым кандидатом или честным manual fallback, top-1 relevant для 10/12 и top-3 relevant для 11/12. Если внешний источник блокирует проверку, это отмечается как verification limitation, а не code defect или weak product.

## Idempotence and Recovery

Все deterministic tests и cached evaluator можно повторять безопасно. Они не читают и не меняют `bot.db`. Search cache остаётся в существующей отдельной SQLite architecture и не очищается. Нельзя использовать reset, restore, checkout изменённых файлов, удаление пользовательских правок или `git add .`. Если live suite прерывается, существующий resumable benchmark может дочитать сохранённый run, но новый полный live suite без необходимости не запускается. Ошибка реализации исправляется точечным patch поверх текущего worktree.

## Artifacts and Notes

Baseline 2026-07-11:

    compileall: exit 0
    search invariants: Ran 21 tests, OK
    search cache: Ran 37 tests, OK
    Alice parser: все проверки пройдены
    golden query count: 6

## Interfaces and Dependencies

Новые production-модули используют только Python standard library и уже установленные зависимости проекта. `app/category_registry.py` экспортирует `CategorySpec`, `CATEGORY_SPECS`, `get_category_spec(name)` и `detect_category(text)`. `app/query_planner.py` экспортирует immutable `QueryPlanItem` и `plan_search_queries(request, max_queries=...)`. `app/exact_match.py` экспортирует string constants, immutable `ExactMatchResult` и `match_candidate(parsed_or_request, candidate, category=None)`. Price extractor сохраняет `extract_price(text, min_price, max_price) -> int | None` и добавляет typed evidence API. Ranking возвращает score и breakdown, которые копируются в `ProductCandidate` и `facts_json`. Evaluator читает только `tools/golden_cases.json` и existing search cache snapshots; сеть доступна только `tools/search_benchmark.py --live`.

Revision note (2026-07-11): создан initial ExecPlan после обязательного safety baseline и первого read-only аудита; решения выбраны для сохранения Telegram, DB и cache compatibility.
