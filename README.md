# Salon booking bot (Telegram, Python, LLM with a hard fallback to a human)

A working Telegram bot for a small beauty salon: price list, booking a slot with a specific master,
a reminder 24 hours before the visit, and an admin panel. One process, no external services except
Telegram and (optionally) the Anthropic API.

Stack: Python 3.11+, aiogram 3, SQLite, APScheduler, anthropic SDK. Russian-language README: `README.ru.md`.

## Measured result (reproducible in under a minute)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q
```

Output on the last run (Linux, Python 3.11, no API key, no network):

```
............                                                             [100%]
12 passed in 4.57s
```

What the 12 tests actually check, so the number means something:

| Test | What is measured |
|---|---|
| `test_race_two_clients_same_slot` | Two threads with separate SQLite connections book the same master and time at the same moment (a `threading.Barrier` releases both together). Exactly one row is written, the other thread gets `SlotTaken`. Count of active appointments after the race: 1. |
| `test_unique_index_exists` | A partial unique index `ux_master_slot` on `(master_id, starts_at) WHERE status = 'active'` exists, so double booking is refused by the database even if application code is bypassed. |
| `test_overlap_rejected_but_adjacent_ok` | A 60-minute booking overlapping an existing one is rejected; a booking that starts exactly when the previous ends is accepted. |
| `test_cancelled_slot_is_free_again` | After cancellation the same slot can be booked again (the partial index ignores cancelled rows). |
| `test_reminder_persisted_and_due` | The reminder is stored in the database, becomes due at the right minute, survives a "restart" (a fresh connection sees it), and is not returned twice once marked sent. |
| `test_free_slots_respect_busy_and_hours` | Free slot grid respects working hours, step size and a blocked lunch interval. |
| `test_scrub_hides_phone_before_llm` | A phone number typed by the client is masked before the text reaches the language model. |
| `test_llm_parse_defaults_to_handoff` | Garbage, an empty answer, or an unparseable model reply all resolve to `handoff = True`, so the customer is passed to a human instead of receiving an invented answer. |
| `test_scrub_keeps_prices` | Masking phone numbers does not damage prices in the text. |
| `test_normalize_phone`, `test_phone_required_for_client_booking`, `test_keyboard_rows` | Phone normalisation, required fields, keyboard layout. |

## Design decisions worth knowing before you read the code

- **Booking is a transaction, not a check.** `Database.book()` opens `BEGIN IMMEDIATE`, checks for
  overlaps, inserts, and relies on the unique index as the last line of defence. See `salon_bot/db.py`.
- **The model answers only from the price list.** The system prompt receives the price list and
  nothing else. The model must reply with JSON `{"answer": "...", "handoff": true|false}`. Any error,
  timeout (8 s), empty answer or parse failure is treated as `handoff = true`. See `salon_bot/llm.py`.
- **Personal data does not go to the model.** Only the message text is sent, and phone numbers are
  scrubbed from it first. Names and phones live in SQLite on the salon's own machine.
- **Reminders are rows, not timers.** A scheduler tick once a minute reads due reminders from the
  database. If the process was down, overdue reminders are sent after restart.
- **Admin access is a whitelist of Telegram IDs** from `.env`, enforced by a router-level filter.

## Run it

1. Copy `.env.example` to `.env`, set `BOT_TOKEN` from @BotFather.
2. `python main.py`, send `/id` to the bot, put the returned ID into `ADMIN_IDS`, restart.
3. `ANTHROPIC_API_KEY` is optional. Without it the bot still works: menu-driven price list and
   booking, and every free-text question goes straight to the administrator.

`deploy/` contains a systemd unit and an install script for a Linux VPS.

## Known simplifications

FSM state is in memory (an unfinished booking is lost on restart), single process, SQLite without
backups. For production: Redis for FSM, database backup, the systemd unit from `deploy/`.

## Honest note on failures

During a 30-question dry run one reply broke on JSON parsing. The price was not invented, the
dialogue went to a human, which is exactly what the `handoff` default is for. That case is now
covered by `test_llm_parse_defaults_to_handoff`.
