"""Download streams/direct URLs on the bot server (temp only), then upload to Telegram."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional
from urllib.parse import unquote, urlparse

import aiofiles
import aiohttp
import yt_dlp

from config import MAX_FILE_SIZE_BYTES, TEMP_DIR

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


@dataclass
class DownloadResult:
    path: Path
    title: str
    source: str
    size: int


def extract_urls(text: str) -> list[str]:
    return URL_RE.findall(text or "")


def is_probably_direct_file(url: str) -> bool:
    path = unquote(urlparse(url).path).lower()
    direct_exts = (
        ".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m4v",
        ".mp3", ".m4a", ".flac", ".wav", ".ogg", ".aac",
        ".pdf", ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
        ".apk", ".exe", ".dmg", ".iso", ".doc", ".docx",
        ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".csv",
        ".json", ".xml", ".srt", ".vtt",
    )
    return any(path.endswith(ext) for ext in direct_exts)


def _safe_filename(name: str, fallback: str = "download") -> str:
    name = re.sub(r"[^\w.\- ()\[\]]+", "_", name, flags=re.UNICODE).strip("._ ")
    return (name or fallback)[:180]


def _fmt_size(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024:
            return f"{x:.1f} {unit}" if unit != "B" else f"{int(x)} B"
        x /= 1024
    return f"{x:.1f} TB"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 10_000):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate unique filename.")


def cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Failed to delete %s: %s", path, exc)


BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


def _http_headers(url: str, referer: Optional[str] = None) -> dict:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    ref = referer or origin or url
    return {
        "User-Agent": BROWSER_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": ref,
        "Origin": origin or ref,
        "Connection": "keep-alive",
    }


def _friendly_http_error(exc: BaseException, url: str) -> str:
    text = str(exc)
    if "403" in text or "Forbidden" in text:
        return (
            "HTTP 403 Forbidden — the site/CDN blocked this server IP "
            "(common on GitHub Actions). Try again later, use a smaller/"
            "different host, or run the bot on a residential VPS.\n"
            f"URL: {url[:200]}"
        )
    if "401" in text or "Unauthorized" in text:
        return f"HTTP 401 Unauthorized — login/cookies may be required.\nURL: {url[:200]}"
    return text


_HTML_MARKERS = (
    b"<!DOCTYPE html",
    b"<!doctype html",
    b"<html",
    b"<head",
)


def _looks_like_html(chunk: bytes, content_type: str) -> bool:
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in {"text/html", "application/xhtml+xml"}:
        return True
    sample = chunk.lstrip()[:64].lower()
    return any(sample.startswith(m) for m in _HTML_MARKERS)


async def download_direct(
    url: str,
    on_progress: Optional[ProgressCallback] = None,
    referer: Optional[str] = None,
) -> DownloadResult:
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
    headers = _http_headers(url, referer=referer)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        try:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status == 403:
                    raise PermissionError(_friendly_http_error(Exception("403"), url))
                resp.raise_for_status()
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
                total = int(resp.headers.get("Content-Length") or 0)
                if total and total > MAX_FILE_SIZE_BYTES:
                    raise ValueError(
                        f"File is too large ({_fmt_size(total)}). "
                        f"Limit is {_fmt_size(MAX_FILE_SIZE_BYTES)}."
                    )

                cd = resp.headers.get("Content-Disposition", "")
                filename = _filename_from_cd(cd)
                if not filename:
                    path_name = Path(unquote(urlparse(str(resp.url)).path)).name
                    filename = path_name if path_name and "." in path_name else "download"
                if "." not in filename:
                    ext = mimetypes.guess_extension(ctype) or ".bin"
                    # Prefer real media extensions; never invent .bin for HTML pages.
                    if ctype.lower() in {"text/html", "application/xhtml+xml"}:
                        ext = ".html"
                    filename = f"{filename}{ext}"

                filename = _safe_filename(filename)
                out_path = _unique_path(TEMP_DIR / filename)
                downloaded = 0
                last_report = 0
                first_chunk: Optional[bytes] = None
                async with aiofiles.open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 256):
                        if first_chunk is None:
                            first_chunk = chunk
                            if _looks_like_html(chunk, ctype):
                                out_path.unlink(missing_ok=True)
                                raise RuntimeError(
                                    "Got an HTML player/page instead of media. "
                                    "This URL needs stream extraction (yt-dlp), "
                                    "not a direct file download.\n"
                                    f"URL: {url[:200]}"
                                )
                        await f.write(chunk)
                        downloaded += len(chunk)
                        if downloaded > MAX_FILE_SIZE_BYTES:
                            out_path.unlink(missing_ok=True)
                            raise ValueError(
                                f"Download exceeded size limit ({_fmt_size(MAX_FILE_SIZE_BYTES)})."
                            )
                        if on_progress and total and downloaded - last_report >= total * 0.1:
                            pct = int(downloaded * 100 / total)
                            await on_progress(
                                f"⬇️ Server download… {pct}% "
                                f"({_fmt_size(downloaded)} / {_fmt_size(total)})"
                            )
                            last_report = downloaded

                return DownloadResult(
                    path=out_path,
                    title=out_path.stem,
                    source="direct",
                    size=out_path.stat().st_size,
                )
        except aiohttp.ClientResponseError as exc:
            raise PermissionError(_friendly_http_error(exc, url)) from exc


async def download_stream(
    url: str,
    on_progress: Optional[ProgressCallback] = None,
) -> DownloadResult:
    loop = asyncio.get_running_loop()
    outtmpl = str(TEMP_DIR / "%(title).180B [%(id)s].%(ext)s")
    progress_state = {"last": ""}

    def hook(d: dict) -> None:
        if d.get("status") != "downloading":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes") or 0
        if total:
            msg = f"⬇️ Server stream… {int(downloaded * 100 / total)}%"
        else:
            msg = f"⬇️ Server stream… {_fmt_size(downloaded)}"
        if msg != progress_state["last"] and on_progress:
            progress_state["last"] = msg
            asyncio.run_coroutine_threadsafe(on_progress(msg), loop)

    ydl_opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "windowsfilenames": True,
        "continuedl": True,
        "nopart": False,
        "overwrites": False,
        "geo_bypass": True,
        "http_headers": _http_headers(url, referer=url),
        "max_filesize": MAX_FILE_SIZE_BYTES,
    }

    def _run() -> DownloadResult:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    raise RuntimeError("Could not extract media info from this URL.")
                if "entries" in info:
                    entries = [e for e in info["entries"] if e]
                    if not entries:
                        raise RuntimeError("No media found in playlist/URL.")
                    info = entries[0]
                filepath = Path(ydl.prepare_filename(info))
                if not filepath.exists():
                    for candidate in filepath.parent.glob(f"{filepath.stem}.*"):
                        if candidate.is_file() and candidate.suffix not in {".part", ".ytdl"}:
                            filepath = candidate
                            break
                if not filepath.exists():
                    raise RuntimeError("Download finished but file was not found.")
                size = filepath.stat().st_size
                if size > MAX_FILE_SIZE_BYTES:
                    filepath.unlink(missing_ok=True)
                    raise ValueError(
                        f"File is too large ({_fmt_size(size)}). "
                        f"Limit is {_fmt_size(MAX_FILE_SIZE_BYTES)}."
                    )
                return DownloadResult(
                    path=filepath,
                    title=info.get("title") or filepath.stem,
                    source="stream",
                    size=size,
                )
        except yt_dlp.utils.DownloadError as exc:
            raise RuntimeError(_friendly_http_error(exc, url)) from exc

    return await loop.run_in_executor(None, _run)


def _hex_reverse_decode(encoded: str) -> str:
    clean = encoded.replace("|", "")
    out = "".join(chr(int(clean[i : i + 2], 16)) for i in range(0, len(clean), 2))
    return out[::-1]


def extract_media_url_from_html(html: str) -> Optional[str]:
    """Pull a real media URL out of an embed/player HTML page when possible."""
    m = re.search(r"_0x1\s*=\s*[\"']([^\"']+)[\"']", html)
    if m:
        try:
            decoded = _hex_reverse_decode(m.group(1))
            if decoded.startswith("http") and any(
                x in decoded for x in (".m3u8", ".mp4", ".mpd")
            ):
                return decoded
        except (ValueError, IndexError):
            pass

    for pat in (
        r"""["'](https?://[^"']+\.m3u8[^"']*)["']""",
        r"""["'](https?://[^"']+\.mp4[^"']*)["']""",
        r"""["'](https?://[^"']+\.mpd[^"']*)["']""",
    ):
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    return None


