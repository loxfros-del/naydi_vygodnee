"""Safe configuration from the process and the existing local .env file."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV_PATH = ROOT / ".env"
REVIEW_PROFILE_PATH = ROOT / "avito_service" / "review_profile.json"
_PROFILE_NAMES = {
    "AVITO_AI_MODEL", "AVITO_AI_TEXT_BATCH_SIZE", "AVITO_AI_TEXT_MAX_LISTINGS",
    "AVITO_AI_MAX_COST_RUB", "AVITO_APIFY_MAX_CHARGE_USD", "AVITO_REPORT_MAX_COST_RUB",
}
_ALLOWED_ENV_NAMES = {
    "APIFY_TOKEN",
    "AVITO_APIFY_ACTOR_ID",
    "APIFY_API_URL",
    "AVITO_APIFY_MAX_CHARGE_USD",
    "AVITO_AI_API_KEY",
    "AVITO_AI_BASE_URL",
    "AVITO_AI_MODEL",
    "AVITO_AI_TIMEOUT_SECONDS",
    "AVITO_AI_CONCURRENCY",
    "AVITO_AI_TEXT_BATCH_SIZE",
    "AVITO_AI_TEXT_MAX_LISTINGS",
    "AVITO_AI_MAX_LISTINGS",
    "AVITO_AI_MAX_COST_RUB",
    "AVITO_AI_CACHE_TTL_SECONDS",
    "AVITO_REPORT_MAX_COST_RUB",
    "AVITO_USD_RUB_RATE",
    "AVITO_MIN_REPORT_PRICE_RUB",
    "AVITO_TARGET_COST_MULTIPLIER",
    "AVITO_MINIMUM_BARGAIN_PERCENT",
    "AVITO_MINIMUM_BARGAIN_RUB",
    "AVITO_COLLECTION_MAX_RAW_PAGES",
    "AVITO_COLLECTION_MAX_RAW_LISTINGS",
    "AVITO_COLLECTION_DUPLICATE_SATURATION_PAGES",
    "AVITO_LIVE_PILOT",
}


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    apify_token: str = ""
    apify_actor_id: str = "zen-studio/avito-listings-scraper"
    apify_api_url: str = "https://api.apify.com/v2"
    apify_max_charge_usd: float = 1.0
    apify_listing_cost_usd: float = 0.00499
    apify_detail_cost_usd: float = 0.00299
    apify_result_cost_usd: float = 0.00001
    apify_start_cost_usd: float = 0.005
    ai_api_key: str = ""
    ai_base_url: str = ""
    ai_model: str = ""
    ai_timeout_seconds: int = 45
    ai_concurrency: int = 3
    ai_text_batch_size: int = 10
    ai_text_max_listings: int = 20
    ai_max_listings: int = 10
    ai_max_cost_rub: float = 15.0
    ai_cache_ttl_seconds: int = 43_200
    report_max_cost_rub: float = 50.0
    usd_rub_rate: float = 100.0
    minimum_report_price_rub: int = 199
    target_cost_multiplier: float = 4.0
    minimum_bargain_percent: float = 10.0
    minimum_bargain_rub: int = 5_000
    collection_max_raw_pages: int = 10
    collection_max_raw_listings: int = 1_000
    collection_duplicate_saturation_pages: int = 2
    live_pilot: bool = False

    @property
    def apify_ready(self) -> bool:
        return bool(self.apify_token)

    @property
    def ai_ready(self) -> bool:
        return bool(self.ai_api_key and self.ai_base_url and self.ai_model)

    @property
    def apify_full_listing_cost_usd(self) -> float:
        return self.apify_listing_cost_usd + self.apify_detail_cost_usd + self.apify_result_cost_usd

    @property
    def effective_ai_budget_rub(self) -> float:
        minimum_apify_rub = self.apify_full_listing_cost_usd * max(self.usd_rub_rate, 1.0)
        return min(self.ai_max_cost_rub, max(0.0, self.report_max_cost_rub - minimum_apify_rub))

    @property
    def safe_apify_listing_limit(self) -> int:
        """Reserve the AI budget, then cap detailed Zen records by the report budget."""
        report_room_rub = max(0.0, self.report_max_cost_rub - self.effective_ai_budget_rub)
        report_room_usd = report_room_rub / max(self.usd_rub_rate, 1.0)
        allowed_usd = min(self.apify_max_charge_usd, report_room_usd)
        if allowed_usd <= 0 or self.apify_full_listing_cost_usd <= 0:
            return 1
        # Three starts: broad market, candidates, final URL refresh.
        available = max(0.0, allowed_usd - 3 * self.apify_start_cost_usd)
        return max(1, min(200, math.floor(available / self.apify_full_listing_cost_usd)))

    @property
    def effective_apify_max_charge_usd(self) -> float:
        estimated_limit = (self.safe_apify_listing_limit * self.apify_full_listing_cost_usd
                           + 3 * self.apify_start_cost_usd)
        return max(0.01, min(self.apify_max_charge_usd, estimated_limit))

    def readiness(self) -> dict[str, bool]:
        return {"apify": self.apify_ready, "ai": self.ai_ready}


def expanded_review_config(config: ServiceConfig) -> ServiceConfig:
    """Owner-authorized production AI allowance; explicit library configs stay usable."""
    apify_cap = min(config.apify_max_charge_usd, 2.0)
    # The shipped profile selects 50 RUB. An explicit lower process limit must
    # stay lower all the way through the production factory.
    ai_budget = config.ai_max_cost_rub
    return replace(
        config,
        apify_max_charge_usd=apify_cap,
        ai_max_cost_rub=ai_budget,
        ai_timeout_seconds=max(config.ai_timeout_seconds, 90),
        ai_concurrency=4,
        ai_text_max_listings=max(config.ai_text_max_listings, 60),
        ai_max_listings=20,
        report_max_cost_rub=config.report_max_cost_rub,
    )


def _read_local_env(path: Path = LOCAL_ENV_PATH) -> dict[str, str]:
    """Read only Avito-related values without mutating or logging the file."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in _ALLOWED_ENV_NAMES:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def _env_value(local: dict[str, str], name: str, default: str = "") -> str:
    return os.environ.get(name, local.get(name, default))


