# Search Engine V2: безопасная миграция

## Режимы

`get_search_engine_mode()` читает `SEARCH_ENGINE_MODE` и принимает только:

- `legacy` — клиентский flow использует прежний поиск;
- `shadow` — клиент получает legacy, V2 выполняется для сравнения и сохраняет diagnostic snapshot;
- `canary` — стабильная доля заявок идёт в V2 по хешу ID заявки, остальные остаются на legacy;
- `v2` — bridge использует V2, а legacy остаётся fallback при системном `ERROR`/`TIMEOUT`.

Пустое, неизвестное или отсутствующее значение всегда превращается в `legacy`. Сейчас production не переключён; `main.py` и `.env` не менялись.

Для `canary` доля задаётся `SEARCH_ENGINE_V2_ROLLOUT_PERCENT` от `0` до `100`. Неверное значение означает `0`. Бакет использует только ID заявки, не текст запроса, товар, город или Telegram-данные. При отсутствии ID заявка остаётся на legacy.

## Bridge contract

```python
outcome = await run_search_for_request(request, mode=None)
snapshot = load_shadow_comparison(request.id)
```

Правила bridge:

- `legacy`: байт-в-байт сохраняет существующий путь и результат;
- `shadow`: сначала/параллельно сохраняет клиентский legacy outcome; V2 не меняет заявку и не отправляет карточки клиенту;
- `v2`: сохраняет нормализованные V2 offers через существующий persistence boundary; legacy вызывается только при системной ошибке, а не при обычном `NO_EXACT_MATCH`;
- Telegram handlers не импортируют adapters и pipeline stages.

## Snapshots

`SearchV2SnapshotStore` использует существующий `SearchCache` и namespaces:

- `search_v2:source` — source diagnostics;
- `search_v2:normalized` — нормализованные offers;
- `search_v2:result` — итог V2;
- `search_v2:shadow` — Legacy ↔ V2 comparison.

`build_shadow_comparison()` хранит кандидатов, rejected/wrong products, цены, рекомендации, source attempts, duration и errors. `load_shadow_comparison(request_id)` предназначен для Admin Debug. Shadow snapshot не является клиентским отчётом или product DB.

`aggregate_shadow_comparisons()` строит обезличенный rollout-отчёт: число кейсов, exact/verified ТОП-1, небезопасные кандидаты, системные и source failures, медианное время. `rollout_ready=True` возможно только на выборке от 30 заявок, когда у каждой есть exact и полностью проверенный ТОП-1, рекомендация, нет leaked wrong-product и системных ошибок. Отчёт не содержит ID заявки, запроса, товара, URL, города или продавца.

## Порядок rollout

1. Оставить `legacy`; прогнать deterministic/regression suites.
2. Проверить один bounded live smoke и сохранение partial snapshot.
3. Включить `shadow` только инфраструктурной настройкой; сравнить exact rate, wrong products, source diversity, prices и duration.
4. Исправить расхождения; не снижать hard-match/manual-verification gates.
5. После зелёных quality gates включить `canary` с малой долей, например `5`; увеличивать долю только после повторной проверки shadow-метрик.
6. Включать `v2` для 100% только отдельным операционным решением после стабильного canary-периода.
7. Для немедленного rollback вернуть `legacy` либо поставить canary-процент `0`; schema migration и удаление legacy не требуются.

Для локальных tests режим лучше передавать аргументом `mode`, не редактируя `.env`. Автоматического production switch, commit или push эта миграция не выполняет.
