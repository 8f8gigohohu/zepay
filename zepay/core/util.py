"""Shared helpers (ported from ZEPAY v2, semantics preserved)."""

from __future__ import annotations

import json
import math
import re
import time
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def utcnow_iso() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ts_ms() -> int:
    return int(time.time() * 1000)


def safe_float(x, d: float = 0.0) -> float:
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return d
        return v
    except (TypeError, ValueError):
        return d


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def pct(x, digits: int = 2) -> str:
    return f"{safe_float(x) * 100:.{digits}f}"


_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|api[_-]?secret|secret|passphrase|password|token|private[_-]?key|"
    r"seed[_-]?phrase|mnemonic|authorization|signature)",
    re.I,
)


def redact(obj):
    """Deep-redact anything that looks like a secret (never logged/sent out)."""
    if isinstance(obj, dict):
        return {
            k: ("***REDACTED***" if _SECRET_KEY_RE.search(str(k)) else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and len(obj) >= 24 and re.fullmatch(r"[A-Za-z0-9+/=_\-]{24,}", obj):
        return obj[:4] + "***REDACTED***"
    return obj


def mask_key(k: str) -> str:
    if not k:
        return ""
    return (k[:4] + "…" + k[-4:]) if len(k) > 12 else "***"


def jdump(o) -> str:
    return json.dumps(o, default=str, separators=(",", ":"))
