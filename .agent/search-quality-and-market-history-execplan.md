# Make NOVA search precise and build a safe market-history base

This ExecPlan is a living document. Its Progress, Surprises & Discoveries, Decision Log, and Outcomes & Retrospective sections must be maintained as work proceeds. It follows `.agent/PLANS.md`.

## Purpose / Big Picture

NOVA must find the same purchasable product that the customer means, understand the important technical parameters, and show a lower market price only when current evidence proves it. After this work, a request such as a MacBook with a specific chip, memory and keyboard, a TV with a particular diagonal and refresh rate, or a coffee machine with a concrete ECAM code will retain those conditions from request through matching and ranking. In parallel, NOVA will build its own separate market history from safe aggregates. That history will show a cautious market trend after enough observations; it will never replace the current-offer evidence required for a saving claim.

The browser, the Telegram Mini App, and the bot search route must use the same Search V2 rules. The work does not modify `bot.db`, payments, credits, `.env`, tokens, or the Telegram entry point.

## Progress

- [x] (2026-08-13 18:55Z) Audited Search V2 parameter flow, grouping, market calculations, source coverage, metrics, cache, and browser API.
- [x] (2026-08-13 18:58Z) Confirmed that `data/search_cache.sqlite3` is a short-lived query cache, not a safe long-term market history.
- [x] (2026-08-13 19:24Z) Integrated title-confirmed category facts into Search V2 offer normalization with laptop, TV, coffee-machine, and phone mismatch regressions.
- [x] (2026-08-13 19:44Z) Added a bounded category-aware second discovery query that preserves every hard token and does not invent specialised configurations.
- [x] (2026-08-13 19:46Z) Made explicit optional preferences rank only already exact/compatible offers, without changing the hard-match gate.
- [x] (2026-08-13 19:12Z) Added an isolated market-history core with daily, anonymous aggregates and deterministic temporary-database tests.
- [x] (2026-08-13 19:29Z) Connected the optional history store to Search V2, the browser, and the Telegram bridge after all matching, direct-link, availability, grouping, and lane gates.
- [x] (2026-08-13 19:18Z) Added aggregate coverage/freshness metrics and fixed cached-attempt accounting without exposing private input or diagnostics to users.
- [x] (2026-08-13 20:05Z) Added strict typed comparisons for units, aliases, booleans, enums, and SKU/MPN identifiers; missing evidence remains unconfirmed.
- [x] (2026-08-13 20:06Z) Added client-safe display of up to four evidence-confirmed characteristics per browser result card.
- [x] (2026-08-13 20:18Z) Restricted market grouping to category-critical configuration fields and normalized their common unit/alias forms.
- [x] (2026-08-13 20:19Z) Made explicit technical parameters in the customer request into hard constraints without inferring vague requirements.
- [x] (2026-08-13 20:20Z) Added a read-only, client-safe market-trend response after sufficient same-configuration daily evidence.
- [x] (2026-08-13 20:24Z) Rendered that trend as a compact browser-card indicator only when the API has sufficient evidence.
- [x] (2026-08-26) Added truthful capability flags, capability-aware structured filters, confirmed-only Yandex region scope, bounded direct-page verification, and deterministic canary rollout.
- [x] (2026-08-26) Verified the live Yandex path without database writes; V2 retained only direct page/price-confirmed exact offers and left missing seller/availability proof for manual review.
- [x] (2026-08-26) Exposed only evidence-gated client-safe trend labels in the shared browser/Mini App result payload.

## Surprises & Discoveries

- Observation: Search V2 already has strict model, storage, SIM/region, condition, availability, direct-link, and independent saving gates.
  Evidence: `app/search_v2/exact_match.py`, `app/search_v2/savings.py`, and `app/search_v2/service.py` reject hard mismatches before recommendation.

- Observation: rich category facts already exist for laptops, TVs, coffee machines, monitors, robots, vacuums, mattresses, and other categories, but Search V2 does not yet consume them consistently.
  Evidence: `app/category_facts.py` exports `extract_category_facts`, while `app/search_v2/normalization.py` currently builds only a small subset of `ProductIdentity.key_configuration`.

- Observation: current market snapshots use `SearchCache`, which stores query text and expires quickly.
  Evidence: `app/search_cache.py` stores `query` in `cache_entries` and its Search V2 TTLs are measured in seconds or minutes.

- Observation: current market statistics and savings are different mechanisms.
  Evidence: `app/search_v2/market_analysis.py` computes a per-run median; `app/search_v2/savings.py` requires three current independent references from two sources. Historical observations must not weaken the latter rule.

