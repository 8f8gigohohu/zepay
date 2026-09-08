"""Cache / streaming layer (§45).

Redis when configured and reachable — used for market cache, event fan-out,
job queues, rate-limit counters, distributed locks and WebSocket state.
An in-process cache provides the same interface so the platform always works;
its status is reported honestly (REDIS: OK | UNAVAILABLE | NOT_CONFIGURED).
PostgreSQL/SQLite remains the source of truth for all persistent trading
records — nothing critical lives only in Redis.
"""

from __future__ import annotations

import threading
import time


class Cache:
    backend = "abstract"

    def get(self, key: str) -> str | None:
        raise NotImplementedError

    def set(self, key: str, value: str, ttl: float | None = None) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def incr(self, key: str, ttl: float | None = None) -> int:
        raise NotImplementedError

    def publish(self, channel: str, message: str) -> None:
        raise NotImplementedError

    def lock(self, name: str, ttl: float = 10.0) -> bool:
        raise NotImplementedError

    def unlock(self, name: str) -> None:
        raise NotImplementedError

    def health(self) -> dict:
        raise NotImplementedError


class MemoryCache(Cache):
    backend = "memory"

    def __init__(self, status_note: str = ""):
        self._d: dict[str, tuple[str, float]] = {}
        self._locks: dict[str, float] = {}
        self._lock = threading.RLock()
        self._channels: dict[str, list[str]] = {}
        self.status_note = status_note

    def _expired(self, exp: float) -> bool:
        return exp > 0 and time.time() > exp

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._d.get(key)
            if not item:
                return None
            val, exp = item
            if self._expired(exp):
                self._d.pop(key, None)
                return None
            return val

    def set(self, key: str, value: str, ttl: float | None = None) -> None:
        with self._lock:
            self._d[key] = (value, time.time() + ttl if ttl else 0.0)

    def delete(self, key: str) -> None:
        with self._lock:
            self._d.pop(key, None)

    def incr(self, key: str, ttl: float | None = None) -> int:
        with self._lock:
            cur = self.get(key)
            n = int(cur or 0) + 1
            self.set(key, str(n), ttl)
            return n

    def publish(self, channel: str, message: str) -> None:
        with self._lock:
            self._channels.setdefault(channel, []).append(message)
            del self._channels[channel][:-1000]

    def lock(self, name: str, ttl: float = 10.0) -> bool:
        with self._lock:
            now = time.time()
            exp = self._locks.get(name, 0)
            if exp > now:
                return False
            self._locks[name] = now + ttl
            return True

    def unlock(self, name: str) -> None:
        with self._lock:
            self._locks.pop(name, None)

    def health(self) -> dict:
        return {
            "backend": "memory",
            "status": "OK",
            "note": self.status_note or "in-process cache (Redis not configured)",
        }


class RedisCache(Cache):
    backend = "redis"

    def __init__(self, url: str, socket_timeout: float = 2.0):
        import redis  # lazy import

        self._r = redis.Redis.from_url(
            url, socket_timeout=socket_timeout, socket_connect_timeout=3.0, decode_responses=True
        )
        self._r.ping()

    def get(self, key: str) -> str | None:
        v = self._r.get(key)
        return v if v is None or isinstance(v, str) else str(v)

    def set(self, key: str, value: str, ttl: float | None = None) -> None:
        if ttl:
            self._r.set(key, value, ex=max(1, int(ttl)))
        else:
            self._r.set(key, value)

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def incr(self, key: str, ttl: float | None = None) -> int:
        n = self._r.incr(key)
        if ttl and n == 1:
            self._r.expire(key, max(1, int(ttl)))
        return int(n)

    def publish(self, channel: str, message: str) -> None:
        self._r.publish(channel, message)

    def lock(self, name: str, ttl: float = 10.0) -> bool:
        return bool(self._r.set(f"lock:{name}", "1", nx=True, ex=max(1, int(ttl))))

    def unlock(self, name: str) -> None:
        self._r.delete(f"lock:{name}")

    def health(self) -> dict:
        try:
            self._r.ping()
            info = self._r.info("server")
            return {"backend": "redis", "status": "OK", "version": info.get("redis_version", "?")}
        except Exception as e:
            return {"backend": "redis", "status": "UNAVAILABLE", "error": str(e)[:160]}


def open_cache(redis_url: str, sys_event=None) -> Cache:
    """Factory with honest degradation: Redis requested but unreachable →
    MemoryCache with a loud note. Trading correctness never depends on Redis."""
    if redis_url:
        try:
            c = RedisCache(redis_url)
            if sys_event:
                sys_event("cache", "INFO", f"Redis connected: {redis_url.split('@')[-1]}")
            return c
        except Exception as e:
            note = f"Redis UNAVAILABLE ({str(e)[:120]}) — using in-process cache"
            if sys_event:
                sys_event("cache", "WARN", note)
            return MemoryCache(status_note=note)
    return MemoryCache(status_note="Redis NOT_CONFIGURED — using in-process cache")
