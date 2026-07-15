# Stabilize product matching, verification, recommendations, and price editing

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds. This document follows `.agent/PLANS.md` from the repository root.

## Purpose / Big Picture

After this pass, a request such as “iPhone 16 Pro, 256 GB, up to 80000, Yaroslavl” keeps model and storage as structured hard requirements, searches with a clean query, rejects iPhone 16 and 128 GB offers, and never creates a client recommendation for the wrong product. Known retail and marketplace platforms remain valuable sources even when automated access is blocked, while seller, product, price, availability, and access evidence stay separate. An administrator can enter prices such as `45000`, `45 000 ₽`, or `45к` without losing the active FSM state after an error. Offline tests demonstrate every changed behavior; live search, commit, and push remain out of scope.

## Progress

- [x] (2026-07-13 00:00+03:00) Captured `git status`, `git diff --stat`, `git diff`, and successful baseline `python -m compileall -q .` without changing or discarding the dirty worktree.
- [x] (2026-07-13 00:00+03:00) Traced request wizard, query planner, exact match, verifier, category quality, final policy, ranking, DB persistence, AI-card generation, admin price FSM, and client rendering.
- [x] (2026-07-13) Normalized structured and legacy request data and built clean search queries from values rather than question labels.
- [x] (2026-07-13) Enforced model and required-spec hard constraints and made search policy the owner of final verify status and cleaned reasons.
- [x] (2026-07-13) Stored independent platform, seller, product, price, availability, and access evidence without changing the SQLite schema.
- [x] (2026-07-13) Repaired admin price FSM, manual price evidence, cancellation, and active-state routing.
- [x] (2026-07-13) Restricted AI-card eligibility, assigned distinct recommendation roles, and removed technical diagnostics and advertising copy from normal admin/client cards.
- [x] (2026-07-13) Added 23 deterministic stabilization tests and passed every required offline and UX check.

## Surprises & Discoveries

- Observation: The current wizard serializes category details into `important_criteria` as strings such as “Какой объём памяти нужен: 256”, while `Request.original_query` contains only the product name.
  Evidence: `RequestWizard.to_request_payload()` appends `_DETAIL_LABELS[question.key]`, and `wizard_confirm()` feeds that text into `build_search_query()`.
- Observation: `match_candidate()` reconstructs a request from `original_query` or `product_name` and does not read `requirements_json`, so wizard storage can disappear before exact matching.
  Evidence: `app/exact_match.py::_request_details` calls `full_parse(query)` for a `Request` object.
- Observation: Category-quality processing promotes the initial search classification into final weak quality even after exact model evidence is known.
  Evidence: `_apply_category_product_quality()` calls `_final_product_quality_level()` before exact evidence, while `normalize_for_admin_save()` later downgrades normal statuses whenever `quality == "weak"`.
- Observation: The admin price handler clears FSM state before parsing, and the general evidence-aware price extractor intentionally rejects a bare number.
  Evidence: `edit_price_process()` calls `await state.clear()` before `_extract_price()`, while `tools/test_price_cases.py` explicitly expects `extract_price("49999") is None`.
- Observation: SearchResult has a flexible `facts_json` column, so the required trust dimensions and manual evidence can be added without a production DB migration.
  Evidence: `app/db.py::SearchResult` and existing create/update functions already persist `facts_json`.
- Observation: Readiness was a separate legacy consumer of the raw Alice TOP row and could still count an ineligible old card.
  Evidence: `app/readiness.py::check_readiness` used `get_alice_top_result()` without the shared AI-card eligibility predicate; the final patch filters both readiness and report-ready checks.

## Decision Log

- Decision: Preserve the current SQLite schema and place new request structure in `requirements_json` and offer evidence in `facts_json`.
  Rationale: The task forbids risky schema work and requires normalization of old rows without a mandatory migration.
  Date/Author: 2026-07-13 / Codex
- Decision: Keep verifier statuses provisional; `app/search_policy.py` will combine exact match, availability, price, page evidence, and category quality into the single final `verify_status`.
  Rationale: This matches the requested one-owner model while retaining current verifier and cache architecture.
  Date/Author: 2026-07-13 / Codex
- Decision: Treat actual required-spec differences as `REQUIRED_SPEC_MISMATCH`; missing proof remains manual-only and cannot create an AI-card.
  Rationale: A contradictory value is a hard rejection, while absent evidence is uncertainty rather than a fabricated mismatch.
  Date/Author: 2026-07-13 / Codex