def _clean_url(value: str) -> str:
    """Accept a plain URL and repair an accidentally pasted Markdown link."""
    cleaned = value.strip()
    markdown = re.fullmatch(r"\[(https?://[^\]]+)\]\((https?://[^)]+)\)", cleaned)
    if markdown:
        cleaned = markdown.group(1)
    return cleaned.rstrip("/")


def _safe_int(local: dict[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(_env_value(local, name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _safe_float(
    local: dict[str, str],
    name: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        value = float(_env_value(local, name, str(default)))
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _safe_bool(local: dict[str, str], name: str, default: bool = False) -> bool:
    raw = _env_value(local, name, "1" if default else "0").strip().casefold()
    return raw in {"1", "true", "yes", "on", "да"}


def load_config() -> ServiceConfig:
    local = _read_local_env()
    # The public owner-selected profile replaces stale non-secret .env choices.
    # Process environment still wins, including an explicit provider/model switch.
    if os.environ.get("AVITO_REVIEW_PROFILE", "").casefold() != "off":
        local = _apply_review_profile(local)
    return ServiceConfig(
        apify_token=_env_value(local, "APIFY_TOKEN").strip(),
        apify_actor_id=_env_value(
            local, "AVITO_APIFY_ACTOR_ID", "zen-studio/avito-listings-scraper"
        ).strip(),
        apify_api_url=_clean_url(_env_value(local, "APIFY_API_URL", "https://api.apify.com/v2")),
        apify_max_charge_usd=_safe_float(local, "AVITO_APIFY_MAX_CHARGE_USD", 1.0, 0.1, 10.0),
        ai_api_key=_env_value(local, "AVITO_AI_API_KEY").strip(),
        ai_base_url=_clean_url(_env_value(local, "AVITO_AI_BASE_URL")),
        ai_model=_env_value(local, "AVITO_AI_MODEL").strip(),
        ai_timeout_seconds=_safe_int(local, "AVITO_AI_TIMEOUT_SECONDS", 45, 8, 60),
        ai_concurrency=_safe_int(local, "AVITO_AI_CONCURRENCY", 3, 1, 4),
        ai_text_batch_size=_safe_int(local, "AVITO_AI_TEXT_BATCH_SIZE", 10, 2, 10),
        ai_text_max_listings=_safe_int(local, "AVITO_AI_TEXT_MAX_LISTINGS", 20, 10, 200),
        ai_max_listings=_safe_int(local, "AVITO_AI_MAX_LISTINGS", 10, 1, 20),
        ai_max_cost_rub=_safe_float(local, "AVITO_AI_MAX_COST_RUB", 15.0, 1.0, 500.0),
        ai_cache_ttl_seconds=_safe_int(
            local, "AVITO_AI_CACHE_TTL_SECONDS", 43_200, 60, 86_400
        ),
        report_max_cost_rub=_safe_float(
            local, "AVITO_REPORT_MAX_COST_RUB", 50.0, 10.0, 2_000.0
        ),
        usd_rub_rate=_safe_float(local, "AVITO_USD_RUB_RATE", 100.0, 50.0, 300.0),
        minimum_report_price_rub=_safe_int(
            local, "AVITO_MIN_REPORT_PRICE_RUB", 199, 0, 100_000
        ),
        target_cost_multiplier=_safe_float(
            local, "AVITO_TARGET_COST_MULTIPLIER", 4.0, 1.0, 20.0
        ),
        minimum_bargain_percent=_safe_float(
            local, "AVITO_MINIMUM_BARGAIN_PERCENT", 10.0, 0.0, 100.0
        ),
        minimum_bargain_rub=_safe_int(
            local, "AVITO_MINIMUM_BARGAIN_RUB", 5_000, 0, 10_000_000
        ),
        collection_max_raw_pages=_safe_int(
            local, "AVITO_COLLECTION_MAX_RAW_PAGES", 10, 1, 50
        ),
        collection_max_raw_listings=_safe_int(
            local, "AVITO_COLLECTION_MAX_RAW_LISTINGS", 1_000, 1, 5_000
        ),
        collection_duplicate_saturation_pages=_safe_int(
            local, "AVITO_COLLECTION_DUPLICATE_SATURATION_PAGES", 2, 1, 10
        ),
        live_pilot=_safe_bool(local, "AVITO_LIVE_PILOT"),
    )


def _apply_review_profile(local: dict[str, str]) -> dict[str, str]:
    """Load a restricted, non-secret profile only for its declared provider."""
    try:
        profile = json.loads(REVIEW_PROFILE_PATH.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return local
    except (OSError, ValueError) as exc:
        raise ValueError("Не удалось прочитать avito_service/review_profile.json") from exc
    if (not isinstance(profile, dict) or set(profile) != {"schema", "provider", "settings"}
            or profile["schema"] != 1 or profile["provider"] != "aitunnel"
            or not isinstance(profile["settings"], dict)):
        raise ValueError("Некорректный профиль Avito")
    settings = profile["settings"]
    if set(settings) - _PROFILE_NAMES or any(not isinstance(v, str) for v in settings.values()):
        raise ValueError("Профиль Avito содержит недопустимые настройки")
    endpoint = _clean_url(_env_value(local, "AVITO_AI_BASE_URL"))
    if urlsplit(endpoint).hostname != "api.aitunnel.ru":
        return local
    return {**local, **settings}
