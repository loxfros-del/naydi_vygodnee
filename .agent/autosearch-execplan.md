# Improve Product Autosearch

This ExecPlan is a living document. It follows `.agent/PLANS.md` and must stay self-contained while the work proceeds.

## Purpose / Big Picture

The Telegram bot should find cleaner product candidates from Russian stores for a user request such as "Нужен телевизор для PS5 до 45к в Ярославле". After this change, `python tools/test_search.py "Нужен телевизор для PS5 до 45к в Ярославле"` should finish without crashing even when sources fail, show per-source diagnostics, reject ads/articles/categories as trash, and keep only real product cards as GOOD, OK, or a small fallback set of WEAK candidates.

## Progress

- [x] (2026-07-04) Read `.agent/PLANS.md`, `app/product_search.py`, `app/config.py`, `app/db.py`, `app/search_links.py`, and `tools/test_search.py`.
- [x] (2026-07-04) Add search source settings to `app/config.py`.
- [x] (2026-07-04) Add a source registry, safe HTTP helper, expanded Russian sources, and GOOD/OK/WEAK/TRASH quality classification to `app/product_search.py`.
- [x] (2026-07-04) Update `tools/test_search.py` output to show RAW, TRASH, GOOD, OK, WEAK, saved count, failed sources, and productive sources.
- [x] (2026-07-04) Validate with `python -m compileall .`, `python tools/test_alice_parser.py`, and `python tools/test_search.py "Нужен телевизор для PS5 до 45к в Ярославле"`.

## Surprises & Discoveries

- Observation: The current search already has a partial v2 implementation with Wildberries API, DDGS generic/site search, fail-soft attempt records, and old NORMAL/WEAK statuses.
  Evidence: `app/product_search.py` contains `WildberriesAdapter`, `GenericSearchAdapter`, `SearchAttemptData`, `quality_stats`, and `run_product_search`.
- Observation: The working tree is already dirty in files that this task may touch, including `app/config.py` and `tools/test_search.py`.
  Evidence: `git status --short` showed existing modifications before my edits.
- Observation: Search engines can return review/article URLs under store subdomains, and those can look product-like unless URL path checks include Russian and plural article markers.
  Evidence: The target test initially allowed Citilink `/otzyvy/` and `/articles/` URLs until these paths were added to the TRASH rules and test guard.

## Decision Log

- Decision: Keep existing DB statuses for compatibility and add a separate in-memory quality level.
  Rationale: Admin UI, AI-card generation, and report code already understand `CANDIDATE`, `WEAK_CANDIDATE`, and `REJECTED_AUTO`; changing the database status vocabulary would risk unrelated regressions.
  Date/Author: 2026-07-04 / Codex
- Decision: Use DDGS site searches for stores without a stable public API, and keep Wildberries on its public JSON endpoint.
  Rationale: This expands source coverage without introducing credentials, scraping-heavy page parsers, proxy hardcoding, or schema changes.
  Date/Author: 2026-07-04 / Codex

## Outcomes & Retrospective

Implemented without changing the SQLite schema, `.env`, `bot.db`, or `main.py`. The final target test completed without crashing even though Wildberries returned a non-JSON response; that source was reported as `ERROR` and other sources continued. The final run showed `RAW=55`, `TRASH=51`, `GOOD=0`, `OK=3`, `WEAK=1`, and `saved=3`; saved candidates had no Bing ad URLs, Amazon/eBay URLs, article URLs, review URLs, category/search URLs, or TRASH rows. The remaining gap is that source pages often omit prices, so current clean candidates are mostly OK rather than GOOD until direct store parsing is added.

## Context and Orientation

`app/product_search.py` collects search rows, normalizes them into `ProductCandidate`, classifies and scores them, then `run_product_search` saves selected candidates to `search_results` and source diagnostics to `search_attempts`. `tools/test_search.py` runs the same collection path without Telegram. `app/config.py` loads environment-backed settings. `search_results` must keep its existing schema, so future fields such as rating, reviews, seller, city, availability, and description will live on the in-memory candidate object and can be folded into snippets/flags until a later schema migration.

## Plan of Work

First, add timeout and source enable settings in `app/config.py` with defaults. Next, update `app/product_search.py` by defining source metadata for Ozon, Яндекс Маркет, Wildberries, Avito, DNS, М.Видео, Ситилинк, Мегамаркет, and generic web search. The collector will run only enabled sources, isolate each source with try/except, and store `ERROR`, `EMPTY`, or `OK` attempts. The classifier will reject Bing ad redirects, foreign marketplaces, articles, reviews, search pages, categories, and wrong product types as TRASH. Product-like rows become GOOD when they have a concrete model, price, store, and direct product URL; OK when they have a concrete model and direct URL but no price; WEAK only when useful but incomplete. Avito candidates get used-item risk flags and cannot be GOOD when the request does not allow used goods. Finally, `tools/test_search.py` will report the new stats and print only saved-quality candidates.

## Concrete Steps

Work from `C:\Users\Пользователь\Documents\naydi_vygodnee`.

Run:

    python -m compileall .
    python tools/test_alice_parser.py
    python tools/test_search.py "Нужен телевизор для PS5 до 45к в Ярославле"

The search test should show no `bing.com/aclick`, no Amazon/eBay, no articles/categories in candidates, and either several GOOD/OK rows or a clear debug message that source failures left too few clean product cards.

## Validation and Acceptance

Acceptance is behavior-based. The console search test must complete without an exception. Its summary must include RAW, TRASH, GOOD, OK, WEAK, saved count, failed sources, and sources that produced GOOD/OK. The candidate list must not include Bing Ads, Amazon/eBay, review articles, category/search pages, or pages for the wrong product type.

## Idempotence and Recovery

All changes are source edits only. Running the test commands repeatedly is safe. The bot database schema is not changed, and `.env`, `.venv`, `bot.db`, and `main.py` are not touched.

## Artifacts and Notes

Validation output will be summarized after commands run.

## Interfaces and Dependencies

No new package is required. Existing `requests`, `ddgs`, and `duckduckgo-search` dependencies remain sufficient. `ProductCandidate` will retain existing fields and add optional in-memory fields: `quality`, `source_type`, `rating`, `reviews_count`, `seller`, `city`, `availability`, and `description`.

Revision note, 2026-07-04: Marked implementation and validation complete, recorded the article/review URL discovery, and summarized final test evidence so the plan reflects the actual delivered behavior.
