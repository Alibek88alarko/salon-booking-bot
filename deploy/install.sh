#!/usr/bin/env bash
# Установка бота на сервер (Ubuntu/Debian). Запускать от root: bash deploy/install.sh
set -euo pipefail

APP_DIR=/opt/salon-bot
RUN_USER=${RUN_USER:-botuser}

echo "1/6 системные пакеты"
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip rsync >/dev/null

echo "2/6 пользователь $RUN_USER"
id -u "$RUN_USER" >/dev/null 2>&1 || useradd -r -m -s /usr/sbin/nologin "$RUN_USER"

echo "3/6 копирование в $APP_DIR"
mkdir -p "$APP_DIR"
rsync -a --exclude '.venv' --exclude '__pycache__' --exclude '.pytest_cache' \
      --exclude '*.db' --exclude '*.db-wal' --exclude '*.db-shm' \
      "$(cd "$(dirname "$0")/.." && pwd)/" "$APP_DIR/"

echo "4/6 виртуальное окружение"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "5/6 .env"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "  ВНИМАНИЕ: впишите BOT_TOKEN и ADMIN_IDS в $APP_DIR/.env"
fi
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env"

echo "6/6 systemd"
sed "s/User=%i/User=$RUN_USER/" "$APP_DIR/deploy/salon-bot.service" > /etc/systemd/system/salon-bot.service
systemctl daemon-reload
systemctl enable salon-bot >/dev/null

echo
echo "Готово. Дальше:"
echo "  nano $APP_DIR/.env          # BOT_TOKEN, потом ADMIN_IDS"
echo "  systemctl start salon-bot"
echo "  journalctl -u salon-bot -f  # смотреть логи"
