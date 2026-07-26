#!/usr/bin/env bash
# Restart the Telegram downloader bot in the background.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"
PYTHON="$ROOT/.venv/bin/python"

pkill -f "[Pp]ython bot.py" 2>/dev/null || true
sleep 2

if [[ ! -x "$PYTHON" ]]; then
  echo "❌ Missing venv at $PYTHON — run: python3 -m venv .venv && pip install -r requirements.txt"
  exit 1
fi

# Use absolute interpreter + disown so the process survives this script exiting
nohup "$PYTHON" "$ROOT/bot.py" >> "$ROOT/bot.log" 2>&1 &
echo $! > "$ROOT/bot.pid"
disown $! 2>/dev/null || true
sleep 3

if kill -0 "$(cat "$ROOT/bot.pid")" 2>/dev/null; then
  echo "✅ Bot running (pid $(cat "$ROOT/bot.pid")). Logs: bot.log"
  tail -n 20 "$ROOT/bot.log"
else
  echo "❌ Bot failed to start. Last log:"
  tail -n 50 "$ROOT/bot.log" || true
  exit 1
fi
