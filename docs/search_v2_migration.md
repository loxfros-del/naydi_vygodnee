# Search Engine V2: безопасная миграция

## Режимы

`get_search_engine_mode()` читает `SEARCH_ENGINE_MODE` и принимает только:

- `legacy` — клиентский flow использует прежний поиск;
- `shadow` — клиент получает legacy, V2 выполняется для сравнения и сохраняет diagnostic snapshot;
- `v2` — bridge использует V2, а legacy остаётся fallback при системном `ERROR`/`TIMEOUT`.

Пустое, неизвестное или отсутствующее значение всегда превращается в `legacy`. Сейчас production не переключён; `main.py` и `.env` не менялись.

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

## Порядок rollout

1. Оставить `legacy`; прогнать deterministic/regression suites.
2. Проверить один bounded live smoke и сохранение partial snapshot.
3. Включить `shadow` только инфраструктурной настройкой; сравнить exact rate, wrong products, source diversity, prices и duration.
4. Исправить расхождения; не снижать hard-match/manual-verification gates.
5. Включать `v2` только отдельным операционным решением после стабильного shadow периода.
6. Для немедленного rollback вернуть `legacy`; schema migration и удаление legacy не требуются.

Для локальных tests режим лучше передавать аргументом `mode`, не редактируя `.env`. Автоматического production switch, commit или push эта миграция не выполняет.
