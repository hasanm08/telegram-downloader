"""Telegram bot: server-side downloads (streams + torrents), upload to chat up to 2 GB."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import traceback
from pathlib import Path

from telegram import Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from config import (
    BOT_TOKEN,
    LOCAL_MODE,
    MAX_CONCURRENT_DOWNLOADS,
    MAX_FILE_SIZE_MB,
    TELEGRAM_API_BASE_URL,
    TELEGRAM_UPLOAD_LIMIT,
    TEMP_DIR,
)
from downloader import cleanup, download_url, extract_urls
from download_queue import DownloadSlot, queue_stats
from torrent_downloader import aria2_available, download_torrent

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v", ".flv"}
AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac", ".opus"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
MAGNET_RE = re.compile(r"magnet:\?[^\s<>\"']+", re.IGNORECASE)
USE_FILE_URI = os.getenv("USE_FILE_URI", "").lower() in ("1", "true", "yes")

_pending_torrent: set[int] = set()


def _normalize_url(url: str) -> str:
    return url.rstrip(").]>\"'")


def _unique_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in urls:
        url = _normalize_url(raw)
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    aria = "ok" if aria2_available() else "MISSING (install aria2)"
    local = (
        f"ON — uploads up to {MAX_FILE_SIZE_MB} MB"
        if LOCAL_MODE
        else "OFF — 50 MB upload cap (need local Bot API for 2 GB)"
    )
    active, waiting, maximum = await queue_stats()
    await update.message.reply_text(
        "Downloader Bot (24/7 server mode)\n\n"
        "Downloads run on the bot SERVER (not your phone/Mac).\n"
        "Works while you are offline — as long as the server is up.\n"
        "Files are uploaded to this chat, then deleted from the server.\n\n"
        f"• Max size: ~{MAX_FILE_SIZE_MB} MB (full quality)\n"
        f"• Concurrent: {maximum} streams/torrents "
        f"(active={active}, waiting={waiting})\n"
        f"• Local Bot API: {local}\n"
        f"• Torrents: {aria}\n\n"
        "Send multiple links in one message, or a .torrent file.\n\n"
        "Commands: /dl /file /torrent /torrnet /queue /start",
        disable_web_page_preview=True,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def queue_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    active, waiting, maximum = await queue_stats()
    await update.message.reply_text(
        f"Download queue\n"
        f"• Slots: {maximum}\n"
        f"• Active: {active}\n"
        f"• Waiting: {waiting}"
    )


async def dl_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args or []).strip()
    if not text:
        await update.message.reply_text("Usage: /dl <url> [more urls…]")
        return
    urls = _unique_urls(extract_urls(text) or [text])
    await _handle_urls(update, context, urls, force_direct=False)


async def file_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args or []).strip()
    if not text:
        await update.message.reply_text("Usage: /file <url> [more urls…]")
        return
    urls = _unique_urls(extract_urls(text) or [text])
    await _handle_urls(update, context, urls, force_direct=True)


async def torrent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not aria2_available():
        await update.message.reply_text("❌ aria2c missing on server.")
        return

    args = " ".join(context.args or []).strip()
    chat_id = update.effective_chat.id
    magnets = MAGNET_RE.findall(args) if args else []
    if magnets:
        await _handle_magnets(update, context, magnets)
        return
    if args.lower().startswith("magnet:"):
        await _enqueue_torrent(update, context, magnet=args)
        return

    reply = update.message.reply_to_message if update.message else None
    if reply and reply.document:
        await _handle_torrent_document(update, context, reply.document)
        return

    _pending_torrent.add(chat_id)
    await update.message.reply_text(
        "🧲 Send a .torrent file now.\nOr: /torrent magnet:?xt=..."
    )


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.document:
        return
    doc = update.message.document
    name = (doc.file_name or "").lower()
    is_torrent = name.endswith(".torrent") or (doc.mime_type or "") == "application/x-bittorrent"
    if not is_torrent:
        return
    _pending_torrent.discard(update.effective_chat.id)
    await _handle_torrent_document(update, context, doc)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    magnets = MAGNET_RE.findall(text)
    urls = _unique_urls(extract_urls(text))

    if magnets and not urls:
        await _handle_magnets(update, context, magnets)
        return
    if urls and magnets:
        await update.message.reply_text(
            f"Queued {len(urls)} link(s) + {len(magnets)} magnet(s)."
        )
        await asyncio.gather(
            _handle_urls(update, context, urls, force_direct=False, announce=False),
            _handle_magnets(update, context, magnets, announce=False),
        )
        return
    if not urls:
        await update.message.reply_text(
            "Send stream link(s), a .torrent file, or /dl / /file / /torrent."
        )
        return
    await _handle_urls(update, context, urls, force_direct=False)


async def _handle_magnets(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    magnets: list[str],
    announce: bool = True,
) -> None:
    magnets = _unique_urls(magnets)
    if announce and len(magnets) > 1:
        await update.message.reply_text(
            f"🧲 Queueing {len(magnets)} magnets "
            f"(max {MAX_CONCURRENT_DOWNLOADS} concurrent)."
        )
    await asyncio.gather(
        *[_enqueue_torrent(update, context, magnet=m) for m in magnets]
    )


async def _handle_torrent_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE, doc
) -> None:
    name = doc.file_name or "file.torrent"
    if not name.lower().endswith(".torrent"):
        name = f"{name}.torrent"
    status = await update.message.reply_text(f"🧲 Got torrent: {name}")
    safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in name)[:120]
    torrent_path = TEMP_DIR / f"{update.effective_chat.id}_{safe}"
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(custom_path=str(torrent_path))
        await _enqueue_torrent(
            update, context, torrent_path=torrent_path, status=status
        )
    except Exception as exc:
        logger.error("Torrent doc failed\n%s", traceback.format_exc())
        cleanup(torrent_path)
        try:
            await status.edit_text(f"❌ Failed:\n{str(exc)[:500]}")
        except TelegramError:
            await update.message.reply_text(f"❌ Failed:\n{str(exc)[:500]}")


async def _enqueue_torrent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    torrent_path: Path | None = None,
    magnet: str | None = None,
    status=None,
) -> None:
    label = magnet[:120] if magnet else (torrent_path.name if torrent_path else "torrent")
    if status is None:
        status = await update.message.reply_text(f"🧲 Queued torrent…\n{label}")

    active, waiting, maximum = await queue_stats()
    if active >= maximum or waiting > 0:
        try:
            await status.edit_text(
                f"⏳ Queued ({active}/{maximum} active, {waiting + 1} waiting)\n{label}"
            )
        except TelegramError:
            pass

    async with DownloadSlot(label=label):
        await _run_torrent(
            update,
            context,
            torrent_path=torrent_path,
            magnet=magnet,
            status=status,
            label=label,
        )


async def _run_torrent(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    torrent_path: Path | None,
    magnet: str | None,
    status,
    label: str,
) -> None:
    async def on_progress(msg: str) -> None:
        try:
            await status.edit_text(msg[:3500])
        except TelegramError:
            pass

    path: Path | None = None
    try:
        await on_progress(f"🧲 Server torrent download…\n{label}")
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.UPLOAD_DOCUMENT,
        )
        result = await download_torrent(
            torrent_path=torrent_path,
            magnet=magnet,
            on_progress=on_progress,
        )
        path = result.path
        await _deliver_file(
            update,
            context,
            status,
            path=path,
            title=result.title,
            source=result.source,
            size=result.size,
            on_progress=on_progress,
        )
    except Exception as exc:
        logger.error("Torrent download failed\n%s", traceback.format_exc())
        err = str(exc).strip() or type(exc).__name__
        try:
            await status.edit_text(f"❌ Failed:\n{err[:500]}")
        except TelegramError:
            await update.message.reply_text(f"❌ Failed:\n{err[:500]}")
    finally:
        if torrent_path is not None:
            cleanup(torrent_path)
        if path is not None:
            cleanup(path)


async def _handle_urls(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    urls: list[str],
    force_direct: bool,
    announce: bool = True,
) -> None:
    urls = _unique_urls(urls)
    if not urls:
        await update.message.reply_text("No valid URLs found.")
        return

    if announce:
        n = len(urls)
        await update.message.reply_text(
            f"⏳ Queued {n} download(s) on server "
            f"(max {MAX_CONCURRENT_DOWNLOADS} concurrent)."
        )

    await asyncio.gather(
        *[
            _enqueue_url(
                update, context, url, force_direct=force_direct, index=i, total=len(urls)
            )
            for i, url in enumerate(urls, start=1)
        ]
    )


async def _enqueue_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    force_direct: bool,
    index: int,
    total: int,
) -> None:
    prefix = f"[{index}/{total}] " if total > 1 else ""
    status = await update.message.reply_text(f"{prefix}⏳ Queued on server:\n{url}")

    active, waiting, maximum = await queue_stats()
    if active >= maximum or waiting > 0:
        try:
            await status.edit_text(
                f"{prefix}⏳ Waiting for slot "
                f"({active}/{maximum} active, {waiting + 1} waiting)\n{url}"
            )
        except TelegramError:
            pass

    async with DownloadSlot(label=url):
        await _run_url(
            update,
            context,
            url,
            force_direct=force_direct,
            status=status,
            prefix=prefix,
        )


async def _run_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    force_direct: bool,
    status,
    prefix: str,
) -> None:
    async def on_progress(msg: str) -> None:
        try:
            await status.edit_text(f"{prefix}{msg}\n\n{url}"[:3500])
        except TelegramError:
            pass

    path: Path | None = None
    try:
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action=ChatAction.UPLOAD_DOCUMENT,
        )
        result = await download_url(
            url, on_progress=on_progress, force_direct=force_direct
        )
        path = result.path
        await _deliver_file(
            update,
            context,
            status,
            path=path,
            title=result.title,
            source=result.source,
            size=result.size,
            on_progress=on_progress,
            prefix=prefix,
        )
    except Exception as exc:
        logger.error("Download failed for %s\n%s", url, traceback.format_exc())
        err = str(exc).strip() or type(exc).__name__
        try:
            await status.edit_text(f"{prefix}❌ Failed:\n{err[:500]}\n\n{url}")
        except TelegramError:
            await update.message.reply_text(f"{prefix}❌ Failed:\n{err[:500]}")
    finally:
        if path is not None:
            cleanup(path)


async def _deliver_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status,
    *,
    path: Path,
    title: str,
    source: str,
    size: int,
    on_progress,
    prefix: str = "",
) -> None:
    if size > TELEGRAM_UPLOAD_LIMIT:
        await status.edit_text(
            f"{prefix}❌ File is {_fmt(size)} — over send limit "
            f"{_fmt(TELEGRAM_UPLOAD_LIMIT)}.\n"
            "Start local Bot API (docker compose / ./start_local_api.sh) "
            "with api_id + api_hash for up to 2 GB.\n"
            "Temp file deleted — nothing kept on server."
        )
        return

    await on_progress("📤 Uploading to Telegram chat…")
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=_chat_action_for(path),
    )
    await _send_file(
        update,
        path,
        caption=(
            f"✅ *{_escape_md(title)}*\n"
            f"Server → Telegram · `{source}` · {_fmt(size)}"
        ),
    )
    await status.edit_text(f"{prefix}Done ✅")


def _media_input(path: Path):
    # file:// only when Bot API shares the same filesystem (optional).
    if LOCAL_MODE and USE_FILE_URI:
        return f"file://{path.resolve()}"
    return path


async def _send_file(update: Update, path: Path, caption: str) -> None:
    ext = path.suffix.lower()
    size = path.stat().st_size
    media = _media_input(path)
    plain = caption.replace("*", "").replace("`", "")

    async def _send(kind: str, payload, *, use_md: bool) -> None:
        kwargs = {"caption": caption if use_md else plain}
        if use_md:
            kwargs["parse_mode"] = ParseMode.MARKDOWN
        if kind == "photo":
            await update.message.reply_photo(photo=payload, **kwargs)
        elif kind == "video":
            await update.message.reply_video(
                video=payload, supports_streaming=True, **kwargs
            )
        elif kind == "audio":
            await update.message.reply_audio(audio=payload, title=path.stem, **kwargs)
        else:
            await update.message.reply_document(
                document=payload, filename=path.name, **kwargs
            )

    try:
        if ext in PHOTO_EXTS and size < 10 * 1024 * 1024:
            try:
                await _send("photo", media, use_md=True)
            except TelegramError:
                await _send("photo", _media_input(path), use_md=False)
            return
        if ext in VIDEO_EXTS and size <= TELEGRAM_UPLOAD_LIMIT:
            try:
                await _send("video", media, use_md=True)
            except TelegramError:
                await _send("video", _media_input(path), use_md=False)
            return
        if ext in AUDIO_EXTS:
            try:
                await _send("audio", media, use_md=True)
            except TelegramError:
                await _send("audio", _media_input(path), use_md=False)
            return
    except TelegramError as exc:
        logger.warning("Typed send failed (%s); falling back to document", exc)

    try:
        await _send("document", _media_input(path), use_md=True)
    except TelegramError:
        await _send("document", _media_input(path), use_md=False)


def _chat_action_for(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        return ChatAction.UPLOAD_VIDEO
    if ext in AUDIO_EXTS:
        return ChatAction.UPLOAD_VOICE
    if ext in PHOTO_EXTS:
        return ChatAction.UPLOAD_PHOTO
    return ChatAction.UPLOAD_DOCUMENT


def _fmt(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def _escape_md(text: str) -> str:
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def build_app() -> Application:
    if not BOT_TOKEN or BOT_TOKEN == "your_telegram_bot_token_here":
        raise SystemExit("Set BOT_TOKEN in .env")

    request = HTTPXRequest(
        connect_timeout=60.0,
        read_timeout=3600.0,
        write_timeout=3600.0,
        pool_timeout=60.0,
        media_write_timeout=3600.0,
    )

    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .get_updates_request(request)
        .concurrent_updates(True)
    )
    if TELEGRAM_API_BASE_URL:
        base = TELEGRAM_API_BASE_URL.rstrip("/")
        builder = (
            builder.base_url(base + "/bot")
            .base_file_url(base + "/file/bot")
            .local_mode(True)
        )
        logger.info("Local Bot API (2 GB): %s", TELEGRAM_API_BASE_URL)

    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("queue", queue_cmd))
    app.add_handler(CommandHandler("dl", dl_cmd))
    app.add_handler(CommandHandler("file", file_cmd))
    app.add_handler(CommandHandler(["torrent", "torrnet"], torrent_cmd))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_error_handler(_on_error)
    return app


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Update error: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"❌ Error:\n{str(context.error)[:400]}"
            )
        except TelegramError:
            pass


def main() -> None:
    app = build_app()
    logger.info(
        "Bot starting… local_mode=%s aria2=%s max_mb=%s concurrent=%s",
        LOCAL_MODE,
        aria2_available(),
        MAX_FILE_SIZE_MB,
        MAX_CONCURRENT_DOWNLOADS,
    )
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        poll_interval=1.0,
    )


if __name__ == "__main__":
    main()
