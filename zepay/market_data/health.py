"""Market health assessment (v2 HealthEngine, dependency-injected).

TRADEABLE / LIMITED / BLOCKED per market, from REAL data conditions only.
BLOCKED markets never receive new entries; LIMITED markets get reduced size.
"""

from __future__ import annotations

from zepay.core.domain import Health
from zepay.core.util import safe_float


class MarketHealthEngine:
    def __init__(self, collector, config):
        self.collector = collector
        self.config = config

    def assess(self, market: str, f) -> dict:
        reasons = []
        if self.collector.is_stale(market) and f.price <= 0:
            return {
                "status": Health.BLOCKED.value,
                "reasons": [
                    "No market data (stale/unreachable). REAL DATA REQUIRED — " "asset blocked."
                ],
            }
        if f.price <= 0 or f.n_candles < 20:
            return {"status": Health.BLOCKED.value, "reasons": ["Insufficient price history"]}
        limited = False
        if self.collector.is_stale(market):
            reasons.append(
                f"Data delayed {self.collector.age_secs(market):.0f}s — " "new entries limited"
            )
            limited = True
        min_liq = safe_float(self.config.get("min_liquidity_usd", 50000))
        if f.liquidity_usd and f.liquidity_usd < min_liq:
            return {
                "status": Health.BLOCKED.value,
                "reasons": [
                    f"Liquidity ${f.liquidity_usd:,.0f} below safety " f"threshold ${min_liq:,.0f}"
                ],
            }
        max_spread = safe_float(self.config.get("max_spread_bps", 25.0), 25.0)
        if f.spread_bps > max_spread:
            reasons.append(f"Wide spread {f.spread_bps:.1f} bps (limit {max_spread:.0f})")
            limited = True
        if f.atr_pct > 0.08:
            reasons.append("Abnormal volatility — size reduced")
            limited = True
        err = self.collector.errors.get(market)
        if err and err.get("count", 0) >= 3:
            reasons.append(f"Repeated API errors ({err.get('count')}): {err.get('msg', '')[:80]}")
            limited = True
        if limited:
            return {"status": Health.LIMITED.value, "reasons": reasons or ["Limited conditions"]}
        return {"status": Health.TRADEABLE.value, "reasons": ["All checks passed"]}
