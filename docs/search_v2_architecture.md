# Search Engine V2: архитектура

## Статус и границы

V2 живёт рядом с legacy в `app/search_v2/` и использует strangler migration. Domain-код не зависит от Telegram, `Message`, `CallbackQuery`, FSM или SQLite-моделей. Production не переключён: режим по умолчанию — `legacy`; `main.py` и `.env` не менялись.

Публичная точка входа V2:

```python
result: SearchResultV2 = await SearchServiceV2().search(legacy_request)
```

Telegram вызывает не адаптеры, а bridge `run_search_for_request(request, mode=None)`. Только bridge выбирает движок и решает, что сохранять или возвращать клиентскому flow.

## Pipeline

1. `normalize_legacy_request()` превращает dict/legacy `Request` в `SearchRequestV2`; вопросы мастера становятся структурированными specs.
2. `build_query_plan()` создаёт не более двух запросов на источник. Потерянный hard token даёт `INVALID_QUERY_PLAN` до сети.
3. `SearchSourceOrchestrator.run()` выбирает максимум три адаптера, применяет source/case timeout, cache и отдаёт partial snapshot после каждого источника.
4. `normalize_offers()` централизованно строит `Offer`, `SellerInfo`, availability, trust и `ProductIdentity` из `RawOffer`.
5. `apply_exact_match()` отсекает wrong model, required-spec mismatch и аксессуары до ranking.
6. `group_offers()` объединяет только одну canonical configuration; одинаковая модель у разных продавцов остаётся разными offers.
7. `analyze_product_groups()`, `apply_risks()`, `rank_offers()` и `select_recommendations()` считают медиану, риски и максимум три уникальные роли: `BEST_OVERALL`, `CHEAP_WITH_RISK`, `RELIABLE`.
8. `apply_automatic_verification()` и manual verification раздельно формируют final state; ручная проверка скрывает только подтверждённые stale warnings.
9. `build_search_metrics()` и `SearchResultV2.to_dict()/to_json()` дают JSON-safe результат для snapshot/debug.

## Основные модели

`models.py` содержит `SearchRequestV2`, `QueryPlan`, `SourceQuery`, `SourceAttempt`, `RawOffer`, `Offer`, `ProductIdentity`, `SellerInfo`, `AvailabilityInfo`, `VerificationState`, `RiskFlag`, `MarketStats`, `ProductGroup`, `Recommendation`, `SearchMetrics` и `SearchResultV2`.

Ключевое разделение:

- `RawOffer` — данные транспорта без продуктового решения;
- `Offer` — нормализованное предложение конкретного продавца;
- `ProductIdentity` — конфигурация товара для exact match/grouping;
- `ProductGroup` — одна конфигурация и offers разных продавцов;
- `SearchResultV2` — полный результат case, включая rejected/manual/debug данные.

## Service и persistence boundaries

- `SearchServiceV2` компонует pipeline, но не отправляет Telegram-сообщения.
- `SourceAdapter` только получает `RawOffer`; он не назначает trust, score или recommendation role.
- `SearchV2SnapshotStore` использует существующий `SearchCache` с namespace `search_v2:source|normalized|result|shadow`; это diagnostic cache, не product DB.
- `build_shadow_comparison()` сравнивает результаты без их изменения; `format_shadow_comparison()` предназначен только для Admin Debug.
- Клиентский formatter не показывает adapter names, confidence, 403/429/captcha и raw diagnostics.

## Расширение

Новый транспорт добавляется через контракт из [search_v2_source_contract.md](search_v2_source_contract.md). Безопасное включение описано в [search_v2_migration.md](search_v2_migration.md).
