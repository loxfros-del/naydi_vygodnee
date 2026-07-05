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
            AI_CARDS_ENABLED=_env_bool("AI_CARDS_ENABLED", False),
            AI_API_KEY=os.getenv("AI_API_KEY", "").strip(),
            AI_BASE_URL=os.getenv("AI_BASE_URL", "").strip(),
            AI_MODEL=os.getenv("AI_MODEL", "").strip(),
        )

settings = Settings.load()
