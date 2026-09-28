"""Настройки из .env. Всё, что меняется от салона к салону, лежит здесь, а не в коде."""
from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int]          # whitelist Telegram ID администраторов
    anthropic_api_key: str | None      # если пусто — все свободные вопросы уходят администратору
    llm_model: str
    db_path: str
    tz: ZoneInfo
    salon_name: str
    work_start_hour: int               # 10
    work_end_hour: int                 # 20
    slot_step_min: int                 # шаг сетки слотов
    horizon_days: int                  # на сколько дней вперёд открыта запись
    reminder_hours_before: int         # «напоминать за день»
    cancel_min_hours: int              # клиент может отменить не позднее чем за N часов


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def load_settings() -> Settings:
    load_dotenv()
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN не задан. Скопируйте .env.example в .env и заполните.")
    raw_admins = os.environ.get("ADMIN_IDS", "").replace(";", ",")
    admin_ids = frozenset(int(x) for x in raw_admins.split(",") if x.strip().lstrip("-").isdigit())
    return Settings(
        bot_token=token,
        admin_ids=admin_ids,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", "").strip() or None,
        llm_model=os.environ.get("LLM_MODEL", "claude-haiku-4-5-20251001"),
        db_path=os.environ.get("DB_PATH", "salon.db"),
        tz=ZoneInfo(os.environ.get("SALON_TZ", "Asia/Almaty")),
        salon_name=os.environ.get("SALON_NAME", "Лилия"),
        work_start_hour=_int("WORK_START_HOUR", 10),
        work_end_hour=_int("WORK_END_HOUR", 20),
        slot_step_min=_int("SLOT_STEP_MIN", 30),
        horizon_days=_int("HORIZON_DAYS", 14),
        reminder_hours_before=_int("REMINDER_HOURS_BEFORE", 24),
        cancel_min_hours=_int("CANCEL_MIN_HOURS", 2),
    )
