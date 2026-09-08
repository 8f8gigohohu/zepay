"""Venue registry (§8/§9): one place that knows every adapter, its real
health, and its capabilities. Evolved from v2's ProviderRegistry.

Honesty rules preserved from v2:
  * health checks hit REAL endpoints and record REAL errors
  * a venue without credentials/spec is NOT_CONFIGURED, never "connected"
  * public API catalogue entries (not integrated) are listed as such
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from zepay.core.util import utcnow_iso

log = logging.getLogger("zepay.exchanges.registry")


class VenueRegistry:
    def __init__(self):
        self._venues: dict[str, Any] = {}
        self._lock = threading.RLock()
        self.last_check: str | None = None

    def register(self, adapter) -> None:
        with self._lock:
            self._venues[adapter.id] = adapter

    def get(self, venue_id: str):
        with self._lock:
            return self._venues.get(venue_id)

    def all(self) -> dict:
        with self._lock:
            return dict(self._venues)

    def enabled(self, config) -> dict:
        """Venues the operator enabled AND that are not NOT_CONFIGURED."""
        flags = config.get("venues_enabled") or {}
        out = {}
        for vid, adapter in self.all().items():
            if flags.get(vid) and adapter.status() != "NOT_CONFIGURED":
                out[vid] = adapter
        return out

    def health_all(self, persist_fn=None) -> dict:
        """Run every adapter's real test_connection; record results."""
        results = {}
        for vid, adapter in self.all().items():
            try:
                results[vid] = adapter.test_connection()
            except Exception as e:
                results[vid] = {
                    "connected": False,
                    "venue": vid,
                    "status": "UNREACHABLE",
                    "error": str(e)[:200],
                }
            if persist_fn:
                try:
                    persist_fn(
                        vid,
                        "health_check",
                        results[vid].get("status", "?"),
                        str(results[vid].get("error", ""))[:300],
                    )
                except Exception:
                    pass
        self.last_check = utcnow_iso()
        return results

    def snapshot(self) -> dict:
        with self._lock:
            return {vid: a.health() for vid, a in self._venues.items()}


# Non-integrated public API catalogue (v2 heritage — informational only).
PUBLIC_API_CATALOG = [
    {
        "name": "CoinGecko",
        "category": "market-data",
        "auth": "none/api-key",
        "base": "https://api.coingecko.com/api/v3",
        "integrated": False,
    },
    {
        "name": "Kraken",
        "category": "exchange",
        "auth": "api-key",
        "base": "https://api.kraken.com",
        "integrated": False,
    },
    {
        "name": "Coinbase Exchange",
        "category": "exchange",
        "auth": "api-key",
        "base": "https://api.exchange.coinbase.com",
        "integrated": False,
    },
    {
        "name": "OKX",
        "category": "exchange",
        "auth": "api-key",
        "base": "https://www.okx.com/api/v5",
        "integrated": False,
    },
    {
        "name": "KuCoin",
        "category": "exchange",
        "auth": "api-key",
        "base": "https://api.kucoin.com",
        "integrated": False,
    },
    {
        "name": "Gate.io",
        "category": "exchange",
        "auth": "api-key",
        "base": "https://api.gateio.ws/api/v4",
        "integrated": False,
    },
    {
        "name": "MEXC",
        "category": "exchange",
        "auth": "api-key",
        "base": "https://api.mexc.com/api/v3",
        "integrated": False,
    },
    {
        "name": "Bybit",
        "category": "exchange",
        "auth": "api-key",
        "base": "https://api.bybit.com/v5",
        "integrated": False,
    },
    {
        "name": "Jupiter (Solana DEX)",
        "category": "dex-aggregator",
        "auth": "none",
        "base": "https://lite-api.jup.ag",
        "integrated": True,
        "note": "used by the Solana wallet adapter for prices/quotes/swaps",
    },
    {
        "name": "Solana RPC",
        "category": "blockchain",
        "auth": "none",
        "base": "https://api.mainnet-beta.solana.com",
        "integrated": True,
        "note": "read-only balances/health; signing happens in Phantom",
    },
    {
        "name": "TradingView (webhook concept)",
        "category": "signals",
        "auth": "webhook",
        "base": "—",
        "integrated": False,
        "note": "OctoBot-style webhook ingest is on the roadmap, not bundled",
    },
]


def public_api_catalog_payload() -> list[dict]:
    return [dict(c) for c in PUBLIC_API_CATALOG]
