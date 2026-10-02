# Build an isolated Avito collection and multimodal review service

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This document is maintained in accordance with `.agent/PLANS.md` from the repository root.

## Purpose / Big Picture

After this change an administrator can run a separate local Avito mini-application, submit a product query, collect detailed listings through the Zen Studio Apify Actor, and receive structured recommendations based on the complete listing description and every listing photo. The Avito feature stays outside the Telegram bot and its SQLite database. It exposes a small HTTP API so the bot can call it later without moving collection or AI logic into `main.py`.

The result is demonstrable without credentials: a fixture endpoint and unit tests use fake Apify and AI clients. A real run requires `APIFY_TOKEN`, `AVITO_AI_API_KEY`, `AVITO_AI_BASE_URL`, and `AVITO_AI_MODEL` in the process environment. No secret is written to the repository or printed in a response.

## Progress

- [x] (2026-09-02 06:35Z) Inspected repository rules, existing Avito adapter, AI client, web prototype, dependencies, and test conventions.
- [x] (2026-09-02 06:40Z) Confirmed the official Apify Actor call and dataset retrieval flow.
- [x] (2026-09-02 09:05Z) Implemented the isolated `avito_service` package and safe process-environment configuration boundary.
- [x] (2026-09-02 09:10Z) Implemented Zen collection, lossless normalization, deterministic risk detection, multimodal AI review, exact comparable grouping, and role assignment.
- [x] (2026-09-02 09:15Z) Added the separate `avito_web` mini-application and HTTP API without changing `main.py`.
- [x] (2026-09-02 09:20Z) Added fixture-driven coverage for complete descriptions, every-photo review, contradiction detection, unsafe offer rejection, comparison boundaries, HTTP behavior, and secret handling.
- [x] (2026-09-02 09:25Z) Passed compilation, 12 focused tests, JavaScript syntax validation, and `git diff --check`; recorded the environment-limited Alice parser check.
- [x] (2026-09-02 11:40Z) Prevented unconfigured AI from producing placeholder cards, added strict structured output plus one automatic full-evidence retry, removed manual-check instructions from the public contract, and added a `3 company : 1 private` public ordering policy.
- [x] (2026-09-02 11:45Z) Expanded focused coverage to 16 passing tests, including AI readiness, retry behavior, hidden incomplete results, absence of manual instructions, and seller mixing.
- [x] (2026-09-02 11:55Z) Added a 50 ₽ report budget, 15 ₽ AI budget, a 12-candidate prefilter, actual Apify/AI cost accounting, and automatic Zen result capping.
- [x] (2026-09-02 12:00Z) Added a 12-hour in-memory AI cache and an administrator-only cost summary with a cost-based minimum report price.
- [x] (2026-09-02 12:05Z) Passed 20 focused tests, full compilation, JavaScript syntax validation, and `git diff --check`.
- [x] (2026-09-02 12:55Z) Added safe loading of the existing local `.env`, tuple-compatible Zen result normalization, low-detail transmission of every photo, and visible Zen/AI/cost progress logs.
- [x] (2026-09-02 13:05Z) Completed one real one-listing Zen smoke run without exposing credentials and expanded the focused suite to 23 passing tests; compilation, JavaScript syntax, and the legacy Alice parser test also pass.
- [x] (2026-09-02 13:35Z) Made AI response parsing tolerant of fenced JSON, localized booleans/verdicts, zero-based photo indexes, and coverage evidenced by photo findings; added an administrator-only fallback of up to 10 non-critical `CAUTION` cards.
- [x] (2026-09-02 13:40Z) Corrected the completed-analysis counter and passed 25 focused tests, full compilation, JavaScript validation, the Alice parser suite, and whitespace checks.
- [x] (2026-09-03 07:25Z) Split AI work into a broad text-first stage and an all-photo stage limited to the best 10 candidates; added separate stage counters and budget allocation.
- [x] (2026-09-03 07:30Z) Added shortlist-ordering and text-rejection regression coverage; 27 focused tests pass.
- [x] (2026-09-03 14:20Z) Replaced per-listing text calls with six-listing batches, added AI Tunnel HTTP-200 error/empty-content compatibility, retry plus circuit breaking, and PS5 console relevance filtering; 34 focused tests pass.
- [x] (2026-09-04 09:00Z) Reworked the local site into a client-first «Найди выгоднее» flow with separate product/value modes, free-form queries, optional refinements, and a requested output of 3–10 verified listings.
- [x] (2026-09-04 09:05Z) Separated customer-safe results from collapsed operator diagnostics, added staged progress messages and compact recommendation cards, and passed 37 focused tests.
- [x] (2026-09-04 09:20Z) Made Zen normalization tolerant of omitted/renamed listing IDs, recovered IDs from direct Avito URLs, and skipped diagnostic dataset rows without aborting a run; 39 focused tests pass.
- [x] (2026-09-04 09:35Z) Diagnosed the live `_upgradeRequired` Dataset row and turned it into an explicit paid-plan error instead of an empty successful report; 40 focused tests pass.
- [x] (2026-09-04 09:35Z) Added request-aware AI text screening, a 2,000 ₽ absolute saving rule, direct-link and availability gates, and reserve photo review that stops after the requested 3/5 complete cards are ready; 48 focused tests pass.
- [x] (2026-09-04 11:10Z) Redesigned the client site in a black, violet, and red visual system with complete light/dark themes, adaptive category fields, six fully populated examples, and a durable local support-request inbox.
- [x] (2026-09-04 11:30Z) Added a real asynchronous search job contract with server-reported stages, an alive heartbeat, and a 45-second customer deadline rather than simulated browser-only messages.
- [x] (2026-09-04 11:45Z) Added customer-selected quality, balanced, and budget priorities; audited the complete AI funnel and exposed only owner-safe counts and timings.
- [x] (2026-09-04 12:10Z) Validated the redesigned UI contract, job/status API, support API, adaptive request parsing, ranking priorities, deadline behavior, compilation, and the mandatory Alice parser suite; 52 focused tests pass.
- [x] (2026-09-04 12:45Z) Simplified the first viewport, replaced the native 3/5 select with an animated accessible segmented control, introduced rotating exact-model examples plus locally remembered successful searches, and refined desktop/mobile typography.
- [x] (2026-09-04 13:10Z) Replaced the platform-dependent Segoe stack with the self-hosted Onest Cyrillic variable font, added a strict local font route, and covered font delivery in the HTTP regression suite.
- [x] (2026-09-05 08:10Z) Audited the customer interface, selection funnel, job lifecycle, external-service failures, and focused regression suite before the final product pass.
- [x] (2026-09-07 09:10Z) Expanded the client site with navigation, concise product sections, resilient progress/error states, responsive menus, and owner-only diagnostics.
- [x] (2026-09-07 09:20Z) Corrected request-scoped caching, customer-safe quota counting, seller-balanced market evidence, complete request filters, and reserve-candidate ranking.
- [x] (2026-09-07 09:30Z) Added regression coverage and completed all 67 focused, syntax, compilation, parser, and whitespace checks.

