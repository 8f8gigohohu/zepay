"""Paper exchange (§22 Stage 1): REAL prices, REAL order books, REAL fee
model — simulated ONLY the fill/balance side, clearly labeled everywhere.

Ported from v2 with identical fill semantics: walk the real book up to a
participation cap (partial fills beyond it), apply explicit slippage + fee +
latency models. No invented liquidity: when no real book exists, fills are
limited to the ticker-price estimate and labeled as such.
"""

from __future__ import annotations

from zepay.core.util import clamp, safe_float


class PaperExchange:
    def __init__(self, collector, cost_model, config):
        self.collector = collector
        self.costs = cost_model
        self.config = config

    def quote(self, market: str):
        t = self.collector.get_ticker(market)
        if not t or not t.get("price"):
            return None, "no real price available"
        d = self.collector.get_depth(market)
        if d and d.get("bids") and d.get("asks"):
            bid = d["bids"][0][0]
            ask = d["asks"][0][0]
            return {
                "bid": bid,
                "ask": ask,
                "mid": (bid + ask) / 2,
                "source": "real-book",
                "spread_bps": (ask - bid) / ((bid + ask) / 2) * 1e4,
            }, ""
        mid = safe_float(t["price"])
        spread = mid * 0.0004
        return {
            "bid": mid - spread / 2,
            "ask": mid + spread / 2,
            "mid": mid,
            "source": "ticker-spread-estimate",
            "spread_bps": 4.0,
        }, ""

    def market_fill(self, market: str, direction: str, qty: float):
        """Walk the REAL order book up to a participation cap.
        Returns (fill_dict, error). Partial fills are explicit."""
        q, err = self.quote(market)
        if not q:
            return None, err
        comps = self.costs.refresh()
        d = self.collector.get_depth(market)
        book = None
        if d:
            book = d.get("asks") if direction == "LONG" else d.get("bids")
        participation = clamp(
            safe_float(self.config.get("paper_participation_max", 0.25), 0.25), 0.01, 1.0
        )
        slip = comps["slip_bps"] / 10000.0
        latency = int(safe_float(self.config.get("paper_latency_ms", 120), 120))
        if book:
            capacity_notional = sum(p * x for p, x in book) * participation
            price_ref = book[0][0]
            capacity_qty = capacity_notional / price_ref if price_ref else 0
            filled_qty = min(qty, capacity_qty)
            if filled_qty <= 0:
                return None, "no fillable liquidity within participation cap"
            remaining = filled_qty
            cost = 0.0
            for p, x in book:
                take = min(remaining, x)
                cost += take * p
                remaining -= take
                if remaining <= 0:
                    break
            vwap = cost / filled_qty
            px = vwap * (1 + slip) if direction == "LONG" else vwap * (1 - slip)
            fee = px * filled_qty * (comps["fee_bps"] / 10000.0)
            return {
                "price": px,
                "fee": fee,
                "slippage_bps": comps["slip_bps"],
                "filled_qty": filled_qty,
                "requested_qty": qty,
                "remaining_qty": round(max(qty - filled_qty, 0), 8),
                "partial": filled_qty < qty - 1e-12,
                "latency_ms": latency,
                "book_source": "real-book",
                "maker": False,
                "expected_price": q["mid"],
                "spread_bps": q.get("spread_bps", 0),
            }, ""
        # no real book → fill at ticker estimate, clearly labeled
        px = q["ask"] * (1 + slip) if direction == "LONG" else q["bid"] * (1 - slip)
        fee = px * qty * (comps["fee_bps"] / 10000.0)
        return {
            "price": px,
            "fee": fee,
            "slippage_bps": comps["slip_bps"],
            "filled_qty": qty,
            "requested_qty": qty,
            "remaining_qty": 0.0,
            "partial": False,
            "latency_ms": latency,
            "book_source": q.get("source"),
            "maker": False,
            "expected_price": q["mid"],
            "spread_bps": q.get("spread_bps", 0),
        }, ""

    def limit_cross(self, market: str, side: str, limit_price: float) -> bool:
        """Would a resting limit at this price cross against the real market?"""
        t = self.collector.get_ticker(market)
        if not t or not t.get("price"):
            return False
        px = safe_float(t["price"])
        return (px <= limit_price) if side == "BUY" else (px >= limit_price)
