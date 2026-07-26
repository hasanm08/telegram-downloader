import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TELEGRAM_API_BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "").strip() or None
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "").strip()
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()

# Up to 2 GB (Telegram local Bot API max)
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2000"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# How many downloads can run at the same time
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "10"))

DOWNLOAD_DIR = BASE_DIR / os.getenv("DOWNLOAD_DIR", "downloads")
TEMP_DIR = BASE_DIR / "temp"

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Official api.telegram.org: 50 MB upload. Local Bot API (--local): up to 2000 MB.
LOCAL_MODE = bool(TELEGRAM_API_BASE_URL)
TELEGRAM_UPLOAD_LIMIT = (
    min(MAX_FILE_SIZE_BYTES, 2000 * 1024 * 1024)
    if LOCAL_MODE
    else 50 * 1024 * 1024
)
