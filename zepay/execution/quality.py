"""Execution quality measurement (§34).

For every fill: expected price (decision time) vs actual price, slippage,
spread, fill ratio, latency, maker/taker, rejection/cancellation rates, and a
composite Execution Quality Score per venue. The opportunity layer can use
the score to avoid poor venues; the risk engine uses repeated failures as a
blocking condition. All numbers come from real recorded fills — a venue with
no history shows NO DATA, not a fabricated score.
"""

from __future__ import annotations

import statistics

from zepay.core.util import clamp, safe_float, utcnow_iso


class ExecutionQuality:
    def __init__(self, storage):
        self.db = storage

    def record(
        self,
        order_id: str,
        venue: str,
        market: str,
        mode: str,
        expected_price: float,
        actual_price: float,
        qty_requested: float,
        qty_filled: float,
        latency_ms: int,
        spread_bps: float,
        maker: bool,
        rejected: bool = False,
    ) -> dict:
        slip_bps = (actual_price - expected_price) / expected_price * 1e4 if expected_price else 0.0
        if mode == "PAPER" or maker:
            pass
        fill_ratio = qty_filled / qty_requested if qty_requested else 0
        # composite: slippage vs spread, fill ratio, latency, rejections
        slip_score = clamp(1 - abs(slip_bps) / max(spread_bps * 2 + 1, 1), 0, 1)
        ratio_score = clamp(fill_ratio, 0, 1)
        lat_score = clamp(1 - (latency_ms / 2000.0), 0, 1)
        quality = (
            round(0.45 * slip_score + 0.35 * ratio_score + 0.2 * lat_score, 4)
            if not rejected
            else 0.0
        )
        row = {
            "order_id": order_id,
            "venue": venue,
            "market": market,
            "mode": mode,
            "expected_price": expected_price,
            "actual_price": actual_price,
            "slippage_bps": round(slip_bps, 3),
            "spread_bps": round(spread_bps, 3),
            "latency_ms": int(latency_ms),
            "fill_ratio": round(fill_ratio, 4),
            "partial": int(fill_ratio < 0.999),
            "rejected": int(rejected),
            "maker": int(maker),
            "market_impact_bps": round(max(slip_bps - spread_bps / 2, 0), 3),
            "quality_score": quality,
        }
        try:
            self.db.execute(
                "INSERT INTO execution_metrics (order_id, venue, market, mode,"
                " expected_price, actual_price, slippage_bps, spread_bps, latency_ms,"
                " fill_ratio, partial, rejected, maker, market_impact_bps, quality_score, ts)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["order_id"],
                    venue,
                    market,
                    mode,
                    expected_price,
                    actual_price,
                    row["slippage_bps"],
                    row["spread_bps"],
                    latency_ms,
                    row["fill_ratio"],
                    row["partial"],
                    row["rejected"],
                    row["maker"],
                    row["market_impact_bps"],
                    quality,
                    utcnow_iso(),
                ),
            )
        except Exception:
            pass
        return row

    def venue_score(self, venue: str, window: int = 200) -> dict | None:
        rows = self.db.query(
            "SELECT slippage_bps, spread_bps, latency_ms, fill_ratio, rejected, maker,"
            " quality_score FROM execution_metrics WHERE venue=? ORDER BY id DESC LIMIT ?",
            (venue, window),
        )
        if not rows:
            return None  # NO DATA — never fabricated
        scores = [safe_float(r["quality_score"]) for r in rows]
        slips = [abs(safe_float(r["slippage_bps"])) for r in rows]
        lats = [safe_float(r["latency_ms"]) for r in rows]
        ratios = [safe_float(r["fill_ratio"]) for r in rows]
        rejections = sum(int(r["rejected"]) for r in rows)
        return {
            "venue": venue,
            "samples": len(rows),
            "quality_score": round(statistics.fmean(scores), 4),
            "avg_abs_slippage_bps": round(statistics.fmean(slips), 3),
            "median_latency_ms": round(statistics.median(lats), 1),
            "avg_fill_ratio": round(statistics.fmean(ratios), 4),
            "rejection_rate": round(rejections / len(rows), 4),
            "maker_rate": round(sum(int(r["maker"]) for r in rows) / len(rows), 4),
            "ts": utcnow_iso(),
        }

    def all_venues(self) -> dict:
        rows = self.db.query("SELECT DISTINCT venue FROM execution_metrics")
        out = {}
        for r in rows:
            s = self.venue_score(r["venue"])
            if s:
                out[r["venue"]] = s
        return out

    def slippage_distribution(self, venue: str = "", bins: int = 8) -> list[dict]:
        q = "SELECT slippage_bps FROM execution_metrics"
        params: tuple = ()
        if venue:
            q += " WHERE venue=?"
            params = (venue,)
        rows = self.db.query(q + " ORDER BY id DESC LIMIT 2000", params)
        vals = [safe_float(r["slippage_bps"]) for r in rows]
        if not vals:
            return []
        lo, hi = min(vals), max(vals)
        width = (hi - lo) / bins or 1
        out = []
        for i in range(bins):
            a, b = lo + i * width, lo + (i + 1) * width
            n = sum(1 for v in vals if (a <= v < b) or (i == bins - 1 and v == b))
            out.append({"from_bps": round(a, 2), "to_bps": round(b, 2), "n": n})
        return out
