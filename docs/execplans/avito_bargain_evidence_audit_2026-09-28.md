# Separate Avito photo admission from market-savings evidence

## Purpose / Big Picture

This living plan follows `.agent/PLANS.md`. It continues the 2026-09-28 audit: market comparison proves savings, while text, photo, price, match, and final-refresh checks prove whether an exact listing is safe to recommend. Missing comparables must not prevent photo review or final revalidation. A bargain label still requires at least three independent exact comparables and positive savings. The saved PS5 replay has 11 text-safe listings and is the offline acceptance fixture; this work must make all eligible items reach the photo shortlist without running live Apify or AI.

## Progress

- [x] Located the latest saved text replay and its 31-listing PS5 source snapshot.
- [x] Replayed the saved candidate decisions offline and found only two qualified independent seller identities in distinct product/kit groups.
- [x] Make pre-photo and final-ranking reference selection use the same one-listing-per-seller rule.
- [x] Persist per-listing bargain decisions and funnel counts in admin telemetry and replay output.
- [x] Add regressions for duplicate-seller price skew, insufficient sample reasons, and telemetry serialization.
- [x] Run targeted tests, the full Avito suite, compileall, and the offline saved-data replay without network or AI calls.
- [x] (2026-09-30) Remove comparables from photo admission while retaining independent price, defect, SKU, condition, and location checks.
- [x] (2026-09-30) Allow final revalidation and exact-match output without market savings; keep unresolved photo evidence blocked in the UI and service.
- [x] (2026-09-30) Add funnel outcomes and regressions for 0, 1–2, and 3+ comparables, safety rejects, and unresolved photos.
- [x] (2026-09-30) Run targeted tests and the saved replay offline; report photo/final stages as pending where real evidence was not saved.

## Context and Orientation

`avito_service/service.py` owns text-to-photo admission, photo calls, and finalist refresh. `avito_service/ranking.py` assigns exact-match roles and computes savings only from one real observation per independent seller after exact SKU/configuration/condition/location/price-basis grouping. Before this phase, `_photo_candidate_ids` also required the savings proof, `_verify_finalists` refreshed only bargains, and `AnalysisReport._public_recommendations` hid safe exact matches without savings. Those are separate admission, revalidation, and display gates. `avito_service/verification.py` remains the safety gate: text/photo evidence, request match, defects, price conditions, and condition/configuration evidence still decide whether an item can advance. `avito_web/app.js` verifies public result policy and labels cards. `avito_service/telemetry.py` stores owner-only funnels.

The saved replay `runtime/avito_text_replays/20260928T122220Z.json` stores 17 text-decision summaries but not the complete AI review payload. Its matching source is `runtime/avito_live_batches/20260928T113148Z/market_cache/4e13854efea2aa363b1ebf3489352f0dcd427c6dd9eb163f733dade93c22f354.json`, which has 31 listings. Any offline reconstruction that combines them must label the missing AI fields as reconstructed from the saved listing facts; it must never imply that a new AI review was run.

## Plan of Work

Keep the shared per-seller price representative from phase one. In `_photo_candidate_ids`, treat comparison as annotation for the later savings label. Text-safe listings with a confirmed full price may enter the existing photo shortlist even when there are zero exact comparables; missing full price remains a safety block. Do not change matching, condition, defect, seller-conflict, or location checks.

In `rank_listings`, choose the best exact-safe TOP/BACKUP/BUDGET result even when no savings are established. Set `below_comparables` and `below_market` only when their existing independent-sample and positive-delta rules pass. In `_verify_finalists`, refresh photo-complete exact-safe candidates regardless of market evidence, and refuse refresh/public final status when photo evidence is unresolved. Public output in bargain mode must accept verified exact matches with `belowMarket=false`, label them “Точное совпадение” and “Выгода не подтверждена”, and retain exact SKU, full-price, location, condition, freshness, and photo checks.

Extend the private funnel with photo attempted/completed, final revalidated, bargain, exact-match, rejected, and pending counts. Update the offline saved-replay tool to report the 11 photo-eligible IDs but zero executed photo/final calls; without saved photo or current refresh evidence it must mark final outcomes pending, never simulate them as confirmed. Add offline regressions for 0, 1–2, and 3+ comparables, unsafe price/defect/SKU cases, and unresolved photos. Run targeted tests and this saved-data replay only; never call Apify, text AI, or photo AI.

## Validation and Acceptance

