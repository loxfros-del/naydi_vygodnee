# Verify Autosearch Candidates

This ExecPlan is a living document. It follows `.agent/PLANS.md`.

## Purpose / Big Picture

Autosearch currently produces many product-looking URLs, but some have no current price, are unavailable, are article pages, or have prices extracted from unrelated text. This change adds a verification layer between raw search and admin display: each candidate page is opened safely, checked as a product page, price and availability are extracted conservatively, bad candidates are rejected, duplicates are collapsed, and only the best verified candidates are saved for the admin.

## Progress

- [x] (2026-07-04) Read the attached task text, `.agent/PLANS.md`, `app/product_search.py`, and `tools/test_search.py`.
- [ ] Add `app/candidate_verifier.py` with `VerifiedCandidate` and required verification functions.
- [ ] Integrate verification, deduplication, and ranking into `app/product_search.py` before final candidate selection and DB save.
- [ ] Update `tools/test_search.py` to print verification stats and reject reasons.
- [ ] Validate with `python -m compileall .`, `python tools/test_alice_parser.py`, and target `tools/test_search.py`.

## Surprises & Discoveries

- Observation: Current candidates already have `quality`, future fields, and source labels, so verifier can mutate existing `ProductCandidate` objects without changing `search_results`.
  Evidence: `ProductCandidate` in `app/product_search.py` has `quality`, `source_type`, `availability`, `description`, `risk_flags`, and DB save still writes the old schema.

## Decision Log

- Decision: Keep verifier outside `app/product_search.py` and let it accept candidate-like objects instead of importing `ProductCandidate`.
  Rationale: This avoids a circular import while keeping the requested module boundary.
  Date/Author: 2026-07-04 / Codex
- Decision: Treat missing page verification as non-fatal but not GOOD.
  Rationale: Sites may block requests because of VPN/tunnels/anti-bot. The bot must continue, but blocked pages should not be promoted as verified good products.
  Date/Author: 2026-07-04 / Codex

## Outcomes & Retrospective

Pending implementation and validation.

## Context and Orientation

`app/product_search.py` collects rows from DDGS and Wildberries, converts them into `ProductCandidate`, filters TRASH, ranks candidates, and saves them to the existing `search_results` table. `tools/test_search.py` calls `collect_product_candidates()` without Telegram and prints diagnostics. The database schema must not change. `app/candidate_verifier.py` will add a page-check layer that returns `VerifiedCandidate` objects and stats, while `product_search.py` will keep saving ordinary `ProductCandidate` rows.

## Plan of Work

Create `app/candidate_verifier.py` with the functions named in the task. It will fetch pages with timeout, reject search/category/article/ad URLs before fetching when possible, parse product title, extract price only from trustworthy price contexts, detect unavailable phrases, classify budget status, and deduplicate by model-like title key. Then update `collect_product_candidates()` to verify a bounded set of non-TRASH candidates before final ranking. Finally, update `tools/test_search.py` to print verification counters and rejected reasons.

## Concrete Steps

From `C:\Users\Пользователь\Documents\naydi_vygodnee`, run:

    python -m compileall .
    python tools/test_alice_parser.py
    python tools/test_search.py "Нужен телевизор для PS5 до 45к в Ярославле"

## Validation and Acceptance

The target search test must finish without crashing. It must show RAW, checked pages, verification status counters, saved candidates, and rejected reasons. Saved candidates should be capped to about 5-10 best verified rows and should not include unavailable products, article/category/search pages, or strong over-budget products.

## Idempotence and Recovery

The change is additive and does not alter `.env`, `.venv`, `bot.db`, `*.db`, `main.py`, or the SQLite schema. Re-running tests is safe.

## Artifacts and Notes

Validation output will be recorded after implementation.

## Interfaces and Dependencies

`app/candidate_verifier.py` must expose:

    verify_candidate(candidate, req) -> VerifiedCandidate
    verify_candidates(candidates, req, limit=30) -> list[VerifiedCandidate]
    is_valid_product_page(url, html, title) -> bool
    extract_verified_price(source, html, text, title) -> int | None
    extract_availability(source, html, text) -> str
    classify_verified_candidate(candidate, req) -> str

It uses existing `requests`, `beautifulsoup4`, `app.config.settings`, and `app.db.Request`.

