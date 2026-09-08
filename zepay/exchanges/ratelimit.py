"""Simple cooperative rate limiter (ported from v2). Per-name minimum
interval; `wait` blocks, `allow` reports."""

from __future__ import annotations

import threading
import time


class RateLimiter:
    def __init__(self):
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def allow(self, name: str, min_interval: float = 0.05) -> bool:
        with self._lock:
            now = time.time()
            if now - self._last.get(name, 0) >= min_interval:
                self._last[name] = now
                return True
            return False

    def wait(self, name: str, min_interval: float = 0.05) -> None:
        while True:
            with self._lock:
                now = time.time()
                dt = now - self._last.get(name, 0)
                if dt >= min_interval:
                    self._last[name] = now
                    return
                sleep_for = min_interval - dt
            time.sleep(min(sleep_for, 0.5))
