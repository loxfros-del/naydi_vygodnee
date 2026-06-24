"""Точка входа Telegram-бота «Найди выгоднее»."""
import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import settings
from app.db import init_db
from app.handlers import user, admin

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    # Инициализация БД
    init_db()
    logger.info("База данных инициализирована")

    # Бот и Dispatcher создаём один раз.
    # Роутеры нельзя подключать повторно при каждом retry.
    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(user.router)
    dp.include_router(admin.router)

    retry_delay = 5
    max_delay = 60

    try:
        while True:
            try:
                logger.info("Бот запущен")
                await dp.start_polling(bot)

            except (TelegramNetworkError, aiohttp.ClientError, ConnectionResetError, OSError) as e:
                logger.warning(
                    "Telegram network error: %s. Retry in %s seconds",
                    e,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)

            except (KeyboardInterrupt, SystemExit):
                logger.info("Бот остановлен вручную")
                break

            except Exception:
                logger.exception("Критическая ошибка бота")
                raise

            else:
                retry_delay = 5

    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())