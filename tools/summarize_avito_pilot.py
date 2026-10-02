"""Aggregate local Avito live-pilot traces without external dependencies."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean, median
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE_DIR = ROOT / "runtime" / "avito_pilot_traces"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def read_traces(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    traces: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not path.exists():
        return traces, [f"Директория traces не найдена: {path}"]
    for file_path in sorted(path.glob("*.json")):
        try:
            value = json.loads(file_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not value.get("job_id"):
                raise ValueError("нет job_id")
            traces.append(value)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            warnings.append(f"Пропущен {file_path.name}: {type(exc).__name__}")
    return traces, warnings


def _percentile_95(values: Iterable[float]) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index]


def _known_median(values: Iterable[Any]) -> float | None:
    known = [number for value in values if (number := _number(value)) is not None]
    return float(median(known)) if known else None


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = {name: 0 for name in ("success", "empty", "error", "cancelled", "running")}
    latencies: list[float] = []
    collected: list[float] = []
    final_results: list[float] = []
    stage_totals: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    cache_hits = 0
    for trace in traces:
        status = str(trace.get("status") or "error")
        statuses[status] = statuses.get(status, 0) + 1
        timings = trace.get("timings") if isinstance(trace.get("timings"), dict) else {}
        collection = trace.get("collection") if isinstance(trace.get("collection"), dict) else {}
        filtering = trace.get("filtering") if isinstance(trace.get("filtering"), dict) else {}
        result = trace.get("result") if isinstance(trace.get("result"), dict) else {}
        cost = trace.get("cost") if isinstance(trace.get("cost"), dict) else {}
        reuse = trace.get("reuse") if isinstance(trace.get("reuse"), dict) else {}
        ai = trace.get("ai") if isinstance(trace.get("ai"), dict) else {}
        total_ms = _number(timings.get("total_ms"))
        if total_ms is not None:
            latencies.append(total_ms)
        raw = _number(collection.get("listings_collected"))
        final = _number(result.get("final_results"))
        if raw is not None:
            collected.append(raw)
        if final is not None:
            final_results.append(final)
        market_cache = reuse.get("market_cache") if isinstance(reuse.get("market_cache"), dict) else {}
        if market_cache.get("hit") or reuse.get("ai_cache_hit"):
            cache_hits += 1
        stage_ms = timings.get("stage_ms") if isinstance(timings.get("stage_ms"), dict) else {}
        for name, value in stage_ms.items():
            number = _number(value)
            if number is not None and name not in {"queued", "complete", "finished"}:
                stage_totals[str(name)] = stage_totals.get(str(name), 0.0) + number
        text = ai.get("text") if isinstance(ai.get("text"), dict) else {}
        photo = ai.get("photo") if isinstance(ai.get("photo"), dict) else {}
        apify_ms = sum(
            number
            for run in collection.get("apify_runs", [])
            if isinstance(run, dict)
            if (number := _number(run.get("duration_ms"))) is not None
        ) if isinstance(collection.get("apify_runs"), list) else None
        rows.append({
            "query": str(trace.get("query") or "")[:42],
            "status": status,
            "total_ms": total_ms,
            "apify_ms": apify_ms if apify_ms is not None else _number(timings.get("collection_ms")),
            "ai_ms": (_number(timings.get("text_ai_ms")) or 0)
                + (_number(timings.get("photo_ai_ms")) or 0)
                + (_number(timings.get("market_analysis_ms")) or 0),
            "collected": raw,
            "after_filters": _number(filtering.get("after_hard_filters")),
            "ai_analyzed": (_number(text.get("listings")) or 0) + (_number(photo.get("listings")) or 0),
            "final_results": final,
            "apify_actual_usd": _number(cost.get("apify_usd")),
            "apify_estimated_usd": _number(cost.get("apify_estimated_usd")),
            "ai_actual_rub": _number(cost.get("ai_rub")),
            "ai_estimated_rub": _number(cost.get("ai_estimated_rub")),
            "total_actual_rub": _number(cost.get("total_rub")),
            "total_estimated_rub": _number(cost.get("estimated_total_rub")),
        })
    slowest_stage = max(stage_totals, key=stage_totals.get) if stage_totals else None
    apify_rub_values = []
    ai_rub_values = []
    for trace in traces:
        cost = trace.get("cost") if isinstance(trace.get("cost"), dict) else {}
        rate = _number(cost.get("usd_rub_rate"))
        apify = _number(cost.get("apify_rub"))
        if apify is None:
            apify = _number(cost.get("apify_estimated_rub"))
        if apify is None and rate is not None:
            estimated_usd = _number(cost.get("apify_estimated_usd"))
            apify = estimated_usd * rate if estimated_usd is not None else None
        ai_cost = _number(cost.get("ai_rub"))
        if ai_cost is None:
            ai_cost = _number(cost.get("ai_estimated_rub"))
        if apify is not None:
            apify_rub_values.append(apify)
        if ai_cost is not None:
            ai_rub_values.append(ai_cost)
    apify_median_rub = float(median(apify_rub_values)) if apify_rub_values else None
    ai_median_rub = float(median(ai_rub_values)) if ai_rub_values else None
    most_expensive = None
    if apify_median_rub is not None or ai_median_rub is not None:
        most_expensive = "Apify" if (apify_median_rub or 0) >= (ai_median_rub or 0) else "AI"
    searches = len(traces)
    rates = {
        name: (count / searches if searches else 0.0)
        for name, count in statuses.items()
    }
    return {
        "searches": searches,
        "statuses": statuses,
        "rates": rates,
        "median_latency_ms": float(median(latencies)) if latencies else None,
        "p95_latency_ms": _percentile_95(latencies),
        "median_apify_cost_usd": _known_median(
            (trace.get("cost") or {}).get("apify_usd") for trace in traces
        ),
        "median_ai_cost_rub": _known_median(
            (trace.get("cost") or {}).get("ai_rub") for trace in traces
        ),
        "median_total_cost_rub": _known_median(
            (trace.get("cost") or {}).get("total_rub") for trace in traces
        ),
        "searches_with_cache_hit": cache_hits,
        "average_listings_collected": mean(collected) if collected else None,
        "average_final_results": mean(final_results) if final_results else None,
        "slowest_stage": slowest_stage,
        "most_expensive_component": most_expensive,
        "rows": rows,
    }


def _fmt_number(value: Any, digits: int = 1, suffix: str = "") -> str:
    number = _number(value)
    return "?" if number is None else f"{number:.{digits}f}{suffix}"


def render(summary: dict[str, Any]) -> str:
    rows = summary["rows"]
    lines = ["LIVE PILOT SUMMARY", ""]
    headers = [
        "Query", "Status", "Time", "Apify", "AI", "Collected", "Filtered",
        "AI checked", "Final", "Apify actual $", "Apify est. $",
        "AI actual ₽", "AI est. ₽", "Total actual ₽", "Total est. ₽",
    ]
    rendered_rows = []
    for row in rows:
        rendered_rows.append([
            row["query"], row["status"], _fmt_number((row["total_ms"] or 0) / 1000, 1, "s") if row["total_ms"] is not None else "?",
            _fmt_number((row["apify_ms"] or 0) / 1000, 1, "s") if row["apify_ms"] is not None else "?",
            _fmt_number((row["ai_ms"] or 0) / 1000, 1, "s"), _fmt_number(row["collected"], 0),
            _fmt_number(row["after_filters"], 0), _fmt_number(row["ai_analyzed"], 0),
            _fmt_number(row["final_results"], 0),
            _fmt_number(row["apify_actual_usd"], 4), _fmt_number(row["apify_estimated_usd"], 4),
            _fmt_number(row["ai_actual_rub"], 2), _fmt_number(row["ai_estimated_rub"], 2),
            _fmt_number(row["total_actual_rub"], 2), _fmt_number(row["total_estimated_rub"], 2),
        ])
    widths = [len(value) for value in headers]
    for row in rendered_rows:
        widths = [max(width, len(value)) for width, value in zip(widths, row)]
    lines.append("  ".join(value.ljust(width) for value, width in zip(headers, widths)))
    lines.append("  ".join("-" * width for width in widths))
    lines.extend("  ".join(value.ljust(width) for value, width in zip(row, widths)) for row in rendered_rows)
    statuses = summary["statuses"]
    rates = summary["rates"]
    lines.extend([
        "",
        f"Searches: {summary['searches']}",
        f"Success: {statuses.get('success', 0)} ({rates.get('success', 0):.1%})",
        f"Empty: {statuses.get('empty', 0)} ({rates.get('empty', 0):.1%})",
        f"Error: {statuses.get('error', 0)} ({rates.get('error', 0):.1%})",
        f"Cancelled: {statuses.get('cancelled', 0)} ({rates.get('cancelled', 0):.1%})",
        f"Median latency: {_fmt_number((summary['median_latency_ms'] or 0) / 1000, 1, 's') if summary['median_latency_ms'] is not None else '?'}",
        f"P95 latency: {_fmt_number((summary['p95_latency_ms'] or 0) / 1000, 1, 's') if summary['p95_latency_ms'] is not None else '?'}",
        f"Median total cost (actual): {_fmt_number(summary['median_total_cost_rub'], 2, ' ₽')}",
        f"Median Apify cost (actual): {_fmt_number(summary['median_apify_cost_usd'], 4, ' $')}",
        f"Median AI cost (actual): {_fmt_number(summary['median_ai_cost_rub'], 2, ' ₽')}",
        f"Searches with cache hit: {summary['searches_with_cache_hit']}",
        f"Average listings collected: {_fmt_number(summary['average_listings_collected'], 1)}",
        f"Average final results: {_fmt_number(summary['average_final_results'], 1)}",
        f"Slowest stage: {summary['slowest_stage'] or '?'}",
        f"Most expensive component: {summary['most_expensive_component'] or '?'}",
    ])
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Summarize Avito live-pilot JSON traces")
    parser.add_argument("--trace-dir", type=Path, default=DEFAULT_TRACE_DIR)
    parser.add_argument("--json", action="store_true", help="Print aggregate JSON instead of a table")
    args = parser.parse_args()
    traces, warnings = read_traces(args.trace_dir)
    for warning in warnings:
        print(f"Warning: {warning}")
    if not traces:
        print("LIVE PILOT SUMMARY\n\nSearches: 0\nНет завершённых traces.")
        return 0
    summary = summarize(traces)
    print(json.dumps(summary, ensure_ascii=False, indent=2) if args.json else render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
