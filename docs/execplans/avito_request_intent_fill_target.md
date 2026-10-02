# Make Avito collection fill the requested product family

This ExecPlan is a living document maintained according to `.agent/PLANS.md`. It is self-contained and must be updated as implementation evidence appears.

## Purpose / Big Picture

An Avito request for 200 PlayStation 5 listings must mean up to 200 unique listings from the requested PS5 family, not 200 raw search rows containing Pro consoles, PS4, accessories, games, and duplicates. The cheap deterministic layer will parse the request into a stable signature, classify each listing into a normalized SKU, reject incompatible families before AI, and let a bounded collector continue through additional batches until the valid target is filled or an explicit safety stop is reached. Saved data will prove the behavior without network or paid AI.

## Progress

- [x] (2026-09-24) Read the owner request, current matching, provider, service, saved replay tools, and `.agent/PLANS.md`.
- [x] (2026-09-24) Implement request signatures, normalized listing SKU, conflicts, compatibility reasons, and query variants.
- [x] (2026-09-24) Implement a provider-independent bounded fill-to-target collector and integrate its metrics with the existing pipeline without running it live.
- [x] (2026-09-24) Add regression tests for PS5 Standard/Pro/Slim/Disc/Digital/storage spellings and the 250-row/200-valid and exhausted 174-valid scenarios.
- [x] (2026-09-24) Recalculate the saved 200-row dataset offline, including old finalists and exact rejection reasons.
- [x] (2026-09-24) Run focused and Avito-wide local tests, compileall, Alice parser check, and diff validation. The repository-wide discovery remains environment-blocked by missing dependencies and has two unrelated timing failures.
- [x] (2026-09-25) Audit query-specific pagination, per-variant exhaustion, description specificity, condition aliases, storage search aliases, location preservation, and distinct collector metrics.
- [x] (2026-09-25) Replay the saved dataset again without provider or AI calls and record the corrected funnel and historical-only yield estimate.

## Surprises & Discoveries

- Observation: Existing `_model_matches` deliberately treats a generic PS5 request as permitting Pro, which now conflicts with the owner’s explicit Standard-versus-Pro family rule.
  Evidence: `avito_service/matching.py` comments that “PS5 includes Slim/Pro”.
- Observation: The Actor adapter currently performs one bounded run whose `maxResults` is raw rows; there is no valid-family target metric or reusable pagination loop.
  Evidence: `ZenStudioProvider.collect_market()` delegates directly to `_collect_payload()`.
- Observation: The saved 200-row sample contains 60 Yaroslavl rows but only 41 Standard-PS5 family matches after deterministic SKU normalization.
  Evidence: `runtime/avito_final/20260924-request-intent/offline-request-intent.json` reports a shortfall of 159 and nine `REQUEST_SKU_MISMATCH` rows.
- Observation: Unit-only structured fields such as `Встроенная память, ГБ = 1000` need the unit recovered from the parameter name.
  Evidence: storage regression tests failed until `_storage_parameter` combined the structured name and value.
- Observation: A global page counter skipped the first page of every query variant after the first.
  Evidence: the old sequence was variant A/page 1, B/page 2, C/page 3; the regression now proves A1, B1, C1 and independent exhaustion.
- Observation: Description-aware specificity removed four former saved-data false positives from the valid target pool.
  Evidence: valid target count changed from 41 to 37; three multi-SKU ads now have `SKU_CONFLICT`, while `8384339954` has `CONDITION_CONFLICT` because “как новая” is excellent/used rather than new.

## Decision Log

- Decision: Add a dedicated request-intent module rather than encoding more family rules into generic token matching.
  Rationale: A request signature and a listing SKU are different concepts, while comparable SKU remains the existing strict `market_engine.normalized_sku`.
  Date/Author: 2026-09-24 / Codex.
- Decision: Build and test fill-to-target as a provider-independent orchestrator with an injected page fetcher, then expose it through the existing provider boundary.
  Rationale: This proves stop conditions offline and avoids paid calls while preserving the current service architecture.
  Date/Author: 2026-09-24 / Codex.
- Decision: Both configured minimum bargain thresholds remain conjunctive and independent from market confidence.
  Rationale: The owner explicitly requires both 10% and 5,000 RUB when those values are configured.
  Date/Author: 2026-09-24 / Codex.
- Decision: Reuse a bounded short fill snapshot from cache instead of automatically launching another paid collection because it contains fewer rows than the requested target.
  Rationale: A bounded fill may legitimately stop on exhaustion, duplicate saturation, raw, page, or budget limits; an immediate retry would violate the cost-safety goal.
  Date/Author: 2026-09-24 / Codex.
- Decision: Track page and exhaustion state independently per query variant and rotate active variants round-robin.
  Rationale: Source pagination is query-scoped; exhaustion or duplicate saturation for one spelling must not suppress the others.
  Date/Author: 2026-09-25 / Codex.
- Decision: Keep the saved raw-yield number only as a historical offline estimate.
  Rationale: The source dataset predates the city URL fix, so its 60/200 city yield is not a valid live-cost forecast.
  Date/Author: 2026-09-25 / Codex.

## Outcomes & Retrospective

Implemented and verified entirely offline. Standard PS5 no longer accepts Pro, the early gate runs before paid review, every query variant starts from its own page 1, description specificity participates in SKU resolution, and all requested funnel/shortfall metrics are exposed. The corrected replay reports `200 raw → 200 unique → 60 city → 37 family → 37 valid → 29 basic → 24 text → 2 data-high → 0 bargain → 0 confirmed`; ID `8139333912` remains `REQUEST_SKU_MISMATCH`. All 485 Avito tests pass, followed by 19/19 focused tests after the final raw/unique metric refinement. No Apify, refresh, text AI, or photo AI call was made.

