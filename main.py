"""Точка входа: python main.py"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from salon_bot.config import load_settings
from salon_bot.db import Database
from salon_bot.handlers import admin, booking, chat, start
from salon_bot.llm import LLMClient
from salon_bot.reminders import setup_scheduler
from salon_bot.seed import seed_if_empty

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


async def main() -> None:
    settings = load_settings()
    if not settings.admin_ids:
        log.warning("ADMIN_IDS пуст: уведомления и админ-команды работать не будут. Узнать ID: /id")

    db = Database(settings.db_path)
    seed_if_empty(db)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())  # FSM в памяти: незавершённая запись при рестарте сбрасывается, это ок
    dp["settings"] = settings
    dp["db"] = db
    dp["llm"] = LLMClient(settings.anthropic_api_key, settings.llm_model, settings.salon_name, db.services())

    # Порядок важен: меню → сценарий записи → админ → свободный текст (ловит остальное).
    dp.include_routers(start.router, booking.router, admin.router, chat.router)

    scheduler = setup_scheduler(bot, db, settings)
    scheduler.start()

    await bot.set_my_commands([
        BotCommand(command="start", description="Начать"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
        BotCommand(command="id", description="Мой Telegram ID"),
    ])
    log.info("Бот запущен. Админы: %s", sorted(settings.admin_ids) or "нет")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
