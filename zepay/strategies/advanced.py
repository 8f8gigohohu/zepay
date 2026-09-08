"""Advanced strategy types (§3, §30, §31, §35-37).

Honesty rules baked in:
  * funding/basis: WAIT with reason when derivatives context is UNAVAILABLE
    (e.g. fapi geo-blocked) — never assumes zero funding.
  * grid: refuses to signal during PANIC/HIGH_VOL/BREAKOUT regimes (§37).
  * DCA: emits accumulation intent only inside risk-defined drawdown bands;
    the Risk Engine caps total capital — no unlimited averaging down (§36).
  * market making: DISABLED unless explicitly configured (§35) — evaluate()
    returns WAIT with a clear reason otherwise.
  * stat arb / cross-venue arb: actionable ONLY when measured edge exceeds
    every real cost component (§31). No false arbitrage.
"""

from __future__ import annotations

import statistics

from zepay.core.domain import Direction
from zepay.core.util import clamp, pct, safe_float
from zepay.features.engine import AssetFeatures
from zepay.strategies.base import Strategy, StrategyContext, StrategySignal


class FundingBasisStrategy(Strategy):
    id = "funding_basis"
    kind = "directional"
    requires_derivs = True
    description = "Fade extreme funding (perp basis vs spot)"

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        if not ctx.derivs or not ctx.derivs.get("available"):
            return Strategy._signal(
                self.id,
                f,
                0.0,
                ["derivatives context UNAVAILABLE (funding/OI blocked or disabled) — WAIT"],
                direction=Direction.WAIT.value,
            )
        fr = safe_float(ctx.derivs.get("funding_rate"))
        rationale = [f"funding rate {fr * 100:+.4f}%/8h (real, from venue)"]
        # extreme funding → crowded positioning → fade
        s = 0.0
        if fr >= 0.0008:
            s = -clamp((fr - 0.0005) * 400, 0, 1)
            rationale.append("funding strongly positive — longs crowded, fade bias")
        elif fr <= -0.0008:
            s = clamp((-fr - 0.0005) * 400, 0, 1)
            rationale.append("funding strongly negative — shorts crowded, fade bias")
        else:
            rationale.append("funding near neutral — no basis edge")
        oi = safe_float(ctx.derivs.get("open_interest"))
        if oi:
            rationale.append(f"open interest {oi:,.0f} contracts (context)")
        return Strategy._signal(self.id, f, s, rationale, entry_th=0.35)


class GridStrategy(Strategy):
    id = "grid"
    kind = "accumulative"
    description = "Volatility-adaptive grid inside a detected range (§37)"

    STOP_REGIMES = ("PANIC", "HIGH_VOL", "BREAKOUT", "UNSTABLE")

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        regime = ctx.regime.get("regime", "UNSTABLE")
        if regime in self.STOP_REGIMES:
            return Strategy._signal(
                self.id,
                f,
                0.0,
                [f"grid STOPPED: abnormal market conditions (regime {regime})"],
                direction=Direction.WAIT.value,
            )
        if f.high_20 <= 0 or f.low_20 <= 0 or f.price <= 0:
            return Strategy._signal(self.id, f, 0.0, ["no range detected — WAIT"])
        rng = f.high_20 - f.low_20
        if rng <= 0 or rng / f.price < 0.01:
            return Strategy._signal(self.id, f, 0.0, ["range too tight for grid — WAIT"])
        pos_in_range = (f.price - f.low_20) / rng  # 0 at support, 1 at resistance
        s = clamp((0.5 - pos_in_range) * 2, -1, 1)  # buy low in range, sell high
        n_grids = max(4, min(20, int(rng / max(f.atr14, f.price * 0.002))))
        rationale = [
            f"range {f.low_20:,.4f}–{f.high_20:,.4f} ({pct(rng / f.price)}% width)",
            f"price at {pos_in_range * 100:.0f}% of range",
            f"volatility-adaptive grid count {n_grids}",
            f"regime {regime} — grid permitted",
        ]
        return Strategy._signal(self.id, f, s, rationale, entry_th=0.4, stop_mult=2.5, tp_mult=2.0)


