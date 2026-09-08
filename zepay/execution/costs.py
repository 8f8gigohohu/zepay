"""Cost model (§18: every potential trade is simulated through real costs).

Fee/slippage/funding are operator-configured estimates; SPREAD comes from the
real order book when available. The pre-trade simulator composes these into
an expected-net-edge number the risk engine gates on.
"""

from __future__ import annotations

from zepay.core.util import safe_float


class CostModel:
    def __init__(self, config):
        self.config = config

    def refresh(self) -> dict:
        return {
            "fee_bps": safe_float(self.config.get("fee_bps", 10.0), 10.0),
            "slip_bps": safe_float(self.config.get("slippage_bps", 5.0), 5.0),
            "fund_bps": safe_float(self.config.get("funding_bps_8h", 1.0), 1.0),
        }

    def round_trip_pct(self, f=None, notional_hint: float = 0) -> float:
        c = self.refresh()
        fee = c["fee_bps"] / 10000.0 * 2
        spread = (f.spread_bps / 10000.0) if (f is not None and f.spread_bps) else 0.0004
        slip = c["slip_bps"] / 10000.0 * 2
        fund = c["fund_bps"] / 10000.0
        return fee + spread + slip + fund

    def components(self, f=None) -> dict:
        c = self.refresh()
        spread_bps = f.spread_bps if (f is not None and f.spread_bps) else 4.0
        return {
            "fee_bps": c["fee_bps"] * 2,
            "spread_bps": spread_bps,
            "slip_bps": c["slip_bps"] * 2,
            "funding_bps": c["fund_bps"],
            "fee_pct": c["fee_bps"] / 10000.0 * 2,
            "spread_pct": spread_bps / 10000.0,
            "slip_pct": c["slip_bps"] / 10000.0 * 2,
            "funding_pct": c["fund_bps"] / 10000.0,
            "round_trip_pct": self.round_trip_pct(f),
        }
