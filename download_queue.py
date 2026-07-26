"""Global download queue — up to N concurrent jobs."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from config import MAX_CONCURRENT_DOWNLOADS

logger = logging.getLogger(__name__)

_semaphore: Optional[asyncio.Semaphore] = None
_active = 0
_waiting = 0
_lock: Optional[asyncio.Lock] = None


def _ensure() -> tuple[asyncio.Semaphore, asyncio.Lock]:
    global _semaphore, _lock
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    if _lock is None:
        _lock = asyncio.Lock()
    return _semaphore, _lock


async def queue_stats() -> tuple[int, int, int]:
    """Return (active, waiting, max_concurrent)."""
    _, lock = _ensure()
    async with lock:
        return _active, _waiting, MAX_CONCURRENT_DOWNLOADS


class DownloadSlot:
    """Async context manager that reserves one concurrent download slot."""

    def __init__(self, label: str = ""):
        self.label = label
        self._sem: Optional[asyncio.Semaphore] = None
        self._acquired = False

    async def __aenter__(self) -> "DownloadSlot":
        global _active, _waiting
        sem, lock = _ensure()
        self._sem = sem
        async with lock:
            _waiting += 1
            active, waiting, maximum = _active, _waiting, MAX_CONCURRENT_DOWNLOADS
        if waiting > 1 or active >= maximum:
            logger.info(
                "Queued %s (active=%s waiting=%s max=%s)",
                self.label[:80],
                active,
                waiting,
                maximum,
            )
        await sem.acquire()
        self._acquired = True
        async with lock:
            _waiting -= 1
            _active += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        global _active
        _, lock = _ensure()
        if self._acquired and self._sem is not None:
            self._sem.release()
            async with lock:
                _active = max(0, _active - 1)
