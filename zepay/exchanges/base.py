"""Exchange adapter contract (§8, hummingbot-inspired connector architecture).

Every venue implements the same interface; the engine never contains
venue-specific trading logic. Capabilities are DECLARED and VERIFIED —
a venue that cannot do something reports it honestly instead of failing deep
inside a trade path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Capabilities:
    market_data: bool = False  # tickers / candles / depth (public, no auth)
    websocket: bool = False
    derivatives_context: bool = False  # funding / OI / mark price
    signed_account: bool = False  # balances via signed REST
    trading: bool = False  # order placement (live)
    order_types: list = field(default_factory=lambda: ["MARKET", "LIMIT"])
    time_in_force: list = field(default_factory=lambda: ["GTC", "IOC"])
    post_only: bool = False
    reduce_only: bool = False
    stop_orders: bool = False
    trailing_stop: bool = False
    leverage: bool = False
    funding_payments: bool = False
    withdrawal_supported: bool = False  # ZEPAY NEVER requests or uses withdrawal permission

    def to_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)


class ExchangeAdapter(ABC):
    """Base connector. Status values (§43): OK | DEGRADED | UNREACHABLE |
    BLOCKED | NOT_CONFIGURED."""

    id: str = "abstract"
    name: str = "Abstract venue"

    def __init__(self):
        self.last_error: str | None = None
        self.last_success: str | None = None
        self._status: str = "NOT_CONFIGURED"

    # ---- metadata ----
    @abstractmethod
    def capabilities(self) -> Capabilities: ...

    def status(self) -> str:
        return self._status

    def health(self) -> dict:
        return {
            "venue": self.id,
            "name": self.name,
            "status": self._status,
            "last_error": self.last_error,
            "last_success": self.last_success,
            "capabilities": self.capabilities().to_dict(),
        }

    # ---- public market data (no credentials required) ----
    def ping(self) -> Any:
        raise NotImplementedError

    def exchange_info(self, symbols: list | None = None) -> Any:
        raise NotImplementedError

    def ticker_24h(self, symbol: str | None = None) -> Any:
        raise NotImplementedError

    def klines(self, symbol: str, interval: str = "1h", limit: int = 200) -> Any:
        raise NotImplementedError

    def depth(self, symbol: str, limit: int = 20) -> Any:
        raise NotImplementedError

    def funding_info(self, symbol: str) -> Any:
        raise NotImplementedError

    # ---- credentials ----
    def has_credentials(self) -> bool:
        return False

    def set_credentials(self, *args, **kwargs) -> None:
        raise NotImplementedError

    def clear_credentials(self) -> None:
        raise NotImplementedError

    # ---- signed account/trading (live only) ----
    def account(self, timeout: float = 12) -> Any:
        raise NotImplementedError

    def open_orders(self, symbol: str | None = None) -> Any:
        raise NotImplementedError

    def get_order(
        self, symbol: str, order_id: str | None = None, client_order_id: str | None = None
    ) -> Any:
        raise NotImplementedError

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
    ) -> Any:
        raise NotImplementedError

    def cancel_order(
        self, symbol: str, order_id: str | None = None, client_order_id: str | None = None
    ) -> Any:
        raise NotImplementedError

    def my_trades(self, symbol: str, limit: int = 50) -> Any:
        raise NotImplementedError

    def test_connection(self) -> dict:
        raise NotImplementedError

    # ---- bookkeeping helpers ----
    def _ok(self) -> None:
        from zepay.core.util import utcnow_iso

        self._status = "OK"
        self.last_success = utcnow_iso()
        self.last_error = None

    def _fail(self, err: str, status: str = "UNREACHABLE") -> None:
        self._status = status
        self.last_error = err[:300]
