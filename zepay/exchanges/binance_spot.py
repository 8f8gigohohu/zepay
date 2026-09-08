"""Binance Spot connector (§9, §51).

Public market data works WITHOUT credentials through the official public
mirror list (data-api.binance.vision first — geo-friendly; then api.binance.com
and the api1-4 cluster). Account/trading endpoints use HMAC-signed requests
against api.binance.com ONLY.

Security invariants (never violated):
  * credentials live in the encrypted SecretsStore, referenced by id in config
  * withdrawal-enabled API keys are REFUSED outright at connection test
  * HTTP 451 (jurisdiction block) is reported as BLOCKED — never bypassed,
    never faked around
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from zepay.core.errors import ExchangeError, GeoBlockedError
from zepay.core.util import mask_key, safe_float, ts_ms, utcnow_iso
from zepay.exchanges.base import Capabilities, ExchangeAdapter
from zepay.exchanges.http import build_url, classify_http_error, http_error_str, http_json
from zepay.exchanges.ratelimit import RateLimiter

log = logging.getLogger("zepay.binance.spot")

# Public market-data hosts, best-first. The .vision mirror is Binance's
# official public REST mirror with the same schema and no auth.
PUBLIC_BASES = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://api4.binance.com",
]
# Signed requests must go to the main API host (mirrors reject signatures).
SIGNED_BASE = "https://api.binance.com"
WS_BASES = [
    "wss://data-stream.binance.vision",
    "wss://stream.binance.com:9443",
    "wss://stream.binance.com:443",
]


class BinanceSpotAdapter(ExchangeAdapter):
    id = "binance_spot"
    name = "Binance (Spot)"

    def __init__(self, secrets, config, audit_fn, rate: RateLimiter | None = None):
        super().__init__()
        self.secrets = secrets
        self.config = config
        self.audit = audit_fn
        self.rate = rate or RateLimiter()
        self.last_signed_ok: str | None = None
        self._public_base_used: str | None = None

    # ---- capabilities ----
    def capabilities(self) -> Capabilities:
        return Capabilities(
            market_data=True,
            websocket=True,
            derivatives_context=False,
            signed_account=True,
            trading=True,
            order_types=["MARKET", "LIMIT", "STOP_LOSS_LIMIT"],
            time_in_force=["GTC", "IOC", "FOK"],
            post_only=False,
            reduce_only=False,
            stop_orders=True,
            trailing_stop=False,
            leverage=False,
            funding_payments=False,
            withdrawal_supported=False,
        )

    # ---- credentials (§50: encrypted store only) ----
    def has_credentials(self) -> bool:
        try:
            return bool(self.secrets.get("exchange_api_key")) and bool(
                self.secrets.get("exchange_api_secret")
            )
        except Exception:
            return False

    def set_credentials(self, api_key: str, api_secret: str) -> None:
        self.secrets.put("exchange_api_key", api_key, purpose="exchange")
        self.secrets.put("exchange_api_secret", api_secret, purpose="exchange")
        self.audit(
            "exchange",
            "credentials_stored",
            {"venue": self.id, "key": mask_key(api_key)},
            actor="user",
        )

    def clear_credentials(self) -> None:
        self.secrets.delete("exchange_api_key")
        self.secrets.delete("exchange_api_secret")
        self.audit("exchange", "credentials_cleared", {"venue": self.id}, actor="user")

    # ---- request plumbing ----
    def _public(self, path: str, params: dict | None = None, timeout: float = 12):
        """Public REST with host failover. Rate-limit responses (418/429) are
        raised immediately — we never hammer a second host to dodge a ban."""
        last: Exception | None = None
        geo_blocked_all = True
        for base in PUBLIC_BASES:
            url = build_url(base, path, params)
            try:
                self.rate.wait("binance-public", 0.05)
                out = http_json(url, timeout=timeout, venue=self.id)
                self._public_base_used = base
                self._ok()
                return out
            except GeoBlockedError as e:
                last = e
                continue  # try the next host
            except ExchangeError as e:
                geo_blocked_all = False
                if e.status == "DEGRADED" and "HTTP 4" in str(e):
                    raise  # client error: same on every host
                last = e
                continue
        if last is not None and geo_blocked_all and isinstance(last, GeoBlockedError):
            self._fail("all public hosts returned HTTP 451 (jurisdiction block)", "BLOCKED")
            raise last
        self._fail(http_error_str(last) if last else "unknown error", "UNREACHABLE")
        raise last or ExchangeError("no public host reachable", self.id, status="UNREACHABLE")

    def _signed(
        self, path: str, params: dict | None = None, method: str = "GET", timeout: float = 12
    ):
        key = self.secrets.get("exchange_api_key")
        secret = self.secrets.get("exchange_api_secret")
        if not key or not secret:
            raise PermissionError("exchange credentials not configured")
        params = dict(params or {})
        params["timestamp"] = ts_ms()
        params["recvWindow"] = 5000
        qs = urllib.parse.urlencode(params)
        sig = hmac.new(secret.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{SIGNED_BASE}{path}?{qs}&signature={sig}"
        req = urllib.request.Request(
            url, method=method, headers={"X-MBX-APIKEY": key, "User-Agent": "ZEPAY/3"}
        )
        try:
            self.rate.wait("binance-signed", 0.05)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace") or "{}")
            self.last_signed_ok = utcnow_iso()
            self._ok()
            return data
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            self._fail(f"HTTP {e.code} {body}", "BLOCKED" if e.code == 451 else "DEGRADED")
            raise classify_http_error(
                urllib.error.HTTPError(url, e.code, f"{e.reason} {body}", e.headers, None), self.id
            ) from e
        except Exception as e:
            self._fail(http_error_str(e))
            raise classify_http_error(e, self.id) from e

    # ---- public endpoints ----
    def ping(self):
        return self._public("/api/v3/ping", timeout=8)

    def exchange_info(self, symbols: list | None = None):
        params = {}
        if symbols:
            params["symbols"] = json.dumps([s.upper() for s in symbols])
        return self._public("/api/v3/exchangeInfo", params, timeout=20)

    def ticker_24h(self, symbol: str | None = None):
        return self._public(
            "/api/v3/ticker/24hr", {"symbol": symbol} if symbol else None, timeout=15
        )

    def klines(self, symbol: str, interval: str = "1h", limit: int = 200):
        return self._public(
            "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit}, timeout=20
        )

    def depth(self, symbol: str, limit: int = 20):
        return self._public("/api/v3/depth", {"symbol": symbol, "limit": limit}, timeout=10)

    def recent_trades(self, symbol: str, limit: int = 100):
        return self._public("/api/v3/trades", {"symbol": symbol, "limit": limit}, timeout=10)

    # ---- signed endpoints ----
    def account(self, timeout: float = 12):
        return self._signed("/api/v3/account", method="GET", timeout=timeout)

    def open_orders(self, symbol: str | None = None):
        return self._signed("/api/v3/openOrders", {"symbol": symbol} if symbol else None)

    def get_order(self, symbol: str, order_id=None, client_order_id=None):
        p: dict = {"symbol": symbol}
        if order_id:
            p["orderId"] = order_id
        if client_order_id:
            p["origClientOrderId"] = client_order_id
        return self._signed("/api/v3/order", p)

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "MARKET",
        price: float | None = None,
        client_order_id: str | None = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        stop_price: float | None = None,
        **kwargs,
    ):
        p: dict = {"symbol": symbol, "side": side, "type": order_type}
        if order_type == "MARKET":
            p["quantity"] = qty
        elif order_type == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            p["quantity"] = qty
            p["price"] = price
            p["timeInForce"] = time_in_force
        elif order_type == "STOP_LOSS_LIMIT":
            if price is None or stop_price is None:
                raise ValueError("STOP_LOSS_LIMIT requires price and stopPrice")
            p["quantity"] = qty
            p["price"] = price
            p["stopPrice"] = stop_price
            p["timeInForce"] = time_in_force
        else:
            raise ValueError(f"unsupported order type for venue: {order_type}")
        if client_order_id:
            p["newClientOrderId"] = str(client_order_id)[:36]
        return self._signed("/api/v3/order", p, method="POST", timeout=12)

    def cancel_order(self, symbol: str, order_id=None, client_order_id=None):
        p: dict = {"symbol": symbol}
        if order_id:
            p["orderId"] = order_id
        if client_order_id:
            p["origClientOrderId"] = client_order_id
        return self._signed("/api/v3/order", p, method="DELETE")

    def my_trades(self, symbol: str, limit: int = 50):
        return self._signed("/api/v3/myTrades", {"symbol": symbol, "limit": limit})

    # ---- connection test (§51: withdrawal keys REFUSED) ----
    def test_connection(self) -> dict:
        out: dict = {"connected": False, "venue": self.id, "checks": {}}
        try:
            self.ping()
            out["checks"]["public_api"] = True
            out["public_base"] = self._public_base_used
        except Exception as e:
            out["checks"]["public_api"] = False
            out["status"] = "BLOCKED" if isinstance(e, GeoBlockedError) else "UNREACHABLE"
            out["error"] = f"public API unreachable: {http_error_str(e)}"
            return out
        if not self.has_credentials():
            out["status"] = "NOT_CONFIGURED"
            out["error"] = "credentials not configured (public market data still available)"
            return out
        try:
            acct = self.account(timeout=10)
        except Exception as e:
            out["checks"]["signed_account"] = False
            out["status"] = "BLOCKED" if isinstance(e, GeoBlockedError) else "DEGRADED"
            out["error"] = f"signed request failed: {http_error_str(e)}"
            return out
        out["checks"]["signed_account"] = True
        can_trade = bool(acct.get("canTrade"))
        can_withdraw = bool(acct.get("canWithdraw"))
        out["checks"]["can_trade"] = can_trade
        out["checks"]["can_withdraw"] = can_withdraw
        out["permissions"] = {"canTrade": can_trade, "canWithdraw": can_withdraw}
        if can_withdraw:
            out["connected"] = False
            out["status"] = "BLOCKED"
            out["error"] = (
                "SECURITY REFUSAL: this API key has WITHDRAWAL permission. "
                "ZEPAY refuses withdrawal-enabled keys. Create a trade-only "
                "key with withdrawals DISABLED."
            )
            self.audit("security", "withdrawal_key_refused", {"venue": self.id})
            return out
        if not can_trade:
            out["connected"] = False
            out["status"] = "DEGRADED"
            out["error"] = "API key lacks trading permission (canTrade=false)."
            return out
        balances = [
            b
            for b in acct.get("balances", [])
            if safe_float(b.get("free")) + safe_float(b.get("locked")) > 0
        ]
        out["connected"] = True
        out["status"] = "OK"
        out["balances"] = balances
        out["account_type"] = "SPOT"
        out["message"] = "CONNECTED — credentials verified against the real exchange account."
        self.audit(
            "exchange", "connection_verified", {"venue": self.id, "n_balances": len(balances)}
        )
        return out

    # ---- websocket bases (used by the WS manager) ----
    def ws_urls(self) -> list[str]:
        return list(WS_BASES)
