# Unify search-result quality gates

This ExecPlan is a living document and is maintained according to `.agent/PLANS.md`.

## Purpose / Big Picture

The Telegram administrator must see every real product card that needs a decision, while only verified in-budget items count as normal. After this work, a candidate moves through verification, category quality classification, one final policy, ranking, deduplication, saving, and counter rendering without two stages rewriting the same status.

## Progress

- [x] (2026-07-11) Inspected the current dirty worktree, final guards, policy, verifier, and admin result storage.
- [x] Simplified category quality so it supplies evidence but does not independently rewrite final statuses.
- [x] Made `app/search_policy.py` the final status and admin-save authority, including real `PRICE_MISSING` cards.
- [x] Persisted verify status in product facts and changed admin counters and cards to use that saved status.
- [x] Added network-free invariants; `compileall`, 20 invariants, and the Alice parser test passed.
- [ ] Ran one final ten-query benchmark; the single process timed out after 604 seconds before flushing buffered output, so no results can be claimed.

## Surprises & Discoveries

- Observation: `product_search.py` currently runs both `_apply_final_product_quality_guard` and `_apply_universal_final_quality_gate`; both can change verification status and admin retention.
  Evidence: the former turns weak quality into `NEED_MANUAL_CHECK`, while the latter invokes `final_quality_gate` and `normalize_for_admin_save`.
- Observation: `PRICE_MISSING` is allowed by `search_policy.should_save_for_admin`, but the collection loop only keeps candidates with a truthy price.
  Evidence: `product_search.py` checks `item.keep_for_admin and item.candidate.price` before appending to `kept`.

## Decision Log

- Decision: keep verification status in `candidate.verify_status`, retain the existing persistent workflow statuses `CANDIDATE`, `WEAK_CANDIDATE`, and `REJECTED_AUTO`, and store the verify status in `facts_json`.
  Rationale: existing Telegram roles and database fields use the workflow status; counters and cards still need the exact verification decision after persistence.
  Date/Author: 2026-07-11 / Codex.
- Decision: `search_policy.normalize_for_admin_save` decides the final status and `should_save_for_admin` decides retention. Category code only adds quality level, reason, and score cap.
  Rationale: this removes status rewrites from parallel guards and makes the listed invariants pure and testable.
  Date/Author: 2026-07-11 / Codex.
- Decision: do not repeat the benchmark after its 10-minute timeout.
  Rationale: the task explicitly permits one final network benchmark only; the timeout is an external-source limitation, not evidence for another run.
  Date/Author: 2026-07-11 / Codex.

## Outcomes & Retrospective

The duplicate status transitions were replaced with category evidence followed by one policy application. Local validation passed: compilation, 20 deterministic invariants, and the Alice parser suite. The only benchmark attempt timed out after 604 seconds without flushing result lines, so search quality cannot honestly be scored from live evidence in this run.

## Context and Orientation

`app/candidate_verifier.py` fetches and verifies product pages. `app/product_quality.py` assesses category-specific evidence such as weak laptop CPUs and PS5 4K. `app/search_policy.py` maps a verified result and quality evidence into final status and admin retention. `app/product_search.py` ranks, deduplicates, and saves candidates. `app/handlers/admin.py` renders saved rows.

A normal result means only `VERIFIED_GOOD` or `VERIFIED_OK`. A manual result means a real product card that needs human review, such as `NEED_MANUAL_CHECK`, valid `VERIFY_BLOCKED`, `PRICE_MISSING`, or soft over-budget. A dropped result is an article, category, unavailable listing, wrong product, hard over-budget item, or broken page.

## Plan of Work

Refactor the two product-search final guards into one category evidence step plus one policy application step. The policy step will build a plain dictionary from the candidate and verifier result, normalize it, copy its final verification status and reasons back, set a workflow status for storage, and decide `keep_for_admin`. The collection loop will retain every policy-approved card rather than filtering on price.

Extend the policy to preserve only genuine blocks as `VERIFY_BLOCKED`, classify non-blocked manual outcomes correctly, and reject article/category pages before retention. Preserve a real product without a confirmed price as `PRICE_MISSING` for the administrator.

Write `tools/test_search_invariants.py` with pure objects and no HTTP calls. It will test final status invariants, prices, exact-model mismatches, brand boundaries, headphone/laptop/TV quality, and accessory exclusion. Then run the required local checks before the one network benchmark.

## Concrete Steps

From `C:\\Users\\Пользователь\\Documents\\naydi_vygodnee`, run:

    python -m compileall -q .
    python -X utf8 tools/test_search_invariants.py
    python tools/test_alice_parser.py

After they pass, run exactly one final benchmark:

    python -X utf8 tools/search_benchmark.py

## Validation and Acceptance

The invariant command must exit with code 0 and report every assertion passed. A real card with `PRICE_MISSING` must be retained, while an article with the same status must be rejected. A normal counter must only include saved rows whose facts contain `VERIFIED_GOOD` or `VERIFIED_OK`. The benchmark must print the ten requested queries, saved status counts, and up to three ranked candidates.

## Idempotence and Recovery

The local tests make no network calls and can be rerun safely. No migrations, database changes, source API changes, or external configuration changes are part of this plan. If the final benchmark reports 401, 403, 429, 498, or malformed external responses, record them as source limitations and do not treat them as code failures.

## Artifacts and Notes

The baseline dirty worktree contains earlier work in `app/search_policy.py`, `app/product_quality.py`, `app/product_search.py`, price extraction, admin rendering, and new source modules. This plan preserves those changes and modifies only the allowed policy, quality, search, verifier, admin, and test files.

## Interfaces and Dependencies

`app/search_policy.py` remains dependency-free and accepts a dictionary candidate. It exposes `normalize_for_admin_save(candidate)`, `should_save_for_admin(candidate)`, and `is_normal_candidate(candidate)`. `app/product_search.py` converts between this dictionary and `ProductCandidate`/`VerifiedCandidate`. `tools/test_search_invariants.py` imports these pure functions and does not initialize the database or call search sources.

Plan created 2026-07-11 because the task changes shared candidate lifecycle behavior and requires an executable, testable record. Updated 2026-07-11 after local validation and the single benchmark timeout.
