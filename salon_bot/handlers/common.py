"""Общее для обработчиков: время салона, уведомление админов, форматирование записи."""
from __future__ import annotations

import logging
import re
from datetime import datetime

from aiogram import Bot
from aiogram.filters import Filter
from aiogram.types import Message, User

from ..config import Settings
from ..db import s2dt
from ..keyboards import esc, fmt_dt

log = logging.getLogger(__name__)


def now_local(settings: Settings) -> datetime:
    """Текущее время салона без tzinfo — в таком виде храним в базе."""
    return datetime.now(settings.tz).replace(tzinfo=None, second=0, microsecond=0)


class IsAdmin(Filter):
    """Whitelist по Telegram ID из ADMIN_IDS. Только ID, никаких имён и паролей."""

    async def __call__(self, message: Message, settings: Settings) -> bool:
        return message.from_user is not None and message.from_user.id in settings.admin_ids


async def notify_admins(bot: Bot, settings: Settings, text: str) -> None:
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:  # админ не нажал /start у бота и т.п. — не роняем клиента
            log.warning("Не удалось уведомить админа %s: %s", admin_id, e)


def user_label(u: User) -> str:
    name = esc(u.full_name)
    return f"{name} (@{u.username})" if u.username else f"{name} (id {u.id})"


def normalize_phone(raw: str) -> str | None:
    """Приводит к +7XXXXXXXXXX. Возвращает None, если это не казахстанский/российский номер."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    if len(digits) != 11 or not digits.startswith("7"):
        return None
    return "+" + digits


def fmt_appt(row, with_phone: bool) -> str:
    """Строка записи. with_phone=True только для администратора."""
    line = f"#{row['id']} {fmt_dt(s2dt(row['starts_at']))} · {esc(row['master_name'])} · {esc(row['service_name'])}"
    if row["user_id"] is not None:
        line += f" · {esc(row['client_name'])}"
        if with_phone:
            line += f" · {esc(row['phone'])}"
    return line
