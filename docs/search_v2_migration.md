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

Production/shadow V2 использует bounded discovery `shopping_search + ozon + avito`. `shopping_search` активируется только при настроенном SearchApi или SerpApi key. Без него adapter возвращает `EMPTY` без сетевого вызова, legacy и остальные V2 sources продолжают работать.

## Snapshots

`SearchV2SnapshotStore` использует существующий `SearchCache` и namespaces:

- `search_v2:source` — source diagnostics;
- `search_v2:normalized` — нормализованные offers;
- `search_v2:result` — итог V2;
- `search_v2:shadow` — Legacy ↔ V2 comparison.

`build_shadow_comparison()` хранит кандидатов, rejected/wrong products, цены, рекомендации, source attempts, duration и errors. `load_shadow_comparison(request_id)` предназначен для Admin Debug. Shadow snapshot не является клиентским отчётом или product DB.

## Acceptance

30 реальных кейсов находятся в `tools/live_acceptance_cases.json`. Runner:

```powershell
python tools/search_v2_acceptance.py `
  --confirm-live `
  --cases tools/live_acceptance_cases.json `
  --output data/search_v2_acceptance.json
```

Он сохраняет checkpoint после каждого case, не пишет secrets/raw HTML и честно фиксирует отсутствие цен или provider. В GitHub есть ручной workflow **Search V2 acceptance**. Для него используются repository secrets `SEARCHAPI_API_KEY` и/или `SERPAPI_API_KEY`; workflow никогда не стартует на обычный push.

Проходные цели:

- top-1 exact: минимум 24/30;
- полезная рекомендация в top-3: минимум 28/30;
- completed cases: 30/30;
- system error cases: 0.

Автоматический exact-match и наличие цены ещё не означают клиентское одобрение: blocked/manual offers проходят admin checklist перед карточкой.

## Порядок rollout

1. Оставить `legacy`; deterministic/regression suites и GitHub CI должны быть зелёными.
2. Настроить один structured price provider и прогнать bounded 30-case acceptance.
3. Включить `shadow`; сравнить Legacy/V2 на реальных Telegram-заявках: exact rate, wrong products, source diversity, prices, seller и duration.
4. Исправлять только воспроизводимые расхождения; не снижать hard-match/manual-verification gates.
5. Включить `v2` сначала только для смартфонов после достижения acceptance targets и ручного Telegram end-to-end.
6. Для немедленного rollback вернуть `legacy`; schema migration и удаление legacy не требуются.

Для локальных tests режим лучше передавать аргументом `mode`, не редактируя `.env`. Автоматического production switch эта миграция не выполняет.