async def _resolve_player_page(
    url: str,
    on_progress: Optional[ProgressCallback] = None,
) -> Optional[str]:
    """If url is an HTML player page, return the embedded media URL."""
    timeout = aiohttp.ClientTimeout(total=45, sock_connect=20, sock_read=30)
    headers = _http_headers(url, referer=url)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if resp.status >= 400:
                    return None
                # Only sniff pages — skip obvious binary downloads.
                if "text/html" not in ctype and "application/xhtml" not in ctype:
                    # Still peek at small responses that claim octet-stream.
                    if "octet-stream" not in ctype and "text/" not in ctype:
                        return None
                raw = await resp.content.read(512 * 1024)
                if not _looks_like_html(raw, ctype):
                    return None
                media = extract_media_url_from_html(raw.decode("utf-8", "ignore"))
                if media and on_progress:
                    await on_progress("🎬 Found stream inside player page…")
                return media
    except Exception as exc:
        logger.info("player page resolve failed for %s: %s", url, exc)
        return None


_PLAYER_HINT_RE = re.compile(
    r"(vidsonic|/e/|/embed/|/player/)",
    re.I,
)


async def download_url(
    url: str,
    on_progress: Optional[ProgressCallback] = None,
    force_direct: bool = False,
) -> DownloadResult:
    """Download on the bot server into temp — caller uploads to Telegram then deletes."""
    page_url = url
    # Known HTML player/embed hosts: pull real HLS/mp4 before yt-dlp.
    if (
        not force_direct
        and not is_probably_direct_file(url)
        and _PLAYER_HINT_RE.search(url)
    ):
        embedded = await _resolve_player_page(url, on_progress)
        if embedded:
            url = embedded

    if force_direct or is_probably_direct_file(url):
        if on_progress:
            await on_progress("⬇️ Server downloading file…")
        try:
            return await download_direct(url, on_progress, referer=page_url)
        except Exception as exc:
            raise RuntimeError(_friendly_http_error(exc, url)) from exc

    try:
        if on_progress:
            await on_progress("🔍 Server resolving stream…")
        return await download_stream(url, on_progress)
    except Exception as exc:
        logger.info("stream download failed (%s); trying player/direct", exc)
        if url == page_url and not is_probably_direct_file(page_url):
            embedded = await _resolve_player_page(page_url, on_progress)
            if embedded:
                try:
                    return await download_stream(embedded, on_progress)
                except Exception as exc_emb:
                    logger.info("embedded stream failed (%s)", exc_emb)
        if on_progress:
            await on_progress("⬇️ Stream failed — trying direct download…")
        try:
            return await download_direct(url, on_progress, referer=page_url)
        except Exception as exc2:
            # Prefer the clearer 403 message if present
            msg = _friendly_http_error(exc2, url)
            if "403" not in msg:
                msg = f"{_friendly_http_error(exc, url)}\nAlso: {msg}"
            raise RuntimeError(msg) from exc2


def _filename_from_cd(header: str) -> Optional[str]:
    if not header:
        return None
    m = re.search(r"filename\*\s*=\s*([^']*)''([^;]+)", header, re.I)
    if m:
        return _safe_filename(unquote(m.group(2).strip().strip('"')))
    m = re.search(r'filename\s*=\s*"([^"]+)"', header, re.I)
    if m:
        return _safe_filename(unquote(m.group(1)))
    m = re.search(r"filename\s*=\s*([^;]+)", header, re.I)
    if m:
        return _safe_filename(unquote(m.group(1).strip().strip('"')))
    return None