- Decision: Keep existing persisted recommendation role values (`BEST`, `BUDGET`, `BACKUP`) as compatibility aliases for BEST_OVERALL, CHEAP_WITH_RISK, and RELIABLE.
  Rationale: Existing DB/report code and role rules already understand these values; changing stored enums would create unnecessary migration risk.
  Date/Author: 2026-07-13 / Codex

## Outcomes & Retrospective

The pass is complete. The structured iPhone request produces `iPhone 16 Pro 256 ГБ до 80000 Ярославль`; base iPhone 16 and 128 GB candidates are hard mismatches. Final policy removes stale weak reasons, trust dimensions persist independently, admin price input preserves FSM on errors, and ineligible/manual-unconfirmed offers cannot become client recommendations. Normal cards are human-readable while raw diagnostics stay in admin Debug.

All required commands exited zero: compileall; 23 stabilization tests; exact-match 3; price 4; category facts 3; ranking 30; search invariants 25; search cache 37; Alice parser; codex smoke; and all existing product/request/link/AI/report UX runners. `git diff --check` passed with line-ending warnings only. No live search, DB migration, commit, or push was performed, and the pre-existing dirty worktree was preserved.

## Context and Orientation

`app/services/request_wizard.py` owns the pure wizard payload. `app/request_parser.py` parses free text and will also normalize persisted structured or legacy request fields. `app/query_planner.py` builds bounded search queries. `app/exact_match.py` compares model identity and hard requirements. `app/candidate_verifier.py` owns page access and extracted page evidence. `app/category_quality.py` describes product quality for a category without deciding persistence. `app/search_policy.py` owns final status and save/drop decisions. `app/ranking.py` owns the final score and cap. `app/product_search.py` orchestrates those modules and serializes `facts_json` into `SearchResult` rows.

`app/ai_cards_service.py` creates recommendation drafts only from approved candidate snapshots. `app/handlers/admin.py` owns the price-edit FSM and admin presentation. `app/ui_formatters.py`, `app/report_builder.py`, and `app/services/recommendations.py` own client-safe roles and rendering. The existing `search_results.facts_json` field is the compatibility envelope for platform trust, seller trust, verification flags, and manual evidence.

“Platform trust” means the known nature of Ozon, Yandex Market, Wildberries, DNS, Citilink, M.Video, or Avito as a platform. “Seller trust” concerns the specific merchant and stays unknown when rating/history were not obtained. “Verification access” records whether automation accessed or was blocked by a page; it never labels a known platform or product as suspicious by itself.

## Plan of Work

First, add request normalization helpers that merge the product text, `requirements_json`, category details, and old question-prefixed requirements into canonical fields. Update the wizard to serialize brand, model, modifiers, storage, required features, optional features, and a clean query. Update query planning and all match/quality/ranking consumers to use that normalized snapshot.

Second, add `REQUIRED_SPEC_MISMATCH`, make model modifiers and required storage/size/display facts hard constraints, and pass exact evidence into final policy. Final policy will reject mismatches, clean empty/duplicate/stale reasons, keep strong exact verified candidates out of stale weak state, and persist a final status snapshot. Ranking remains the only score owner and applies its cap last.

Third, extend evidence assessment with platform class/trust, seller trust, product-page verification, exact-product verification, price verification, availability verification, seller verification, and access status. Known retail/marketplace platforms will retain high platform trust under 401/403/429/captcha/timeout. Avito remains classified rather than bad; new active exact offers may remain candidates, while wrong models and unavailable listings are dropped. Structured direct/page prices will stay verified through persistence.

Fourth, introduce an admin-only price parser for bare, spaced, currency, and Russian-thousands forms. Validate before clearing FSM, preserve state after invalid input, write manual price evidence into `facts_json`, and clear only the price state after success or cancel before returning to the same candidate.

Fifth, filter AI-card input using final facts. Wrong products, required mismatches, unavailable/removed rows, and blocked/manual rows without explicit confirmations cannot generate cards. Preserve the candidate facts snapshot in the generated row, create factual fallback prose rather than copying store snippets, assign up to three unique compatible roles, and keep diagnostics in Debug only. Client cards will show human platform/seller copy, confirmed facts, and checks while filtering status codes, browser diagnostics, confidence fields, raw flags, and “unverified site” wording. Welcome image failures will be logged while `/start` still falls back to text.

