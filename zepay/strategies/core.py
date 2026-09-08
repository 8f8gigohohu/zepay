"""Core directional strategies. Scoring formulas ported UNCHANGED from the v2
StrategyEngine so trained AI feature semantics remain consistent; each is now
a first-class Strategy emitting a StrategySignal (§30)."""

from __future__ import annotations

from zepay.core.util import clamp, pct
from zepay.features.engine import AssetFeatures
from zepay.strategies.base import Strategy, StrategyContext, StrategySignal


class TrendStrategy(Strategy):
    id = "trend"
    kind = "directional"
    description = "Moving-average alignment + displacement trend following"

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        if f.price <= 0 or f.n_candles < 20:
            return Strategy._signal(self.id, f, 0.0, ["insufficient data — WAIT"])
        atr_ref = f.atr14 or (f.price * 0.01)
        s = 0.0
        rationale = []
        if f.sma20 and f.sma50:
            disp = clamp((f.price - f.sma50) / atr_ref * 0.4, -1, 1)
            cross = clamp((f.sma20 - f.sma50) / atr_ref * 0.3, -1, 1)
            s += disp + cross
            rationale.append(
                f"price vs SMA50 displacement {disp:+.2f}, SMA20/50 slope {cross:+.2f}"
            )
        ema_part = 0.3 if f.ema12 > f.ema26 else (-0.3 if f.ema12 < f.ema26 else 0)
        s += ema_part
        rationale.append(
            f"EMA12/26 {'bullish' if ema_part > 0 else 'bearish' if ema_part < 0 else 'flat'}"
        )
        if f.supertrend_dir:
            s += 0.2 * f.supertrend_dir
            rationale.append(f"supertrend {'up' if f.supertrend_dir > 0 else 'down'}")
        return Strategy._signal(self.id, f, clamp(s, -1, 1), rationale)


class MomentumStrategy(Strategy):
    id = "momentum"
    kind = "directional"
    description = "10/20-bar momentum composite"

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        if f.price <= 0 or f.n_candles < 25:
            return Strategy._signal(self.id, f, 0.0, ["insufficient data — WAIT"])
        s = clamp(f.mom_10 * 8, -1, 1) * 0.6 + clamp(f.mom_20 * 4, -1, 1) * 0.4
        rationale = [
            f"mom10 {pct(f.mom_10)}%, mom20 {pct(f.mom_20)}%",
            f"volume ratio {f.vol_ratio:.2f}x",
        ]
        if f.vol_ratio > 1.5 and abs(s) > 0.3:
            s = clamp(s * 1.15, -1, 1)
            rationale.append("momentum confirmed by elevated volume")
        return Strategy._signal(self.id, f, s, rationale)


class BreakoutStrategy(Strategy):
    id = "breakout"
    kind = "directional"
    description = "20-bar range breakout with volume confirmation"

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        if f.price <= 0 or f.n_candles < 25:
            return Strategy._signal(self.id, f, 0.0, ["insufficient data — WAIT"])
        s = (
            clamp(f.breakout_up * 40, -1, 1)
            if f.breakout_up > 0
            else clamp(f.breakout_dn * 40, -1, 1)
        )
        rationale = [
            f"distance from 20-bar high {pct(f.breakout_up)}%, " f"low {pct(f.breakout_dn)}%"
        ]
        if abs(s) > 0.2:
            vol_conf = clamp((f.vol_ratio - 1) * 0.5, -0.3, 0.3)
            s = clamp(s + (vol_conf if s > 0 else -vol_conf), -1, 1)
            rationale.append(f"volume confirmation {f.vol_ratio:.2f}x")
        return Strategy._signal(self.id, f, s, rationale)


class MeanReversionStrategy(Strategy):
    id = "mean_reversion"
    kind = "directional"
    description = "Bollinger %B fade with RSI filter"

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        if f.price <= 0 or f.n_candles < 25:
            return Strategy._signal(self.id, f, 0.0, ["insufficient data — WAIT"])
        base = clamp((0.5 - f.bb_pctb) * 2, -1, 1)
        gate = 1 if (f.rsi14 < 45 or f.rsi14 > 55) else 0.3
        s = base * gate
        rationale = [f"BB %B {f.bb_pctb:.2f} (0=lower band, 1=upper)", f"RSI14 {f.rsi14:.1f}"]
        if f.structure == "RANGE" or ctx.regime.get("regime") in ("SIDEWAYS", "MEAN_REVERSION"):
            s = clamp(s * 1.2, -1, 1)
            rationale.append("range regime boosts mean-reversion fit")
        return Strategy._signal(self.id, f, s, rationale)


class VolatilityStrategy(Strategy):
    id = "volatility"
    kind = "directional"
    description = "Volatility regime positioning (band expansion/contraction)"

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        if f.price <= 0 or f.n_candles < 25:
            return Strategy._signal(self.id, f, 0.0, ["insufficient data — WAIT"])
        s = clamp((f.bb_width - 0.04) * 10, -1, 1)
        rationale = [
            f"BB width {f.bb_width:.3f} vs 0.04 pivot",
            f"realized vol {pct(f.realized_vol / 100 if f.realized_vol > 1 else f.realized_vol)}%",
        ]
        # direction from momentum only when volatility expands
        if s > 0.2:
            dirn = 0.5 * clamp(f.mom_10 * 6, -1, 1)
            s = clamp(0.5 * s + dirn, -1, 1)
            rationale.append("expansion — direction taken from short momentum")
        return Strategy._signal(self.id, f, s, rationale)


class OrderFlowStrategy(Strategy):
    id = "orderflow"
    kind = "directional"
    description = "Order-book imbalance + MACD micro-confirmation"

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        if f.price <= 0 or f.n_candles < 20:
            return Strategy._signal(self.id, f, 0.0, ["insufficient data — WAIT"])
        atr_ref = f.atr14 or (f.price * 0.01)
        imb = clamp(f.ob_imbalance * 2, -1, 1)
        macd_part = clamp(f.macd_hist / atr_ref * 2, -1, 1) * 0.5
        s = clamp(imb + macd_part, -1, 1)
        rationale = [
            f"book imbalance {f.ob_imbalance:+.2f} (top-of-book notional)",
            f"MACD hist/ATR {macd_part:+.2f}",
            f"visible depth ${f.depth_usd:,.0f}",
        ]
        if f.depth_usd and f.depth_usd < 10000:
            s *= 0.5
            rationale.append("thin visible depth — signal halved")
        return Strategy._signal(self.id, f, s, rationale)


CORE_STRATEGIES: list[Strategy] = [
    TrendStrategy(),
    MomentumStrategy(),
    BreakoutStrategy(),
    MeanReversionStrategy(),
    VolatilityStrategy(),
    OrderFlowStrategy(),
]