The tests must prove that exact-comparable count alone never excludes a text-safe, fully priced listing from the photo shortlist; zero or one/two comparables cannot create savings, while three or more independent sellers and a positive delta can. A price/defect/SKU mismatch remains rejected regardless of sample size. Incomplete photo evidence prevents final refresh/public status. Existing replay output must show 11 text-safe, 11 photo-eligible, zero actual photo calls, zero final refreshes, and 11 pending final outcomes because no such saved evidence exists. The targeted Avito tests and `python tools/diagnose_avito_bargain_replay.py` must pass without network use.

## Decision Log

- 2026-09-28: Keep three independent sellers and any strictly positive delta. The saved PS5 snapshot has too little fully supported comparable evidence to justify lowering either rule.
- 2026-09-28: Compare full comparable keys, including SKU/version, storage, condition, and known kit. Product instructions require like-for-like comparisons and prohibit invented monetary adjustments for different bundles.
- 2026-09-28: Use a real per-seller listing representative, matching final ranking, rather than taking a synthetic median across duplicate ads.
- 2026-09-28: Do not run live Apify, paid AI, or photo calls. The latest summary does not contain raw text-review fields; diagnostic output must disclose that limitation.
- 2026-09-30: Comparable evidence only controls savings labels; it does not control photo admission or exact-match admission after photo and final revalidation.
- 2026-09-30: Unresolved photo evidence, unknown full price, price conditions, safety conflicts, and request/SKU mismatch remain independent fail-closed checks.
- 2026-09-30: The offline replay reports unexecuted photo and final-refresh stages as pending; it does not fabricate successful evidence to force a final label.

## Outcomes & Retrospective

The newest saved text replay is still `20260928T122220Z.json`; no newer PS5 replay exists as of 2026-09-30. Its matching source snapshot contains 31 listings. The saved text report identifies 11 `NEEDS_EVIDENCE`/text-safe candidates, but omitted the full AI model/storage/condition payload. The offline diagnostic therefore marks those fields as reconstructed from saved structured listing facts and does not claim a fresh AI review.

The diagnostic evaluated all 11 candidates. Each has zero exact-key external seller comparables, so the reference price and delta are correctly undefined. Each fails with `INSUFFICIENT_COMPARABLE_SELLERS` against the existing minimum of 3 independent sellers; the positive-savings rule is 1 RUB (0% minimum). Full per-listing gate inputs and machine-readable reasons are in `runtime/avito_bargain_replays/20260928T155000Z.json`.

The 31-listing snapshot has only four safe reference records after text safety: two seller identities, split across base PS5 and Slim Disc, differing kits and seller/market lanes. It contains no PS5 Pro. Those records do not form an exact comparable set for any candidate. With no valid exact groups, no market median or outlier conclusion can be drawn. The minimum-sample rule remains unchanged.

Fixed the confirmed pre-photo/final-ranking mismatch: duplicate ads from one seller previously contributed an arithmetic median in the photo gate, while ranking used one real lower-median listing. Both now use the same one-real-listing-per-independent-seller helper. No candidate passes in this historical replay after the fix; this is expected and does not justify lowering the evidence floor.

Phase one is complete: the duplicate-seller representative mismatch is fixed, detailed admin telemetry exists, and the saved replay explained 11/11 prior evidence-gate failures. This phase changes the boundary: those evidence failures must no longer block photo admission. Its offline result must distinguish what the fixture proves (photo eligibility and exact-comparable counts) from what it cannot prove (photo review, fresh listing activity, and final classification). Final labels can only be counted from completed evidence, never replayed from an absent provider response.

Plan update (2026-09-30): the owner clarified that three comparables are a requirement for a savings claim, not a universal admission gate. The implementation sequence and acceptance criteria were changed to carry exact matches through photo and final refresh while preserving all safety checks.

Phase two complete (2026-09-30): the saved replay now reports 11/11 text-safe, 11/11 bargain-evaluated, 0 savings passes, 11 `INSUFFICIENT_COMPARABLE_SELLERS` evidence failures, 11/11 photo-eligible, zero photo/final calls, and 11 pending outcomes. The 11 exact-comparable counts remain zero; none has a supported reference price or delta, so none is called a bargain. The replay does not contain photo or current-page evidence and therefore does not classify any item as a final exact match or reject. Live Apify and AI were not called.

The service now carries complete exact-safe listings through photo selection and final refresh independently of market evidence. Final output without comparables is an exact match with unconfirmed savings; unresolved photo evidence remains blocked. The public Avito UI was updated to accept the current optional-savings result policy while preserving the independent savings badge requirements. Regression coverage confirms 0/1–2 comparables can reach photo but cannot claim savings, three independent sellers plus positive delta can establish a bargain, and price/defect/SKU/photo blockers remain fail-closed. Validation: 529 Avito Python tests passed, targeted market/service regressions passed, 3 JS presentation/query tests passed, `compileall` passed, and `tools/test_alice_parser.py` passed.