- Observation: browser search must not disclose raw source diagnostics or quality telemetry.
  Evidence: `app/web_api.py` deliberately returns a small client-safe response and filters technical text.

- Observation: a cached source result carried cache metadata but its individual attempt still reported `cache_hit=False`.
  Evidence: the regression in `tools/test_search_v2_orchestrator.py` now verifies the returned cached attempt is marked explicitly.

- Observation: structured source metadata may be stale or describe a nearby SKU, while title facts are immediately tied to the visible offer.
  Evidence: `tools/test_search_v2_parameter_matrix.py` proves title-confirmed facts can satisfy a requirement but opaque metadata alone cannot invent a new exact match.

- Observation: category query templates include specialised terms such as “автоматическая”, “IPS”, and “лидар” that would be unsafe for a general request.
  Evidence: `tools/test_search_v2_query_planner.py` now verifies these templates are skipped unless the matching parameter is explicitly required.
- Observation: several adapters claimed city support even though their collector only issued a national query.
  Evidence: source capabilities now distinguish structural city filters, textual locality, and unsupported locality; market history uses the request city only after `region_scope_confirmed`.
- Observation: verifying every discovery row first wastes page budget on wrong models and category/search URLs.
  Evidence: `SearchServiceV2` now rejects hard mismatches first and verifies at most six best remaining direct-page candidates with concurrency two.

## Decision Log

- Decision: first reuse the existing category-fact extractor instead of creating a second parser.
  Rationale: one data-driven extractor avoids different interpretations of RAM, SSD, resolution, dimensions, and category features between the legacy flow and Search V2.
  Date/Author: 2026-08-13 / Codex.

- Decision: create a separate optional SQLite file at `data/market_history.sqlite3`, never reuse `bot.db` or the query cache.
  Rationale: market history needs durable indexed observations, but it must remain isolated from user, payment, and request data.
  Date/Author: 2026-08-13 / Codex.

- Decision: store daily anonymous aggregate observations, not raw listings.
  Rationale: an aggregate has enough information for a market trend without retaining search text, title, URLs, seller identity, city, Telegram identity, cookies, source responses, or tokens.
  Date/Author: 2026-08-13 / Codex.

- Decision: a historical trend can never by itself create an “экономия” claim.
  Rationale: a current saving must still have at least three current comparable direct offers across two independent sources under `app/search_v2/savings.py`.
  Date/Author: 2026-08-13 / Codex.

- Decision: separate market lanes for new retail, new marketplace, new private, used, and refurbished goods.
  Rationale: a used or private listing cannot set the reference price for a new commercial item, even when the product model is identical.
  Date/Author: 2026-08-13 / Codex.

- Decision: unknown mandatory facts remain unconfirmed and cannot improve a card to TOP-1.
  Rationale: this keeps wider search coverage from turning into false exact matches.
  Date/Author: 2026-08-13 / Codex.

- Decision: quality coverage is stored only on `SearchMetrics` as counts, ratios, freshness, and an aggregate price span.
  Rationale: it is enough to reject false confidence while avoiding storage or browser disclosure of any customer or merchant data.
  Date/Author: 2026-08-13 / Codex.

- Decision: category facts from offer titles fill missing source facts, while source structured facts retain precedence.
  Rationale: a visible product-card title is strong evidence for a named offer, and retaining explicit source facts avoids overwriting verified structured data.
  Date/Author: 2026-08-13 / Codex.

- Decision: use exactly one category-aware discovery variant inside the existing per-source cap of two queries.
  Rationale: wider coverage is valuable, but an unlimited template fan-out would slow sources down and lower price freshness.
  Date/Author: 2026-08-13 / Codex.

- Decision: normalize units and aliases only from direct title or structured evidence, never opaque snippet metadata.
  Rationale: formats such as 4K, 3840x2160, 1 TB, and 1024 GB must compare consistently without turning a nearby SKU into a false exact match.
  Date/Author: 2026-08-13 / Codex.

- Decision: show only a short list of evidence-confirmed facts on a customer card.
  Rationale: useful configuration detail should be visible, but raw source diagnostics, URLs, and weak metadata must stay internal.
  Date/Author: 2026-08-13 / Codex.
- Decision: an unresolved Yandex city produces an unconfirmed market scope, never a guessed local price.
  Rationale: national delivery evidence is useful, but it must not be represented as a city-specific market observation.
  Date/Author: 2026-08-26 / Codex.