class DCAStrategy(Strategy):
    id = "dca"
    kind = "accumulative"
    description = "Trend-filtered, drawdown-triggered accumulation (§36)"

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        if f.price <= 0 or f.n_candles < 50:
            return Strategy._signal(self.id, f, 0.0, ["insufficient history — WAIT"])
        # drawdown from recent high
        dd = (f.high_20 - f.price) / f.high_20 if f.high_20 else 0
        # trend filter: only accumulate with or neutral against the higher TF trend
        trend_ok = f.ema50 <= 0 or f.price >= f.ema50 * 0.92
        s = 0.0
        rationale = [
            f"drawdown from 20-bar high {pct(dd)}%",
            f"trend filter {'PASS' if trend_ok else 'FAIL (price far below EMA50)'}",
        ]
        if dd >= 0.05 and trend_ok and ctx.regime.get("regime") not in ("PANIC", "UNSTABLE"):
            s = clamp(dd * 6, 0, 1)  # deeper drawdown → stronger accumulation intent
            rationale.append("volatility-adjusted DCA tranche triggered")
        elif dd >= 0.05:
            rationale.append("drawdown band reached but trend/panic filter blocks adding")
        # NOTE: total DCA capital is capped by the Risk Engine (max_asset_exposure,
        # max_total_exposure) — this signal can NEVER drive unlimited averaging down.
        return Strategy._signal(
            self.id,
            f,
            s,
            rationale,
            direction=Direction.LONG.value if s >= 0.3 else Direction.WAIT.value,
            entry_th=0.3,
        )


class MarketMakingStrategy(Strategy):
    id = "market_making"
    kind = "market_making"
    default_enabled = False  # §35: never enabled without explicit configuration
    description = "Inventory-skewed two-sided quoting (explicit opt-in only)"

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        params = (ctx.portfolio or {}).get("mm_params") or {}
        if not params.get("enabled"):
            return Strategy._signal(
                self.id,
                f,
                0.0,
                [
                    "market making NOT ENABLED — requires explicit configuration "
                    "(§35: never auto-enabled)"
                ],
                direction=Direction.WAIT.value,
            )
        if f.spread_bps <= 0 or f.depth_usd <= 0:
            return Strategy._signal(
                self.id,
                f,
                0.0,
                ["no real book data — quoting would be blind, WAIT"],
                direction=Direction.WAIT.value,
            )
        if ctx.regime.get("regime") in ("PANIC", "BREAKOUT", "HIGH_VOL"):
            return Strategy._signal(
                self.id,
                f,
                0.0,
                [
                    f"quote cancellation: regime {ctx.regime.get('regime')} "
                    "(adverse-selection protection)"
                ],
                direction=Direction.WAIT.value,
            )
        half_spread_bps = max(f.spread_bps / 2, safe_float(params.get("min_spread_bps"), 3))
        vol_adj = clamp(f.atr_pct * 100, 0.5, 5)
        half_spread_bps = max(half_spread_bps, vol_adj)
        inventory = safe_float((ctx.position or {}).get("inventory_skew"), 0)  # -1..1
        skew = clamp(inventory, -1, 1) * half_spread_bps * 0.5
        bid = f.price * (1 - (half_spread_bps + skew) / 1e4)
        ask = f.price * (1 + (half_spread_bps - skew) / 1e4)
        rationale = [
            f"quote bid {bid:,.6f} / ask {ask:,.6f} (half-spread {half_spread_bps:.1f}bps)",
            f"volatility adjustment {vol_adj:.1f}bps",
            f"inventory skew {skew:+.1f}bps",
            "stale-quote protection: quotes cancelled if book age > threshold",
        ]
        sig = Strategy._signal(
            self.id,
            f,
            -skew / max(half_spread_bps, 1e-9) * 0.3,
            rationale,
            direction=Direction.WAIT.value,
        )
        sig.expected_vol = f.atr_pct
        return sig


