"""One explicitly requested, cost-capped live Avito pilot with local diagnostics."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from avito_service.apify import ZenStudioProvider
from avito_service.config import ServiceConfig, load_config
from avito_service.http_api import build_service
from avito_service.models import SearchRequest, CollectionBatch
from avito_service.normalization import normalize_dataset
from avito_service.risk_rules import evaluate_rules
from avito_service.telemetry import PilotTraceStore, SearchTrace


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def funnel_from_audit(report):
    audit = report.get("adminAudit", {})
    return {
        "collected": audit.get("collected", report.get("collectedCount", 0)),
        "correct_city": audit.get("correctCity", 0),
        "basic_filters": audit.get("basicFilters", audit.get("deterministicEligible", 0)),
        "text_check": audit.get("textCompleted", 0),
        "photo_check": audit.get("photoCompleted", 0),
        "HIGH_CONFIDENCE": audit.get("highConfidence", 0),
        "CONFIRMED": audit.get("confirmed", audit.get("finalVisible", 0)),
    }


def closest_failures(report, limit=10):
    """Return owner-only, actionable blockers for the nearest reviewed ads."""
    result = []
    for card in report.get("adminRecommendations", ()):
        analysis = card.get("analysis", {})
        blockers = []
        if analysis.get("error"):
            blockers.append(analysis["error"])
        if not analysis.get("textAnalyzed"):
            blockers.append("Текстовая проверка не завершена.")
        elif not analysis.get("matchesRequest"):
            blockers.append(analysis.get("mismatchReason") or "AI не подтвердил совпадение с запросом.")
        if analysis.get("verdict") != "approve":
            blockers.append(f"Вердикт AI: {analysis.get('verdict') or 'неизвестен'}.")
        blockers.extend(analysis.get("defects") or ())
        blockers.extend(analysis.get("conflicts") or ())
        blockers.extend(analysis.get("priceConditions") or ())
        blockers.extend(card.get("risks") or ())
        result.append({
            "id": card.get("listing", {}).get("id"),
            "title": card.get("listing", {}).get("title"),
            "price": card.get("listing", {}).get("acquisitionPrice"),
            "role": card.get("role"),
            "reasons": list(dict.fromkeys(str(value) for value in blockers if value)),
        })
        if len(result) >= limit:
            break
    return result


class PilotProvider(ZenStudioProvider):
    def __init__(self, config, output, resume_market=None, spending_guard=None):
        super().__init__(config, spending_guard)
        self.output = output
        self.runs = {}
        self.calls = []
        self.resume_market = resume_market
        self._collection_stage = "candidates"

    def collect_market(self, search, **kwargs):
        if self.resume_market is None:
            previous_stage = self._collection_stage
            self._collection_stage = "market"
            try:
                return super().collect_market(search, **kwargs)
            finally:
                self._collection_stage = previous_stage
        source = Path(self.resume_market)
        rows = json.loads(source.read_text(encoding="utf-8"))
        receipt = json.loads((source.parent / "receipt.json").read_text(encoding="utf-8"))
        request = json.loads((source.parent / "request.json").read_text(encoding="utf-8"))
        if request["query"] != search.query or request["location"] != search.location:
            raise ValueError("Recovered market does not match this pilot query")
        batch = CollectionBatch(tuple(rows), float(receipt["usageTotalUsd"]), False,
                                requested_count=search.max_results, capped_count=len(rows))
        print("PILOT_REUSE existing paid market; no actor start", flush=True)
        self._record_batch("market", batch)
        return batch

    def _json_request(self, method, url, **kwargs):
        result = super()._json_request(method, url, **kwargs)
        data = result.get("data") if isinstance(result, dict) else None
        if isinstance(data, dict) and data.get("id") and data.get("status"):
            self.runs[data["id"]] = {key: data.get(key) for key in
                ("id", "status", "usageTotalUsd", "defaultDatasetId", "startedAt", "finishedAt")}
            write_json(self.output / "runs.json", list(self.runs.values()))
        return result

    def _collect_payload(
        self, payload, requested_count, deadline_at, max_charge_usd,
        cancel_requested=None, telemetry=None, purpose="collection",
    ):
        stage = "refresh" if "listingUrls" in payload else self._collection_stage
        self.calls.append({"stage": stage, "maxResults": requested_count, "capUsd": max_charge_usd})
        write_json(self.output / "calls.json", self.calls)
        batch = super()._collect_payload(
            payload, requested_count, deadline_at, max_charge_usd,
            cancel_requested, telemetry, purpose,
        )
        self._record_batch(stage, batch)
        return batch

    def _record_batch(self, stage, batch):
        listings = normalize_dataset(batch.items)
        summary = {
            "stage": stage, "rawCount": len(batch.items), "normalizedCount": len(listings),
            "costUsd": batch.apify_cost_usd, "estimated": batch.apify_cost_estimated,
            "rawKeys": dict(Counter(key for row in batch.items for key in row)),
            "rawStock": dict(Counter(str(row.get("stock"))[:120] for row in batch.items)),
            "rawStatus": dict(Counter(str(row.get("status"))[:120] for row in batch.items)),
            "riskCounts": dict(Counter(risk.code for item in listings for risk in evaluate_rules(item))),
            "coverage": {key: sum(bool(value(item)) for item in listings) for key, value in {
                "description": lambda x:x.description, "photos": lambda x:x.images,
                "sellerId": lambda x:x.seller.identity_hash, "location": lambda x:x.location,
                "delivery": lambda x:x.delivery, "kit": lambda x:x.completeness,
                "repair": lambda x:x.repair_status, "parts": lambda x:x.parts_status,
                "battery": lambda x:x.battery_health_percent,
            }.items()},
        }
        write_json(self.output / f"{stage}-summary.json", summary)
        write_json(self.output / f"{stage}-normalized.json", [asdict(item) for item in listings])
        # Keep input facts needed to reproduce mapping failures, not phone/profile data.
        permitted = {"id", "avitoId", "title", "url", "price", "currency", "status", "stock",
                     "availability", "description", "images", "imageCount", "parameters", "location",
                     "address", "delivery", "deliveryAvailable", "scrapedAt", "collectedAt"}
        write_json(self.output / f"{stage}-facts.json", [
            {key:value for key,value in row.items() if key in permitted} for row in batch.items])
        print("PILOT_DIAGNOSTICS " + json.dumps(summary, ensure_ascii=False), flush=True)


def _positive_amount(value: str) -> float:
    try:
        amount = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Укажите положительную конечную сумму") from None
    if not math.isfinite(amount) or amount <= 0:
        raise argparse.ArgumentTypeError("Укажите положительную конечную сумму")
    return amount


def parse_options(argv=None):
    """Parse the pilot request without reading credentials or starting services."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Actually use configured paid APIs")
    parser.add_argument("--query", default="iPhone 13")
    parser.add_argument("--location", default="Москва")
    parser.add_argument("--pickup-only", action="store_true", help="Only local pickup; reject mandatory delivery")
    parser.add_argument("--category", default="phones")
    parser.add_argument("--storage", default=None, help="Defaults to 128 GB for phones; empty for other categories")
    parser.add_argument("--sim", default=None, help="Defaults to SIM + eSIM for phones; empty for other categories")
    parser.add_argument("--max-results", type=int, default=30)
    parser.add_argument("--price-max", type=int, default=None)
    parser.add_argument("--price-min", type=int)
    parser.add_argument("--condition", default="Отличное")
    parser.add_argument("--model", help="Override the configured AI model for this pilot only")
    parser.add_argument("--apify-cap-usd", type=_positive_amount, help="Lower the pilot Apify ceiling; never raise configured limits")
    parser.add_argument("--ai-budget-rub", type=_positive_amount, help="Lower the configured AI budget for this pilot")
    parser.add_argument("--collect-only", action="store_true", help="Collect and inspect locally; make no AI calls")
    parser.add_argument("--resume-market", help="Reuse a recovered paid market JSON for the same request")
    return parser.parse_args(argv)