- Decision: all Telegram and web V2 entry points use the same conditional external-page verifier.
  Rationale: a search snippet or result card cannot become a verified offer through one interface while remaining unverified through another.
  Date/Author: 2026-08-26 / Codex.

## Outcomes & Retrospective

This implementation slice is complete. Search V2 carries strict typed facts, separates conflicting configurations, writes only anonymous market aggregates, and exposes an evidence-gated trend that cannot affect current savings. Source capability and regional truthfulness are now enforced, Yandex/search-card rows require bounded direct-page proof, and rollout is guarded by shadow metrics plus deterministic canary fallback. Enabling production still requires a real consented sample to pass the documented gate.

## Context and Orientation

`app/request_parser.py` turns free text into a category, product model, required criteria, optional criteria, budget, city, and condition. `app/search_v2/request_normalizer.py` converts that result into `SearchRequestV2`. A required criterion is a fact the chosen offer must satisfy; an optional criterion affects ranking but must not reject a correct item.

Search sources return `RawOffer` records. `app/search_v2/normalization.py` turns them into `Offer` records and `ProductIdentity` values. `app/search_v2/exact_match.py` rejects a different model or known wrong required specification. `app/search_v2/grouping.py` puts identical configurations together. `app/search_v2/market_analysis.py` calculates a current market distribution inside one group. `app/search_v2/savings.py` proves a customer-visible saving from fresh independent current offers. `app/search_v2/service.py` is the only pipeline coordinator.

`app/category_facts.py` is a pure local extractor. It recognizes category facts from a product title and optional verified page text and attaches evidence such as `title` or `page_text`. It has no network or database dependency and is the correct common source of category fields.

The existing `data/search_cache.sqlite3` is a cache and benchmark store. It retains raw queries and expires quickly, so it must not be used as the market-history database. The new `data/market_history.sqlite3` contains only aggregates keyed by a SHA-256 hash of the canonical product identity and a market lane. A market lane is a deliberately separate comparison pool such as a new retail product or a used private listing.

## Plan of Work

### Milestone 1 — Carry all important product facts through Search V2

Update `app/search_v2/normalization.py` to call `app.category_facts.extract_category_facts(category, title, verified_page_text)` after source metadata has been safely collected. Merge extracted facts with source metadata deterministically: structured source metadata with explicit values wins, extracted facts fill gaps, and an evidence map remains visible only internally. Copy category-critical values such as RAM, SSD, processor, resolution, panel, diagonal, refresh rate, product size, SIM/region, and condition into `ProductIdentity.key_configuration` where the matcher and grouping key can use them.

Update `app/search_v2/request_normalizer.py` only where required parser criteria need canonical aliases. Do not change a user’s explicit model into an inferred one. Required criteria stay strict; optional criteria stay non-blocking. Extend `app/search_v2/exact_match.py` with clear type-aware comparisons only after each parameter has a deterministic fixture: numbers compare with compatible units, sets require the requested member, and enum aliases use one normalized vocabulary. A missing required fact must result in a generic/manual candidate, not a false exact match.

Create `tools/test_search_v2_parameter_matrix.py`. It must cover at least phone storage/SIM, laptop RAM/SSD/CPU, TV diagonal/resolution/refresh rate, monitor panel/refresh rate, coffee-machine type/cappuccinator, and a dimension-based category. Every case includes a matching title and a wrong configuration that cannot become exact. Continue to run `tools/test_category_facts.py` so the shared extractor stays stable.

### Milestone 2 — Build an isolated market-history core

Create `app/search_v2/market_history.py` with a `MarketHistoryStore` protocol, `NullMarketHistoryStore`, and `SQLiteMarketHistoryStore`. The null implementation does nothing and is the default, so history can never slow down or break a normal search. SQLite is enabled only by explicitly injecting a store into `SearchServiceV2` in a later milestone.

The SQLite schema stores one daily aggregate only when a group has at least three fresh comparable prices from at least two independent sources. The row contains the observation day, SHA-256 identity hash, category, condition, market lane, scope (`local`, `delivery`, or `unknown`), source count, comparable count, minimum, lower quartile, median, upper quartile, maximum, fresh count, and quality level. It must not store request text, model text, title, URL, product ID, seller ID, seller name, city, user ID, Telegram data, raw HTML, source errors, cookies, tokens, or credentials.

