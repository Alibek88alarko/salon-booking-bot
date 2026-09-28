"""Напоминания.

Состояние живёт в таблице reminders (send_at / sent_at). Планировщик не хранит ничего:
раз в минуту спрашивает базу «что пора отправить» и отмечает отправленное.
После перезапуска первый же тик дошлёт всё, что просрочилось, пока бот лежал.
"""
from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramNotFound
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import Settings
from .db import Database, s2dt
from .keyboards import esc, fmt_dt, reminder_kb

log = logging.getLogger(__name__)


async def reminder_tick(bot: Bot, db: Database, settings: Settings) -> None:
    now = datetime.now(settings.tz).replace(tzinfo=None, second=0, microsecond=0)
    for r in db.due_reminders(now):
        if r["status"] != "active" or r["user_id"] is None:
            db.reminder_done(r["reminder_id"], now)  # запись отменена — напоминать нечего
            continue
        text = (
            f"Напоминаем: вы записаны в салон «{settings.salon_name}»\n"
            f"<b>{fmt_dt(s2dt(r['starts_at']))}</b> · {esc(r['service_name'])} · мастер {esc(r['master_name'])}\n"
            "Придёте?"
        )
        try:
            await bot.send_message(r["user_id"], text, reply_markup=reminder_kb(r["id"]))
            db.reminder_done(r["reminder_id"], now)
        except (TelegramForbiddenError, TelegramNotFound):
            log.info("Клиент %s заблокировал бота, напоминание #%s пропущено", r["user_id"], r["id"])
            db.reminder_done(r["reminder_id"], now)
        except Exception as e:  # сеть/лимиты: попробуем в следующий тик, не более 3 раз
            log.warning("Напоминание #%s не отправлено: %s", r["id"], e)
            db.reminder_failed(r["reminder_id"], now)


def setup_scheduler(bot: Bot, db: Database, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.tz)
    scheduler.add_job(
        reminder_tick, "interval", seconds=60, args=[bot, db, settings],
        max_instances=1, coalesce=True, next_run_time=datetime.now(settings.tz),
    )
    return scheduler
