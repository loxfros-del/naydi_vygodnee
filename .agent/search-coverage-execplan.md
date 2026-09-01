# Broaden safe technical-product discovery

This ExecPlan is a living document. It follows `.agent/PLANS.md`; its progress, discoveries, decisions, and outcome notes are updated as the work advances.

## Purpose / Big Picture

NOVA currently refuses a product when the category parser does not already know it. That excludes real technical products such as game consoles, cameras, refrigerators, watches, and power tools before the search sources can try to find them. After this increment, a clearly named technical product can enter a conservative generic-technology route. It keeps the supplied model as a hard query token, rejects services and accessories, and only recommends an offer when the usual V2 exact-match and price gates pass. A user can prove the change with a deterministic `Canon EOS R50` or `PlayStation 5 Slim` case; a cable or repair request remains unsupported.

The second improvement treats two sparse prices as insufficient market coverage. The source pipeline will run its already bounded broad-web fallback when fewer than three comparable priced offers are available across fewer than two sources. It does not claim a saving without existing independent reference evidence, and it does not introduce retries, scraping, cookies, or bypasses of source protection.

## Progress

- [x] (2026-08-13 16:35Z) Audited category detection, V2 request normalization, source policy, query planning, direct-retail limits, Yandex Web, Wildberries, Avito, and page verification.
- [x] (2026-08-13 16:13Z) Added the conservative `generic_tech` route and deterministic acceptance/rejection tests.
- [x] (2026-08-13 16:16Z) Required both source diversity and a minimum comparable-offer count before skipping broad fallback; added a regression test.
- [x] (2026-08-13 16:18Z) Ran focused V2 tests, parser safety checks, compilation, and diff validation.

## Surprises & Discoveries

- Observation: unknown categories return `UNSUPPORTED_CATEGORY` before any source adapter runs.
  Evidence: `app/search_v2/service.py` returns immediately when `SearchRequestV2.supported_category` is false; `app/search_v2/request_normalizer.py` only supports keys already in `CATEGORY_SPECS`.
- Observation: browser search has a bounded page verifier for Yandex Web, Wildberries, and generic web links, but the default V2 bridge does not attach that verifier.
  Evidence: `app/web_api.py` builds `ExternalProductPageVerifier`; `app/services/search_engine_bridge.py` constructs default `SearchServiceV2()` without one.
- Observation: the source fallback currently checks only distinct sources, not the number of comparable offers.
  Evidence: `SearchServiceV2.search()` compares `_priced_exact_sources(preview)` with `minimum_exact_sources_before_fallback`.

## Decision Log

- Decision: introduce one conservative generic technical category instead of pretending every unknown text is a purchasable product.
  Rationale: it broadens coverage for named equipment while keeping services and accessories outside automated recommendations.
  Date/Author: 2026-08-13 / Codex.
- Decision: do not change source credentials, the browser frontend, runtime settings, databases, or bot entrypoint in this increment.
  Rationale: this work is a safe domain-level coverage change; external access configuration and Mini App transport belong to the parent implementation.
  Date/Author: 2026-08-13 / Codex.
- Decision: use three comparable priced offers across two sources as the minimal condition for skipping broad fallback.
  Rationale: two prices cannot establish a useful market view or satisfy the existing savings-evidence rule.
  Date/Author: 2026-08-13 / Codex.

## Outcomes & Retrospective

Completed. Clearly named equipment outside the original category set can now enter `generic_tech` only with a recognised technical brand and a concrete model code: for example `Canon EOS R50`. Vague descriptions, accessories and repair requests remain unsupported before network search. The service now continues its bounded broad fallback unless it sees at least three comparable priced offers from two sources. External source blocking or rate limiting remains a partial result, not a reason to retry aggressively.

## Context and Orientation

`app/category_registry.py` owns pure category text rules. `app/request_parser.py` maps legacy and browser request fields into parser details. `app/search_v2/request_normalizer.py` creates `SearchRequestV2`; `supported_category` controls whether `app/search_v2/service.py` invokes any source. Search V2 adapters only return `RawOffer`; `normalization.py`, `exact_match.py`, and `verification.py` decide whether a value can be grouped and recommended. `query_planner.py` preserves hard product tokens in every outbound query. A broad fallback is the existing `generic_search` adapter stage; it remains bounded by the existing source and query limits.

## Plan of Work

Add a `generic_tech` `CategorySpec` with generic technical product markers, accessory/service rejection markers, and a low but nonzero plausible-price floor. Keep `detect_category()` unchanged for ordinary categories. Add a helper that identifies an unknown request as a technical product only if it contains a recognized equipment marker and does not contain an accessory or service marker.

In `request_normalizer.py`, use that helper only after normal parsing yields `unknown`. Use the explicit model/product text already supplied by the transport, remove only generic leading product-type wording, and preserve the remaining model as a hard token. That makes `Canon EOS R50` and `PlayStation 5 Slim` queryable without declaring an arbitrary word like “repair” or “USB cable” supported.

In `service.py`, count comparable offers as well as their distinct sources before deciding to skip generic fallback. Keep existing timeouts and adapter caps unchanged. Add regression tests proving a sparse two-source result invokes generic fallback, while a three-offer two-source result does not.

## Concrete Steps

Run from `C:\Users\Пользователь\Documents\naydi_vygodnee`:

    python tools/test_search_v2_query_planner.py
    python tools/test_search_v2_exact_match.py
    python tools/test_search_v2_end_to_end.py
    python tools/test_alice_parser.py
    python -m compileall app tools
    git diff --check

## Validation and Acceptance

`normalize_legacy_request` must return a supported `generic_tech` request with hard model tokens for `Фотоаппарат Canon EOS R50`, `Sony PlayStation 5 Slim`, `Холодильник Haier C2F637C`, and `Apple Watch Series 10`. It must still return an unsupported request for `Кабель USB-C` and a repair service. A matching direct product offer for `Canon EOS R50` may pass the exact-match gate; a different model code cannot. With only two comparable offers from two sources, `generic_exact` is called; with three offers across two sources it is not.

## Idempotence and Recovery

All edits are pure Python and deterministic tests. No migrations, environment files, cache databases, requests, or source credentials are changed. If a regression appears, reverting only the added generic category and coverage predicate returns the former fail-closed behavior.

## Artifacts and Notes

The audit found the source limits already bounded at three concurrent adapters, two queries per source, one generic fallback stage, and page verifier concurrency of two. This change keeps those ceilings.

## Interfaces and Dependencies

`app.category_registry.is_generic_tech_request(text: str) -> bool` will be a pure predicate. `app.category_registry.generic_tech_model(text: str) -> str` will remove only generic request and product-type prefixes. `SearchServiceV2` gains `minimum_exact_offers_before_fallback: int = 3`; it is used together with `minimum_exact_sources_before_fallback` to decide whether broad fallback is necessary. No new package or external API is introduced.

Change 2026-08-13: created during the Mini App and universal-technology search expansion to record the safe source-coverage slice separately from transport work.