## Surprises & Discoveries

- Observation: `app/search_v2/adapters/avito.py` is only a generic discovery bridge and does not call Zen Studio or inspect descriptions and photos.
  Evidence: its `AvitoAdapter` delegates to `generic_web_legacy_search` and exposes no detail or vision contract.
- Observation: the sandboxed runner cannot launch the project's `.venv` executable through the Cyrillic user path, although the user's active VS Code process successfully uses the installed Python 3.13 interpreter.
  Evidence: the local server is running from that Python installation, while the restricted test shell reports a process-launch error; the mandatory parser test passes when executed with the same installed interpreter outside the sandbox.
- Observation: a bundled Python 3.12 runtime is available but has no project packages such as `requests` or `pydantic`.
  Evidence: import probing raises `ModuleNotFoundError: No module named 'requests'`.
- Observation: the sample Zen dataset includes materially conflicting evidence. One listing marked as excellent describes a failed NAND chip; another structured as not activated says it was activated by customs.
  Evidence: listing IDs `8181079987` and `8361240586` in the user-provided dataset.
- Observation: a naive activation keyword rule incorrectly interpreted the negated phrase `не активирован` as positive activation evidence.
  Evidence: the regression test initially exposed the false conflict; `_positive_activation` now handles Russian negation and the test passes.