Finally, add deterministic tests covering the live iPhone case, legacy request normalization, stale final reasons, trust separation, Avito behavior, price FSM/parser behavior, AI eligibility, role selection, and client/admin rendering. Run only offline commands listed below.

## Concrete Steps

Work from `C:\Users\Пользователь\Documents\naydi_vygodnee`. Edit with focused patches and do not touch `.env`, `.venv`, databases, `main.py`, payment, credits, or cache architecture. Do not run live search.

After request and exact-policy changes, run:

    python -X utf8 tools/test_request_parser_cases.py
    python -X utf8 tools/test_request_wizard.py
    python -X utf8 tools/test_exact_match_cases.py
    python -X utf8 tools/test_search_invariants.py

After price, AI, and rendering changes, run the new stabilization runner plus all UX runners. At completion run:

    python -m compileall -q .
    python -X utf8 tools/test_exact_match_cases.py
    python -X utf8 tools/test_price_cases.py
    python -X utf8 tools/test_category_facts.py
    python -X utf8 tools/test_ranking_cases.py
    python -X utf8 tools/test_search_invariants.py
    python -X utf8 tools/test_search_cache.py
    python tools/test_alice_parser.py
    python tools/codex_smoke.py
    git diff --check

Also run every existing offline UX test under `tools/test_product_*.py`, `tools/test_request_*.py`, `tools/test_ai_cards.py`, and `tools/test_report_readiness.py`. Expected output is exit code zero for every runner.

## Validation and Acceptance

The structured regression creates an iPhone 16 Pro 256 GB request and observes canonical `brand=Apple`, `model=iPhone 16`, `model_modifiers=["Pro"]`, `storage_gb=256`, and query `iPhone 16 Pro 256 ГБ до 80000 Ярославль` without a wizard question label. A legacy row containing “Какой объём памяти нужен: 256” produces the same storage field without writing the DB.

Exact tests observe MODEL_MISMATCH for iPhone 16 and REQUIRED_SPEC_MISMATCH for 128 GB. Final-policy tests observe that neither can be normal, saved for admin recommendations, or converted into an AI-card. A fully verified exact candidate has no stale “нет признаков конкретной модели” reason.

Trust tests observe high platform trust and unknown seller trust for blocked Ozon, high retail platform trust for DNS/Citilink/M.Video, separate seller evidence, and classified rather than suspicious Avito. A cheap exact new active Avito offer receives a risk/check requirement but is not automatically dropped; used and new remain distinct.

Price tests parse all requested forms, preserve FSM data on invalid input, and store manual price evidence on success. Rendering tests assert that normal client output excludes raw status, confidence, evidence, HTTP codes, captcha/browser text, and “непроверенный сайт”, while admin Debug retains diagnostics.

## Idempotence and Recovery

All parser, policy, and renderer operations are pure or overwrite derived JSON fields deterministically. Tests use temporary databases and may be rerun. No destructive git command, migration rewrite, commit, or push is allowed. If a test exposes an unrelated existing dirty-worktree failure, record the exact command and output and isolate the stabilization patch rather than reverting user changes.

## Artifacts and Notes

Baseline evidence:

    git status --short
    M .agent/product-ux-service.md
    M app/ai_cards_service.py
    ... existing user changes preserved ...

    python -m compileall -q .
    exit code 0

No live benchmark or network search was run.

## Interfaces and Dependencies

Use only the standard library and installed project dependencies. `app.request_parser` will expose a canonical request-normalization helper usable by exact match, category quality, ranking, query planning, and tests. `app.exact_match` will expose `REQUIRED_SPEC_MISMATCH`. `app.search_evidence.SourceTrustAssessment` will retain existing confidence attributes and add the independent trust/verification attributes. `app.ai_cards_service` will expose an eligibility predicate and deterministic role assignment for offline tests. The admin price parser will be a pure helper in `app.handlers.admin` or an existing parser module and will not weaken the general price extractor’s false-positive protections.

Plan change note (2026-07-13): created after the baseline audit to capture the dirty-worktree constraints, verified root causes, compatibility strategy, and complete offline acceptance path before implementation. Updated after implementation with the readiness bypass fix and final green regression results.
