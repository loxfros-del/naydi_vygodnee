# Сократить избыточную Avito-проверку без ослабления safety

Этот ExecPlan — живой документ и поддерживается по правилам `.agent/PLANS.md`. Разделы `Progress`, `Surprises & Discoveries`, `Decision Log` и `Outcomes & Retrospective` должны оставаться актуальными.

## Purpose / Big Picture

После изменений владелец увидит в pilot trace причины отсева на каждой естественной стадии, отдельную стоимость collection/text/photo/revalidation и корректную судьбу резерва при success, error и cancel. Дешёвые детерминированные решения и консервативный shortlist сократят лишние AI-вызовы только там, где это эквивалентно текущим обязательным проверкам. Похожие price-filter searches смогут переиспользовать свежий market snapshot, но финалисты по-прежнему проходят live revalidation.

## Progress

- [x] (2026-09-27) Прочитано ТЗ и полностью перечитан `.agent/PLANS.md`.
- [x] (2026-09-27) Завершён repository audit rejection paths, AI obligations, Actor capabilities и spending lifecycle.
- [x] (2026-09-27) Зафиксирован targeted baseline: 161 тест, 160 pass и одна compatibility error старого `PilotProvider`; error исправлена.
- [x] (2026-09-27) Добавлены rejection aggregation и cost-per-stage telemetry.
- [x] (2026-09-27) Добавлены fail-closed ambiguity routing и conservative photo gating без смены safety semantics.
- [x] (2026-09-27) Reusable market identity отделён от full job fingerprint и покрыт совместимостью.
- [x] (2026-09-27) Reservation reconciliation исправлен для success/error/cancel без ослабления hard cap.
- [x] (2026-09-27) Добавлены требуемые regression tests и audit-документ.
- [x] (2026-09-27) Выполнены расширенная регрессия (184 tests), `python -m compileall .` и `tools/test_alice_parser.py`.

## Surprises & Discoveries

- Observation: текущий `SpendingGuard` считает active reservation и settled spend одной суммой; non-final settlement увеличивает reserve до max, но не освобождает неиспользованную часть.
  Evidence: `SpendingGuard._totals` берёт `reserved_units`, если `settled_units is None`; `settle(..., final=False)` не разделяет active и conservative settled estimate.
- Observation: Actor уже поддерживает bounded page widening через `collect_to_target`, но каждая страница — отдельный paid Actor run.
  Evidence: `collect_to_target` вызывает `_collect_payload` для page number; текущий fill loop уже останавливается по target, budget, duplicates, pages и raw count.
- Observation: публичная schema Actor поддерживает `maxResults`, быстрый `includeDetails=false` и batch `listingUrls`, но не документирует cursor/page continuation одного run.
  Evidence: официальный input schema Zen Studio; поэтому discovery/detail split оставлен для малого live validation.
- Observation: старый photo gate вычислял savings только как score и всё равно отправлял кандидатов без 3 comparables/положительной разницы.
  Evidence: `_photo_candidate_ids` заполнял `savings`, но до изменения не фильтровал по нему.
- Observation: candidate path повторно вызывал text AI для уже чисто проверенного market listing.
  Evidence: `_review_with_budget` проверял только private full-review cache и игнорировал `market_analyzed` text review.

## Decision Log

- Decision: снача восстановить реальные rejection codes и обязательные AI invariants, а затем менять routing.
  Rationale: преждевременный skip AI может незаметно ослабить strict recommendation policy.
  Date/Author: 2026-09-27 / Codex
- Decision: не внедрять discovery/detail split или новую progressive strategy до доказательств в текущем Actor contract.
  Rationale: ТЗ запрещает новый scraper и дублирующие paid runs без надёжной экономии.
  Date/Author: 2026-09-27 / Codex
- Decision: KNOWN publishable listing не пропускает mandatory text safety review; skip разрешён только deterministic reject или exact cached evidence.
  Rationale: полный seller text остаётся единственным источником скрытых условий цены, дефектов и противоречий.
  Date/Author: 2026-09-27 / Codex
- Decision: bargain photo gate требует доказанную положительную разницу до vision только в реальном search path с market evidence.
  Rationale: text review фиксирует comparable identity, а vision не меняет цену или число независимых продавцов.
  Date/Author: 2026-09-27 / Codex

## Outcomes & Retrospective

Реализация и offline-проверки завершены. Старый pilot нельзя ретроспективно разложить по новым reason codes, но будущие traces показывают primary/secondary причины, stage costs и явный reservation class. Расширенная регрессия: 184/184; повторная выборочная регрессия затронутого service/cache path: 102/102; compileall завершился с кодом 0; Alice parser завершился успешно. Платный pilot не запускался.

## Context and Orientation

