"""Strategy orchestrator (§30): one dispatch point for all strategies.

Outputs (per market):
  * signals  — list[StrategySignal] from every ENABLED strategy
  * evald    — v2-compatible {strategy: {score, fit, weighted}} dict consumed
               by the AI ensemble and opportunity ranker
  * aggregate — fit-weighted mean score (v2 `StrategyEngine.aggregate`)
  * best      — best-fitting strategy id

Enabled set = config `strategies_enabled` (default: the six core directional
strategies + funding_basis when derivatives data exists). Market making is
never auto-enabled (§35).
"""

from __future__ import annotations

import logging

from zepay.core.domain import Direction
from zepay.core.util import clamp
from zepay.features.engine import AssetFeatures
from zepay.regime.engine import REGIME_STRAT_FIT
from zepay.strategies.advanced import ADVANCED_STRATEGIES
from zepay.strategies.base import Strategy, StrategyContext, StrategySignal
from zepay.strategies.core import CORE_STRATEGIES

log = logging.getLogger("zepay.strategies.orchestrator")

DEFAULT_ENABLED = [
    "trend",
    "momentum",
    "breakout",
    "mean_reversion",
    "volatility",
    "orderflow",
    "funding_basis",
]


class StrategyOrchestrator:
    def __init__(self, config):
        self.config = config
        self._by_id: dict[str, Strategy] = {}
        for s in CORE_STRATEGIES + ADVANCED_STRATEGIES:
            self._by_id[s.id] = s

    def all_strategies(self) -> list[dict]:
        return [
            {
                "id": s.id,
                "kind": s.kind,
                "description": s.description,
                "default_enabled": s.default_enabled,
                "requires_derivs": s.requires_derivs,
                "requires_pairs": s.requires_pairs,
                "requires_multi_venue": s.requires_multi_venue,
            }
            for s in self._by_id.values()
        ]

    def enabled_ids(self) -> list[str]:
        ids = self.config.get("strategies_enabled") or DEFAULT_ENABLED
        out = []
        for i in ids:
            s = self._by_id.get(i)
            if s and (s.default_enabled or i in ids):
                out.append(i)
        return out

    def evaluate(self, f: AssetFeatures, ctx: StrategyContext) -> dict:
        regime = (ctx.regime or {}).get("regime", "UNSTABLE")
        fit = REGIME_STRAT_FIT.get(regime, dict.fromkeys(self._by_id, 0.5))
        signals: list[StrategySignal] = []
        evald: dict[str, dict] = {}
        for sid in self.enabled_ids():
            strat = self._by_id.get(sid)
            if strat is None:
                continue
            if strat.requires_derivs and not (ctx.derivs or {}).get("available"):
                # still emit the honest WAIT signal, but zero-weight it
                sig = strat.evaluate(f, ctx)
                signals.append(sig)
                evald[sid] = {
                    "score": 0.0,
                    "fit": fit.get(sid, 0.5),
                    "weighted": 0.0,
                    "note": "required data UNAVAILABLE",
                }
                continue
            try:
                sig = strat.evaluate(f, ctx)
            except Exception as e:
                log.warning("strategy %s failed on %s: %s", sid, f.market, e)
                evald[sid] = {
                    "score": 0.0,
                    "fit": fit.get(sid, 0.5),
                    "weighted": 0.0,
                    "note": f"strategy error: {e}"[:120],
                }
                continue
            signals.append(sig)
            score = self._signal_to_score(sig)
            evald[sid] = {
                "score": round(score, 4),
                "fit": round(fit.get(sid, 0.5), 3),
                "weighted": round(score * fit.get(sid, 0.5), 4),
                "direction": sig.direction,
                "confidence": sig.confidence,
                "rationale": sig.rationale[:4],
            }
        # strategies not evaluated (disabled) keep v2-shaped zero entries so
        # downstream consumers can rely on stable keys
        for sid in self._by_id:
            evald.setdefault(
                sid, {"score": 0.0, "fit": fit.get(sid, 0.5), "weighted": 0.0, "note": "disabled"}
            )
        return {
            "signals": signals,
            "evald": evald,
            "aggregate": round(self.aggregate(evald), 4),
            "best": self.best(evald),
            "regime": regime,
        }

    @staticmethod
    def _signal_to_score(sig: StrategySignal) -> float:
        """Map a StrategySignal back to the v2 [-1,1] score space."""
        if sig.direction == Direction.LONG.value:
            return clamp(sig.confidence, 0, 1)
        if sig.direction == Direction.SHORT.value:
            return clamp(-sig.confidence, -1, 0)
        return 0.0

    @staticmethod
    def aggregate(evald: dict) -> float:
        tot_w = sum(v["fit"] for v in evald.values()) or 1
        return sum(v["weighted"] for v in evald.values()) / tot_w

    @staticmethod
    def best(evald: dict) -> str:
        if not evald:
            return ""
        return max(evald.items(), key=lambda kv: kv[1]["fit"] * (0.5 + abs(kv[1]["score"])))[0]
