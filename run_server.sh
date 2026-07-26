#!/usr/bin/env bash
# Run the 24/7 stack with Docker (bot + local Bot API for 2 GB).
# Deploy this folder on a VPS so it keeps working while your Mac is offline.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
  echo "Copy .env.example to .env and fill BOT_TOKEN, TELEGRAM_API_ID, TELEGRAM_API_HASH"
  exit 1
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

if [[ -z "${BOT_TOKEN:-}" || "$BOT_TOKEN" == "your_telegram_bot_token_here" ]]; then
  echo "Set BOT_TOKEN in .env"
  exit 1
fi

if [[ -z "${TELEGRAM_API_ID:-}" || -z "${TELEGRAM_API_HASH:-}" ]]; then
  echo "Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env"
  echo "Get them from https://my.telegram.org/apps (needed for 2 GB uploads)"
  exit 1
fi

# Ensure compose uses the local API service
if ! grep -q '^TELEGRAM_API_BASE_URL=' .env; then
  echo 'TELEGRAM_API_BASE_URL=http://telegram-bot-api:8081' >> .env
else
  sed -i.bak 's|^TELEGRAM_API_BASE_URL=.*|TELEGRAM_API_BASE_URL=http://telegram-bot-api:8081|' .env
fi

if ! grep -q '^MAX_CONCURRENT_DOWNLOADS=' .env; then
  echo 'MAX_CONCURRENT_DOWNLOADS=10' >> .env
fi
if ! grep -q '^MAX_FILE_SIZE_MB=' .env; then
  echo 'MAX_FILE_SIZE_MB=2000' >> .env
fi

echo "Building & starting (restart: unless-stopped)…"
docker compose up -d --build
docker compose ps
echo ""
echo "✅ Stack is up. Bot keeps running even if your laptop is offline"
echo "   (as long as THIS host stays online — use a VPS for true 24/7)."
echo "   Logs: docker compose logs -f bot"