`avito_service/http_api.py` создаёт service, market cache и persistent spending guard. `avito_service/jobs.py` владеет job lifecycle и cancel. `avito_service/apify.py` ставит reserve до paid POST, опрашивает Actor, читает dataset и receipt. `avito_service/normalization.py` превращает provider JSON в `NormalizedListing`. `avito_service/risk_rules.py` создаёт детерминированные `RiskFinding` с code и severity. `avito_service/service.py` выполняет hard eligibility, text/photo review, final refresh и ranking. `avito_service/telemetry.py` записывает pilot trace. `avito_service/market_cache.py` хранит временные market snapshots. `avito_service/spending.py` атомарно резервирует budget под file lock.

Rejection reason — стабильный код уже существующего `RiskFinding`, AI conflict или eligibility-условия. Primary reason — первая блокирующая причина в текущем порядке проверок; secondary reasons — остальные коды того же объявления. Ambiguity router — чистая функция, которая классифицирует обязательные факты как known, unknown или conflict и не пропускает mandatory AI checks.

## Plan of Work

Снача завершить audit и по saved pilot trace связать 21→15→0 с конкретными existing reason codes. Затем добавить в service агрегатор primary/secondary reasons и передать его в `PipelineAudit` и trace. Агрегатор не меняет решение и не хранит raw descriptions.

После аудита mandatory checks добавить fail-closed ambiguity routing. Явно детерминированный reject не идёт в AI; unknown/conflict и любая обязательная verification идут. Если current policy требует text AI для всех потенциально публикуемых Avito listings, router будет измерять known/unknown/conflict, но не будет пропускать mandatory review. Фото gating будет применён только после blocking text checks и с conservative configurable cap, достаточным для final quota и replacement candidates.

`MarketSnapshotCache.key` будет описывать reusable market segment: normalized model family/query, region, category, required hardware variant, condition segment и pickup policy. Price bounds, output quota, mode и priority не войдут в key; full `search_fingerprint` в telemetry/jobs останется полным.

`SpendingGuard` получит явные active reservation и settled conservative amount. Любой terminal path после известного run start закроет active reservation в actual или conservative estimate. Неопределённая ошибка paid POST сохранит conservative settled amount, а pre-POST failure освободит active reservation. File lock и reserve-before-POST останутся.

## Concrete Steps

Рабочая директория: `C:\Users\Пользователь\Documents\naydi_vygodnee`.

После audit запустить targeted baseline по service, market cache, spending, AI budget, collection и telemetry. После каждого milestone запускать узкие новые тесты. В финале выполнить:

    python -B -m unittest tools.test_avito_service tools.test_avito_market_cache tools.test_avito_spending tools.test_avito_pilot_telemetry
    python -m compileall .
    python tools/test_alice_parser.py

Из-за сломанного launcher старой `.venv` разрешен bundled Python; для Alice parser существующий `.venv/Lib/site-packages` добавляется в конец `sys.path`. Платные network tests не запускать.

## Validation and Acceptance

Synthetic pipeline test должен показать primary и secondary rejection counts без raw text. Deterministic reject не должен тратить AI; ambiguity/conflict обязаны его вызвать. Photo gate должен отбирать configurable conservative shortlist и не пропускать blocking conflicts.

Два запроса с одинаковым market segment и разным `price_max` должны иметь один cache key; несовместимые model/category/region — разные. Full job fingerprints должны отличаться при изменении цены.

Spending tests должны доказать: actual заменяет reserve; unknown terminal run закрывается conservative estimate; pre-POST failure не блокирует budget; cancel после run start не освобождает возможно потраченные деньги; concurrent reservations не превышают hard cap.

## Idempotence and Recovery

Все миграции spending journal должны быть read-compatible с schema 1 и не должны редактировать текущий `runtime/avito_spend.json` в тестах. Тесты используют temporary directories. Cache format можно версионировать; stale entries безопасно игнорируются. Любая telemetry ошибка остаётся вторичной и не ломает search.

## Artifacts and Notes

Pilot trace `runtime/avito_pilot_traces/2QC5WK2Nf7LOa8oD.json` фиксирует 199 raw, 158 unique, 21 hard-filter survivors, 21 text candidates, 15 photo candidates и 0 final. Два Apify runs заняли 127,672 с; text/photo стадии — 36,531/45,547 с. Фактический current bottleneck — collection, но потеря 15→0 пока не имеет reason histogram.

## Interfaces and Dependencies

Новые внешние зависимости не добавляются. Rejection aggregation будет храниться в `PipelineAudit` как JSON-safe mapping/list с кодами. Ambiguity router будет чистой функцией в `avito_service/service.py` или узком новом модуле, если audit покажет переиспользование. `SpendingGuard` сохранит public `reserve`/`settle`, но получит явную terminal reconciliation operation; `ZenStudioProvider` вызовет её во всех terminal branches.

Change note (2026-09-27): создан первоначальный ExecPlan после чтения ТЗ и первичного audit spending/Actor lifecycle.

Change note (2026-09-27): реализованы audit telemetry, market/text reuse, bargain photo gate, stage cost и terminal reservation reconciliation; Actor discovery split сознательно не внедрён без live validation.
