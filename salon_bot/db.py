"""Хранилище. Синхронный sqlite3: запросы короткие, для одного процесса этого достаточно.

Ключевые места для проверки:
  - ux_master_slot   — уникальный индекс «мастер + время начала» среди активных записей.
  - Database.book()  — BEGIN IMMEDIATE + проверка пересечений + INSERT в одной транзакции.
                       Второй параллельный book() на тот же слот ждёт блокировку, затем
                       получает SlotTaken. Даже если проверка пересечений пропустит —
                       индекс не даст вставить дубль.
  - таблица reminders — напоминания лежат в базе; планировщик только опрашивает её,
                       поэтому перезапуск процесса ничего не теряет.

Время храним как naive-строки 'YYYY-MM-DD HH:MM' в часовом поясе салона.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

FMT = "%Y-%m-%d %H:%M"


def dt2s(d: datetime) -> str:
    return d.strftime(FMT)


def s2dt(s: str) -> datetime:
    return datetime.strptime(s, FMT)


class SlotTaken(Exception):
    """Слот занят: гонка двух клиентов или пересечение с другой записью/перерывом."""


@dataclass(frozen=True)
class Master:
    id: int
    name: str


@dataclass(frozen=True)
class Service:
    id: int
    name: str
    price_text: str
    duration_min: int


SCHEMA = """
CREATE TABLE IF NOT EXISTS masters (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS services (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    price_text   TEXT NOT NULL,
    duration_min INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS master_services (
    master_id  INTEGER NOT NULL REFERENCES masters(id),
    service_id INTEGER NOT NULL REFERENCES services(id),
    PRIMARY KEY (master_id, service_id)
);
CREATE TABLE IF NOT EXISTS appointments (
    id          INTEGER PRIMARY KEY,
    master_id   INTEGER NOT NULL REFERENCES masters(id),
    service_id  INTEGER REFERENCES services(id),   -- NULL = перерыв/обед мастера
    user_id     INTEGER,                            -- Telegram ID клиента, NULL для перерывов
    client_name TEXT NOT NULL,
    phone       TEXT,                               -- обязателен для клиентских записей (см. book())
    starts_at   TEXT NOT NULL,
    ends_at     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',     -- active | cancelled
    created_at  TEXT NOT NULL
);
-- Главная защита от двойной записи: у одного мастера не может быть двух активных
-- записей с одним временем начала. Отменённые записи слот освобождают.
CREATE UNIQUE INDEX IF NOT EXISTS ux_master_slot
    ON appointments(master_id, starts_at) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS ix_appt_user ON appointments(user_id, status);
CREATE INDEX IF NOT EXISTS ix_appt_time ON appointments(starts_at);

CREATE TABLE IF NOT EXISTS reminders (
    id             INTEGER PRIMARY KEY,
    appointment_id INTEGER NOT NULL UNIQUE REFERENCES appointments(id),
    send_at        TEXT NOT NULL,
    sent_at        TEXT,                            -- NULL = ещё не отправлено
    attempts       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_rem_pending ON reminders(send_at) WHERE sent_at IS NULL;

CREATE TABLE IF NOT EXISTS handoffs (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    username   TEXT,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved   INTEGER NOT NULL DEFAULT 0
);
"""

_APPT_SELECT = """
SELECT a.*, m.name AS master_name, COALESCE(s.name, 'Перерыв') AS service_name
FROM appointments a
JOIN masters m ON m.id = a.master_id
LEFT JOIN services s ON s.id = a.service_id
"""


class Database:
    def __init__(self, path: str):
        # isolation_level=None: автокоммит, транзакции открываем явно (BEGIN IMMEDIATE).
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(SCHEMA)

    # ---------- справочники ----------

    def masters(self) -> list[Master]:
        rows = self.conn.execute("SELECT id, name FROM masters ORDER BY id").fetchall()
        return [Master(r["id"], r["name"]) for r in rows]

    def master(self, master_id: int) -> Master | None:
        r = self.conn.execute("SELECT id, name FROM masters WHERE id=?", (master_id,)).fetchone()
        return Master(r["id"], r["name"]) if r else None

    def master_by_name(self, name: str) -> Master | None:
        r = self.conn.execute(
            "SELECT id, name FROM masters WHERE lower(name)=lower(?)", (name.strip(),)
        ).fetchone()
        return Master(r["id"], r["name"]) if r else None

    def services(self) -> list[Service]:
        rows = self.conn.execute(
            "SELECT id, name, price_text, duration_min FROM services ORDER BY id"
        ).fetchall()
        return [Service(*r) for r in rows]

    def service(self, service_id: int) -> Service | None:
        r = self.conn.execute(
            "SELECT id, name, price_text, duration_min FROM services WHERE id=?", (service_id,)
        ).fetchone()
        return Service(*r) if r else None

    def masters_for_service(self, service_id: int) -> list[Master]:
        rows = self.conn.execute(
            "SELECT m.id, m.name FROM masters m JOIN master_services ms ON ms.master_id=m.id "
            "WHERE ms.service_id=? ORDER BY m.id",
            (service_id,),
        ).fetchall()
        return [Master(r["id"], r["name"]) for r in rows]

    # ---------- слоты ----------

    def busy_intervals(self, master_id: int, day: date) -> list[tuple[datetime, datetime]]:
        start = dt2s(datetime.combine(day, time.min))
        end = dt2s(datetime.combine(day + timedelta(days=1), time.min))
        rows = self.conn.execute(
            "SELECT starts_at, ends_at FROM appointments "
            "WHERE master_id=? AND status='active' AND starts_at>=? AND starts_at<?",
            (master_id, start, end),
        ).fetchall()
        return [(s2dt(r["starts_at"]), s2dt(r["ends_at"])) for r in rows]

    def book(
        self,
        *,
        master_id: int,
        service_id: int | None,
        user_id: int | None,
        client_name: str,
        phone: str | None,
        starts_at: datetime,
        duration_min: int,
        reminder_at: datetime | None,
        now: datetime,
    ) -> int:
        """Создать запись. Всё внутри одной транзакции с блокировкой на запись.

        Возвращает id записи. Бросает SlotTaken, если время занято.
        Клиентская запись (service_id не NULL) без телефона запрещена — требование салона.
        """
        if service_id is not None and not phone:
            raise ValueError("Телефон обязателен для записи клиента")
        ends_at = starts_at + timedelta(minutes=duration_min)
        s_start, s_end = dt2s(starts_at), dt2s(ends_at)

        # BEGIN IMMEDIATE берёт write-lock сразу: параллельные бронирования выстраиваются
        # в очередь, и второй увидит запись первого при проверке пересечений.
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            clash = self.conn.execute(
                "SELECT 1 FROM appointments WHERE master_id=? AND status='active' "
                "AND starts_at < ? AND ends_at > ? LIMIT 1",
                (master_id, s_end, s_start),
            ).fetchone()
            if clash:
                raise SlotTaken
            cur = self.conn.execute(
                "INSERT INTO appointments(master_id, service_id, user_id, client_name, phone, "
                "starts_at, ends_at, status, created_at) VALUES (?,?,?,?,?,?,?,'active',?)",
                (master_id, service_id, user_id, client_name, phone, s_start, s_end, dt2s(now)),
            )
            appt_id = cur.lastrowid
            if reminder_at is not None:
                self.conn.execute(
                    "INSERT INTO reminders(appointment_id, send_at) VALUES (?,?)",
                    (appt_id, dt2s(reminder_at)),
                )
            self.conn.execute("COMMIT")
            return appt_id
        except sqlite3.IntegrityError as e:
            # Сработал ux_master_slot — вторая линия защиты.
            self.conn.execute("ROLLBACK")
            raise SlotTaken from e
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def cancel(self, appt_id: int) -> bool:
        cur = self.conn.execute(
            "UPDATE appointments SET status='cancelled' WHERE id=? AND status='active'", (appt_id,)
        )
        return cur.rowcount == 1

    def appointment(self, appt_id: int) -> sqlite3.Row | None:
        return self.conn.execute(_APPT_SELECT + " WHERE a.id=?", (appt_id,)).fetchone()

    def user_upcoming(self, user_id: int, now: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            _APPT_SELECT + " WHERE a.user_id=? AND a.status='active' AND a.starts_at>=? "
            "ORDER BY a.starts_at",
            (user_id, dt2s(now)),
        ).fetchall()

    def user_active_count(self, user_id: int, now: datetime) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE user_id=? AND status='active' AND starts_at>=?",
            (user_id, dt2s(now)),
        ).fetchone()[0]

    def appointments_between(self, start: datetime, end: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            _APPT_SELECT + " WHERE a.status='active' AND a.starts_at>=? AND a.starts_at<? "
            "ORDER BY a.starts_at, m.name",
            (dt2s(start), dt2s(end)),
        ).fetchall()

    # ---------- напоминания ----------

    def due_reminders(self, now: datetime) -> list[sqlite3.Row]:
        """Неотправленные напоминания, чьё время пришло, вместе с данными записи."""
        return self.conn.execute(
            "SELECT r.id AS reminder_id, r.attempts, a.id, a.user_id, a.status, a.starts_at, "
            "m.name AS master_name, COALESCE(s.name,'') AS service_name "
            "FROM reminders r JOIN appointments a ON a.id=r.appointment_id "
            "JOIN masters m ON m.id=a.master_id LEFT JOIN services s ON s.id=a.service_id "
            "WHERE r.sent_at IS NULL AND r.send_at<=? ORDER BY r.send_at",
            (dt2s(now),),
        ).fetchall()

    def reminder_done(self, reminder_id: int, now: datetime) -> None:
        self.conn.execute("UPDATE reminders SET sent_at=? WHERE id=?", (dt2s(now), reminder_id))

    def reminder_failed(self, reminder_id: int, now: datetime, max_attempts: int = 3) -> None:
        self.conn.execute("UPDATE reminders SET attempts=attempts+1 WHERE id=?", (reminder_id,))
        self.conn.execute(
            "UPDATE reminders SET sent_at=? WHERE id=? AND attempts>=?",
            (dt2s(now), reminder_id, max_attempts),
        )

    # ---------- передача администратору ----------

    def open_handoff(self, user_id: int, now: datetime, ttl_hours: int = 24) -> bool:
        since = dt2s(now - timedelta(hours=ttl_hours))
        r = self.conn.execute(
            "SELECT 1 FROM handoffs WHERE user_id=? AND resolved=0 AND created_at>=? LIMIT 1",
            (user_id, since),
        ).fetchone()
        return r is not None

    def create_handoff(self, user_id: int, username: str | None, text: str, now: datetime) -> int:
        cur = self.conn.execute(
            "INSERT INTO handoffs(user_id, username, text, created_at) VALUES (?,?,?,?)",
            (user_id, username, text, dt2s(now)),
        )
        return cur.lastrowid

    def close_handoffs(self, user_id: int) -> int:
        return self.conn.execute(
            "UPDATE handoffs SET resolved=1 WHERE user_id=? AND resolved=0", (user_id,)
        ).rowcount

    # ---------- статистика ----------

    def stats(self, since: datetime) -> dict[str, int]:
        s = dt2s(since)
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        return {
            "booked": q("SELECT COUNT(*) FROM appointments WHERE user_id IS NOT NULL AND created_at>=?", s),
            "cancelled": q(
                "SELECT COUNT(*) FROM appointments WHERE user_id IS NOT NULL AND status='cancelled' AND created_at>=?", s
            ),
            "reminders_sent": q("SELECT COUNT(*) FROM reminders WHERE sent_at>=?", s),
            "handoffs": q("SELECT COUNT(*) FROM handoffs WHERE created_at>=?", s),
        }


# ---------- чистая функция: свободные слоты ----------

def free_slots(
    busy: list[tuple[datetime, datetime]],
    day: date,
    duration_min: int,
    *,
    work_start_hour: int,
    work_end_hour: int,
    step_min: int,
    now: datetime,
) -> list[datetime]:
    """Начала слотов в рабочее время, не пересекающиеся с занятыми интервалами и не в прошлом."""
    duration = timedelta(minutes=duration_min)
    t = datetime.combine(day, time(hour=work_start_hour))
    day_end = datetime.combine(day, time(hour=work_end_hour))
    out: list[datetime] = []
    while t + duration <= day_end:
        if t > now and not any(bs < t + duration and be > t for bs, be in busy):
            out.append(t)
        t += timedelta(minutes=step_min)
    return out
