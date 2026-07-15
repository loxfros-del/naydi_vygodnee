# Search V2: операторский runbook

## 1. Безопасное состояние

До завершения acceptance:

```env
SEARCH_ENGINE_MODE=legacy
```

`main` и production DB не переключаются. Все изменения находятся в draft PR `feature/search-engine-v2`.

## 2. Настройка источника цен

В GitHub repository settings добавить **один** secret:

- `SEARCHAPI_API_KEY`, либо
- `SERPAPI_API_KEY`.

Ключ нельзя писать в `.env.example`, PR, issue, Telegram или acceptance JSON.

Локально используется тот же контракт:

```env
SEARCH_V2_SHOPPING_PROVIDER=auto
SEARCHAPI_ENABLED=true
SEARCHAPI_API_KEY=...
```

или:

```env
SEARCH_V2_SHOPPING_PROVIDER=serpapi
SERPAPI_ENABLED=true
SERPAPI_API_KEY=...
```

`auto` предпочитает SearchApi и использует SerpApi как fallback. Без ключа `shopping_search` возвращает `EMPTY` без network call.

## 3. Provider-backed acceptance

GitHub → Actions → **Search V2 acceptance** → Run workflow:

- branch: `feature/search-engine-v2`;
- provider: `auto`;
- limit: `0`;
- require pass: сначала `false`.

Workflow запускается только вручную. Он сохраняет:

- `data/search_v2_acceptance.json`;
- `search-v2-acceptance.log`;
- artifact `search-v2-acceptance-<run>`.

Runner делает checkpoint после каждого из 30 кейсов и не сохраняет secrets/raw HTML.

Проходные цели:

- completed: 30/30;
- system errors: 0;
- top-1 exact: минимум 24/30;
- полезная рекомендация в top-3: минимум 28/30.

После первого диагностического запуска повторить workflow с `require pass=true` только после исправления воспроизводимых провалов.

## 4. Shadow в Telegram

После provider-backed acceptance локально:

```env
SEARCH_ENGINE_MODE=shadow
```

Перезапустить бота. Клиент продолжает получать legacy; V2 работает рядом и сохраняет snapshot. В админке открыть «Legacy ↔ V2».

Проверить минимум 10 заявок:

1. точная модель и обязательные specs;
2. цена и прямая product link;
3. наличие;
4. продавец отдельно от площадки;
5. wrong model/accessory/unavailable не попали в рекомендации;
6. роли BEST_OVERALL, CHEAP_WITH_RISK, RELIABLE уникальны;
7. 403/429 остаются только в Admin Debug;
8. после ручного подтверждения клиент видит «Проверено специалистом» и реальные нерешённые риски.

## 5. Первый rollout

После зелёного shadow acceptance включать V2 только для смартфонов в тестовом контуре. Полный `SEARCH_ENGINE_MODE=v2` не включать сразу для всех категорий.

При системном `ERROR`/`TIMEOUT` bridge возвращает legacy fallback. При обычном `NO_EXACT_MATCH` не подменять результат мусором.

## 6. Rollback

Немедленный rollback:

```env
SEARCH_ENGINE_MODE=legacy
```

Перезапустить бота. Схема DB и legacy-код не удалялись, поэтому откат миграции не требуется.

## 7. Что считается готовым к платным клиентам

- GitHub CI зелёный;
- provider-backed 30-case acceptance достиг целей;
- 10 Telegram shadow-кейсов проверены вручную;
- admin review занимает не более 2–3 минут;
- wrong model/accessory/unavailable у клиента — 0;
- client card не содержит technical diagnostics;
- fallback/rollback проверен.
