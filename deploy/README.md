# Развёртывание бота

## Вариант А. На своём компьютере, за 3 минуты (для скриншотов и показа)

1. В Telegram открыть **@BotFather**, команда `/newbot`.
   Имя: `Салон красоты, запись`. Username: любой свободный, оканчивается на `bot`.
2. Скопировать токен, который пришлёт BotFather.
3. В папке `salon-booking-bot`:
   ```bash
   python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   cp .env.example .env
   ```
4. В `.env` вписать `BOT_TOKEN`. Запустить `python main.py`.
5. Написать боту `/id`, вписать полученный ID в `ADMIN_IDS` в `.env`, перезапустить.
6. Пройти сценарий: прайс, запись, подтверждение. Снять скриншоты.

Бот работает без ключа модели: кнопки и запись доступны, свободные вопросы уходят администратору.

## Вариант Б. На сервере, чтобы работал всегда

С сервера (Ubuntu/Debian, от root), из скопированной папки проекта:

```bash
bash deploy/install.sh
nano /opt/salon-bot/.env      # BOT_TOKEN
systemctl start salon-bot
journalctl -u salon-bot -f    # логи
```

Затем написать боту `/id`, вписать ID в `ADMIN_IDS`, `systemctl restart salon-bot`.

Обновление кода: скопировать файлы в `/opt/salon-bot` и `systemctl restart salon-bot`.
База `salon.db` лежит там же, при обновлении её не трогать.

Резервная копия базы (раз в сутки, cron):
```
0 3 * * * sqlite3 /opt/salon-bot/salon.db ".backup '/opt/salon-bot/backup-$(date +\%F).db'"
```
