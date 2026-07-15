# Search V2: операторский runbook

## 1. Безопасное состояние

До завершения acceptance:

```env
SEARCH_ENGINE_MODE=legacy
```

`main` и production DB не переключаются. Все изменения находятся в draft PR `feature/search-engine-v2`.

## 2. Источник структурированных цен

В GitHub repository settings нужен хотя бы один secret:

- `SEARCHAPI_API_KEY`, либо
- `SERPAPI_API_KEY`.

Ключ нельзя писать в `.env.example`, PR, issue, Telegram, acceptance JSON или лог.

Локальный SearchApi:

```env
SEARCH_V2_SHOPPING_PROVIDER=auto
SEARCHAPI_ENABLED=true
SEARCHAPI_API_KEY=...
```

SerpApi fallback:

```env
SEARCH_V2_SHOPPING_PROVIDER=auto
SERPAPI_ENABLED=true
SERPAPI_API_KEY=...
```

`auto` использует SearchApi первым и SerpApi вторым. Без ключей `shopping_search` возвращает `EMPTY` без network call.

## 3. Quota preflight

Перед каждым большим acceptance workflow вызывает `tools/searchapi_preflight.py` и проверяет SearchApi Account API без выполнения товарного поиска.

Preflight сравнивает:

- оставшиеся запросы;
- количество выбранных acceptance cases;
- небольшой safety buffer;
- наличие SerpApi fallback.

Если лимита недостаточно и fallback отсутствует, workflow останавливается до сетевого benchmark. Это защищает бесплатный лимит от повторного расходования.

После ответа `RATE_LIMITED` процесс-local circuit breaker прекращает следующие вызовы `shopping_search` до перезапуска/истечения блокировки. Другие источники продолжают работать.

## 4. Текущий provider-backed baseline

Первый полный прогон 15 июля 2026 года сохранён в:

```text
data/search_v2_acceptance_2026-07-15.json
```

Фактический результат:

- 30/30 cases завершены;
- SearchApi secret принят;
- Ozon и Avito продолжали давать discovery candidates;
- valid prices и recommendations остались нулевыми;
- бесплатный месячный SearchApi quota был исчерпан во время acceptance и диагностических запусков.

После baseline исправлены recovery `extracted_price`, currency guard, direct product links, классификация 429, circuit breaker и разделение system/partial errors. Повторный live acceptance возможен после reset/upgrade SearchApi quota либо после добавления `SERPAPI_API_KEY`.

## 5. Provider-backed acceptance

GitHub → Actions → **Search V2 acceptance** → Run workflow:

- branch: `feature/search-engine-v2`;
- provider: `auto`;
- limit: `0` для всех 30 cases;
- require pass: сначала `false`.

Workflow сохраняет:

- `data/searchapi_preflight.json`;
- `data/search_v2_acceptance.json`;
- preflight/acceptance logs;
- artifact `search-v2-acceptance-<run>`.

Runner делает checkpoint после каждого case и не сохраняет secrets/raw HTML.

Проходные цели:

- completed: 30/30;
- system errors: 0;
- top-1 exact: минимум 24/30;
- полезная рекомендация в top-3: минимум 28/30.

После диагностического запуска повторить с `require pass=true` только после исправления воспроизводимых провалов.

## 6. Shadow в Telegram

После provider-backed acceptance:

```env
SEARCH_ENGINE_MODE=shadow
```

Перезапустить бота. Клиент получает legacy; V2 работает рядом и сохраняет snapshot. В админке открыть «Legacy ↔ V2».

Проверить минимум 10 заявок:

1. точная модель и обязательные specs;
2. цена и прямая product link;
3. наличие;
4. продавец отдельно от площадки;
5. wrong model/accessory/unavailable не попали в рекомендации;
6. роли BEST_OVERALL, CHEAP_WITH_RISK, RELIABLE уникальны;
7. 403/429 остаются только в Admin Debug;
8. после ручного подтверждения клиент видит «Проверено специалистом» и реальные нерешённые риски.

## 7. Первый rollout

После зелёного shadow acceptance включать V2 только для смартфонов в тестовом контуре. Полный `SEARCH_ENGINE_MODE=v2` не включать сразу для всех категорий.

При системном `ERROR`/`TIMEOUT` bridge возвращает legacy fallback. При обычном `NO_EXACT_MATCH` не подменять результат мусором.

## 8. Rollback

```env
SEARCH_ENGINE_MODE=legacy
```

Перезапустить бота. Схема DB и legacy-код не удалялись, поэтому откат миграции не требуется.

## 9. Готовность к платным клиентам

- GitHub CI зелёный;
- provider-backed 30-case acceptance достиг целей;
- 10 Telegram shadow-cases проверены вручную;
- admin review занимает не более 2–3 минут;
- wrong model/accessory/unavailable у клиента — 0;
- client card не содержит technical diagnostics;
- fallback/rollback проверен.