- Observation: the bundled dependency-light runtime cannot start the Alice parser test because it lacks `pydantic_settings`, but the installed project Python can run it successfully.
  Evidence: the bundled run stops during imports; the unchanged parser suite completes successfully with the installed Python 3.13 interpreter.
- Observation: two server processes briefly listened on port 8091, so the browser could send work to a background process instead of the visible VS Code terminal.
  Evidence: after stopping only the extra process, a single listener remains and `/health` reports both integrations ready.
- Observation: the live server still listening on port 8091 reported `apify=false` and `ai=false`; therefore the visible "analysis incomplete" cards were created by `UnavailableReviewer` and no neural-network request had occurred.
  Evidence: `GET http://127.0.0.1:8091/health` returned both readiness flags as false while the card text exactly matched the previous `incomplete_review` fallback.
- Observation: the authenticated Apify Run object exposes `usageTotalUsd`, while AI Tunnel places the charged ruble amount in `usage.cost_rub`.
  Evidence: the provider contracts allow recording real per-run cost; when either value is absent, the service labels the Apify amount as an estimate rather than presenting it as exact.
- Observation: AI Tunnel may return a generation error or no generated content after an HTTP 200 response, so HTTP status alone is not a reliable success signal.
  Evidence: the live 40-listing run produced 38 empty text results, and AI Tunnel documents HTTP-200 generation errors and temporary empty content during provider warm-up or scaling.
- Observation: a successful Zen run can include a row without the canonical `id` field, which previously converted an otherwise successful collection into HTTP 400.
  Evidence: the live search returned one dataset row and then failed with «В записи Zen отсутствует идентификатор объявления».
- Observation: the row was not an advertisement at all; the last Dataset contained only `_upgradeRequired`, `_message`, and `_upgradeUrl`.
  Evidence: a read-only inspection of the latest successful Actor run returned exactly that three-field shape, so adding Apify balance alone does not turn it into listing data until the required plan upgrade is completed.
- Observation: a fixed photo shortlist either wasted vision calls after enough cards were ready or returned too few cards when early photo reviews were rejected.
  Evidence: the new reserve-candidate regression rejects the first two photo reviews and still reaches three complete customer cards after five photo attempts.
- Observation: an OpenAI-compatible route can accept `response_format` at the HTTP boundary yet fail the generation itself.
  Evidence: text and photo calls now make one bounded compatibility retry without `response_format`, with a regression test proving the second payload and accumulated cost.
- Observation: truthful sub-45-second delivery cannot rely on browser animation or a single synchronous request.
  Evidence: the asynchronous job tests prove that progress comes from server stages, the heartbeat advances independently of UI rendering, and a job becomes timed out after the fixed customer deadline even if an external worker is still returning.
- Observation: a complete AI review cannot be cached only by listing evidence because text matching is evaluated against the current customer request.
  Evidence: the same cached listing review could be reused for a different product query and retain the earlier `matches_request` decision.
- Observation: stopping the photo stage after any complete review lets `CAUTION` cards consume the requested 3/5 quota.
  Evidence: three complete caution verdicts stop reserve processing even though none can appear in the customer-safe result list.
- Observation: counting every listing price gives a seller with duplicate advertisements disproportionate influence over the market median.
  Evidence: the previous market calculation required two sellers but did not balance the median by seller before calculating savings.
- Observation: returning an empty successful report after two malformed model responses hides a provider outage as if no good listings existed.
  Evidence: malformed text or photo JSON now raises the typed `AI_INVALID_RESPONSE` error after the bounded compatibility retry.
