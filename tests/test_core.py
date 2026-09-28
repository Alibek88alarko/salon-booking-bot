"""Проверки того, что смотрит ревьюер. Запуск: pytest -q"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

import pytest

from salon_bot.db import Database, SlotTaken, free_slots
from salon_bot.handlers.common import normalize_phone
from salon_bot.llm import _parse, scrub
from salon_bot.seed import seed_if_empty

NOW = datetime(2026, 9, 7, 9, 0)
DAY = date(2026, 9, 8)


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "t.db")
    seed_if_empty(Database(p))
    return p


def _book(db: Database, starts: datetime, minutes: int = 60, user: int = 1, phone="+77010000000"):
    return db.book(
        master_id=1, service_id=1, user_id=user, client_name="Тест", phone=phone,
        starts_at=starts, duration_min=minutes, reminder_at=starts - timedelta(days=1), now=NOW,
    )


def test_unique_index_exists(db_path):
    sql = Database(db_path).conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='ux_master_slot'"
    ).fetchone()[0]
    assert "UNIQUE" in sql and "master_id, starts_at" in sql and "status = 'active'" in sql


def test_race_two_clients_same_slot(db_path):
    """Два клиента жмут «Записаться» одновременно: ровно одна запись, второму — SlotTaken."""
    results: list[str] = []
    barrier = threading.Barrier(2)

    def worker(user_id: int):
        db = Database(db_path)  # у каждого «клиента» своё соединение, как у двух апдейтов в реальности
        barrier.wait()
        try:
            _book(db, datetime(2026, 9, 8, 12, 0), user=user_id)
            results.append("ok")
        except SlotTaken:
            results.append("taken")

    threads = [threading.Thread(target=worker, args=(i,)) for i in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok", "taken"]
    n = Database(db_path).conn.execute("SELECT COUNT(*) FROM appointments WHERE status='active'").fetchone()[0]
    assert n == 1


def test_overlap_rejected_but_adjacent_ok(db_path):
    db = Database(db_path)
    _book(db, datetime(2026, 9, 8, 10, 0), minutes=90)
    with pytest.raises(SlotTaken):
        _book(db, datetime(2026, 9, 8, 11, 0))      # 11:00 внутри 10:00–11:30
    with pytest.raises(SlotTaken):
        _book(db, datetime(2026, 9, 8, 9, 30))       # 9:30–10:30 задевает начало
    _book(db, datetime(2026, 9, 8, 11, 30))          # встык — можно


def test_cancelled_slot_is_free_again(db_path):
    db = Database(db_path)
    appt = _book(db, datetime(2026, 9, 8, 15, 0))
    db.cancel(appt)
    _book(db, datetime(2026, 9, 8, 15, 0), user=2)  # индекс частичный, отменённая не мешает


def test_phone_required_for_client_booking(db_path):
    db = Database(db_path)
    with pytest.raises(ValueError):
        _book(db, datetime(2026, 9, 8, 16, 0), phone=None)


def test_reminder_persisted_and_due(db_path):
    db = Database(db_path)
    appt = _book(db, datetime(2026, 9, 8, 14, 0))
    assert db.due_reminders(datetime(2026, 9, 7, 13, 59)) == []
    due = db.due_reminders(datetime(2026, 9, 7, 14, 0))
    assert [r["id"] for r in due] == [appt]
    # «перезапуск»: новое соединение видит то же самое
    assert len(Database(db_path).due_reminders(datetime(2026, 9, 7, 14, 0))) == 1
    db.reminder_done(due[0]["reminder_id"], datetime(2026, 9, 7, 14, 0))
    assert db.due_reminders(datetime(2026, 9, 7, 14, 0)) == []


def test_free_slots_respect_busy_and_hours():
    busy = [(datetime(2026, 9, 8, 13, 0), datetime(2026, 9, 8, 14, 0))]  # обед
    slots = free_slots(busy, DAY, 60, work_start_hour=10, work_end_hour=20, step_min=30, now=NOW)
    hhmm = [s.strftime("%H:%M") for s in slots]
    assert hhmm[0] == "10:00" and hhmm[-1] == "19:00"        # 19:00–20:00 последний
    assert "12:30" not in hhmm and "13:00" not in hhmm and "13:30" not in hhmm
    assert "14:00" in hhmm


def test_scrub_hides_phone_before_llm():
    assert "+7" not in scrub("Запишите меня, мой номер +7 701 123 45 67")
    assert "номер скрыт" in scrub("тел 87011234567")
    assert scrub("Сколько стоит стрижка?") == "Сколько стоит стрижка?"


def test_llm_parse_defaults_to_handoff():
    assert _parse("мусор").handoff
    assert _parse('{"answer": "", "handoff": false}').handoff   # пустой ответ = человек
    r = _parse('```json\n{"answer": "Стрижка — 6 000 ₸", "handoff": false}\n```')
    assert not r.handoff and "6 000" in r.answer


def test_normalize_phone():
    assert normalize_phone("8 (701) 123-45-67") == "+77011234567"
    assert normalize_phone("7011234567") == "+77011234567"
    assert normalize_phone("+7 701 123 45 67") == "+77011234567"
    assert normalize_phone("12345") is None


def test_scrub_keeps_prices():
    assert scrub("маникюр 7000 15000 это дорого?") == "маникюр 7000 15000 это дорого?"


def test_keyboard_rows():
    from salon_bot.keyboards import _rows
    assert _rows(14, 3) == [3, 3, 3, 3, 2] and _rows(8, 4) == [4, 4] and _rows(0, 4) == [1]
