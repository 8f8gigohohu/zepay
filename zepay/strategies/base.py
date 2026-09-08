"""Strategy framework (§30).

Every strategy — trend following to market making — implements the same
interface and emits the same StrategySignal. Strategies NEVER execute: they
feed the ONE pipeline
    MARKET DATA → FEATURES → SIGNAL → OPPORTUNITY → RISK → EXECUTION
There are no independent trading loops anywhere in V3.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from zepay.core.domain import Direction, StrategySignal
from zepay.features.engine import AssetFeatures


@dataclass
class StrategyContext:
    """Everything a strategy may look at besides its own market's features.
    Explicit data access — strategies cannot reach into global state."""

    regime: dict = field(default_factory=dict)
    derivs: dict | None = None  # funding/OI (may be None = UNAVAILABLE)
    depth: dict | None = None
    cross_venue_prices: dict = field(default_factory=dict)  # venue -> price for arb
    pairs: dict = field(default_factory=dict)  # stat-arb partner features
    portfolio: dict = field(default_factory=dict)  # positions/exposure snapshot
    costs: dict = field(default_factory=dict)  # fee/slip/spread bps
    position: dict | None = None  # open position on this market


class Strategy(ABC):
    id = "abstract"
    kind = "directional"  # directional | accumulative | market_making | arbitrage
    requires_derivs = False
    requires_pairs = False
    requires_multi_venue = False
    default_enabled = True
    description = ""

    @abstractmethod
    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> StrategySignal:
        """Return a StrategySignal. Honest strategies return direction=WAIT
        with a rationale when their required data is unavailable."""

    # ---- helpers shared by implementations ----
    @staticmethod
    def _levels(
        f: AssetFeatures, atr_mult_stop: float = 1.5, atr_mult_tp: float = 3.0
    ) -> tuple[float, float, float]:
        stop_dist = max(f.atr14 * atr_mult_stop, f.price * 0.004) if f.price else 0
        return f.price, stop_dist, stop_dist * (atr_mult_tp / max(atr_mult_stop, 1e-9))

    @staticmethod
    def _signal(
        strategy: str,
        f: AssetFeatures,
        score: float,
        rationale: list,
        direction: str | None = None,
        entry_th: float = 0.15,
        stop_mult: float = 1.5,
        tp_mult: float = 3.0,
        expected_return: float | None = None,
        model_version: str = "strategy-3.0.0",
    ) -> StrategySignal:
        score = max(-1.0, min(1.0, score))
        if direction is None:
            direction = (
                Direction.LONG.value
                if score >= entry_th
                else Direction.SHORT.value if score <= -entry_th else Direction.WAIT.value
            )
        confidence = min(abs(score), 1.0)
        stop_dist = max(f.atr14 * stop_mult, f.price * 0.004) if f.price else 0
        if direction == Direction.LONG.value:
            entry, stop, tp = (
                f.price,
                f.price - stop_dist,
                f.price + stop_dist * (tp_mult / stop_mult),
            )
        elif direction == Direction.SHORT.value:
            entry, stop, tp = (
                f.price,
                f.price + stop_dist,
                f.price - stop_dist * (tp_mult / stop_mult),
            )
        else:
            entry, stop, tp = f.price, 0, 0
        exp_ret = expected_return if expected_return is not None else score * max(f.atr_pct, 0.004)
        from zepay.core.util import utcnow_iso

        return StrategySignal(
            strategy=strategy,
            symbol=f.market,
            venue=f.venue,
            direction=direction,
            confidence=round(confidence, 4),
            entry=round(entry, 10),
            stop=round(stop, 10),
            take_profit=round(tp, 10),
            expected_return=round(exp_ret, 6),
            expected_vol=round(max(f.atr_pct * 1.1, 0.002), 6),
            rationale=rationale,
            required_data=["candles", "ticker"],
            ts=utcnow_iso(),
            model_version=model_version,
        )