- Observation: an arbitrary valid Avito URL is not proof of a direct, orderable listing, while listings without a named seller cannot prove independent market evidence.
  Evidence: customer cards now require a numeric item identifier recoverable from the direct URL, and unknown sellers are excluded from independent-seller counts.

## Decision Log

- Decision: Create `avito_service/` and `avito_web/` as additive, isolated components and leave `main.py`, `.env`, `app/db.py`, and all database files unchanged.
  Rationale: Avito should remain replaceable and independently testable before Telegram integration.
  Date/Author: 2026-09-02 / Codex
- Decision: Use only the Python standard library in the new service.
  Rationale: it keeps the service executable in the available runtime and avoids modifying the broken `.venv`; HTTP, JSON, dataclasses, and tests are sufficient for this MVP.
  Date/Author: 2026-09-02 / Codex
- Decision: Treat full-description and all-photo analysis as a hard eligibility gate for `TOP` and `BUDGET` roles.
  Rationale: the user made this the main product rule, and the sample proves structured marketplace fields can hide critical defects or contradictions.
  Date/Author: 2026-09-02 / Codex
- Decision: Keep deterministic text-risk rules before the AI call and require strict structured AI output after it.
  Rationale: obvious defects must fail closed even if the model is unavailable or misses them; AI is used for semantic text and visual evidence rather than as the only safety layer.
  Date/Author: 2026-09-02 / Codex
- Decision: Never serialize incomplete or rejected listings to the client and never serialize `manual_checks`.
  Rationale: incomplete evidence is an internal processing failure, not a task to delegate to the customer; rejected listings are also forbidden by the product rules.
  Date/Author: 2026-09-02 / Codex
- Decision: Order visible recommendations as three companies/shops followed by one private seller while preserving the ranking within each seller group.
  Rationale: this implements the requested marketplace mix without misclassifying known company records from Zen.
  Date/Author: 2026-09-02 / Codex
- Decision: Reserve 15 ₽ of the default 50 ₽ report budget for AI, limit multimodal review to 12 deterministic candidates, and cap Zen detailed records with the remaining budget.
  Rationale: comparable raw records are inexpensive enough to establish a market, but sending every listing and every image to AI is unnecessary and can make a report unprofitable.
  Date/Author: 2026-09-02 / Codex
- Decision: Keep cost data out of the default client serialization and expose it only through the local administrator HTTP response.
  Rationale: internal unit economics helps the operator set prices but should not leak into customer-facing bot cards.
  Date/Author: 2026-09-02 / Codex
- Decision: Analyze full text and structured fields for up to 40 viable records before sending any images, then analyze every image only for the best 10 text-qualified records.
  Rationale: text is cheaper and sufficient to reject defects, hidden price conditions, and mismatches early; vision cost is reserved for finalists that can realistically reach the report.
  Date/Author: 2026-09-03 / Codex
- Decision: Send text evidence in batches of six using broadly compatible JSON-object output, retry one empty provider response, and stop the remaining calls when the retried batch still has no usable results.
  Rationale: this reduces normal text-stage request count by roughly six times and prevents dozens of repeated waits or charges during an AI provider outage.
  Date/Author: 2026-09-03 / Codex
- Decision: Let the customer request 3, 5, 7, or 10 results, but never fill the quota with incomplete or rejected listings.
  Rationale: a minimum of three is a product target, while evidence completeness and truthful availability remain hard safety gates.
  Date/Author: 2026-09-04 / Codex
- Decision: Keep costs, Dataset import, and processing diagnostics in a collapsed owner section and expose only verified recommendations in the main response list.
  Rationale: the normal user should see a simple shopping flow, while the operator still needs unit-economics and failure diagnostics.
  Date/Author: 2026-09-04 / Codex
- Decision: Treat a verified 2,000 ₽ absolute saving as useful even when it is below five percent, while retaining the same-comparable and independent-seller evidence gates.
  Rationale: this captures materially cheaper offers without weakening the rule that prevents false market comparisons.
  Date/Author: 2026-09-04 / Codex
