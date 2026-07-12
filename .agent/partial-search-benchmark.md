# Preserve partial source results in benchmark cache

This ExecPlan is a living document and is maintained according to `.agent/PLANS.md`.

## Purpose / Big Picture

Live benchmark cases must retain useful source candidates even when later sources or page verification exceed the case deadline. After this change, each source completion writes a source cache entry and a partial benchmark snapshot immediately. Cached mode prefers a fresh successful snapshot, then a partial snapshot, and never calls the network.

## Progress

- [x] (2026-07-11) Identified the timeout boundary: the parent kills the child process after 90 seconds, while cache writes currently happen only after `collect_product_candidates()` returns.
- [x] Added source/stage observers and `full`, `fast`, and `none` verification modes to the collection pipeline.
- [x] Persisted source entries and partial snapshots from the live child process.
- [x] Prioritized SUCCESS/PARTIAL_SUCCESS snapshots and expanded cache statistics.
- [x] Added deterministic regression tests and ran local validation.
- [x] Ran the required coffee-machine live/cached pair and one smoke live/cached pair.

## Surprises & Discoveries

- Observation: the existing source functions are sequential, but each emits a `SearchAttemptData` record once it returns.
  Evidence: `app/product_search.py` appends one or more attempts after every adapter/direct-retail call.
- Observation: the current source cache is populated only in the parent after a successful child result, so a child timeout loses all in-memory collection state.
  Evidence: `_store_source_entries` is called only in the `DONE` branch of `tools/search_benchmark.py`.
- Observation: the remaining 90-second delay was a multiprocessing queue deadlock, not slow final processing.
  Evidence: the child wrote the final SUCCESS snapshot to SQLite and then blocked while sending the same large payload through `Queue`; sending only the small status message reduced smoke cases to 30-38 seconds.
- Observation: Wildberries returned malformed JSON or 429 and Citilink direct returned rate-limit status, but nine other source families completed and were cached.
  Evidence: the final smoke source diagnostics list those two failed sources while all three cases reached SUCCESS.

## Decision Log

- Decision: use sequential source collection with a 45-second source-stage budget for this pass.
  Rationale: one active source is below the required maximum concurrency of three and avoids extra rate-limit pressure while source-level persistence makes the run useful.
  Date/Author: 2026-07-11 / Codex.
- Decision: emit callbacks at source-step and stage boundaries from `collect_product_candidates`, then write cache data from the child process.
  Rationale: only the child owns partial collection state before the outer case timeout.
  Date/Author: 2026-07-11 / Codex.
- Decision: persist large snapshots only in SQLite and pass only status metadata through the multiprocessing queue.
  Rationale: the parent waits for process exit before reading the queue, so a large queued snapshot can fill the pipe and prevent exit.
  Date/Author: 2026-07-11 / Codex.

## Outcomes & Retrospective

The incremental cache pipeline is working. Final local checks passed: compileall, 21 search invariants, 37 cache/stage tests, and the Alice parser suite.

Coffee-machine live preserved 54 raw candidates and 10 admin candidates; the original parent still reported PARTIAL_SUCCESS because it hit the queue deadlock, but the SUCCESS snapshot was already durable. Cached replay selected that SUCCESS snapshot in 12 ms with zero network calls.

The final smoke live completed all three cases as SUCCESS in 104.748 seconds total: robot vacuum had 52 raw/3 admin candidates, monitor had 54 raw/10 admin candidates, and microwave had 50 raw/9 admin candidates. Cached smoke restored the same 22 admin candidates in 40 ms with zero network calls, about 2,619 times faster. Cache stats show 61 fresh entries: 53 source entries and 8 benchmark snapshots.

## Context and Orientation

`app/product_search.collect_product_candidates` currently collects source candidates and runs full page verification before returning. `tools/search_benchmark.py` executes it in a spawned process with a 90-second parent timeout. `app/search_cache.py` owns the separate `data/search_cache.sqlite3` database. No application database or migration is involved.

`SUCCESS` means the live case reached final processing. `PARTIAL_SUCCESS` means at least one real source candidate was persisted but the case did not finish. `CASE_TIMEOUT` means no useful source candidate was obtained. The three verification modes are `full`, `fast`, and `none`; benchmark smoke defaults to `fast`.

## Plan of Work

Add optional callbacks and source/verification budgets to `collect_product_candidates`. A source-step wrapper will check the source deadline, run one adapter call, notify observers with new attempts and candidates, and emit a normalisation snapshot. Fast verification will verify at most three candidates without broad browser fallback; none will create safe manual-review records without page fetches.

In the spawned benchmark child, cache every source attempt and overwrite only the partial snapshot key. Keep SUCCESS, PARTIAL_SUCCESS, CASE_TIMEOUT, and ERROR snapshots under distinct source-key variants so a newer timeout cannot replace a still-fresh success. Update cached selection and stats accordingly.

## Concrete Steps

From `C:\\Users\\Пользователь\\Documents\\naydi_vygodnee`, run:

    python -m compileall -q .
    python -X utf8 tools/test_search_invariants.py
    python -X utf8 tools/test_search_cache.py
    python tools/test_alice_parser.py
    python -u -X utf8 tools/search_benchmark.py --live --verification none --case coffee_machine
    python -u -X utf8 tools/search_benchmark.py --cached --case coffee_machine
    python -u -X utf8 tools/search_benchmark.py --suite smoke --live --verification fast
    python -u -X utf8 tools/search_benchmark.py --suite smoke --cached

## Validation and Acceptance

The cache test suite will prove source persistence before case completion, partial-success selection, negative caching, verification limits, and zero loader calls in cached mode. The coffee-machine cached replay must complete in less than one second with real candidates and no network calls. Smoke must persist each case even if one source fails or times out.

## Idempotence and Recovery

Every source and snapshot cache write is an upsert. Partial snapshots can be overwritten by newer partial data, but a SUCCESS snapshot uses a separate key and remains preferred. Resume uses saved case completion data; interrupted cases retain their last partial snapshot.

## Artifacts and Notes

The cache contains normalised candidates, source attempts, and diagnostics only. It does not store full HTML, credentials, cookies, or application data.

## Interfaces and Dependencies

`collect_product_candidates` gains optional `source_observer`, `stage_observer`, `verification_mode`, and `source_timeout_seconds` arguments that preserve default application behavior. `SearchCache` remains standard-library SQLite. `tools/search_benchmark.py` is responsible for child-process callbacks and CLI output.

Plan created 2026-07-11 for partial source preservation and useful live benchmark runs. Updated 2026-07-11 with final validation and benchmark evidence.
