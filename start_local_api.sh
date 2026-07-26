#!/usr/bin/env bash
# Start local Telegram Bot API server — required to upload original-quality files up to 2 GB.
# Docs: https://tdlib.github.io/telegram-bot-api/
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -z "${TELEGRAM_API_ID:-}" || -z "${TELEGRAM_API_HASH:-}" ]]; then
  echo "❌ Missing TELEGRAM_API_ID / TELEGRAM_API_HASH in .env"
  echo ""
  echo "Official Telegram bots can only upload 50 MB."
  echo "A local Bot API server unlocks uploads up to 2 GB at full quality."
  echo ""
  echo "1) Open https://my.telegram.org/apps"
  echo "2) Log in with your phone number"
  echo "3) Create an application"
  echo "4) Copy api_id and api_hash into .env:"
  echo "     TELEGRAM_API_ID=12345678"
  echo "     TELEGRAM_API_HASH=your_hash_here"
  echo "     TELEGRAM_API_BASE_URL=http://127.0.0.1:8081"
  echo "5) Run this script again, then: python bot.py"
  exit 1
fi

DATA_DIR="$(pwd)/bot_api_data"
mkdir -p "$DATA_DIR"

ensure_docker() {
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    return 0
  fi
  if command -v colima >/dev/null 2>&1; then
    echo "Starting Colima (Docker runtime)…"
    colima start --cpu 2 --memory 4 --disk 40 || colima start
  fi
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

if ensure_docker; then
  echo "Starting local Bot API via Docker on :8081 …"
  docker rm -f telegram-bot-api >/dev/null 2>&1 || true
  docker pull aiogram/telegram-bot-api:latest
  docker run -d --name telegram-bot-api --restart unless-stopped \
    -p 8081:8081 \
    -v "$DATA_DIR:/var/lib/telegram-bot-api" \
    aiogram/telegram-bot-api:latest \
    --api-id="$TELEGRAM_API_ID" \
    --api-hash="$TELEGRAM_API_HASH" \
    --local \
    --http-port=8081 \
    --dir=/var/lib/telegram-bot-api

  # Point bot at local server
  if grep -q '^TELEGRAM_API_BASE_URL=' .env; then
    sed -i.bak 's|^TELEGRAM_API_BASE_URL=.*|TELEGRAM_API_BASE_URL=http://127.0.0.1:8081|' .env
  else
    echo 'TELEGRAM_API_BASE_URL=http://127.0.0.1:8081' >> .env
  fi

  echo ""
  echo "✅ Local Bot API running at http://127.0.0.1:8081"
  echo "   TELEGRAM_API_BASE_URL updated in .env"
  echo "   Restart the bot:  source .venv/bin/activate && python bot.py"
  exit 0
fi

BIN=""
if command -v telegram-bot-api >/dev/null 2>&1; then
  BIN="$(command -v telegram-bot-api)"
elif [[ -x ./bin/telegram-bot-api ]]; then
  BIN="./bin/telegram-bot-api"
fi

if [[ -z "$BIN" ]]; then
  echo "❌ Docker/Colima not available and telegram-bot-api binary not found."
  echo "Install Docker Desktop, or: brew install colima docker"
  exit 1
fi

echo "Starting $BIN on :8081 …"
if grep -q '^TELEGRAM_API_BASE_URL=' .env; then
  sed -i.bak 's|^TELEGRAM_API_BASE_URL=.*|TELEGRAM_API_BASE_URL=http://127.0.0.1:8081|' .env
else
  echo 'TELEGRAM_API_BASE_URL=http://127.0.0.1:8081' >> .env
fi
exec "$BIN" \
  --api-id="$TELEGRAM_API_ID" \
  --api-hash="$TELEGRAM_API_HASH" \
  --local \
  --http-port=8081 \
  --dir="$DATA_DIR"