The lane classifier must be explicit: high-trust retail becomes `new_retail`, a known marketplace becomes `new_marketplace`, classified/professional and classified/private are distinct, used becomes `used`, and refurbished becomes `refurbished`. Keep these lanes separate in every index and query. Use an index for `(identity_hash, market_lane, scope, observed_day DESC)`. Repeated writes for the same product, lane, scope, and day upsert one aggregate. Prune records older than ninety days in a bounded transaction.

Create `tools/test_search_v2_market_history.py` using `TemporaryDirectory`. Prove no raw sensitive strings can be found in the stored database, that lanes and scopes do not mix, same-day writes are idempotent, old records are pruned, insufficient evidence is rejected, and a simulated storage error does not escape the null/error boundary.

### Milestone 3 — Attach the history only after current search safety gates

Extend `SearchServiceV2.__init__` in `app/search_v2/service.py` with an optional `market_history_store`. Record a historical aggregate only after normalization, direct-product URL validation, external page verification where required, exact/compatible matching, availability filtering, grouping, and market analysis. Wrap writing in a bounded `try/except`; an unavailable history file must not alter result status, recommendations, timings, or user text.

At this point history remains write-only for live results. Add a pure query method that returns a trend only if the same hashed identity, lane, and scope has enough independent daily aggregates. A trend may say that the seven-day median moved up or down, or that data is insufficient. It cannot supply a baseline to `SavingsEvidence` and cannot change `PriceClass` for the current result.

Add tests proving a stale historical low price cannot create a current saving and that service completion remains successful when the history store raises an exception.

### Milestone 4 — Improve source and regional truthfulness

Extend `SourceCapabilities` in `app/search_v2/adapters/base.py` to state whether each source actually applies city, condition, SKU, price, and category filters. `app/search_v2/query_planner.py` must pass a city only to an adapter that can apply it structurally or honestly preserve it in a local search query. It must not imply that a national source returned a city-specific price when it did not.

Improve the Yandex region resolver in `app/sources/yandex_search_source.py` only with verified region identifiers; unresolved cities become an explicit unconfirmed scope rather than a guessed location. Keep external web discovery fail-closed: a snippet price is never a recommendation until `app/search_v2/external_page_verifier.py` confirms a direct product page, product signal, and structured price.

For every source, add fixtures for exact product, wrong configuration, unavailable listing, missing price, city mismatch, and a blocked or rate-limited reply. Do not introduce scraping, browser cookies, CAPTCHA bypass, proxy rotation, or private endpoints.

### Milestone 5 — Measure quality and rank by what the user asked for

Extend `SearchMetrics` in `app/search_v2/models.py` and `build_search_metrics` in `app/search_v2/metrics.py` with attempted and successful source counts, priced exact source count, comparable offer count, fresh and stale comparable counts, top-group price-span percentage, and current market-group count. These are aggregate internal metrics. Never include input query, model, title, URL, city, seller, user, raw error, or secret data.

Fix cache-hit accounting in `app/search_v2/orchestrator.py` so an attempt served by cache records `cache_hit=True`. Add `tools/test_search_v2_metrics.py` cases with a fixed clock for fresh/stale offers and bounded price spread, and an orchestrator cache-hit regression test.

Update `app/search_v2/ranking.py` so optional criteria and category ranking preferences influence only an already exact/compatible candidate. Reward confirmed required facts, direct verification, reliable availability, current evidence, and delivery/city evidence where the source really applied them. Do not make a missing optional fact a rejection. Price is evaluated after correctness and safety, not before them.

### Milestone 6 — Show only safe market information

Extend `app/web_api.py` with a compact, client-safe trend object. It can contain a label such as “за 7 дней медиана снизилась на 4%” only when the history query has sufficient data. Otherwise it returns no trend. The browser and Mini App show the trend separately from the current saving statement and preserve the existing distinction between an exact item, a configuration variant, and a manual check.

Do not expose individual historical prices, source error text, internal metrics, identity hashes, or source debugging information in the browser, Mini App, or Telegram formatter. Add web API tests confirming their absence.

### Milestone 7 — Expand hardware coverage deliberately

Once the parameter matrix and current-price gates are green, add categories one at a time through `app/category_registry.py` and `app/category_facts.py`. For each category, add at least ten deterministic parameter cases, direct-card fixtures, wrong-model fixtures, and a live review sample before promoting it from `generic_tech` to a named category. “Any technology” means any clearly identifiable model with verifiable current offers; it does not mean arbitrary vague searches receive a fabricated market comparison.

## Concrete Steps

Run all commands from `C:\Users\Пользователь\Documents\naydi_vygodnee`.

