"""Команды администратора. Весь роутер закрыт фильтром IsAdmin (whitelist Telegram ID).
Не-админ эти команды просто не видит: сообщение уходит дальше, в обычный чат.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import Settings
from ..db import Database, SlotTaken
from ..keyboards import esc, fmt_day, fmt_dt
from .common import IsAdmin, fmt_appt, now_local

router = Router(name="admin")
router.message.filter(IsAdmin())

HELP = """Команды администратора:
/today — записи на сегодня
/tomorrow — на завтра
/week — на 7 дней
/cancel_appt ID — отменить запись
/block Мастер ГГГГ-ММ-ДД ЧЧ:ММ минут — обед/перерыв, например <code>/block Айгуль 2026-09-08 13:00 60</code>
/reply USER_ID текст — ответить клиенту
/done USER_ID — закрыть обращение, бот снова отвечает сам
/stats — цифры за 30 дней"""


@router.message(Command("admin", "help"))
async def admin_help(m: Message) -> None:
    await m.answer(HELP)


async def _list(m: Message, db: Database, settings: Settings, days: int, title: str, offset: int = 0) -> None:
    start = datetime.combine(now_local(settings).date() + timedelta(days=offset), time.min)
    rows = db.appointments_between(start, start + timedelta(days=days))
    if not rows:
        await m.answer(f"{title}: записей нет.")
        return
    lines, cur_day = [f"<b>{title}</b>"], None
    for r in rows:
        d = r["starts_at"][:10]
        if d != cur_day:
            cur_day = d
            lines.append(f"\n<u>{fmt_day(datetime.fromisoformat(d).date())}</u>")
        lines.append(fmt_appt(r, with_phone=True))
    await m.answer("\n".join(lines))


@router.message(Command("today"))
async def today(m: Message, db: Database, settings: Settings) -> None:
    await _list(m, db, settings, 1, "Сегодня")


@router.message(Command("tomorrow"))
async def tomorrow(m: Message, db: Database, settings: Settings) -> None:
    await _list(m, db, settings, 1, "Завтра", offset=1)


@router.message(Command("week"))
async def week(m: Message, db: Database, settings: Settings) -> None:
    await _list(m, db, settings, 7, "Ближайшие 7 дней")


@router.message(Command("cancel_appt"))
async def cancel_appt(m: Message, command: CommandObject, db: Database, bot: Bot) -> None:
    if not (command.args or "").strip().isdigit():
        await m.answer("Использование: /cancel_appt ID")
        return
    appt_id = int(command.args)
    row = db.appointment(appt_id)
    if not row or not db.cancel(appt_id):
        await m.answer("Активной записи с таким ID нет.")
        return
    await m.answer(f"Отменена: {fmt_appt(row, with_phone=True)}")
    if row["user_id"]:
        try:
            await bot.send_message(
                row["user_id"],
                f"Ваша запись #{appt_id} ({fmt_dt(datetime.strptime(row['starts_at'], '%Y-%m-%d %H:%M'))}) "
                "отменена салоном. Администратор свяжется с вами.",
            )
        except Exception:
            pass


@router.message(Command("block"))
async def block(m: Message, command: CommandObject, db: Database, settings: Settings) -> None:
    """Обед/перерыв мастера. Занимает слот так же, как запись, — через book()."""
    parts = (command.args or "").split()
    if len(parts) != 4:
        await m.answer("Использование: /block Мастер ГГГГ-ММ-ДД ЧЧ:ММ минут")
        return
    master = db.master_by_name(parts[0])
    if not master:
        await m.answer("Мастер не найден: " + ", ".join(x.name for x in db.masters()))
        return
    try:
        starts_at = datetime.strptime(f"{parts[1]} {parts[2]}", "%Y-%m-%d %H:%M")
        minutes = int(parts[3])
    except ValueError:
        await m.answer("Дата/время в формате ГГГГ-ММ-ДД ЧЧ:ММ, минуты — число.")
        return
    try:
        appt_id = db.book(
            master_id=master.id, service_id=None, user_id=None, client_name="Перерыв",
            phone=None, starts_at=starts_at, duration_min=minutes, reminder_at=None,
            now=now_local(settings),
        )
    except SlotTaken:
        await m.answer("В это время у мастера уже есть запись.")
        return
    await m.answer(f"Перерыв #{appt_id}: {esc(master.name)}, {fmt_dt(starts_at)}, {minutes} мин.")


@router.message(Command("reply"))
async def reply(m: Message, command: CommandObject, bot: Bot) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[0].lstrip("-").isdigit():
        await m.answer("Использование: /reply USER_ID текст")
        return
    try:
        await bot.send_message(int(parts[0]), f"💬 Администратор: {esc(parts[1])}")
        await m.answer("Отправлено.")
    except Exception as e:
        await m.answer(f"Не доставлено: {e}")


@router.message(Command("done"))
async def done(m: Message, command: CommandObject, db: Database) -> None:
    if not (command.args or "").strip().lstrip("-").isdigit():
        await m.answer("Использование: /done USER_ID")
        return
    n = db.close_handoffs(int(command.args))
    await m.answer("Обращение закрыто, бот снова отвечает сам." if n else "Открытых обращений нет.")


@router.message(Command("stats"))
async def stats(m: Message, db: Database, settings: Settings) -> None:
    s = db.stats(now_local(settings) - timedelta(days=30))
    await m.answer(
        "За 30 дней:\n"
        f"• <b>Записей через бота: {s['booked']}</b>  ← главная цифра\n"
        f"• из них отменено: {s['cancelled']}\n"
        f"• напоминаний отправлено: {s['reminders_sent']}\n"
        f"• обращений к администратору: {s['handoffs']}"
    )
