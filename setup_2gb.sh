#!/usr/bin/env bash
# Wire TELEGRAM_API_ID + TELEGRAM_API_HASH for uploads up to 2 GB.
# 1) Create app: https://my.telegram.org/apps  → copy api_id + api_hash
# 2) Run:  ./setup_2gb.sh <api_id> <api_hash>
#    or:   ./setup_2gb.sh   (interactive)
set -euo pipefail

cd "$(dirname "$0")"

API_ID="${1:-}"
API_HASH="${2:-}"

if [[ -z "$API_ID" || -z "$API_HASH" ]]; then
  echo "Create an app at https://my.telegram.org/apps (login with phone),"
  echo "then paste api_id and api_hash here."
  echo ""
  read -r -p "TELEGRAM_API_ID: " API_ID
  read -r -p "TELEGRAM_API_HASH: " API_HASH
fi

API_ID="$(echo "$API_ID" | tr -d '[:space:]')"
API_HASH="$(echo "$API_HASH" | tr -d '[:space:]')"

if [[ ! "$API_ID" =~ ^[0-9]+$ ]]; then
  echo "❌ api_id must be numeric (from my.telegram.org/apps)"
  exit 1
fi
if [[ ${#API_HASH} -lt 16 ]]; then
  echo "❌ api_hash looks too short"
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

# Update local .env
if grep -q '^TELEGRAM_API_ID=' .env; then
  sed -i.bak "s|^TELEGRAM_API_ID=.*|TELEGRAM_API_ID=$API_ID|" .env
else
  echo "TELEGRAM_API_ID=$API_ID" >> .env
fi
if grep -q '^TELEGRAM_API_HASH=' .env; then
  sed -i.bak "s|^TELEGRAM_API_HASH=.*|TELEGRAM_API_HASH=$API_HASH|" .env
else
  echo "TELEGRAM_API_HASH=$API_HASH" >> .env
fi
rm -f .env.bak

echo "✅ Wrote TELEGRAM_API_ID / TELEGRAM_API_HASH to .env"

# GitHub Actions secrets (bot runs there 24/7)
if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  printf '%s' "$API_ID" | gh secret set TELEGRAM_API_ID
  printf '%s' "$API_HASH" | gh secret set TELEGRAM_API_HASH
  echo "✅ Set GitHub secrets TELEGRAM_API_ID + TELEGRAM_API_HASH"
  echo "   Restarting Actions workflow so local Bot API starts…"
  gh run cancel "$(gh run list --workflow=telegram-bot.yml --limit 1 --json databaseId -q '.[0].databaseId')" 2>/dev/null || true
  sleep 2
  gh workflow run telegram-bot.yml
  echo "✅ Workflow re-dispatched — uploads up to 2 GB once the job is green."
else
  echo "⚠️  gh not authenticated — set secrets manually in the repo, then re-run the workflow."
fi

# Optional: local Docker Bot API
if command -v docker >/dev/null 2>&1; then
  if ! docker info >/dev/null 2>&1; then
    if command -v colima >/dev/null 2>&1; then
      echo "Starting Colima…"
      colima start --cpu 2 --memory 4 --disk 40 || colima start || true
    fi
  fi
  if docker info >/dev/null 2>&1; then
    ./start_local_api.sh || true
  else
    echo "ℹ️  Docker still unavailable — GitHub Actions path is enough for the hosted bot."
  fi
fi

echo ""
echo "Done. Send /start to the bot; Local Bot API should show ON (up to 2000 MB)."
