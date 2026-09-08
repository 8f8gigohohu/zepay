"""ID generation: sortable, unique, prefixed (§32: every order gets a
client_order_id; decisions/risks/experiments all get traceable ids)."""

from __future__ import annotations

import os
import secrets
import threading
import time

_counter_lock = threading.Lock()
_counter = 0
_BOOT = secrets.token_hex(2)


def _next_counter() -> int:
    global _counter
    with _counter_lock:
        _counter += 1
        return _counter


def new_id(prefix: str) -> str:
    """e.g. new_id('ord') -> 'ord-1a2b3c4d-000001-ab12cd'."""
    ts = format(int(time.time() * 1000) & 0xFFFFFFFF, "08x")
    n = format(_next_counter() & 0xFFFFFF, "06x")
    return f"{prefix}-{ts}-{n}-{secrets.token_hex(3)}"


def client_order_id(symbol: str, side: str) -> str:
    """Exchange-safe client order id (Binance allows ^[\\.A-Za-z0-9_-]{1,36}$)."""
    base = "".join(ch for ch in symbol if ch.isalnum())[:10]
    return f"zp-{base}-{side[:1]}-{_BOOT}-{_next_counter():06d}"[:36]


def idempotency_key(*parts: str) -> str:
    import hashlib

    h = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]
    return f"idk-{h}"


def pid_file_path(data_dir: str) -> str:
    return os.path.join(data_dir, "zepay.pid")