- Decision: Offer 3 or 5 results in the client interface and inspect up to three reserve photo candidates, stopping when the chosen quota is complete.
  Rationale: this matches the requested product choice and avoids both empty output after early failures and unnecessary vision cost.
  Date/Author: 2026-09-04 / Codex
- Decision: Retry an empty, malformed, or generation-error AI response once without `response_format` while keeping the JSON-only prompt and strict parser.
  Rationale: it improves AI Tunnel model compatibility without accepting unstructured evidence or repeating transport timeouts.
  Date/Author: 2026-09-04 / Codex
- Decision: Keep the existing dependency-free Python and static-site architecture for the product-readiness milestone, and add capabilities without replacing the local service or touching the Telegram bot and SQLite database.
  Rationale: the current application already serves a coherent local product surface; preserving it avoids destabilizing unrelated bot, payment, and database work.
  Date/Author: 2026-09-04 / Codex
- Decision: Model category-specific refinements as a bounded map of customer requirements, while retaining the legacy phone fields for compatibility.
  Rationale: a washing machine, chair, or unusual object must never be shown phone-only fields, and AI still needs structured requirements for semantic matching.
  Date/Author: 2026-09-04 / Codex
- Decision: Use an asynchronous job endpoint and poll it from the page once per second.
  Rationale: this provides truthful collection, text-review, price-comparison, photo-review, and ranking progress while proving the server is still alive during a potentially long external request.
  Date/Author: 2026-09-04 / Codex
- Decision: Store local support requests in an append-only JSON Lines inbox outside the bot database and ignore that runtime directory in Git.
  Rationale: the user requested a functional support form, while project rules prohibit changing the existing database and the local site has no hosted persistence binding.
  Date/Author: 2026-09-04 / Codex
- Decision: Seed the examples carousel with exact, high-volume models and promote a customer query only after it has produced at least three verified results on that device.
  Rationale: examples should remain useful and change over time without falsely promising that a static listing is always available in every city.
  Date/Author: 2026-09-04 / Codex
- Decision: Self-host the Onest variable font and serve it from the existing allowlisted static-file map.
  Rationale: the interface needs consistent, high-quality Cyrillic typography without depending on an external font CDN or weakening the content-security policy.
  Date/Author: 2026-09-04 / Codex
- Decision: Keep the search controls in the first viewport, then add anchored proof, use-case, FAQ, and support sections behind a compact sticky navigation bar.
  Rationale: the site should feel complete and easy to explore without placing marketing content in front of the customer's primary task.
  Date/Author: 2026-09-05 / Codex
- Decision: Count only fully complete, request-matching, non-caution reviews toward the 3/5 quota and keep examining reserve finalists until that quota or a hard resource boundary is reached.
  Rationale: a completed AI response is not automatically a safe customer recommendation.
  Date/Author: 2026-09-05 / Codex
- Decision: Compute comparable-market medians from one representative price per independent seller while retaining the total listing and seller counts as evidence.
  Rationale: duplicate advertisements from one shop must not move the market baseline or create a false below-market claim.
  Date/Author: 2026-09-05 / Codex
- Decision: Require confirmed structured availability, a numeric direct-listing URL, and three named independent sellers before a customer card can claim to be below market.
  Rationale: a category page, unknown seller, or missing stock field cannot prove that the advertised item is orderable or that its comparison market is independent.
  Date/Author: 2026-09-07 / Codex

## Outcomes & Retrospective

The isolated MVP now includes loss protection. A local browser interface can either start a bounded Zen Studio search or analyze an exported Zen dataset. The service preserves the full description and every safe Avito photo, applies deterministic fail-closed rules before multimodal review, and permits `TOP` only after exact-configuration market comparison and complete evidence coverage. The cost controller records Apify and AI charges, stops avoidable AI calls, reuses complete reviews, and calculates a minimum report price. It does not touch the Telegram entry point, SQLite schema, payment flow, credentials, or the contents of `.env`.

