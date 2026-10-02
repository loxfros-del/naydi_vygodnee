# Audit стоимости и задержки Avito — 27.09.2026

## Реальный pipeline

1. `ZenStudioProvider.collect_to_target`: bounded collection, budget reserve до POST, страницы/варианты запроса до target или safety stop.
2. `normalize_dataset`: нормализация полной цены и доказательств, затем dedup по `listing_id`.
3. `evaluate_rules` и `route_text_ai`: город, URL/активность, описание/фото, полная цена, явные дефекты, модель/версия/accessory, обязательные поля и price range.
4. `_review_market`: text-only AI для чистых независимых рыночных ссылок.
5. `_review_with_budget`: обязательный text AI либо reuse точного свежего review; затем text blockers.
6. `_photo_candidate_ids`: неизменяемая комплектность, рыночная доказанность в bargain mode, preliminary value rank, seller diversity и configurable `ai_max_listings` cap.
7. Photo AI: все фото одного listing в одном вызове с одной повторной попыткой только для неполного JSON/coverage.
8. `rank_listings`: safety, сопоставимость, независимые продавцы, положительная разница с медианой и роли.
9. `_verify_finalists`: повторно загружаются только точные URL выбранных финалистов; цена, активность и неизменность evidence проверяются перед публикацией.

## Где правило может выполняться

| Группа правила | Самая ранняя эквивалентная стадия | Почему не раньше |
|---|---|---|
| Город, pickup, цена/полная стоимость, active URL, наличие описания/фото, deterministic defect, модель/версия/accessory, structured required attributes | A: до AI | Это уже явные нормализованные факты. |
| AI mismatch, скрытое условие цены, дефект/противоречие в полном тексте, confidence, final required attributes | B: после text AI | Нужен полный недоверенный текст; structured fields недостаточны. |
| Положительная разница и минимум 3 независимых сопоставимых продавца | B: до photo AI | Фото не меняет цену и text-derived comparable identity; может только отклонить кандидата. |
| Полнота seller-stated комплекта | B: до photo AI | Photo review не добавляет seller-stated `completeness`. |
| Полный photo coverage, видимые дефекты, расхождения text/photo | C: после photo AI | Эти факты возникают только из изображений. |
| Актуальность страницы, текущая цена/активность, изменение исходного evidence | D: final revalidation | Только повторная загрузка точного URL даёт свежий факт. |

Dedup объявления остаётся после normalization. Seller diversity применяется при shortlist, а независимость продавцов для выгоды — при рыночном сравнении. Эти правила не объединены: у них разная семантика.

## Почему старый pilot дал 21 → 15 → 0

- 21 прошли deterministic filters. Каждый потенциально публикуемый listing обязан был пройти text AI: только он читал полный текст на скрытые условия цены, дефекты и противоречия.
- 15 прошли text blockers и попали в photo shortlist. Старый gate ранжировал выгоду, но не исключал отсутствие положительной доказанной разницы/3 независимых продавцов и неизменяемый пробел комплекта.
- Старый trace не содержит per-listing reason codes, поэтому точное распределение 15 → 0 восстановить нельзя. Лог подтверждает одну неполную photo coverage; остальные photo reviews завершились, но `completed_target` оставался 0. Это совместимо прежде всего с отсутствием подтверждённой выгоды или condition/config evidence. Final revalidation не запускался, потому что до него не было финалистов.

Теперь trace агрегирует primary/secondary codes на стадиях `before_text_ai`, `after_text_ai`, `before_or_during_photo_ai`, `ranking`, `final_revalidation`, `final_safety` без raw seller text.

## Cache и AI reuse

- Full job fingerprint не изменён: price bounds по-прежнему разделяют active jobs.
- Market key теперь использует canonical market identity, регион, категорию, конфигурацию/состояние и pickup policy; price bounds, mode, priority и quota в него не входят.
- PS5 и PlayStation 5 получают один segment key; разные модель, регион или категория — разные.
- Точный in-memory AI cache теперь сохраняет завершённый text stage, а не только полный vision result. Свежий clean market text review также может заменить дублирующий candidate text call только внутри проверенного search path и при неизменном evidence.
- Photo reuse остаётся строгим: cache key включает полный evidence и список image URLs. Изменившиеся изображения дают miss; по отдельным image hash кэша нет.

## Actor

Публичная schema текущего `zen-studio/avito-listings-scraper` подтверждает `maxResults`, `includeDetails=false` для быстрого поиска и batch `listingUrls` для полных данных. `includeDetails=true` возвращает описание, фото, характеристики, адрес и продавца и работает медленнее: [input schema](https://apify.com/zen-studio/avito-listings-scraper/input-schema), [Actor README/API](https://apify.com/zen-studio/avito-listings-scraper).

Текущий код всегда ставит `includeDetails=true`. `collect_to_target` уже делает bounded progressive widening и останавливается по target/budget/pages/duplicates, но каждая следующая страница — новый paid Actor run. Документированного продолжения одного run по page cursor нет, поэтому новая widening strategy не внедрена.

Discovery/detail split технически возможен (`includeDetails=false` → `listingUrls`), но не внедрён: сохранённые fixtures не доказывают достаточность basic output для city/category/identity shortlist, а два paid этапа требуют отдельного малого live validation. Новый scraper не нужен.

## Spending

Журнал остаётся schema-1 compatible. Запись теперь различает:

- `actualSpendUsd` — окончательные receipts;
- `settledEstimateUsd` — terminal conservative estimates;
- `activeReservationUsd` — только реально незавершённые paid attempts.

Hard availability по-прежнему считает сумму settled actual/estimate и active reservations под одним межпроцессным file lock. Success заменяет reserve фактом; delayed/error/cancel закрывает active reserve в conservative settled estimate; поздний final receipt может заменить estimate. Неопределённый POST не повторяется.
