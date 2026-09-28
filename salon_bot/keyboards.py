"""Клавиатуры и форматирование дат. Никакой логики."""
from __future__ import annotations

import html
from datetime import date, datetime, timedelta

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import Master, Service

BTN_BOOK = "💇 Записаться"
BTN_PRICES = "💰 Услуги и цены"
BTN_MY = "📋 Мои записи"
BTN_HUMAN = "🙋 Позвать администратора"
BTN_CANCEL = "❌ Отмена"

WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def fmt_day(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d:%d.%m}"


def fmt_dt(dt: datetime) -> str:
    return f"{fmt_day(dt.date())} {dt:%H:%M}"


def esc(s: str | None) -> str:
    return html.escape(s or "")


def _rows(n: int, per_row: int) -> list[int]:
    """Размеры рядов для n кнопок по per_row в ряд; «назад» потом отдельным рядом."""
    rows = [per_row] * (n // per_row)
    if n % per_row:
        rows.append(n % per_row)
    return rows or [1]


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK), KeyboardButton(text=BTN_PRICES)],
            [KeyboardButton(text=BTN_MY), KeyboardButton(text=BTN_HUMAN)],
        ],
        resize_keyboard=True,
    )


def phone_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить мой номер", request_contact=True)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def name_kb(suggest: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=suggest)], [KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def services_kb(services: list[Service]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for s in services:
        b.button(text=f"{s.name} · {s.price_text}", callback_data=f"svc:{s.id}")
    b.adjust(1)
    return b.as_markup()


def masters_kb(masters: list[Master]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for m in masters:
        b.button(text=m.name, callback_data=f"mst:{m.id}")
    b.button(text="◀️ Другая услуга", callback_data="back:svc")
    b.adjust(max(len(masters), 1), 1)
    return b.as_markup()


def days_kb(today: date, horizon_days: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i in range(horizon_days):
        d = today + timedelta(days=i)
        b.button(text=fmt_day(d), callback_data=f"day:{d.isoformat()}")
    b.button(text="◀️ Другой мастер", callback_data="back:mst")
    b.adjust(*_rows(horizon_days, 3), 1)
    return b.as_markup()


def slots_kb(slots: list[datetime]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for t in slots:
        b.button(text=f"{t:%H:%M}", callback_data=f"slot:{t:%Y-%m-%d %H:%M}")
    b.button(text="◀️ Другой день", callback_data="back:day")
    b.adjust(*_rows(len(slots), 4), 1)
    return b.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Записаться", callback_data="confirm:yes"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="confirm:no"),
        ]]
    )


def my_appts_kb(appt_ids: list[int]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i in appt_ids:
        b.button(text=f"Отменить запись #{i}", callback_data=f"cancel:{i}")
    b.adjust(1)
    return b.as_markup()


def reminder_kb(appt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Приду", callback_data=f"rem_ok:{appt_id}"),
            InlineKeyboardButton(text="❌ Не смогу, отменить", callback_data=f"cancel:{appt_id}"),
        ]]
    )
