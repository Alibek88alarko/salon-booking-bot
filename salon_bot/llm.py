"""Модель отвечает ТОЛЬКО по прайсу. Всё остальное — handoff администратору.

Что проверить:
  - в промпт уходит только прайс (из базы) и текст сообщения клиента;
  - телефон, имя, Telegram ID клиента в промпт не попадают — модуль их не получает
    вообще, а на случай, если клиент сам написал номер в сообщении, scrub() его маскирует;
  - любая ошибка/таймаут/непарсящийся ответ → handoff, а не выдумка.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from .db import Service

log = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{7,}\d")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

SYSTEM_TEMPLATE = """Ты — консультант салона красоты «{salon}». У тебя есть ТОЛЬКО прайс ниже.

Правила:
1. Отвечай только на вопросы об услугах и ценах из прайса. Коротко (1–3 предложения), дружелюбно, на языке клиента.
2. Не выдумывай цены, услуги, скидки, акции. Если чего-то нет в прайсе — это не твой вопрос.
3. Всё, что не про прайс — запись, перенос, отмена, адрес, график работы, мастера, жалобы, оплата,
   любые другие темы, — не отвечай: верни handoff=true и пустой answer.
4. Сомневаешься — handoff=true.
5. Никогда не проси у клиента телефон, адрес или другие личные данные.

Формат ответа — строго JSON без пояснений и без markdown:
{{"answer": "текст для клиента или пустая строка", "handoff": true или false}}

Прайс:
{price_list}"""


@dataclass(frozen=True)
class Reply:
    answer: str
    handoff: bool


def scrub(text: str) -> str:
    """Маскирует телефоны и email, если клиент сам написал их в вопросе.
    Цены вроде «7000 15000» не трогаем: телефон — это 10+ цифр."""
    def _mask(m: re.Match) -> str:
        return "[номер скрыт]" if sum(c.isdigit() for c in m.group()) >= 10 else m.group()
    text = _PHONE_RE.sub(_mask, text)
    return _EMAIL_RE.sub("[email скрыт]", text)


def price_list_text(services: list[Service]) -> str:
    return "\n".join(f"• {s.name} — {s.price_text}" for s in services)


class LLMClient:
    def __init__(self, api_key: str | None, model: str, salon_name: str, services: list[Service],
                 timeout_s: float = 8.0):
        self.model = model
        self.timeout_s = timeout_s
        self.system = SYSTEM_TEMPLATE.format(salon=salon_name, price_list=price_list_text(services))
        self._client = None
        if api_key:
            from anthropic import AsyncAnthropic  # импорт здесь, чтобы бот работал и без ключа

            self._client = AsyncAnthropic(api_key=api_key)
        else:
            log.warning("ANTHROPIC_API_KEY не задан: свободные вопросы уходят администратору")

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def answer(self, user_text: str) -> Reply:
        if not self._client:
            return Reply("", True)
        try:
            resp = await asyncio.wait_for(
                self._client.messages.create(
                    model=self.model,
                    max_tokens=300,
                    system=self.system,
                    messages=[{"role": "user", "content": scrub(user_text)[:1000]}],
                ),
                timeout=self.timeout_s,
            )
            raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            return _parse(raw)
        except Exception as e:  # таймаут, сеть, лимиты — что угодно → человек
            log.warning("LLM недоступна: %s", e)
            return Reply("", True)


def _parse(raw: str) -> Reply:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):]
    try:
        data = json.loads(raw)
        answer = str(data.get("answer", "")).strip()
        handoff = bool(data.get("handoff", True)) or not answer
        return Reply(answer, handoff)
    except (json.JSONDecodeError, AttributeError):
        return Reply("", True)
