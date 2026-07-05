import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS: list[int]
    PROXY_URL: str = ""
    USE_PROXY: bool = False
    PROXY_FOR_HTTP: bool = True
    PROXY_FOR_BROWSER: bool = True
    BROWSER_PROVIDER: str = "local"
    BROWSERBASE_API_KEY: str = ""
    BROWSERBASE_PROJECT_ID: str = ""
    FETCH_TIMEOUT_SECONDS: int = 20
    FETCH_RETRIES: int = 2
    DOMAIN_RATE_LIMIT_SECONDS: int = 2
    ENABLE_BROWSER_FALLBACK: bool = True
    DB_PATH: str = "bot.db"
    SEARCH_RESULTS_PER_REQUEST: int = 15
    SEARCH_TIMEOUT_SECONDS: int = 8
    ENABLE_SEARCH_OZON: bool = True
    ENABLE_SEARCH_YANDEX_MARKET: bool = True
    ENABLE_SEARCH_WILDBERRIES: bool = True
    ENABLE_SEARCH_AVITO: bool = True
    ENABLE_SEARCH_DNS: bool = True
    ENABLE_SEARCH_MVIDEO: bool = True
    ENABLE_SEARCH_CITILINK: bool = True
    ENABLE_SEARCH_MEGAMARKET: bool = True
    ENABLE_SEARCH_GENERIC: bool = True
    SEARCHAPI_ENABLED: bool = False
    SEARCHAPI_API_KEY: str = ""
    SEARCHAPI_GL: str = "ru"
    SEARCHAPI_HL: str = "ru"
    SEARCHAPI_LOCATION: str = "Russia"
    SEARCHAPI_MAX_RESULTS: int = 5
    SEARCHAPI_TIMEOUT_SECONDS: int = 15
    ENABLE_PLAYWRIGHT_VERIFIER: bool = False
    PLAYWRIGHT_HEADLESS: bool = True
    PLAYWRIGHT_TIMEOUT_MS: int = 8000
    PLAYWRIGHT_MAX_CANDIDATES: int = 5
    AI_CARDS_ENABLED: bool = False
    AI_API_KEY: str = ""
    AI_BASE_URL: str = ""
    AI_MODEL: str = ""
    class Config:
        env_file = ".env"
        extra = "ignore"

    @classmethod
    def load(cls) -> "Settings":
        admin_ids_raw = os.getenv("ADMIN_IDS", "")
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        if not bot_token:
            raise RuntimeError("BOT_TOKEN пустой. Заполните BOT_TOKEN в .env.")

        try:
            admin_ids = [int(x.strip()) for x in admin_ids_raw.split(",") if x.strip()]
        except ValueError as exc:
            raise RuntimeError("ADMIN_IDS должен содержать Telegram ID через запятую.") from exc
        if not admin_ids:
            raise RuntimeError("ADMIN_IDS пустой. Укажите хотя бы один Telegram ID админа.")

        return cls(
            PROXY_URL=os.getenv("PROXY_URL", "").strip(),
            USE_PROXY=_env_bool("USE_PROXY", False),
            PROXY_FOR_HTTP=_env_bool("PROXY_FOR_HTTP", True),
            PROXY_FOR_BROWSER=_env_bool("PROXY_FOR_BROWSER", True),

            BROWSER_PROVIDER=os.getenv("BROWSER_PROVIDER", "local").strip(),
            BROWSERBASE_API_KEY=os.getenv("BROWSERBASE_API_KEY", "").strip(),
            BROWSERBASE_PROJECT_ID=os.getenv("BROWSERBASE_PROJECT_ID", "").strip(),

            FETCH_TIMEOUT_SECONDS=_env_int("FETCH_TIMEOUT_SECONDS", 20),
            FETCH_RETRIES=_env_int("FETCH_RETRIES", 2),
            DOMAIN_RATE_LIMIT_SECONDS=_env_int("DOMAIN_RATE_LIMIT_SECONDS", 2),
            ENABLE_BROWSER_FALLBACK=_env_bool("ENABLE_BROWSER_FALLBACK", True),
            BOT_TOKEN=bot_token,
            ADMIN_IDS=admin_ids,
            DB_PATH=os.getenv("DB_PATH", "bot.db"),
            SEARCH_RESULTS_PER_REQUEST=int(os.getenv("SEARCH_RESULTS_PER_REQUEST", "15")),
            SEARCH_TIMEOUT_SECONDS=_env_int("SEARCH_TIMEOUT_SECONDS", 8),
            ENABLE_SEARCH_OZON=_env_bool("ENABLE_SEARCH_OZON", True),
            ENABLE_SEARCH_YANDEX_MARKET=_env_bool("ENABLE_SEARCH_YANDEX_MARKET", True),
            ENABLE_SEARCH_WILDBERRIES=_env_bool("ENABLE_SEARCH_WILDBERRIES", True),
            ENABLE_SEARCH_AVITO=_env_bool("ENABLE_SEARCH_AVITO", True),
            ENABLE_SEARCH_DNS=_env_bool("ENABLE_SEARCH_DNS", True),
            ENABLE_SEARCH_MVIDEO=_env_bool("ENABLE_SEARCH_MVIDEO", True),
            ENABLE_SEARCH_CITILINK=_env_bool("ENABLE_SEARCH_CITILINK", True),
            ENABLE_SEARCH_MEGAMARKET=_env_bool("ENABLE_SEARCH_MEGAMARKET", True),
            ENABLE_SEARCH_GENERIC=_env_bool("ENABLE_SEARCH_GENERIC", True),
            SEARCHAPI_ENABLED=_env_bool("SEARCHAPI_ENABLED", False),
            SEARCHAPI_API_KEY=os.getenv("SEARCHAPI_API_KEY", "").strip(),
            SEARCHAPI_GL=os.getenv("SEARCHAPI_GL", "ru").strip(),
            SEARCHAPI_HL=os.getenv("SEARCHAPI_HL", "ru").strip(),
            SEARCHAPI_LOCATION=os.getenv("SEARCHAPI_LOCATION", "Russia").strip(),
            SEARCHAPI_MAX_RESULTS=_env_int("SEARCHAPI_MAX_RESULTS", 5),
            SEARCHAPI_TIMEOUT_SECONDS=_env_int("SEARCHAPI_TIMEOUT_SECONDS", 15),
            ENABLE_PLAYWRIGHT_VERIFIER=_env_bool("ENABLE_PLAYWRIGHT_VERIFIER", False),
            PLAYWRIGHT_HEADLESS=_env_bool("PLAYWRIGHT_HEADLESS", True),
            PLAYWRIGHT_TIMEOUT_MS=_env_int("PLAYWRIGHT_TIMEOUT_MS", 8000),
            PLAYWRIGHT_MAX_CANDIDATES=_env_int("PLAYWRIGHT_MAX_CANDIDATES", 5),
            AI_CARDS_ENABLED=_env_bool("AI_CARDS_ENABLED", False),
            AI_API_KEY=os.getenv("AI_API_KEY", "").strip(),
            AI_BASE_URL=os.getenv("AI_BASE_URL", "").strip(),
            AI_MODEL=os.getenv("AI_MODEL", "").strip(),
        )

settings = Settings.load()