## Context and Orientation

`avito_service/models.py` defines `SearchRequest`, normalized listings, collection batches, pipeline metrics, and reports. `avito_service/matching.py` performs deterministic matching before AI. `avito_service/apify.py` translates a request into Zen Studio Actor input. `avito_service/service.py` coordinates collection, AI review, ranking, and final refresh. `avito_service/market_engine.py` already defines the exact SKU used for comparables; it must remain narrower than the new request-family match. `tools/analyze_avito_saved_market.py` replays saved facts and is the safe place to produce the requested offline funnel.

A request signature describes what the buyer permits: Standard PS5, Pro-only, Slim of either edition, or an exact Slim edition and optional storage/condition. A listing SKU describes one ad. A comparable SKU describes the exact candidate configuration and remains the existing model/variant/storage/condition key.

## Plan of Work

Create `avito_service/request_intent.py` with immutable structures for `RequestSignature`, `ListingSku`, and `RequestCompatibility`. Normalize PS5 spellings including compact `PlayStation5`, Russian drive phrases, GB/TB, condition, and product type. Structured parameters take precedence, while title can refine a generic parameter. Explicit incompatible structured/title facts produce `SKU_CONFLICT`; explicit condition disagreement produces `CONDITION_CONFLICT`. A generic Standard PS5 signature excludes Pro while allowing Base/Fat/Slim and Disc/Digital.

Call the compatibility gate near the start of `matches_listing_request` and from candidate selection before AI. Return `REQUEST_SKU_MISMATCH` from offline diagnostics instead of the generic request mismatch. Generate query variants from the signature so Standard searches never expand to Pro and Pro searches never expand to Slim/Base.

Create `avito_service/fill_target.py` with safety limits, result metrics, and an injected page callback. Each batch is normalized, deduplicated by listing ID, city-filtered, and family-filtered before it contributes to the target. Stop on target filled, source exhaustion, raw/page/budget limits, or duplicate saturation. Preserve explicit counts and shortfall. Wire the provider’s bounded page/variant fetch path additively; existing single-run behavior remains available for callers that do not request fill-to-target.

Extend the saved replay output with raw, deduplicated, city, family, cheap filter, text, data-confidence, bargain, and final counts. Include normalized SKU and rejection reason for former finalists, especially Pro ID `8139333912` under the saved Standard PS5 request.

## Concrete Steps

Work from `C:\Users\Пользователь\Documents\naydi_vygodnee` with bundled Python:

    C:\Users\Пользователь\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -X utf8 -m unittest tools.test_avito_request_intent tools.test_avito_fill_target -v
    C:\Users\Пользователь\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -X utf8 -m unittest discover -s tools -p "test_avito_*.py"
    C:\Users\Пользователь\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -X utf8 -m compileall -q app avito_service tools
    C:\Users\Пользователь\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -B -X utf8 tools\test_alice_parser.py
    git diff --check

The saved replay command must read existing `runtime/avito_pilot/20260923T104021Z-expanded-gpt` facts and write a new offline JSON artifact. It must not invoke `build_service`, Apify, the AI reviewer, or browser automation.

## Validation and Acceptance

For a Standard `PlayStation 5` request, Pro, PS4, and accessories are deterministic `REQUEST_SKU_MISMATCH`; Slim Disc, Slim Digital, Fat, and Base pass. Pro-only and Slim-only variants follow the owner matrix. Explicit storage is mandatory; omitted storage remains open. The fill test returns 200 valid unique rows after consuming 250 raw rows across three pages. The exhaustion test returns 174, `target_filled=false`, `shortfall=26`, and `SEARCH_EXHAUSTED`. No incompatible row reaches mocked AI or comparable input.

The saved replay shows ID `8139333912` as a Pro listing rejected from a Standard PS5 request. Exact comparable grouping continues to use `market_engine.normalized_sku`, so the broad query family never broadens market evidence.

## Idempotence and Recovery

All replay and tests are offline and repeatable. Do not edit `.env`, `.venv`, databases, tokens, `main.py`, payment, or credit code. Do not reset the dirty worktree. A failure must leave saved paid facts untouched. No live flags or provider POSTs are allowed.

## Artifacts and Notes

The source replay directory is `runtime/avito_pilot/20260923T104021Z-expanded-gpt`. The resulting report will be placed under a new `runtime/avito_final/20260924-request-intent` directory and linked in the final response.

## Interfaces and Dependencies

Use only the Python standard library and existing project models. `parse_request_signature(request)` returns a `RequestSignature`. `normalize_listing_sku(listing)` returns a `ListingSku`. `check_request_compatibility(listing, request_or_signature)` returns a `RequestCompatibility` with `matches`, `reason`, and conflicts. `search_query_variants(signature)` returns deterministic deduplicated strings. `fill_to_target(request, fetch_page, limits)` returns listings plus metrics including `raw_collected`, `deduplicated`, `correct_city`, `request_family_matched`, `requested_target`, `target_filled`, `shortfall`, and `stop_reason`.

Revision note 2026-09-24: created for the owner’s request to separate raw collection size from valid requested-family results and to prove it entirely offline before any paid action.

Revision note 2026-09-24: completed implementation, offline replay, regression validation, and cost-safety audit; documented environment-only full-suite limitations.

Revision note 2026-09-25: completed the final collector correctness audit, per-query pagination fix, description/condition/storage recall fixes, and second offline replay.