For the current Milestones 1 and 2:

    python tools/test_category_facts.py
    python tools/test_search_v2_parameter_matrix.py
    python tools/test_search_v2_market_history.py
    python tools/test_search_v2_normalization.py
    python tools/test_search_v2_exact_match.py
    python tools/test_search_v2_market_analysis.py
    python tools/test_search_v2_savings.py
    python tools/test_alice_parser.py
    python -m compileall app tools
    git diff --check

After Milestones 3 through 6, add:

    python tools/test_search_v2_metrics.py
    python tools/test_search_v2_orchestrator.py
    python tools/test_search_v2_end_to_end.py
    python tools/test_web_api.py
    python tools/test_telegram_miniapp.py

For a manual browser check, run `python tools/run_nova_web.py`, open `http://localhost:8080`, request a concrete model with a configuration, and confirm the shown card names the same configuration. After enough separate searches have safely populated the optional history store, confirm a trend appears only for that identical configuration and lane; a new item must never show a trend derived from used or refurbished listings.

## Validation and Acceptance

Acceptance requires all parameter-matrix cases to reject their intentionally wrong variant. A search for an exact model must not gain a lower price from a different memory size, CPU, generation, SIM version, condition, package, diagonal, or required category feature.

The history store must create no changes to `bot.db`, no existing `*.db` file, `.env`, or Telegram state. A database inspection using a temporary test store must show aggregates and hashes only; it must not reveal a raw query, product title, URL, city, seller, user ID, token, cookie, or source error. Failure to open or write the history store must leave a search result usable.

A displayed “экономия N ₽” continues to require current direct independent evidence under `SavingsEvidence`. A historical trend may appear only with enough same-identity, same-lane, same-scope aggregate observations and is visibly separate from the saving claim.

## Idempotence and Recovery

All tests use temporary directories and mocked offers. The live feature defaults to `NullMarketHistoryStore`; enabling a real history store is additive and can be disabled by omitting the injected store. Same-day aggregate writes are idempotent. Retention pruning is safe to rerun. If a migration or storage implementation is faulty, remove only `data/market_history.sqlite3`; no user or bot database needs recovery. A storage exception is caught and recorded only as an internal aggregate counter, never sent to a customer.

## Artifacts and Notes

The core source of truth for a current saving remains:

    app/search_v2/savings.py
    build_savings_evidence(selected, product_group)

The new history must remain separate:

    app/search_v2/market_history.py
    SQLiteMarketHistoryStore

The new file path is intentionally different from the cache:

    data/market_history.sqlite3

The current cache path remains:

    data/search_cache.sqlite3

## Interfaces and Dependencies

`app/search_v2/market_history.py` will define a small storage interface similar to:

    class MarketHistoryStore(Protocol):
        def record_group(self, group: ProductGroup, *, scope: str, observed_at: datetime | None = None) -> None: ...
        def trend_for(self, identity: ProductIdentity, *, lane: str, scope: str, days: int = 7) -> MarketTrend | None: ...

`NullMarketHistoryStore` implements both methods as no-ops. `SQLiteMarketHistoryStore` owns only the separate history file and creates its own tables. It must accept a filesystem path for deterministic tests.

`MarketTrend` must contain only safe aggregate fields: period days, observation count, median start/end, percent change, quality level, and human-safe availability state. It must not contain a raw identifier or listing data.

Change 2026-08-13: created after the request to improve every search parameter and build a market-offer base. The plan deliberately starts with matching correctness and anonymous evidence before source breadth.

Change 2026-08-13: recorded completion of the standalone anonymous store and internal coverage metric slice. The store remains disconnected from live search until its write boundary is tested.

Change 2026-08-13: the tested write boundary is now connected. `SearchServiceV2` writes only after current direct exact/compatible offers have been grouped, keeping history outside recommendation and savings decisions.

Change 2026-08-13: completed the parameter-extraction bridge using `app/category_facts.py`. The bridge accepts title-confirmed facts while retaining structured-source precedence and has explicit wrong-variant tests.

Change 2026-08-13: completed the bounded query-coverage and optional-preference slice. The second discovery query is category-aware but preserves all user constraints; optional preferences only break ties among already safe candidates.

Change 2026-08-13: completed typed parameter matching and client-safe fact rendering. Unit aliases, enum aliases, boolean values, and SKU/MPN values are tested against direct evidence; unverified metadata stays non-exact.

Change 2026-08-13: completed the configuration-grouping and market-trend slice. Known conflicting CPU/RAM/SSD, resolution/refresh, and coffee-machine type facts split market groups; the browser can show only a short, evidence-gated trend label.