class StatArbStrategy(Strategy):
    id = "stat_arb"
    kind = "arbitrage"
    requires_pairs = True
    description = "Correlated-pair z-score mean reversion (§30)"

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        partner = (ctx.pairs or {}).get(f.market)
        if not partner:
            return Strategy._signal(
                self.id, f, 0.0, ["no correlated partner with sufficient history — WAIT"]
            )
        p_f: AssetFeatures = partner["features"]
        ratio_hist = partner.get("ratio_history") or []
        if len(ratio_hist) < 30 or f.price <= 0 or p_f.price <= 0:
            return Strategy._signal(
                self.id, f, 0.0, ["insufficient ratio history for z-score — WAIT"]
            )
        cur_ratio = f.price / p_f.price
        mu = statistics.fmean(ratio_hist)
        sd = statistics.pstdev(ratio_hist) or 1e-9
        z = (cur_ratio - mu) / sd
        costs_pct = safe_float((ctx.costs or {}).get("round_trip_pct"), 0.002)
        expected_move = abs(z) * sd * mu / 2  # half-spread convergence estimate
        rationale = [
            f"pair {f.market} vs {p_f.market}: z={z:+.2f} (ratio {cur_ratio:.6f}, "
            f"mean {mu:.6f})",
            f"expected convergence move {pct(expected_move)}% vs costs {pct(costs_pct)}%",
        ]
        if abs(z) < 1.5:
            rationale.append("z-score inside entry band — WAIT")
            return Strategy._signal(self.id, f, 0.0, rationale)
        if expected_move <= costs_pct * 2:
            rationale.append("convergence edge does not exceed 2x costs — REJECT (§31 rule)")
            return Strategy._signal(self.id, f, 0.0, rationale, direction=Direction.WAIT.value)
        s = clamp(-z / 3.0, -1, 1)  # long the cheap side via direction on this asset
        rationale.append("long/short the pair legs toward ratio mean")
        return Strategy._signal(self.id, f, s, rationale, entry_th=0.3)


class CrossVenueArbStrategy(Strategy):
    id = "cross_venue_arb"
    kind = "arbitrage"
    requires_multi_venue = True
    description = "Cross-venue price difference net of ALL real costs (§31)"

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        prices = ctx.cross_venue_prices or {}
        venues = [(v, safe_float(p)) for v, p in prices.items() if safe_float(p) > 0]
        if len(venues) < 2:
            return Strategy._signal(
                self.id,
                f,
                0.0,
                [f"only {len(venues)} venue(s) with real prices — arb UNAVAILABLE, WAIT"],
                direction=Direction.WAIT.value,
            )
        lo_v, lo_p = min(venues, key=lambda x: x[1])
        hi_v, hi_p = max(venues, key=lambda x: x[1])
        diff_pct = (hi_p - lo_p) / lo_p if lo_p else 0
        costs = ctx.costs or {}
        fee_pct = safe_float(costs.get("fee_pct"), 0.001) * 2  # both legs
        slip_pct = safe_float(costs.get("slip_pct"), 0.0005) * 2
        spread_pct = safe_float(costs.get("spread_pct"), 0.0004)
        transfer_pct = safe_float(costs.get("transfer_pct"), 0.0)  # withdrawal/deposit+network
        latency_risk_pct = safe_float(costs.get("latency_risk_pct"), 0.0005)
        total_cost = fee_pct + slip_pct + spread_pct + transfer_pct + latency_risk_pct
        net = diff_pct - total_cost
        rationale = [
            f"buy {lo_v} @ {lo_p:,.6f} / sell {hi_v} @ {hi_p:,.6f}",
            f"gross difference {pct(diff_pct)}%",
            f"costs: fees {pct(fee_pct)}% + slippage {pct(slip_pct)}% + spread "
            f"{pct(spread_pct)}% + transfer/network {pct(transfer_pct)}% + latency risk "
            f"{pct(latency_risk_pct)}% = {pct(total_cost)}%",
            f"net edge {pct(net)}%",
        ]
        if net <= 0:
            rationale.append("NO FALSE ARBITRAGE: net edge ≤ 0 after all costs — WAIT")
            return Strategy._signal(self.id, f, 0.0, rationale, direction=Direction.WAIT.value)
        s = clamp(net * 100, 0, 1)
        rationale.append(
            "execution probability < 1 (legs can diverge mid-flight) — " "sized down by ranker"
        )
        return Strategy._signal(
            self.id, f, s, rationale, direction=Direction.LONG.value, entry_th=0.2
        )


ADVANCED_STRATEGIES: list[Strategy] = [
    FundingBasisStrategy(),
    GridStrategy(),
    DCAStrategy(),
    MarketMakingStrategy(),
    StatArbStrategy(),
    CrossVenueArbStrategy(),
]
