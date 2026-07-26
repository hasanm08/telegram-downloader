"""Download torrents from .torrent files or magnet links via aria2c."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Optional

from config import MAX_FILE_SIZE_BYTES, TEMP_DIR

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


@dataclass
class TorrentResult:
    path: Path
    title: str
    size: int
    source: str = "torrent"


def aria2_available() -> bool:
    return shutil.which("aria2c") is not None


def _fmt_size(n: int) -> str:
    x = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if x < 1024:
            return f"{x:.1f} {unit}" if unit != "B" else f"{int(x)} B"
        x /= 1024
    return f"{x:.1f} TB"


def _session_key(torrent_path: Optional[Path], magnet: Optional[str]) -> str:
    """Stable id so Action restarts resume the same aria2 session directory."""
    if magnet:
        raw = magnet.strip().encode()
    else:
        assert torrent_path is not None
        raw = torrent_path.read_bytes()
    return hashlib.sha1(raw).hexdigest()[:16]


def _pick_output(session_dir: Path) -> Path:
    files = [p for p in session_dir.rglob("*") if p.is_file()]
    files = [p for p in files if not p.name.endswith(".aria2") and p.suffix != ".torrent"]
    if not files:
        raise RuntimeError("Torrent finished but no files were found.")

    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    largest = files[0]
    size = largest.stat().st_size
    if size > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"Largest file is {_fmt_size(size)} (limit {_fmt_size(MAX_FILE_SIZE_BYTES)})."
        )
    if size == 0:
        raise RuntimeError("Downloaded file is empty.")
    return largest


async def download_torrent(
    torrent_path: Optional[Path] = None,
    magnet: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> TorrentResult:
    if not aria2_available():
        raise RuntimeError("aria2c is not installed. Run: brew install aria2")
    if not torrent_path and not magnet:
        raise ValueError("Provide a .torrent file or a magnet link.")

    key = _session_key(torrent_path, magnet)
    session_dir = TEMP_DIR / f"torrent_{key}"
    session_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "aria2c",
        "--dir", str(session_dir),
        "--seed-time=0",
        "--max-overall-upload-limit=1K",
        "--bt-stop-timeout=600",
        "--bt-max-peers=64",
        "--file-allocation=none",
        "--continue=true",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--max-connection-per-server=16",
        "--split=16",
        "--min-split-size=1M",
        "--summary-interval=5",
        "--console-log-level=notice",
        "--max-download-limit=0",
        "--bt-max-open-files=100",
    ]

    if torrent_path:
        if not torrent_path.exists():
            raise FileNotFoundError(f"Torrent file not found: {torrent_path}")
        local_torrent = session_dir / "input.torrent"
        if (
            not local_torrent.exists()
            or local_torrent.stat().st_size != torrent_path.stat().st_size
        ):
            shutil.copy2(torrent_path, local_torrent)
        cmd.append(str(local_torrent))
        label = torrent_path.name
    else:
        cmd.append(magnet.strip())
        label = "magnet"

    if on_progress:
        await on_progress(f"🧲 Starting torrent (resumable)…\n{label}")

    loop = asyncio.get_running_loop()

    def _run() -> tuple[int, str]:
        import subprocess

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        lines: list[str] = []
        last_pct = ""
        for line in proc.stdout:
            line = line.rstrip()
            lines.append(line)
            if "%" in line and ("DL:" in line or "ETA:" in line or "[" in line):
                if line != last_pct and on_progress:
                    last_pct = line
                    msg = f"🧲 {_short_progress(line)}"
                    asyncio.run_coroutine_threadsafe(on_progress(msg), loop)
        code = proc.wait()
        return code, "\n".join(lines[-40:])

    code, tail = await loop.run_in_executor(None, _run)
    if code != 0:
        # Keep session_dir so the next Action run can resume
        raise RuntimeError(f"aria2c failed (exit {code}):\n{tail[:800]}")

    chosen = _pick_output(session_dir)
    final = TEMP_DIR / chosen.name
    final = _unique_path(final)
    shutil.move(str(chosen), str(final))
    shutil.rmtree(session_dir, ignore_errors=True)

    size = final.stat().st_size
    if size > MAX_FILE_SIZE_BYTES:
        final.unlink(missing_ok=True)
        raise ValueError(
            f"File is too large ({_fmt_size(size)}). "
            f"Limit is {_fmt_size(MAX_FILE_SIZE_BYTES)}."
        )

    return TorrentResult(path=final, title=final.stem, size=size)


def _short_progress(line: str) -> str:
    text = line.strip()
    if len(text) > 180:
        text = text[:177] + "…"
    return text


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 10_000):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("Could not allocate unique filename.")
