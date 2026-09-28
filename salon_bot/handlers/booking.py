"""Запись: услуга → мастер → день → время → телефон → имя → подтверждение.
Плюс «Мои записи», отмена и кнопки из напоминания.
"""
from __future__ import annotations

from datetime import date, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..config import Settings
from ..db import Database, SlotTaken, free_slots, s2dt
from ..keyboards import (
    BTN_BOOK, BTN_MY, confirm_kb, days_kb, esc, fmt_day, fmt_dt, main_menu, masters_kb,
    my_appts_kb, name_kb, phone_kb, services_kb, slots_kb,
)
from .common import fmt_appt, normalize_phone, notify_admins, now_local, user_label

router = Router(name="booking")

MAX_ACTIVE_PER_USER = 2  # защита от спама записями


class Booking(StatesGroup):
    service = State()
    master = State()
    day = State()
    slot = State()
    phone = State()
    name = State()
    confirm = State()


# ---------- шаги ----------

@router.message(F.text == BTN_BOOK)
async def start_booking(m: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    await state.clear()
    if db.user_active_count(m.from_user.id, now_local(settings)) >= MAX_ACTIVE_PER_USER:
        await m.answer(
            f"У вас уже {MAX_ACTIVE_PER_USER} активные записи. Отмените одну в «{BTN_MY}», "
            "чтобы записаться ещё.", reply_markup=main_menu(),
        )
        return
    await state.set_state(Booking.service)
    await m.answer("Какая услуга?", reply_markup=services_kb(db.services()))


@router.callback_query(Booking.service, F.data.startswith("svc:"))
@router.callback_query(Booking.day, F.data == "back:mst")
async def pick_service(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    sid = int(cq.data.split(":")[1]) if cq.data.startswith("svc:") else data["service_id"]
    svc = db.service(sid)
    if not svc:
        await cq.answer("Услуга не найдена", show_alert=True)
        return
    await state.update_data(service_id=sid)
    await state.set_state(Booking.master)
    await cq.message.edit_text(
        f"Услуга: <b>{esc(svc.name)}</b> ({esc(svc.price_text)})\nК какому мастеру?",
        reply_markup=masters_kb(db.masters_for_service(sid)),
    )
    await cq.answer()


@router.callback_query(Booking.master, F.data == "back:svc")
async def back_to_services(cq: CallbackQuery, state: FSMContext, db: Database) -> None:
    await state.set_state(Booking.service)
    await cq.message.edit_text("Какая услуга?", reply_markup=services_kb(db.services()))
    await cq.answer()


@router.callback_query(Booking.master, F.data.startswith("mst:"))
@router.callback_query(Booking.slot, F.data == "back:day")
async def pick_master(cq: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    data = await state.get_data()
    mid = int(cq.data.split(":")[1]) if cq.data.startswith("mst:") else data["master_id"]
    master = db.master(mid)
    if not master:
        await cq.answer("Мастер не найден", show_alert=True)
        return
    await state.update_data(master_id=mid)
    await state.set_state(Booking.day)
    await cq.message.edit_text(
        f"Мастер: <b>{esc(master.name)}</b>\nНа какой день?",
        reply_markup=days_kb(now_local(settings).date(), settings.horizon_days),
    )
    await cq.answer()


@router.callback_query(Booking.day, F.data.startswith("day:"))
async def pick_day(cq: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    data = await state.get_data()
    day = date.fromisoformat(cq.data.split(":")[1])
    svc = db.service(data["service_id"])
    slots = free_slots(
        db.busy_intervals(data["master_id"], day), day, svc.duration_min,
        work_start_hour=settings.work_start_hour, work_end_hour=settings.work_end_hour,
        step_min=settings.slot_step_min, now=now_local(settings),
    )
    if not slots:
        await cq.answer("На этот день свободного времени нет, выберите другой", show_alert=True)
        return
    await state.update_data(day=day.isoformat())
    await state.set_state(Booking.slot)
    await cq.message.edit_text(
        f"{esc(svc.name)} у {esc(db.master(data['master_id']).name)}, {fmt_day(day)}\nВыберите время:",
        reply_markup=slots_kb(slots),
    )
    await cq.answer()


@router.callback_query(Booking.slot, F.data.startswith("slot:"))
async def pick_slot(cq: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(starts_at=cq.data.split(":", 1)[1])
    await state.set_state(Booking.phone)
    await cq.message.edit_text(f"Время: <b>{fmt_dt(s2dt(cq.data.split(':', 1)[1]))}</b>")
    await cq.message.answer(
        "Для записи нужен телефон — без него салон не записывает. "
        "Отправляя номер, вы соглашаетесь на обработку персональных данных для записи.\n"
        "Нажмите кнопку или напишите номер в формате +7 7XX XXX XX XX.",
        reply_markup=phone_kb(),
    )
    await cq.answer()


@router.message(Booking.phone, F.contact)
async def phone_from_contact(m: Message, state: FSMContext) -> None:
    if m.contact.user_id and m.contact.user_id != m.from_user.id:
        await m.answer("Нужен ваш собственный номер, а не чужой контакт.", reply_markup=phone_kb())
        return
    await _accept_phone(m, state, m.contact.phone_number)


@router.message(Booking.phone, F.text)
async def phone_from_text(m: Message, state: FSMContext) -> None:
    await _accept_phone(m, state, m.text)


async def _accept_phone(m: Message, state: FSMContext, raw: str) -> None:
    phone = normalize_phone(raw)
    if not phone:
        await m.answer("Не похоже на номер. Формат: +7 7XX XXX XX XX", reply_markup=phone_kb())
        return
    await state.update_data(phone=phone)
    await state.set_state(Booking.name)
    await m.answer("Как вас зовут?", reply_markup=name_kb(m.from_user.first_name or "Клиент"))


@router.message(Booking.name, F.text)
async def take_name(m: Message, state: FSMContext, db: Database) -> None:
    name = m.text.strip()[:60]
    if len(name) < 2:
        await m.answer("Напишите имя, пожалуйста.")
        return
    await state.update_data(name=name)
    data = await state.get_data()
    svc, master = db.service(data["service_id"]), db.master(data["master_id"])
    await state.set_state(Booking.confirm)
    await m.answer(
        "Проверьте запись:\n"
        f"• {esc(svc.name)} ({esc(svc.price_text)}, ~{svc.duration_min} мин)\n"
        f"• Мастер: {esc(master.name)}\n"
        f"• {fmt_dt(s2dt(data['starts_at']))}\n"
        f"• {esc(name)}, {esc(data['phone'])}",
        reply_markup=main_menu(),
    )
    await m.answer("Всё верно?", reply_markup=confirm_kb())


@router.callback_query(Booking.confirm, F.data == "confirm:no")
async def confirm_no(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cq.message.edit_text("Запись отменена. Начать заново — «Записаться» в меню.")
    await cq.answer()


@router.callback_query(Booking.confirm, F.data == "confirm:yes")
async def confirm_yes(cq: CallbackQuery, state: FSMContext, db: Database, settings: Settings, bot: Bot) -> None:
    data = await state.get_data()
    svc, master = db.service(data["service_id"]), db.master(data["master_id"])
    starts_at = s2dt(data["starts_at"])
    now = now_local(settings)
    reminder_at = starts_at - timedelta(hours=settings.reminder_hours_before)
    if reminder_at <= now:
        reminder_at = None  # записались меньше чем за сутки — напоминать некогда
    try:
        appt_id = db.book(
            master_id=master.id, service_id=svc.id, user_id=cq.from_user.id,
            client_name=data["name"], phone=data["phone"], starts_at=starts_at,
            duration_min=svc.duration_min, reminder_at=reminder_at, now=now,
        )
    except SlotTaken:
        # Кто-то успел раньше. Показываем актуальные слоты того же дня.
        day = starts_at.date()
        slots = free_slots(
            db.busy_intervals(master.id, day), day, svc.duration_min,
            work_start_hour=settings.work_start_hour, work_end_hour=settings.work_end_hour,
            step_min=settings.slot_step_min, now=now,
        )
        await state.set_state(Booking.slot)
        await cq.message.edit_text(
            "Это время только что заняли 😔 Выберите другое:" if slots else
            "Это время только что заняли, и на этот день больше ничего нет. Выберите другой день:",
            reply_markup=slots_kb(slots) if slots else days_kb(now.date(), settings.horizon_days),
        )
        if not slots:
            await state.set_state(Booking.day)
        await cq.answer()
        return

    await state.clear()
    remind_line = (
        f"\nНапомню за {settings.reminder_hours_before} ч." if reminder_at else ""
    )
    await cq.message.edit_text(
        f"✅ Записал! #{appt_id}\n{esc(svc.name)} у {esc(master.name)}, {fmt_dt(starts_at)}.{remind_line}\n"
        f"Посмотреть или отменить — «{BTN_MY}»."
    )
    await cq.answer()
    await notify_admins(
        bot, settings,
        f"🆕 Новая запись #{appt_id}: {fmt_dt(starts_at)} · {esc(master.name)} · {esc(svc.name)}\n"
        f"{esc(data['name'])} {esc(data['phone'])} · {user_label(cq.from_user)}",
    )


# ---------- мои записи / отмена ----------

@router.message(F.text == BTN_MY)
async def my_appointments(m: Message, db: Database, settings: Settings) -> None:
    rows = db.user_upcoming(m.from_user.id, now_local(settings))
    if not rows:
        await m.answer("Активных записей нет.", reply_markup=main_menu())
        return
    text = "Ваши записи:\n" + "\n".join(fmt_appt(r, with_phone=False) for r in rows)
    await m.answer(text, reply_markup=my_appts_kb([r["id"] for r in rows]))


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_appointment(cq: CallbackQuery, db: Database, settings: Settings, bot: Bot) -> None:
    appt_id = int(cq.data.split(":")[1])
    row = db.appointment(appt_id)
    if not row or row["user_id"] != cq.from_user.id:  # чужую запись отменить нельзя
        await cq.answer("Запись не найдена", show_alert=True)
        return
    if row["status"] != "active":
        await cq.answer("Эта запись уже отменена")
        return
    now = now_local(settings)
    if s2dt(row["starts_at"]) - now < timedelta(hours=settings.cancel_min_hours):
        await cq.answer(
            f"Отменить можно не позднее чем за {settings.cancel_min_hours} ч. Позвоните в салон.",
            show_alert=True,
        )
        return
    db.cancel(appt_id)
    await cq.message.edit_text(f"Запись #{appt_id} отменена. Будем ждать в другой раз!")
    await cq.answer()
    await notify_admins(bot, settings, f"❌ Клиент отменил запись:\n{fmt_appt(row, with_phone=True)}")


@router.callback_query(F.data.startswith("rem_ok:"))
async def reminder_ok(cq: CallbackQuery) -> None:
    await cq.message.edit_reply_markup(reply_markup=None)
    await cq.answer("Отлично, ждём вас!", show_alert=False)


@router.message(StateFilter(Booking))
async def booking_fallback(m: Message) -> None:
    await m.answer("Выберите вариант кнопками выше или нажмите /cancel.")