The product-readiness milestone adds a responsive black/violet/red interface, complete light/dark themes, category-adaptive inputs, six complete examples, quality/balanced/budget ranking, a real asynchronous progress contract, a hard 45-second customer deadline, owner-only funnel auditing, and a functional local support inbox. Text screening still precedes photo review, and only finalists receive the expensive vision pass.

The follow-up visual pass removes repeated explanatory copy from the primary path, keeps optional filters collapsed, gives the 3/5 choice a native-feeling animated segmented control, uses a lighter Windows variable-font hierarchy, and rotates three concise examples at a time. Successful three-result searches are stored locally and promoted into the example carousel.

The final product pass adds sticky desktop/mobile navigation, proof and FAQ sections, a support dialog, reconnectable 45-second progress, safe outbound links, request-scoped AI caching, seller-balanced market medians, conservative unnamed-seller handling, and explicit provider failures instead of false empty success. Customer quota is now filled only by safe request-matching results; photo reserves continue until 3/5 eligible cards are ready or a hard time/cost boundary is reached.

Validation completed with 67 passing focused tests, clean full Python compilation, JavaScript syntax validation, a working local HTTP health/UI flow, and a successful mandatory `tools/test_alice_parser.py` run. No paid Apify/AI call was made during this milestone, and no credential value was printed, committed, or modified. Live commercial latency and result quality still require a paid-plan pilot before they can be called production-proven.

## Context and Orientation

The existing bot starts in `main.py`, stores requests and results through `app/db.py`, and has a general search engine under `app/search_v2/`. Its current Avito adapter in `app/search_v2/adapters/avito.py` discovers public links but does not return the detailed Zen data already validated manually by the user. Existing AI-card generation in `app/ai_cards_service.py` is text-only and is coupled to bot settings, so it is not reused directly.

The new `avito_service` package is a standalone application layer. A provider is an object that returns raw Avito listing dictionaries; the production provider calls the Zen Studio Actor, while tests provide in-memory dictionaries. A normalizer converts the large Zen response into listing-specific evidence. A reviewer sends the complete description and every photo URL to an OpenAI-compatible multimodal chat endpoint and validates the returned JSON. A ranker compares only equivalent configurations and assigns the project roles `TOP`, `BACKUP`, `BUDGET`, `CAUTION`, or `REJECTED`. The separate `avito_web` directory contains the browser interface.

## Plan of Work

Create typed dataclasses in `avito_service/models.py` for search input, normalized listings, AI evidence, and final recommendations. Create `avito_service/config.py` to read process environment variables lazily, never load or write `.env`, redact secrets from errors, and allow offline fixture mode.

Create `avito_service/apify.py` using `urllib.request`. It will start `zen-studio/avito-listings-scraper` with `includeDetails=true`, `includePhone=false`, `includeReviews=false`, and a bounded result count. It will use bearer authorization rather than placing the token in logged URLs, wait for the run, and retrieve its default dataset. The provider will reject attempts to disable detail enrichment because full descriptions and photos are mandatory.

Create `avito_service/normalization.py` to preserve full plain-text descriptions, all image URLs, listing parameters, price, seller summary, availability, badges, and collection time while discarding repeated catalog specifications and HTML duplication. Create `avito_service/risk_rules.py` to detect explicit hardware defects, repair or activation caveats, hidden price conditions, payment commissions, condition conflicts, and missing evidence.

Create `avito_service/ai.py` with an interface and an OpenAI-compatible implementation. The request will clearly mark seller text and images as untrusted evidence, include the full description without truncation, include every image as `image_url` content, and request one strict JSON object. The response parser will reject unknown verdicts, missing photo coverage, missing text coverage, or image counts that do not match the sent count. Tests use a fake reviewer and do not make network calls.

