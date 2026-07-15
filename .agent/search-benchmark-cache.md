# Make benchmark runs cached and resumable

This ExecPlan is a living document and is maintained according to `.agent/PLANS.md`.

## Purpose / Big Picture

The search benchmark must keep each finished case even if a later source hangs. A live smoke run will save normalised candidates and source diagnostics in `data/search_cache.sqlite3`; a cached rerun will use that snapshot only and finish in seconds without calling a source.

## Progress

- [x] (2026-07-11) Read the existing benchmark and product-search collection path. The previous ten-case run timed out after 604 seconds before buffered output was delivered.
- [x] Added a separate SQLite cache and run/case persistence API.
- [x] Added cache-aware benchmark modes, suites, progress output, timeout, negative snapshots, and resume.
- [x] Added deterministic cache tests and ran all required local checks.
- [x] Ran live smoke and cached smoke; live cases were preserved as timeouts, while cached mode completed in 35 ms with zero source calls.

## Surprises & Discoveries

- Observation: `collect_product_candidates` calls multiple sources and then verifies result pages before returning, so an in-memory retry cannot recover completed work from a killed benchmark process.
  Evidence: source collection ends with `_apply_verification(collection, req)` in `app/product_search.py`.
- Observation: the existing benchmark prints only after each case returns, and a buffered process timeout lost all intermediate output.
  Evidence: the previous ten-case execution reached the 604-second command limit without output being flushed.
- Observation: three live smoke cases reached the new 90-second case deadline rather than blocking the whole process.
  Evidence: run `97780b9ed0c0441fac45745a9274086f` recorded three `CASE_TIMEOUT` cases and exited after 270.2 seconds.
- Observation: the live collection exposed a missing default `ProductCandidate.why_not_verified_good`; it is now covered by an integration invariant.
  Evidence: the first smoke recorded `AttributeError`; after adding defaults, the invariant that sends `PRICE_MISSING` through the final policy passes.

## Decision Log

- Decision: store a normalised final benchmark snapshot and source-attempt entries in a separate cache database, rather than alter application source adapters.
  Rationale: the snapshot gives reliable no-network replay and avoids changes to protected source integrations. A full pre-verification replay would require page-response snapshots and would be materially riskier.
  Date/Author: 2026-07-11 / Codex.
- Decision: run each live benchmark case in a child process with a bounded join timeout, saving the result in the parent immediately after it returns.
  Rationale: this prevents a single case from blocking the whole run without interrupting threads in the main benchmark process.
  Date/Author: 2026-07-11 / Codex.
- Decision: cache `CASE_TIMEOUT` and other case errors as negative snapshots for the negative TTL.
  Rationale: `--auto` must not immediately retry a source failure, while `--cached` can report the failure without network access.
  Date/Author: 2026-07-11 / Codex.

## Outcomes & Retrospective

`app/search_cache.py` now provides the isolated cache schema, WAL, timeout/recovery, redaction, TTL handling, and resumable run/case records. `tools/search_benchmark.py` provides the requested modes and progress output. Local checks passed: compileall, 21 search invariants, 22 cache tests, and the Alice parser suite.

The live smoke did not produce successful source snapshots because all three cases reached their 90-second deadline. The cached smoke was correctly source-free and took 35 ms, but it reported `CACHE_MISS` because successful snapshots did not exist. This proves recovery and no-network behavior, but not a meaningful same-data speedup yet.

## Context and Orientation

`app/product_search.py` collects source rows and verifies candidate pages. `tools/search_benchmark.py` currently calls it directly and has no persistence. The new `app/search_cache.py` will use its own `data/search_cache.sqlite3`, never `bot.db` or application migrations. A cache entry stores JSON only, with a version and expiry. A benchmark run stores per-case results in the same separate database.

The active snapshot is final normalised benchmark output. Cached replay can recompute report counters and ranking display from it, but it does not refetch source rows or page HTML. Source-attempt records are also cached for diagnostics and future adapter integration.

## Plan of Work

Create a dependency-free SQLite cache module. It will open one connection per operation, enable WAL and a busy timeout, recreate a corrupt cache file safely, redact secret-like JSON keys, calculate SHA-256 keys from normalised parsed request context, and expose cache plus benchmark-run operations.

Rewrite the benchmark command around explicit case definitions and `smoke`, `core`, and `extended` suites. Its default mode will be `--auto`: use a fresh snapshot when available, otherwise run the case live and persist the snapshot immediately. `--cached` never invokes the loader. `--resume` reads finished case IDs and skips them. Each case prints START, DONE, ERROR, or CASE_TIMEOUT with `flush=True`.

The live smoke and immediate cached smoke are the only network validation for this task. The cached run must report no source calls and be at least ten times faster when the live smoke succeeds.

## Concrete Steps

From `C:\\Users\\Пользователь\\Documents\\naydi_vygodnee`, run:

    python -m compileall -q .
    python -X utf8 tools/test_search_invariants.py
    python -X utf8 tools/test_search_cache.py
    python tools/test_alice_parser.py
    python -u -X utf8 tools/search_benchmark.py --suite smoke --live
    python -u -X utf8 tools/search_benchmark.py --suite smoke --cached

## Validation and Acceptance

`tools/test_search_cache.py` must cover stable keys, context differences, expiry, version mismatch, JSON corruption, purge, run/case persistence, resume, updates, Unicode redaction, and the fact that cached mode does not call a supplied loader. The cached smoke must print `network_calls=0` and use saved cases only.

## Idempotence and Recovery

Schema creation, upserts, purge, stats, and cached replay are safe to run repeatedly. If a cache database is corrupt, it is renamed with a `.corrupt-<timestamp>` suffix and a fresh cache is created. If a case exceeds its timeout, the parent records `CASE_TIMEOUT` and continues. A later `--resume` continues only unfinished cases in the named run.

## Artifacts and Notes

The cache file is technical benchmark data only. It contains no application tables, cookies, tokens, full HTML, or credentials. It should not be committed.

## Interfaces and Dependencies

`app.search_cache.SearchCache` will provide `make_cache_key`, `get`, `put`, `cache_stats`, `purge_expired`, and benchmark run/case methods. `tools/search_benchmark.py` will be its only caller in this change. The implementation uses only Python standard library modules.

Plan created 2026-07-11 for the cache and benchmark reliability feature. Updated 2026-07-11 with the final test and smoke evidence.
