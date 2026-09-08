"""Market regime engine (§29).

Ported from v2 with additional regimes: COMPRESSION (pre-breakout squeeze),
RECOVERY (post-panic stabilization), MEAN_REVERSION (range oscillation).
Regime detection uses ONLY observed features — when history is insufficient
the regime is UNSTABLE and the risk engine pauses entries. Strategy weights
adapt to regime via REGIME_STRAT_FIT (used by the strategy orchestrator).
"""

from __future__ import annotations

from zepay.core.util import pct
from zepay.features.engine import AssetFeatures

REGIMES = [
    "BULLISH_TREND",
    "BEARISH_TREND",
    "SIDEWAYS",
    "HIGH_VOL",
    "LOW_VOL",
    "BREAKOUT",
    "COMPRESSION",
    "PANIC",
    "RECOVERY",
    "MEAN_REVERSION",
    "UNSTABLE",
]


class RegimeEngine:
    @staticmethod
    def detect(f: AssetFeatures) -> dict:
        if f.n_candles < 30 or f.price <= 0:
            return {
                "regime": "UNSTABLE",
                "confidence": 0.3,
                "reasons": ["Insufficient history for regime detection"],
            }
        trend_up = (f.price > f.sma20 > f.sma50) or (f.ema12 > f.ema26 and f.mom_20 > 0.02)
        trend_dn = (f.price < f.sma20 < f.sma50) or (f.ema12 < f.ema26 and f.mom_20 < -0.02)
        mom = f.mom_20
        vol = f.atr_pct
        reasons: list[str] = []

        # panic: extreme move or crash volatility
        if abs(f.change24) > 12 or (vol > 0.06 and mom < -0.08):
            return {
                "regime": "PANIC",
                "confidence": 0.8,
                "reasons": [f"Extreme move: 24h {f.change24:+.1f}%, ATR {pct(vol)}%"],
            }

        # recovery: strong bounce off lows with expanding volume after a drawdown
        if f.low_20 and f.price > 0:
            bounce = (f.price - f.low_20) / f.low_20
            if bounce > 0.05 and f.change24 > 2 and f.vol_ratio > 1.2 and mom > 0:
                return {
                    "regime": "RECOVERY",
                    "confidence": 0.65,
                    "reasons": [
                        f"Bounce {pct(bounce)}% off 20-bar low on "
                        f"{f.vol_ratio:.1f}x volume, positive 24h"
                    ],
                }

        # breakout: above/below range on volume
        if f.breakout_up > 0.002 and f.vol_ratio > 1.5:
            return {
                "regime": "BREAKOUT",
                "confidence": 0.75,
                "reasons": ["Price above 20-bar high with elevated volume"],
            }
        if f.breakout_dn < -0.002 and f.vol_ratio > 1.5:
            return {
                "regime": "BREAKOUT",
                "confidence": 0.7,
                "reasons": ["Price below 20-bar low with elevated volume (downside)"],
            }

        # compression: bollinger squeeze, directionless, low ATR
        if f.bb_width < 0.025 and vol < 0.012 and abs(mom) < 0.015:
            return {
                "regime": "COMPRESSION",
                "confidence": 0.65,
                "reasons": [
                    f"Bollinger width {f.bb_width:.3f} compressed, " f"ATR {pct(vol)}% — squeeze"
                ],
            }

        if vol > 0.045:
            reg, conf = "HIGH_VOL", 0.7
            reasons.append(f"ATR {pct(vol)}% above high-vol threshold")
        elif vol < 0.008 and abs(mom) < 0.02:
            # sideways + oscillating around BB mid → mean-reversion environment
            if 0.3 < f.bb_pctb < 0.7 and abs(f.macd_hist) < (f.atr14 or 1) * 0.15:
                reg, conf = "MEAN_REVERSION", 0.6
                reasons.append(
                    "Range-bound oscillation around the mean — " "mean-reversion environment"
                )
            else:
                reg, conf = "LOW_VOL", 0.65
                reasons.append("Compressed volatility, directionless")
        elif trend_up and mom > 0.015:
            reg, conf = "BULLISH_TREND", 0.7
            reasons.append("Price above rising averages, positive momentum")
        elif trend_dn and mom < -0.015:
            reg, conf = "BEARISH_TREND", 0.7
            reasons.append("Price below falling averages, negative momentum")
        else:
            reg, conf = "SIDEWAYS", 0.6
            reasons.append("No dominant trend; range-like behavior")
        if f.n_candles < 60:
            conf *= 0.85
            reasons.append("Limited history — confidence reduced")
        return {"regime": reg, "confidence": round(conf, 3), "reasons": reasons}