def build_request(args) -> SearchRequest:
    phones = args.category == "phones"
    return SearchRequest(
        args.query, location=args.location, category=args.category, max_results=args.max_results,
        desired_results=3, price_min=args.price_min, price_max=args.price_max,
        required_storage=args.storage if args.storage is not None else ("128 GB" if phones else ""),
        required_sim=args.sim if args.sim is not None else ("SIM + eSIM" if phones else ""),
        required_condition=args.condition,
        pickup_only=args.pickup_only,
    )


def apply_config_overrides(config: ServiceConfig, args) -> ServiceConfig:
    """Pure per-run configuration; tokens, endpoints and persisted settings stay intact."""
    return replace(
        config,
        ai_model=args.model if args.model is not None else config.ai_model,
        apify_max_charge_usd=min(config.apify_max_charge_usd, 2.0,
                                 args.apify_cap_usd if args.apify_cap_usd is not None else 2.0),
        ai_max_cost_rub=min(config.ai_max_cost_rub,
                            args.ai_budget_rub if args.ai_budget_rub is not None else config.ai_max_cost_rub),
    )


def collection_only_allowances(config: ServiceConfig) -> tuple[float, float]:
    total = min(config.apify_max_charge_usd, 0.273)
    market = min(0.173, total * 21 / 32)
    return market, max(0.0, total - market)


