"""Position sizer (v2 logic, instrument-aware).

Risk-per-trade sizing bounded by: exchange step size / min qty / min notional
(REAL filters from the instrument), max position %, volatility adjustment,
confidence scaling, and stage notional caps handed down by the Risk Engine.
"""

from __future__ import annotations

import math

from zepay.core.util import clamp, safe_float


class Sizer:
    def __init__(self, universe, config):
        self.universe = universe
        self.config = config

    def round_qty(self, market: str, qty: float) -> float:
        flt = self.universe.filters(market)
        if flt and flt.get("step_size"):
            step = flt["step_size"]
            if step > 0:
                qty = math.floor(qty / step) * step
        return round(qty, 8)

    def size(self, opp, f, acct: dict | None, sizing_hint: dict | None) -> dict:
        equity = safe_float((acct or {}).get("equity", 0)) or safe_float(
            self.config.get("paper_starting_balance", 10000)
        )
        risk_pct = safe_float(self.config.get("risk_per_trade_pct", 0.01), 0.01)
        mult = safe_float((sizing_hint or {}).get("size_mult", 1.0), 1.0)
        stop_dist = max(f.atr14 * 1.5, opp.price * 0.004) if opp.price else equity * 0.01
        risk_amt = equity * risk_pct * mult
        qty = risk_amt / stop_dist if stop_dist else 0
        notional = qty * opp.price
        cap = equity * safe_float(self.config.get("max_position_pct", 0.2), 0.2)
        if notional > cap and opp.price:
            notional = cap
            qty = notional / opp.price
        # stage cap from the risk engine (LIMITED_LIVE etc.)
        stage_cap = (sizing_hint or {}).get("max_notional_usd")
        if stage_cap and notional > stage_cap and opp.price:
            notional = stage_cap
            qty = notional / opp.price
        vol_adj = clamp(0.02 / max(f.atr_pct, 0.005), 0.4, 1.2)
        qty *= min(vol_adj, 1.0)
        qty *= 0.6 + 0.4 * clamp(opp.confidence, 0, 1)
        qty = self.round_qty(opp.symbol, qty)
        notional = qty * opp.price
        # exchange minimums (REAL instrument filters)
        flt = self.universe.filters(opp.symbol)
        min_notional = safe_float((flt or {}).get("min_notional"), 0) or safe_float(
            self.config.get("min_order_notional_usd", 10.0), 10.0
        )
        min_qty = safe_float((flt or {}).get("min_qty"), 0)
        if qty > 0 and (notional < min_notional or (min_qty and qty < min_qty)):
            return {
                "qty": 0,
                "notional": 0,
                "reject": True,
                "reject_reason": f"Order below exchange minimum (min notional "
                f"${min_notional:,.2f}, min qty {min_qty})",
            }
        sl = opp.price - stop_dist if opp.direction == "LONG" else opp.price + stop_dist
        tp_dist = stop_dist * 2.0
        tp = opp.price + tp_dist if opp.direction == "LONG" else opp.price - tp_dist
        return {
            "qty": qty,
            "notional": notional,
            "stop": sl,
            "take_profit": tp,
            "risk_amount": risk_amt,
            "size_mult": mult,
            "reject": False,
            "stop_distance_pct": round(stop_dist / opp.price, 5) if opp.price else 0,
        }