Create `avito_service/ranking.py` and `avito_service/service.py`. The comparable key includes canonical model, storage, SIM variant, condition, and activation state. Deterministic critical risks force `REJECTED`; incomplete text or photo analysis forces `CAUTION`. A claim of below-market value requires at least three comparable listings from three known sellers and either 2,000 ₽ or 5% saving against their median. Before AI, select distinct low-price sellers in the requested three-company-to-one-private mix, enforce a candidate and ruble budget, and reuse complete in-memory reviews for 12 hours. Text review receives the customer request and rejects mismatched products before photos; vision then works through ranked candidates plus a three-item reserve until the selected 3/5 quota is complete. Roles are assigned without inventing a `TOP` if no candidate passes the hard gates.

Create `avito_service/http_api.py`, `avito_service/__main__.py`, and static files in `avito_web/`. The server exposes `GET /health`, `POST /api/avito/search`, and `POST /api/avito/analyze-dataset`. The browser form shows progress, final roles, text risks, photo findings, conflicts, price evidence, seller, and direct links. Browser responses never include tokens, raw AI prompts, seller user keys, phone numbers, or internal exceptions.

For the product-readiness milestone, preserve the synchronous endpoints for compatibility and add `POST /api/avito/jobs` plus `GET /api/avito/jobs/<id>`. A job reports a bounded public stage, message, percent, elapsed seconds, and remaining seconds. Its worker receives a monotonic 45-second deadline; the Apify and AI HTTP clients cap their own waits by the remaining time, and the service stops starting new AI work when the remaining time is reserved for ranking and response serialization. Add an owner-only funnel summary with counts for deterministic eligibility, completed text reviews, request matches, completed photo reviews, and final visible cards.

Redesign `avito_web/index.html`, `avito_web/styles.css`, and `avito_web/app.js` as a responsive working surface. The first viewport keeps search primary, provides a light/dark toggle, purpose and priority selectors, adaptive fields for phones, computers, household appliances, furniture, gaming, tools, auto parts, fashion, and unusual goods, and six complete examples that populate all relevant controls. During search, render the real server stages, an alive indicator, elapsed time, and the 45-second target. Add a keyboard-accessible support dialog that submits to `POST /api/support`, returning a non-secret ticket identifier.

Add `tools/test_avito_service.py` with unit and local HTTP tests. Update `README.md` with safe setup and run commands, including a warning not to paste tokens into chat or commit them.

## Concrete Steps

Work from `C:\Users\Пользователь\Documents\naydi_vygodnee`. Run focused tests with the bundled runtime:

    & 'C:\Users\Пользователь\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest tools.test_avito_service -v

Start the standalone app with environment variables supplied by the operator, not stored in source:

    $env:APIFY_TOKEN = '<set locally>'
    $env:AVITO_AI_API_KEY = '<set locally>'
    $env:AVITO_AI_BASE_URL = 'https://provider.example/v1'
    $env:AVITO_AI_MODEL = '<multimodal model>'
    & 'C:\Users\Пользователь\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m avito_service --port 8091

Opening `http://127.0.0.1:8091/` should show the separate Avito form. `http://127.0.0.1:8091/health` should return JSON with `ok: true` and boolean readiness for Apify and AI without exposing their values.

## Validation and Acceptance

The focused test suite must prove that the complete description and all image URLs reach the fake reviewer, a NAND defect is rejected, an activation contradiction is surfaced, mixed Pro and Pro Max or mixed memory sizes are not treated as comparable, and no `TOP` is emitted when photo review is incomplete. An HTTP test must prove that dataset analysis returns safe JSON and no credential values.

Run the mandatory repository checks with an available Python runtime:

    python -m compileall .
    python tools/test_alice_parser.py

If the project `.venv` remains unusable, run `compileall` and the isolated tests with the bundled Python and record the exact failure of `tools/test_alice_parser.py` rather than modifying `.venv` or installing packages without permission.

## Idempotence and Recovery

All additions are source files and tests; rerunning tests or the local server does not modify the bot database. The real search endpoint can spend Apify credit, so it requires an explicit request and enforces a small maximum result count. Fixture analysis has no external effects. Stopping the server and deleting the new directories cleanly removes the feature without affecting the existing bot.

## Artifacts and Notes

