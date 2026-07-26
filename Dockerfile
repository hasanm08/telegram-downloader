FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    aria2 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./
COPY restart_bot.sh start_local_api.sh ./

RUN mkdir -p /app/temp /app/downloads /app/bot_api_data \
    && chmod +x restart_bot.sh start_local_api.sh || true

ENV PYTHONUNBUFFERED=1 \
    DOWNLOAD_DIR=/app/temp \
    MAX_FILE_SIZE_MB=2000 \
    MAX_CONCURRENT_DOWNLOADS=10

CMD ["python", "bot.py"]
