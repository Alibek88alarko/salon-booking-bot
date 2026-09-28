"""Свободный текст. Подключается последним — ловит всё, что не поймали кнопки и команды.

Порядок:
  1. команды, которые не распознали выше (в т.ч. админские от не-админа) → подсказка меню;
  2. у клиента открыто обращение к администратору → просто пересылаем админам, модель не трогаем;
  3. иначе → модель отвечает по прайсу или отдаёт handoff.
В модель уходит только текст сообщения (см. llm.py), никаких данных о клиенте.
"""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from ..config import Settings
from ..db import Database
from ..keyboards import BTN_BOOK, esc, main_menu
from ..llm import LLMClient
from .common import notify_admins, now_local, user_label

router = Router(name="chat")


@router.message(F.text)
async def free_text(m: Message, bot: Bot, db: Database, settings: Settings, llm: LLMClient) -> None:
    text = m.text.strip()
    if text.startswith("/"):
        await m.answer("Такой команды нет. Пользуйтесь меню 👇", reply_markup=main_menu())
        return

    now = now_local(settings)
    admin_hint = f"Ответить: <code>/reply {m.from_user.id} текст</code>"

    if db.open_handoff(m.from_user.id, now):
        await notify_admins(bot, settings, f"💬 {user_label(m.from_user)}: {esc(text)}\n{admin_hint}")
        await m.answer("Передал администратору.")
        return

    await bot.send_chat_action(m.chat.id, "typing")
    reply = await llm.answer(text)
    if reply.handoff:
        db.create_handoff(m.from_user.id, m.from_user.username, text, now)
        await notify_admins(
            bot, settings,
            f"🙋 Вопрос от {user_label(m.from_user)}: {esc(text)}\n{admin_hint}, "
            f"закрыть: <code>/done {m.from_user.id}</code>",
        )
        await m.answer(
            "Это вопрос для администратора — передал ей, она ответит здесь же.\n"
            f"Записаться можно прямо сейчас — кнопка «{BTN_BOOK}» 👇",
            reply_markup=main_menu(),
        )
        return
    await m.answer(reply.answer, reply_markup=main_menu())


@router.message()
async def not_text(m: Message) -> None:
    await m.answer("Я понимаю только текст и кнопки меню 👇", reply_markup=main_menu())


@router.callback_query()
async def stale_button(cq: CallbackQuery) -> None:
    """Кнопка из старого сообщения после перезапуска/отмены сценария."""
    await cq.answer("Эта кнопка уже неактуальна. Начните заново: /start", show_alert=True)