# Regime → strategy fitness (§29). Unknown/unstable regimes reduce everything —
# the orchestrator multiplies raw strategy scores by these fits.
REGIME_STRAT_FIT = {
    "BULLISH_TREND": {
        "trend": 1.0,
        "momentum": 0.9,
        "breakout": 0.8,
        "mean_reversion": 0.3,
        "volatility": 0.5,
        "orderflow": 0.7,
        "funding_basis": 0.6,
        "grid": 0.3,
        "dca": 0.8,
        "market_making": 0.4,
        "stat_arb": 0.5,
        "cross_venue_arb": 0.6,
    },
    "BEARISH_TREND": {
        "trend": 1.0,
        "momentum": 0.9,
        "breakout": 0.7,
        "mean_reversion": 0.3,
        "volatility": 0.5,
        "orderflow": 0.7,
        "funding_basis": 0.6,
        "grid": 0.3,
        "dca": 0.5,
        "market_making": 0.4,
        "stat_arb": 0.5,
        "cross_venue_arb": 0.6,
    },
    "SIDEWAYS": {
        "trend": 0.3,
        "momentum": 0.4,
        "breakout": 0.4,
        "mean_reversion": 1.0,
        "volatility": 0.5,
        "orderflow": 0.6,
        "funding_basis": 0.7,
        "grid": 0.9,
        "dca": 0.7,
        "market_making": 0.9,
        "stat_arb": 0.9,
        "cross_venue_arb": 0.7,
    },
    "MEAN_REVERSION": {
        "trend": 0.2,
        "momentum": 0.3,
        "breakout": 0.3,
        "mean_reversion": 1.0,
        "volatility": 0.4,
        "orderflow": 0.6,
        "funding_basis": 0.7,
        "grid": 1.0,
        "dca": 0.7,
        "market_making": 1.0,
        "stat_arb": 1.0,
        "cross_venue_arb": 0.8,
    },
    "HIGH_VOL": {
        "trend": 0.5,
        "momentum": 0.5,
        "breakout": 0.6,
        "mean_reversion": 0.4,
        "volatility": 1.0,
        "orderflow": 0.5,
        "funding_basis": 0.8,
        "grid": 0.2,
        "dca": 0.4,
        "market_making": 0.2,
        "stat_arb": 0.4,
        "cross_venue_arb": 0.5,
    },
    "LOW_VOL": {
        "trend": 0.4,
        "momentum": 0.4,
        "breakout": 0.7,
        "mean_reversion": 0.6,
        "volatility": 0.3,
        "orderflow": 0.5,
        "funding_basis": 0.6,
        "grid": 0.7,
        "dca": 0.8,
        "market_making": 0.7,
        "stat_arb": 0.7,
        "cross_venue_arb": 0.6,
    },
    "COMPRESSION": {
        "trend": 0.4,
        "momentum": 0.5,
        "breakout": 0.9,
        "mean_reversion": 0.5,
        "volatility": 0.7,
        "orderflow": 0.6,
        "funding_basis": 0.6,
        "grid": 0.5,
        "dca": 0.7,
        "market_making": 0.6,
        "stat_arb": 0.6,
        "cross_venue_arb": 0.6,
    },
    "BREAKOUT": {
        "trend": 0.8,
        "momentum": 0.9,
        "breakout": 1.0,
        "mean_reversion": 0.2,
        "volatility": 0.6,
        "orderflow": 0.8,
        "funding_basis": 0.5,
        "grid": 0.2,
        "dca": 0.5,
        "market_making": 0.3,
        "stat_arb": 0.4,
        "cross_venue_arb": 0.6,
    },
    "RECOVERY": {
        "trend": 0.6,
        "momentum": 0.8,
        "breakout": 0.7,
        "mean_reversion": 0.4,
        "volatility": 0.6,
        "orderflow": 0.6,
        "funding_basis": 0.5,
        "grid": 0.4,
        "dca": 0.8,
        "market_making": 0.5,
        "stat_arb": 0.5,
        "cross_venue_arb": 0.6,
    },
    "PANIC": {
        "trend": 0.2,
        "momentum": 0.2,
        "breakout": 0.2,
        "mean_reversion": 0.3,
        "volatility": 0.6,
        "orderflow": 0.3,
        "funding_basis": 0.4,
        "grid": 0.0,
        "dca": 0.2,
        "market_making": 0.0,
        "stat_arb": 0.2,
        "cross_venue_arb": 0.3,
    },
    "UNSTABLE": {
        "trend": 0.2,
        "momentum": 0.2,
        "breakout": 0.2,
        "mean_reversion": 0.2,
        "volatility": 0.3,
        "orderflow": 0.2,
        "funding_basis": 0.2,
        "grid": 0.0,
        "dca": 0.2,
        "market_making": 0.0,
        "stat_arb": 0.2,
        "cross_venue_arb": 0.2,
    },
}
