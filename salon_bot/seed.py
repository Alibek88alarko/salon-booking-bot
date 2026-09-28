"""Тестовый прайс и мастера. Заливается один раз в пустую базу.

Длительности услуг и кто что делает — ДОПУЩЕНИЯ, у Лилии не уточнены
(см. 01-вопросы-клиенту.md). Поменять — здесь.
"""
from __future__ import annotations

from .db import Database

SERVICES = [
    # (название, цена текстом, длительность в минутах)
    ("Стрижка", "6 000 ₸", 60),
    ("Окрашивание", "от 15 000 ₸", 150),
    ("Маникюр", "7 000 ₸", 60),
    ("Маникюр с покрытием", "7 000 ₸", 90),
    ("Брови", "по договорённости", 30),
    ("Ресницы", "по договорённости", 90),
]

MASTERS = {
    "Айгуль": ["Стрижка", "Окрашивание"],
    "Динара": ["Маникюр", "Маникюр с покрытием"],
    "Марина": ["Стрижка", "Брови", "Ресницы"],
}


def seed_if_empty(db: Database) -> None:
    if db.conn.execute("SELECT COUNT(*) FROM masters").fetchone()[0]:
        return
    db.conn.execute("BEGIN")
    try:
        for name, price, dur in SERVICES:
            db.conn.execute(
                "INSERT INTO services(name, price_text, duration_min) VALUES (?,?,?)", (name, price, dur)
            )
        for master, svcs in MASTERS.items():
            cur = db.conn.execute("INSERT INTO masters(name) VALUES (?)", (master,))
            for s in svcs:
                db.conn.execute(
                    "INSERT INTO master_services(master_id, service_id) "
                    "SELECT ?, id FROM services WHERE name=?",
                    (cur.lastrowid, s),
                )
        db.conn.execute("COMMIT")
    except Exception:
        db.conn.execute("ROLLBACK")
        raise
