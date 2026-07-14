# Search Engine V2: контракт источника

## Интерфейс

Каждый источник реализует `SourceAdapter` из `app/search_v2/adapters/base.py`:

```python
class SourceAdapter(ABC):
    name: str
    platform: str
    version: str = "1"
    capabilities: SourceCapabilities

    async def search(
        self,
        request: SearchRequestV2,
        source_query: SourceQuery,
        context: SourceContext,
    ) -> SourceResult: ...
```

`SourceResult` содержит `status`, `raw_offers`, `attempts`, `duration`, `error`, `rate_limit_info`, `cache_info`. Допустимые статусы: `SUCCESS`, `PARTIAL_SUCCESS`, `EMPTY`, `BLOCKED`, `RATE_LIMITED`, `UNAUTHORIZED`, `TIMEOUT`, `INVALID_RESPONSE`, `INVALID_QUERY_PLAN`, `ERROR`.

## Обязательные правила

- Проверить `SourceQuery` до I/O. При потерянном hard token вернуть `INVALID_QUERY_PLAN`; сеть не вызывать.
- Возвращать только `RawOffer` и факты транспорта. Не определять exact match, platform/seller trust, risk, score или recommendation role.
- Ошибка одной строки не должна уничтожать полезные строки; допустим `PARTIAL_SUCCESS`.
- 403/429/captcha — технический статус, не оценка площадки или продавца. Защиту сайта не обходить.
- Не делать retry-loop. Bridge не повторяет вызов; timeout и negative cache обрабатываются выше.
- Не выдумывать rating, reviews, warranty, availability или seller status.
- Уважать `SourceContext.limit` и `SourceContext.timeout`.

Оркестратор дополнительно ограничивает выполнение: максимум три адаптера параллельно, максимум два query на источник, per-source и общий case timeout. Завершившиеся источники сохраняются даже при timeout другого.

## Текущие источники

- Discovery: `yandex_market`, `ozon`, `avito`; optional `wildberries`.
- Reliable anchors: `dns`; optional `citilink`, `mvideo`.
- Fallback: `generic_exact`, только с exact query.

`build_default_registry(include_optional=True)` создаёт реестр. Алиасы канонизируются в `source_registry.py`. `SourcePolicy` применяет приоритет и query budget.

## Как добавить источник

1. Создать `app/search_v2/adapters/<source>.py`.
2. Для существующего синхронного collector использовать `LegacyBridgeAdapter`; для exact site-query — `SiteExactSearchBridge`; для нового транспорта реализовать `SourceAdapter` напрямую.
3. Задать стабильные `name`, `platform`, `version` и только транспортные `SourceCapabilities`.
4. Преобразовывать строки через `raw_offer_from_legacy()` либо явно заполнить `RawOffer`; продуктовую нормализацию не дублировать.
5. Экспортировать адаптер из `adapters/__init__.py`, зарегистрировать в `build_default_registry()` и при необходимости добавить alias/source tier в policy.
6. Добавить deterministic tests: valid result, empty, malformed row, timeout, 403/429 и lost hard token без сетевого вызова.

## Cache key

`build_source_cache_key()` включает namespace/version, adapter version, source, tier, query и все identity-critical hard constraints. Поэтому `iPhone 16` и `iPhone 16 Pro` не делят cache entry. После изменения transport semantics увеличивается `adapter.version`.
