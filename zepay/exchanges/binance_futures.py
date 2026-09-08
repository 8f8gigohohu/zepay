"""Binance USDT-M Futures connector (§9) — public context first.

Provides funding rate / mark price / index price / open interest as market
CONTEXT for the AI (real values only), plus the signed futures account/order
API for when futures trading is explicitly enabled by the operator.

Honesty: fapi.binance.com is jurisdiction-restricted in some regions
(HTTP 451). When blocked, this adapter reports status=BLOCKED and every
consumer shows funding/OI as UNAVAILABLE — never estimated, never faked.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from zepay.core.errors import ExchangeError, GeoBlockedError
from zepay.core.util import safe_float, ts_ms, utcnow_iso
from zepay.exchanges.base import Capabilities, ExchangeAdapter
from zepay.exchanges.http import classify_http_error, http_error_str, http_json
from zepay.exchanges.ratelimit import RateLimiter

log = logging.getLogger("zepay.binance.futures")

FAPI_BASES = ["https://fapi.binance.com"]
FAPI_WS = ["wss://fstream.binance.com"]


class BinanceFuturesAdapter(ExchangeAdapter):
    id = "binance_futures"
    name = "Binance (USDT-M Futures)"

    def __init__(self, secrets, config, audit_fn, rate: RateLimiter | None = None):
        super().__init__()
        self.secrets = secrets
        self.config = config
        self.audit = audit_fn
        self.rate = rate or RateLimiter()
        self.last_signed_ok: str | None = None

    def capabilities(self) -> Capabilities:
        return Capabilities(
            market_data=True,
            websocket=True,
            derivatives_context=True,
            signed_account=True,
            trading=True,
            order_types=[
                "MARKET",
                "LIMIT",
                "STOP",
                "STOP_MARKET",
                "TAKE_PROFIT",
                "TAKE_PROFIT_MARKET",
                "TRAILING_STOP_MARKET",
            ],
            time_in_force=["GTC", "IOC", "FOK"],
            post_only=True,
            reduce_only=True,
            stop_orders=True,
            trailing_stop=True,
            leverage=True,
            funding_payments=True,
            withdrawal_supported=False,
        )

    # ---- credentials (separate trade-only key recommended for futures) ----
    def has_credentials(self) -> bool:
        try:
            return bool(self.secrets.get("futures_api_key")) and bool(
                self.secrets.get("futures_api_secret")
            )
        except Exception:
            return False

    def set_credentials(self, api_key: str, api_secret: str) -> None:
        from zepay.core.util import mask_key

        self.secrets.put("futures_api_key", api_key, purpose="exchange")
        self.secrets.put("futures_api_secret", api_secret, purpose="exchange")
        self.audit(
            "exchange",
            "credentials_stored",
            {"venue": self.id, "key": mask_key(api_key)},
            actor="user",
        )

    def clear_credentials(self) -> None:
        self.secrets.delete("futures_api_key")
        self.secrets.delete("futures_api_secret")

    # ---- plumbing ----
    def _public(self, path: str, params: dict | None = None, timeout: float = 10):
        last: Exception | None = None
        for base in FAPI_BASES:
            qs = ("?" + urllib.parse.urlencode(params)) if params else ""
            try:
                self.rate.wait("binance-fapi", 0.06)
                out = http_json(f"{base}{path}{qs}", timeout=timeout, venue=self.id)
                self._ok()
                return out
            except GeoBlockedError as e:
                last = e
            except ExchangeError as e:
                last = e
        if isinstance(last, GeoBlockedError):
            self._fail(
                "fapi returned HTTP 451 — derivatives context BLOCKED in this "
                "jurisdiction; funding/OI show as UNAVAILABLE",
                "BLOCKED",
            )
        else:
            self._fail(http_error_str(last) if last else "unreachable", "UNREACHABLE")
        raise last or ExchangeError("futures public API unreachable", self.id)

    def _signed(
        self, path: str, params: dict | None = None, method: str = "GET", timeout: float = 12
    ):
        key = self.secrets.get("futures_api_key")
        secret = self.secrets.get("futures_api_secret")
        if not key or not secret:
            raise PermissionError("futures credentials not configured")
        params = dict(params or {})
        params["timestamp"] = ts_ms()
        params["recvWindow"] = 5000
        qs = urllib.parse.urlencode(params)
        sig = hmac.new(secret.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{FAPI_BASES[0]}{path}?{qs}&signature={sig}"
        req = urllib.request.Request(
            url, method=method, headers={"X-MBX-APIKEY": key, "User-Agent": "ZEPAY/3"}
        )
        try:
            self.rate.wait("binance-fapi-signed", 0.06)
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

    # ---- public data ----
    def ping(self):
        return self._public("/fapi/v1/ping", timeout=8)

    def premium_index(self, symbol: str | None = None):
        return self._public("/fapi/v1/premiumIndex", {"symbol": symbol} if symbol else None)

    def open_interest(self, symbol: str):
        return self._public("/fapi/v1/openInterest", {"symbol": symbol})

    def funding_rate_history(self, symbol: str, limit: int = 10):
        return self._public("/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})

    def klines(self, symbol: str, interval: str = "1h", limit: int = 200):
        return self._public(
            "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit}, timeout=20
        )

    def depth(self, symbol: str, limit: int = 20):
        return self._public("/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    def long_short_ratio(self, symbol: str, period: str = "1h"):
        return self._public(
            "/futures/data/globalLongShortAccountRatio",
            {"symbol": symbol, "period": period, "limit": 5},
        )

    def exchange_info(self, symbols=None):
        return self._public("/fapi/v1/exchangeInfo", timeout=20)

    def ticker_24h(self, symbol: str | None = None):
        return self._public(
            "/fapi/v1/ticker/24hr", {"symbol": symbol} if symbol else None, timeout=15
        )

    def funding_info(self, symbol: str) -> dict:
        """Real funding/mark/index/OI bundle. available=False when blocked."""
        out: dict[str, Any] = {
            "symbol": symbol,
            "funding_rate": None,
            "mark_price": None,
            "index_price": None,
            "open_interest": None,
            "next_funding_time": None,
            "available": False,
            "source": self.id,
            "ts": ts_ms(),
        }
        try:
            d = self.premium_index(symbol)
            out["funding_rate"] = safe_float(d.get("lastFundingRate"))
            out["mark_price"] = safe_float(d.get("markPrice"))
            out["index_price"] = safe_float(d.get("indexPrice"))
            nft = d.get("nextFundingTime")
            out["next_funding_time"] = int(nft) if nft else None
            out["available"] = True
        except Exception as e:
            out["error"] = http_error_str(e)
        if out["available"]:
            try:
                d = self.open_interest(symbol)
                out["open_interest"] = safe_float(d.get("openInterest"))
            except Exception:
                pass  # OI optional; funding already real
        return out

    # ---- signed account/trading (only used when futures explicitly enabled) ----
    def account(self, timeout: float = 12):
        return self._signed("/fapi/v2/account", timeout=timeout)

    def position_risk(self, symbol: str | None = None):
        return self._signed("/fapi/v2/positionRisk", {"symbol": symbol} if symbol else None)

    def open_orders(self, symbol: str | None = None):
        return self._signed("/fapi/v1/openOrders", {"symbol": symbol} if symbol else None)

    def get_order(self, symbol: str, order_id=None, client_order_id=None):
        p: dict = {"symbol": symbol}
        if order_id:
            p["orderId"] = order_id
        if client_order_id:
            p["origClientOrderId"] = client_order_id
        return self._signed("/fapi/v1/order", p)

    def place_order(
        self,
        symbol: str,
        side: str,
        qty,
        order_type: str = "MARKET",
        price=None,
        client_order_id: str | None = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        stop_price=None,
        position_side: str | None = None,
        **kwargs,
    ):
        p: dict = {"symbol": symbol, "side": side, "type": order_type, "quantity": qty}
        if order_type in ("LIMIT", "STOP", "STOP_LIMIT", "TAKE_PROFIT"):
            p["timeInForce"] = time_in_force
            if price is not None:
                p["price"] = price
        if order_type in (
            "STOP",
            "STOP_MARKET",
            "STOP_LIMIT",
            "TAKE_PROFIT",
            "TAKE_PROFIT_MARKET",
            "TRAILING_STOP_MARKET",
        ):
            if stop_price is None:
                raise ValueError(f"{order_type} requires stopPrice")
            p["stopPrice"] = stop_price
        if reduce_only:
            p["reduceOnly"] = "true"
        if position_side:
            p["positionSide"] = position_side
        if client_order_id:
            p["newClientOrderId"] = str(client_order_id)[:36]
        return self._signed("/fapi/v1/order", p, method="POST")

    def cancel_order(self, symbol: str, order_id=None, client_order_id=None):
        p: dict = {"symbol": symbol}
        if order_id:
            p["orderId"] = order_id
        if client_order_id:
            p["origClientOrderId"] = client_order_id
        return self._signed("/fapi/v1/order", p, method="DELETE")

    def cancel_all(self, symbol: str):
        return self._signed("/fapi/v1/allOpenOrders", {"symbol": symbol}, method="DELETE")

    def my_trades(self, symbol: str, limit: int = 50):
        return self._signed("/fapi/v1/userTrades", {"symbol": symbol, "limit": limit})

    def set_leverage(self, symbol: str, leverage: int):
        return self._signed(
            "/fapi/v1/leverage", {"symbol": symbol, "leverage": int(leverage)}, method="POST"
        )

    def test_connection(self) -> dict:
        out: dict = {"connected": False, "venue": self.id, "checks": {}}
        try:
            self.ping()
            out["checks"]["public_api"] = True
            out["status"] = "OK"
        except GeoBlockedError as e:
            out["checks"]["public_api"] = False
            out["status"] = "BLOCKED"
            out["error"] = str(e)
            return out
        except Exception as e:
            out["checks"]["public_api"] = False
            out["status"] = "UNREACHABLE"
            out["error"] = http_error_str(e)
            return out
        if not self.has_credentials():
            out["status"] = "NOT_CONFIGURED" if not out["checks"]["public_api"] else "OK"
            out["error"] = (
                "futures credentials not configured — public derivatives " "context only"
                if out["checks"]["public_api"]
                else out.get("error")
            )
            return out
        try:
            acct = self.account(timeout=10)
            out["checks"]["signed_account"] = True
            out["connected"] = True
            out["total_wallet_balance"] = safe_float(acct.get("totalWalletBalance"))
        except Exception as e:
            out["checks"]["signed_account"] = False
            out["error"] = f"signed request failed: {http_error_str(e)}"
        return out

    def ws_urls(self) -> list[str]:
        return list(FAPI_WS)