The normalized record intentionally excludes `descriptionHtml`, repeated `productSpecs`, detailed model review snippets, coordinates, phone data, and seller user keys. These fields either duplicate trusted listing evidence, increase model cost, or introduce unnecessary personal data.

## Interfaces and Dependencies

`avito_service.apify.ZenStudioProvider.collect(request: SearchRequest) -> CollectionBatch` returns raw Zen records plus actual or estimated Apify cost and cap metadata. `avito_service.normalization.normalize_listing(raw: dict) -> NormalizedListing` creates the stable evidence contract. `avito_service.ai.MultimodalReviewer.review(listing: NormalizedListing) -> AIReview` guarantees full text and all-photo coverage and records `usage.cost_rub`. `avito_service.service.AvitoAnalysisService.search(request: SearchRequest) -> AnalysisReport` orchestrates a real run, while `analyze_dataset(raw_items: list[dict])` supports fixture imports. `AnalysisReport.public_dict()` stays customer-safe; the local HTTP API calls `public_dict(include_admin=True)` to add `adminCosts`. `avito_service.http_api.make_server(host, port, service)` returns a standard-library `ThreadingHTTPServer`.

Revision note (2026-09-02): Initial executable plan created after repository and environment inspection. The design is isolated because the user requested Avito as a separate component and project rules forbid touching the existing entry point and database without an explicit request.

Revision note (2026-09-02): Updated after implementation and validation with completed milestones, real sample-data discoveries, the activation-negation regression, final outcomes, and the exact environment limitation affecting the legacy Alice parser test.

Revision note (2026-09-02): Added the post-pilot correction for AI readiness, retryable complete coverage, public-output safety, and the requested company/private display mix.

Revision note (2026-09-02): Added cost-control implementation details so every Zen and AI run stays bounded and the administrator can price reports above their estimated cost.

Revision note (2026-09-02): Recorded safe local configuration loading, real Zen smoke validation, AI latency optimization, Windows progress logging, the duplicate-listener diagnosis, and the final 23-test result.

Revision note (2026-09-02): Added the ten-card administrator fallback and robust AI response normalization after a live 40-listing run produced 12 incomplete reviews and no visible cards.

Revision note (2026-09-03): Replaced the single expensive multimodal pass with text-first screening and photo review only for the ranked final shortlist.

Revision note (2026-09-03): Batched the text stage, hardened AI Tunnel response/error handling, added a provider circuit breaker, and excluded obvious PS5 games/accounts/services from console searches.

Revision note (2026-09-04): Added the client-first search modes, free-form query UX, customer-selected 3–10 result limit, safe public/admin response split, and compact result cards.

Revision note (2026-09-04): Hardened normalization against Zen ID aliases and diagnostic rows after the live PS5 search exposed an omitted canonical ID.

Revision note (2026-09-04): Detected Zen Studio's `_upgradeRequired` sentinel and now reports the plan limitation directly instead of silently returning zero collected listings.

Revision note (2026-09-04): Added request-aware text validation, the 2,000 ₽ absolute saving threshold, direct-link/availability gates, and quota-driven photo reserves for the 3/5 client choice.

Revision note (2026-09-04): Added a bounded AI Tunnel compatibility retry without `response_format` after generation/JSON failures and recorded the final 48-test validation.

Revision note (2026-09-04): Completed the product-readiness milestone with adaptive client UX, light/dark visual themes, server-backed live progress, a 45-second deadline, priority-aware ranking, support intake, funnel auditing, and 52 focused passing tests.

Revision note (2026-09-04): Refined the interaction design with compact copy, modern typography, a segmented result-count control, rotating exact-model examples, and locally learned successful examples.

Revision note (2026-09-04): Added the OFL-licensed Onest variable font as a local asset and applied one consistent type system across headings, controls, prices, and body copy.

Revision note (2026-09-07): Completed the final interface, selection, error-handling, and regression pass; customer-safe quota filling, seller-balanced market evidence, confirmed stock/direct-link gates, explicit AI failures, responsive navigation, support UX, and 67 focused tests are now recorded as complete.
