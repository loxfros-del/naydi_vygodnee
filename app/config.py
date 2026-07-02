import os
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS: list[int]
    DB_PATH: str = "bot.db"
    SEARCH_RESULTS_PER_REQUEST: int = 15
    class Config:
        env_file = ".env"

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
        )

settings = Settings.load()
