"""/start, меню, прайс, «Позвать администратора». Эти кнопки работают из любого состояния."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from ..config import Settings
from ..db import Database
from ..keyboards import BTN_CANCEL, BTN_HUMAN, BTN_PRICES, esc, main_menu
from ..llm import price_list_text
from .common import notify_admins, now_local, user_label

router = Router(name="start")

WELCOME = (
    "Здравствуйте! Я бот-помощник салона «{salon}». Я — ИИ, не человек.\n\n"
    "Могу рассказать про услуги и цены и записать вас к мастеру. "
    "Если нужен живой человек — в любой момент нажмите «{human}», и администратор ответит здесь же."
)


@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await m.answer(WELCOME.format(salon=settings.salon_name, human=BTN_HUMAN), reply_markup=main_menu())


@router.message(Command("id"))
async def cmd_id(m: Message) -> None:
    """Нужна один раз: узнать свой ID, чтобы вписать в ADMIN_IDS."""
    await m.answer(f"Ваш Telegram ID: <code>{m.from_user.id}</code>")


@router.message(Command("cancel"))
@router.message(F.text == BTN_CANCEL)
async def cmd_cancel(m: Message, state: FSMContext) -> None:
    await state.clear()
    await m.answer("Хорошо, отменил. Чем ещё помочь?", reply_markup=main_menu())


@router.message(F.text == BTN_PRICES)
async def prices(m: Message, db: Database) -> None:
    await m.answer("Наши услуги:\n" + esc(price_list_text(db.services())), reply_markup=main_menu())


@router.message(F.text == BTN_HUMAN)
async def call_human(m: Message, state: FSMContext, bot: Bot, db: Database, settings: Settings) -> None:
    await state.clear()
    now = now_local(settings)
    db.create_handoff(m.from_user.id, m.from_user.username, "Нажал «Позвать администратора»", now)
    await notify_admins(
        bot, settings,
        f"🙋 {user_label(m.from_user)} просит администратора.\n"
        f"Ответить: <code>/reply {m.from_user.id} текст</code>, закрыть: <code>/done {m.from_user.id}</code>",
    )
    await m.answer(
        "Передал администратору — она ответит здесь же. "
        "Можете сразу написать вопрос, я перешлю.",
        reply_markup=main_menu(),
    )
