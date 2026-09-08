"""ZEPAY native venue adapter (§9: INR balances, markets, orders, fills).

HONESTY: no ZEPAY venue API specification ships with this build (same policy
as v2's license adapter). Until an operator configures a real endpoint spec
(Settings → ZEPAY venue: base URL + auth scheme), this adapter reports
NOT_CONFIGURED and every ZEPAY-venue feature in the UI shows exactly that.
Nothing is simulated and presented as real: paper trading uses the PAPER
venue (real prices, simulated fills — clearly labeled), never this adapter.

The class defines the FULL contract (balances / markets / orders / fills /
positions / fees / limits / real-time data) so that when a real ZEPAY API spec
becomes available, integration is configuration + implementing the marked
TODO(spec) points — no engine changes.
"""

from __future__ import annotations

import logging
from typing import Any

from zepay.core.errors import ExchangeError
from zepay.exchanges.base import Capabilities, ExchangeAdapter
from zepay.exchanges.http import http_error_str, http_json

log = logging.getLogger("zepay.venue.zepay")


class ZepayVenueAdapter(ExchangeAdapter):
    id = "zepay"
    name = "ZEPAY (INR)"

    def __init__(self, secrets, config, audit_fn):
        super().__init__()
        self.secrets = secrets
        self.config = config
        self.audit = audit_fn
        self._status = "NOT_CONFIGURED"
        self.last_error = (
            "ZEPAY venue API specification is not bundled with this build. "
            "Configure a real endpoint in Settings to activate. Until then "
            "this venue is NOT_CONFIGURED — never faked as connected."
        )

    # ---- configuration ----
    def base_url(self) -> str | None:
        return self.config.get("zepay_venue_base_url") or None

    def configured(self) -> bool:
        return bool(self.base_url())

    def capabilities(self) -> Capabilities:
        return Capabilities(
            market_data=self.configured(),
            websocket=False,
            derivatives_context=False,
            signed_account=self.configured(),
            trading=self.configured(),
            order_types=["MARKET", "LIMIT"],
            time_in_force=["GTC", "IOC"],
            withdrawal_supported=False,
        )

    # ---- credentials (only ever used against an operator-configured endpoint) ----
    def has_credentials(self) -> bool:
        try:
            return bool(self.secrets.get("zepay_api_key"))
        except Exception:
            return False

    def set_credentials(self, api_key: str, api_secret: str = "") -> None:
        from zepay.core.util import mask_key

        self.secrets.put("zepay_api_key", api_key, purpose="zepay_venue")
        if api_secret:
            self.secrets.put("zepay_api_secret", api_secret, purpose="zepay_venue")
        self.audit(
            "exchange",
            "credentials_stored",
            {"venue": self.id, "key": mask_key(api_key)},
            actor="user",
        )

    def clear_credentials(self) -> None:
        self.secrets.delete("zepay_api_key")
        self.secrets.delete("zepay_api_secret")

    def _require_configured(self):
        if not self.configured():
            self._status = "NOT_CONFIGURED"
            raise ExchangeError(
                self.last_error or "ZEPAY venue not configured", self.id, status="NOT_CONFIGURED"
            )

    def _headers(self) -> dict:
        key = self.secrets.get("zepay_api_key") or ""
        return {"Authorization": f"Bearer {key}"} if key else {}

    # ---- TODO(spec): exact paths/payloads come from the real ZEPAY API spec ----
    def ping(self):
        self._require_configured()
        out = http_json(
            f"{self.base_url()}/health", timeout=8, headers=self._headers(), venue=self.id
        )
        self._ok()
        return out

    def exchange_info(self, symbols=None):
        self._require_configured()
        out = http_json(
            f"{self.base_url()}/markets", timeout=15, headers=self._headers(), venue=self.id
        )
        self._ok()
        return out

    def ticker_24h(self, symbol: str | None = None):
        self._require_configured()
        path = "/tickers" + (f"?symbol={symbol}" if symbol else "")
        out = http_json(
            f"{self.base_url()}{path}", timeout=12, headers=self._headers(), venue=self.id
        )
        self._ok()
        return out

    def klines(self, symbol: str, interval: str = "1h", limit: int = 200):
        self._require_configured()
        out = http_json(
            f"{self.base_url()}/candles?symbol={symbol}&interval={interval}" f"&limit={limit}",
            timeout=15,
            headers=self._headers(),
            venue=self.id,
        )
        self._ok()
        return out

    def depth(self, symbol: str, limit: int = 20):
        self._require_configured()
        out = http_json(
            f"{self.base_url()}/orderbook?symbol={symbol}&limit={limit}",
            timeout=10,
            headers=self._headers(),
            venue=self.id,
        )
        self._ok()
        return out

    def account(self, timeout: float = 12):
        """INR + asset balances from the real venue."""
        self._require_configured()
        out = http_json(
            f"{self.base_url()}/balances", timeout=timeout, headers=self._headers(), venue=self.id
        )
        self._ok()
        return out

    def open_orders(self, symbol: str | None = None):
        self._require_configured()
        path = "/orders/open" + (f"?symbol={symbol}" if symbol else "")
        return http_json(
            f"{self.base_url()}{path}", timeout=12, headers=self._headers(), venue=self.id
        )

    def get_order(self, symbol: str, order_id=None, client_order_id=None):
        self._require_configured()
        ref = order_id or client_order_id or ""
        return http_json(
            f"{self.base_url()}/orders/{ref}", timeout=12, headers=self._headers(), venue=self.id
        )

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
        self._require_configured()
        payload = {"symbol": symbol, "side": side, "type": order_type, "quantity": qty}
        if price is not None:
            payload["price"] = price
        if client_order_id:
            payload["clientOrderId"] = client_order_id
        return http_json(
            f"{self.base_url()}/orders",
            timeout=15,
            method="POST",
            data=payload,
            headers=self._headers(),
            venue=self.id,
        )

    def cancel_order(self, symbol: str, order_id=None, client_order_id=None):
        self._require_configured()
        ref = order_id or client_order_id or ""
        return http_json(
            f"{self.base_url()}/orders/{ref}",
            timeout=12,
            method="DELETE",
            headers=self._headers(),
            venue=self.id,
        )

    def my_trades(self, symbol: str, limit: int = 50):
        self._require_configured()
        return http_json(
            f"{self.base_url()}/fills?symbol={symbol}&limit={limit}",
            timeout=12,
            headers=self._headers(),
            venue=self.id,
        )

    def test_connection(self) -> dict:
        out: dict[str, Any] = {"connected": False, "venue": self.id, "checks": {}}
        if not self.configured():
            out["status"] = "NOT_CONFIGURED"
            out["error"] = self.last_error
            return out
        try:
            self.ping()
            out["checks"]["endpoint"] = True
            out["connected"] = True
            out["status"] = "OK"
        except Exception as e:
            out["checks"]["endpoint"] = False
            out["status"] = "UNREACHABLE"
            out["error"] = http_error_str(e)
        return out
