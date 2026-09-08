"""HTTP helpers with honest error classification (§43).

  * HTTP 451        → GeoBlockedError  (venue refuses this jurisdiction)
  * HTTP 429/418    → RateLimitedError (back off; never hammer)
  * HTTP 401/403    → AuthError        (credentials/permissions)
  * network failure → ExchangeError(status=UNREACHABLE, retryable)

No response is ever faked; callers receive structured failures.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from zepay.core.config import VERSION
from zepay.core.errors import AuthError, ExchangeError, GeoBlockedError, RateLimitedError

UA = f"ZEPAY/{VERSION}"


def http_error_str(e: Exception) -> str:
    if isinstance(e, urllib.error.HTTPError):
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return f"HTTP {e.code} {e.reason} {body}".strip()
    if isinstance(e, urllib.error.URLError):
        return f"network: {e.reason}"
    return f"{type(e).__name__}: {e}"


def classify_http_error(e: Exception, venue: str = "") -> ExchangeError:
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 451:
            return GeoBlockedError(
                "venue refused request from this jurisdiction (HTTP 451 — legal "
                "restriction). Market data from this host is BLOCKED here.",
                venue,
            )
        if e.code in (418, 429):
            retry_after = 5.0
            try:
                retry_after = float(e.headers.get("Retry-After", 5))
            except Exception:
                pass
            return RateLimitedError(f"rate limited (HTTP {e.code})", venue, retry_after)
        if e.code in (401, 403):
            return AuthError(f"auth/permission failure (HTTP {e.code})", venue, status="BLOCKED")
        return ExchangeError(
            http_error_str(e), venue, status="DEGRADED", retryable=500 <= e.code < 600
        )
    return ExchangeError(http_error_str(e), venue, status="UNREACHABLE", retryable=True)


def http_json(
    url: str,
    timeout: float = 15,
    method: str = "GET",
    data=None,
    headers: dict | None = None,
    venue: str = "",
) -> Any:
    """GET/POST JSON with classification. Raises typed ExchangeError subclasses."""
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(
        url,
        method=method,
        headers=hdrs,
        data=(json.dumps(data).encode() if data is not None else None),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace") or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raise classify_http_error(e, venue) from e
    except Exception as e:
        raise classify_http_error(e, venue) from e


def build_url(base: str, path: str, params: dict | None = None) -> str:
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    return f"{base}{path}{qs}"
