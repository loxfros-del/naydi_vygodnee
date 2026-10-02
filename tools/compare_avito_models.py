"""Compare text-only AI decisions on saved normalized listings; offline by default.

Input: {"request": <SearchRequest fields>, "listings": [<NormalizedListing fields>]}.
A pilot's *-normalized.json array also works with its adjacent request.json.
Full description is required: public descriptionExcerpt is not sufficient evidence.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from avito_service.ai import MODEL_COST_RESERVE_RATES, OpenAICompatibleReviewer
from avito_service.config import ServiceConfig, load_config
from avito_service.errors import ExternalServiceError
from avito_service.models import NormalizedListing, SearchRequest, SellerSummary

ALIASES = {
    "qwen/qwen3.8-flash": "qwen3.8-flash",
    "openai/gpt-4.1-mini": "gpt-4.1-mini",
    "google/gemini-2.5-flash": "gemini-2.5-flash",
    "openai/gpt-5.6-sol": "gpt-5.6-sol",
}

# These failures need a provider/account change, not another listing. Keep the
# first attempt's unknown charge reserved, but do not repeat the failed route.
MODEL_STOP_CODES = frozenset({"AI_AUTH", "AI_QUOTA"})


class ComparisonBudgetExceeded(RuntimeError):
    # Separate from provider errors: review_text must not wrap this local stop
    # into AI_INVALID_RESPONSE or start a compatibility retry.
    code = "COMPARE_BUDGET_LIMIT"


def positive_budget(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0 < number <= 500:
        raise argparse.ArgumentTypeError("Бюджет должен быть конечным числом от 0 до 500 ₽.")
    return number


def listing_limit(value: str) -> int:
    count = int(value)
    if not 1 <= count <= 20:
        raise argparse.ArgumentTypeError("Допускается от 1 до 20 объявлений.")
    return count


def output_limit(value: str) -> int:
    count = int(value)
    if not 1 <= count <= 2400:
        raise argparse.ArgumentTypeError("Лимит ответа должен быть от 1 до 2400 токенов.")
    return count


def _read_json(path: Path):
    if path.suffix.lower() != ".json" or path.stat().st_size > 5_000_000:
        raise ValueError("Требуется JSON размером не более 5 МБ.")
    return json.loads(path.read_text(encoding="utf-8-sig"),
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Неконечное число в JSON.")))


def read_input(path: Path, limit: int) -> tuple[SearchRequest, tuple[NormalizedListing, ...]]:
    raw = _read_json(path)
    if isinstance(raw, list):
        request_data, listing_data = _read_json(path.with_name("request.json")), raw
    elif isinstance(raw, dict):
        request_data, listing_data = raw.get("request"), raw.get("listings")
    else:
        raise ValueError("Ожидается объект request/listings или массив normalized с соседним request.json.")
    if not isinstance(request_data, dict) or not isinstance(listing_data, list):
        raise ValueError("Отсутствуют request или listings.")
    request_args = {item.name: request_data[item.name] for item in fields(SearchRequest)
                    if item.name in request_data}
    attributes = request_args.get("attributes", ())
    request_args["attributes"] = tuple(attributes.items()) if isinstance(attributes, dict) else tuple(tuple(x) for x in attributes)
    request = SearchRequest(**request_args)
    if not isinstance(request.query, str) or len(request.query) > 500:
        raise ValueError("Слишком длинный запрос.")
    listings = []
    identifiers = set()
    for item in listing_data[:limit]:
        if not isinstance(item, dict) or not isinstance(item.get("description"), str) or not item["description"].strip():
            raise ValueError("Нужно полное description из normalized JSON, не descriptionExcerpt.")
        if len(item["description"]) > 200_000:
            raise ValueError("Описание превышает допустимый размер.")
        args = {field.name: item[field.name] for field in fields(NormalizedListing) if field.name in item}
        seller = args.get("seller", {})
        if not isinstance(seller, dict):
            raise ValueError("Некорректные поля продавца в normalized JSON.")
        args["seller"] = SellerSummary(**{field.name: seller[field.name] for field in fields(SellerSummary) if field.name in seller})
        # Keep complete text/parameters exactly as saved; this tool never loads photos.
        args["images"] = ()
        args["badges"] = tuple(args.get("badges", ()))
        listing = NormalizedListing(**args)
        if (not isinstance(listing.listing_id, str) or not listing.listing_id
                or len(listing.listing_id) > 80 or listing.listing_id in identifiers
                or not isinstance(listing.parameters, dict)):
            raise ValueError("Некорректный или повторяющийся listing_id.")
        identifiers.add(listing.listing_id)
        listings.append(listing)
    if not listings:
        raise ValueError("Нет объявлений для сравнения.")
    return request, tuple(listings)


@dataclass
class SharedBudget:
    limit: float
    accounted: float = 0.0
    reported: float = 0.0
    estimated: float = 0.0
    calls: int = 0

    def reserve(self, amount: float) -> None:
        if not math.isfinite(amount) or amount <= 0 or self.accounted + amount > self.limit + 1e-9:
            raise ComparisonBudgetExceeded("Общий резерв сравнения исчерпан.")
        self.accounted += amount
        self.estimated += amount
        self.calls += 1

    def settle(self, reserve: float, response: Mapping, reviewer: OpenAICompatibleReviewer) -> None:
        usage = response.get("usage")
        if not isinstance(usage, Mapping) or not reviewer._reported_stream_cost(usage):
            return  # Ambiguous spending remains reserved, including after errors.
        cost = float(usage["cost_rub"])
        if usage.get("cost_estimated") is True:
            return  # The pre-call reserve is more conservative than missing-usage estimates.
        self.accounted += cost - reserve
        self.estimated -= reserve
        self.reported += cost

    def summary(self) -> dict:
        return {"limitRub": self.limit, "accountedRub": round(self.accounted, 4),
                "reportedRub": round(self.reported, 4), "reservedUnconfirmedRub": round(self.estimated, 4),
                "requestCalls": self.calls, "overBudget": self.accounted > self.limit + 1e-9}


class BudgetedTextReviewer(OpenAICompatibleReviewer):
    # A separately budgeted CLI call performs one transport attempt. Production
    # keeps its existing HTTP429 retry; JSON compatibility retries reserve again.
    retry_rate_limits = False

    def __init__(self, config: ServiceConfig, budget: SharedBudget, max_output_tokens: int = 2400):
        super().__init__(config)
        self.budget = budget
        self.max_output_tokens = max_output_tokens

    def _request(self, payload: dict):
        payload = {**payload, "max_tokens": min(payload["max_tokens"], self.max_output_tokens)}
        reserve = self.estimate_payload_cost(payload)
        self.budget.reserve(reserve)
        response = super()._request(payload)
        if isinstance(response, Mapping):
            self.budget.settle(reserve, response, self)
            choices = response.get("choices", [])
            if isinstance(choices, list) and any(isinstance(choice, Mapping)
                    and choice.get("finish_reason") not in (None, "stop") for choice in choices):
                raise ExternalServiceError("Ответ модели обрезан или не завершён.", code="AI_INVALID_RESPONSE")
        return response


def safe_text(value, secrets: tuple[str, ...] = ()) -> str:
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return re.sub(r"[\x00-\x1f\x7f]", " ", text)[:500]


def review_summary(review, secrets: tuple[str, ...]) -> dict:
    return {"textAnalyzed": review.text_analyzed, "photosAnalyzed": False,
            "verdict": review.verdict.value, "matchesRequest": review.matches_request,
            "identifiedModel": safe_text(review.identified_model, secrets),
            "storage": safe_text(review.storage, secrets), "simVariant": safe_text(review.sim_variant, secrets),
            "condition": safe_text(review.condition, secrets),
            "mismatchReason": safe_text(review.mismatch_reason, secrets),
            "defects": [safe_text(x, secrets) for x in review.defects[:12]],
            "priceConditions": [safe_text(x, secrets) for x in review.price_conditions[:12]],
            "conflicts": [safe_text(x, secrets) for x in review.conflicts[:12]],
            "confidence": review.confidence}


def compare_live(plan: dict, request: SearchRequest, listings: tuple[NormalizedListing, ...],
                 config: ServiceConfig, save) -> None:
    parsed = urlsplit(config.ai_base_url)
    if parsed.scheme != "https" or parsed.hostname != "api.aitunnel.ru" or parsed.username or parsed.password:
        raise ValueError("Сравнение тарифов разрешено только через HTTPS api.aitunnel.ru.")
    if not config.ai_api_key:
        raise ValueError("Не настроен ключ AI.")
    budget = SharedBudget(plan["maxCostRub"])
    reviewers = {model: BudgetedTextReviewer(replace(config, ai_model=model, ai_max_cost_rub=budget.limit),
                                            budget, plan["maxOutputTokens"])
                 for model in plan["models"]}
    secrets = (config.ai_api_key, config.apify_token)
    plan["status"] = "running"
    plan["costs"] = budget.summary()
    save(plan)
    stopped_models: dict[str, tuple[str, str]] = {}
    # Interleave models per listing so an exhausted budget is explicitly partial.
    for listing in listings:
        for model, reviewer in reviewers.items():
            before = budget.summary()
            row = {"listingId": safe_text(listing.listing_id, secrets), "model": model}
            started = time.monotonic()
            if model in stopped_models:
                code, first_listing_id = stopped_models[model]
                row.update(status="skipped", errorCode=code,
                           skippedAfterListingId=first_listing_id)
            else:
                try:
                    row["review"] = review_summary(reviewer.review_text(listing, request), secrets)
                    row["status"] = "completed"
                except Exception as exc:
                    code = getattr(exc, "code", "COMPARE_ERROR")
                    row.update(status="error", errorCode=code if re.fullmatch(r"[A-Z_]{1,50}", str(code)) else "COMPARE_ERROR")
                    if row["errorCode"] in MODEL_STOP_CODES:
                        stopped_models[model] = (row["errorCode"], row["listingId"])
            after = budget.summary()
            row.update(elapsedSeconds=round(time.monotonic() - started, 3),
                       accountedCostRub=round(after["accountedRub"] - before["accountedRub"], 4),
                       reportedCostRub=round(after["reportedRub"] - before["reportedRub"], 4),
                       reservedUnconfirmedRub=round(after["reservedUnconfirmedRub"] - before["reservedUnconfirmedRub"], 4),
                       requestCalls=after["requestCalls"] - before["requestCalls"])
            plan["results"].append(row)
            plan["costs"] = after
            save(plan)
            if row.get("errorCode") == "COMPARE_BUDGET_LIMIT" or after["overBudget"]:
                plan["status"] = "budget_stopped"
                save(plan)
                return
    plan["status"] = "completed" if all(row["status"] == "completed" for row in plan["results"]) else "completed_with_errors"
    save(plan)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--model", action="append", required=True, choices=tuple(MODEL_COST_RESERVE_RATES))
    parser.add_argument("--max-listings", type=listing_limit, default=3)
    parser.add_argument("--max-cost-rub", type=positive_budget, default=10.0)
    parser.add_argument("--max-output-tokens", type=output_limit, default=2400,
                        help="Лимит ответа только для сравнения; обрезанный JSON считается ошибкой.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live", action="store_true", help="Явно разрешить платную текстовую проверку через AITUNNEL.")
    args = parser.parse_args(argv)
    models = list(dict.fromkeys(ALIASES.get(model, model) for model in args.model))
    if len(args.model) > 3:
        parser.error("Укажите не более трёх моделей.")
    try:
        request, listings = read_input(args.input, args.max_listings)
        base_config = ServiceConfig(ai_max_cost_rub=args.max_cost_rub)
        estimator = OpenAICompatibleReviewer(base_config)
        reservations = [{"model": model, "listingId": safe_text(listing.listing_id),
                         "reserveRub": estimator.estimate_payload_cost({
                             **estimator.build_text_payload(listing, model, request), "max_tokens": args.max_output_tokens})}
                        for listing in listings for model in models]
        fingerprint = hashlib.sha256(json.dumps({"request": estimator._search_evidence(request),
                         "listings": [estimator._listing_evidence(item) for item in listings]},
                         ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        plan = {"status": "offline_plan", "textOnly": True, "live": args.live,
                "models": models, "listingCount": len(listings), "inputSha256": fingerprint,
                "maxCostRub": args.max_cost_rub, "maxOutputTokens": args.max_output_tokens, "reservations": reservations,
                "plannedReserveRub": round(sum(item["reserveRub"] for item in reservations), 2),
                "costBasis": "estimated reserve; reported usage takes precedence; retries share budget",
                "results": []}
        output = args.output or ROOT / "runtime" / f"avito-model-comparison-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.json"
        if output.suffix.lower() != ".json" or output.resolve() == args.input.resolve():
            raise ValueError("Выход должен быть новым JSON-файлом, отличным от входного.")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            def save(value):
                text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
                handle.seek(0)
                handle.write(text + "\n")
                handle.truncate()
                handle.flush()
            save(plan)
            if args.live:
                # This is deliberately the first configuration read in the CLI.
                config = load_config()
                try:
                    compare_live(plan, request, listings, config, save)
                except Exception:
                    plan["status"] = "configuration_or_run_error"
                    save(plan)
                    raise
        print(json.dumps({"status": plan["status"], "output": str(output.resolve()),
                          "models": models, "listingCount": len(listings),
                          "costs": plan.get("costs"), "plannedReserveRub": plan["plannedReserveRub"],
                          "fitsBudgetWithoutRetries": plan["plannedReserveRub"] <= args.max_cost_rub}, ensure_ascii=False))
        return 0 if plan["status"] in {"offline_plan", "completed"} else 1
    except Exception:
        # Paths, seller evidence and provider errors can contain personal data or
        # credentials. Full exceptions are intentionally neither printed nor saved.
        print("COMPARE_INPUT_OR_CONFIG_ERROR: проверьте normalized JSON, новый путь отчёта и параметры.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