def main(argv=None):
    args = parse_options(argv)
    request = build_request(args)
    from avito_service.config import expanded_review_config
    from avito_service.jobs import WORKER_DEADLINE_SECONDS
    config = apply_config_overrides(expanded_review_config(load_config()), args)
    print(json.dumps({"ready":config.readiness(), "apifyCapUsd":config.apify_max_charge_usd,
                      "aiBudgetRub":config.ai_max_cost_rub, "model":config.ai_model,
                      "reportBudgetRub":config.report_max_cost_rub, "live":args.live,
                      "request":asdict(request)}, ensure_ascii=False), flush=True)
    if not args.live:
        return
    if not all(config.readiness().values()):
        raise SystemExit("Pilot configuration is incomplete")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "runtime" / "avito_pilot" / stamp
    output.mkdir(parents=True, exist_ok=False)
    trace = None if args.collect_only else SearchTrace(
        f"pilot-{stamp}", request,
        store=PilotTraceStore(ROOT / "runtime" / "avito_pilot_traces"),
        usd_rub_rate=config.usd_rub_rate,
    )
    write_json(output / "request.json", asdict(request))
    service = build_service(config)
    service.provider = PilotProvider(service.provider.config, output, args.resume_market,
                                     spending_guard=service.provider.spending_guard)
    started = time.monotonic()
    print("PILOT_OUTPUT " + str(output), flush=True)
    try:
        if args.collect_only:
            deadline = time.monotonic() + 180
            market_allowance, candidate_allowance = collection_only_allowances(config)
            market = service.provider.collect_market(replace(request, max_results=min(21, request.max_results)),
                deadline_at=deadline, max_charge_usd=market_allowance)
            candidates = service.provider.collect(replace(request, max_results=min(11, request.max_results)),
                deadline_at=deadline, max_charge_usd=candidate_allowance)
            summary = {"mode":"collect-only", "market":len(market.items),
                       "candidates":len(candidates.items), "aiCalls":0,
                       "accountedApifyCostUsd":market.apify_cost_usd + candidates.apify_cost_usd}
            write_json(output / "collection-only.json", summary)
            print("PILOT_RESULT " + json.dumps(summary, ensure_ascii=False), flush=True)
            return
        assert trace is not None
        def progress(stage, message, percent):
            trace.stage(stage)
            print(f"[{percent}% {stage}] {message}", flush=True)

        progress.trace = trace
        report = service.search(request, deadline_seconds=WORKER_DEADLINE_SECONDS,
                                progress=progress)
        public = report.public_dict()
        write_json(output / "report.json", public)
        audit = report.public_dict(include_admin=True)
        write_json(output / "audit.json", audit)
        write_json(output / "all-decisions.json", [item.public_dict() for item in report.recommendations])
        funnel = funnel_from_audit(audit)
        write_json(output / "funnel.json", funnel)
        print("PILOT_FUNNEL " + " → ".join(f"{key}={value}" for key, value in funnel.items()), flush=True)
        if funnel["CONFIRMED"] == 0:
            closest = closest_failures(audit)
            write_json(output / "closest-failures.json", closest)
            print("PILOT_CLOSEST " + json.dumps(closest, ensure_ascii=False), flush=True)
        trace.finish_report(report, usd_rub_rate=config.usd_rub_rate)
        trace.worker_stopped(result_published=True)
        print("PILOT_RESULT " + json.dumps({"visible":len(public["recommendations"]),
            "costs":report.costs.admin_dict(), "audit":report.pipeline.admin_dict(),
            "warnings":report.admin_warnings, "elapsed":round(time.monotonic()-started,2)}, ensure_ascii=False), flush=True)
    except Exception as exc:
        if trace is not None:
            trace.finish_error(getattr(exc, "code", "PILOT_ERROR"), type(exc).__name__)
            trace.worker_stopped(result_published=False)
        error = {"type":type(exc).__name__, "code":getattr(exc,"code",""),
                 "message":str(exc), "elapsed":round(time.monotonic()-started,2)}
        if getattr(exc, "apify_context", None):
            error["apify_context"] = exc.apify_context
        write_json(output / "failure.json", error)
        print("PILOT_FAILED " + json.dumps(error, ensure_ascii=False), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
