# Telegram Downloader Bot

Python Telegram bot that downloads **streams** (yt-dlp) and **torrents** (aria2), then sends files back in chat.

## Features

- Multiple links in one message
- Up to **10 concurrent** downloads
- Up to **2 GB** uploads when Local Bot API secrets are set
- Runs on **GitHub Actions** (free for public repos) with auto-restart every ~5 hours

## Limits (GitHub Actions)

GitHub Actions is not a permanent VPS. Each job can run at most **6 hours**, so the workflow restarts on a schedule. Expect a short gap when a new run replaces the old one.

## Secrets

Repo → **Settings → Secrets and variables → Actions**:

| Secret | Required | Description |
|--------|----------|-------------|
| `BOT_TOKEN` | Yes | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_API_ID` | For 2 GB | From [my.telegram.org/apps](https://my.telegram.org/apps) |
| `TELEGRAM_API_HASH` | For 2 GB | From [my.telegram.org/apps](https://my.telegram.org/apps) |

## Start

1. Push to `main` or open **Actions → Telegram Downloader Bot → Run workflow**
2. Message the bot on Telegram (`/start`)

## Local / VPS (optional)

```bash
cp .env.example .env   # fill tokens
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python bot.py
# or 24/7 on a VPS:
./run_server.sh
```
