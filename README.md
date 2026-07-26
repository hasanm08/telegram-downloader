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

## Persistence across Action restarts

Partial downloads are kept so the next job can **resume**:

1. **Actions cache** — full `temp/` including large files (up to cache limits)
2. **`bot-data` git branch** — files under ~90MB (GitHub file size limit)
3. Torrents use a **stable session folder**; streams use yt-dlp `continuedl`

Do not commit media into `main` (public + size limits).