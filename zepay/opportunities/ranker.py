"""Opportunity ranker (§19) + mandatory pre-trade simulation (§18).

Score optimizes RISK-ADJUSTED opportunity quality — not raw predicted profit:

  score = signal strength + model confidence + liquidity + momentum
          + order flow + regime compatibility + expected NET edge
          − volatility risk − spread − slippage − funding
          − correlation risk − execution risk − model uncertainty

Every candidate additionally passes the pre-trade simulation chain:
  SIGNAL → SIMULATED ENTRY → SPREAD → SLIPPAGE → FEES → FUNDING → LATENCY
  → EXPECTED EXIT → EXPECTED NET P&L → (risk/portfolio/execution checks are
  the Risk Engine's job). If expected net edge does not exceed estimated
  costs and uncertainty: REJECT (score penalized, decision gated downstream).
"""

from __future__ import annotations

import math

from zepay.core.domain import Opportunity
from zepay.core.util import clamp, pct, safe_float


class PreTradeSimulator:
    """§18 — deterministic cost/uncertainty simulation on REAL book data."""

    def __init__(self, cost_model, config):
        self.costs = cost_model
        self.config = config

    def simulate(self, f, infer) -> dict:
        comps = self.costs.components(f)
        exp_ret = safe_float(infer.get("expected_return"))
        exp_vol = safe_float(infer.get("expected_vol"))
        uncertainty = safe_float(infer.get("uncertainty"))
        # expected exit: horizon move net of an uncertainty haircut
        gross = exp_ret
        cost = comps["round_trip_pct"]
        latency = safe_float(self.config.get("paper_latency_ms", 120), 120)
        latency_risk = exp_vol * (latency / 1000.0) * 0.05  # small, vol-scaled
        funding_hold = comps["funding_pct"]
        # risk-adjusted: subtract half the expected vol and the disagreement
        # uncertainty — the trade must beat costs AND its own noise
        net = gross - cost - latency_risk - 0.5 * exp_vol - 0.5 * uncertainty * exp_vol
        min_edge = safe_float(self.config.get("min_edge_pct", 0.0015))
        return {
            "gross_expected_pct": round(gross, 5),
            "costs": comps,
            "latency_ms": int(latency),
            "latency_risk_pct": round(latency_risk, 6),
            "funding_hold_pct": round(funding_hold, 6),
            "uncertainty": round(uncertainty, 4),
            "expected_net_pct": round(net, 5),
            "min_edge_pct": min_edge,
            "passes": bool(net >= min_edge),
            "verdict": (
                "APPROVE_SIM"
                if net >= min_edge
                else "REJECT_SIM: expected net edge does not exceed costs+uncertainty"
            ),
        }


class OpportunityRanker:
    def __init__(self, config, cost_model, simulator: PreTradeSimulator):
        self.config = config
        self.costs = cost_model
        self.sim = simulator

    def build(
        self,
        market: str,
        f,
        regime_info: dict,
        strat_result: dict,
        infer: dict,
        venue: str = "binance_spot",
        corr_exposure: float = 0.0,
    ) -> tuple[Opportunity, dict]:
        simulation = self.sim.simulate(f, infer)
        exp_net = simulation["expected_net_pct"]
        cost = simulation["costs"]["round_trip_pct"]

        # ---- risk-adjusted score (conceptual formula from §19) ----
        base = 50 + (safe_float(infer.get("p_long"), 0.5) - 0.5) * 120  # signal strength
        base += safe_float(infer.get("quality")) * 20 - 10  # model confidence/quality
        base += regime_info.get("confidence", 0) * 10 - 5  # regime compatibility
        if infer.get("direction") == "WAIT":
            base -= 25  # cash is a valid decision
        if exp_net < safe_float(self.config.get("min_edge_pct", 0.0015)):
            base -= 15  # fails pre-trade simulation
        if f.depth_usd:
            base += clamp(math.log10(max(f.depth_usd, 1)) - 5, -1, 2)  # liquidity
        if f.spread_bps:
            base -= clamp(f.spread_bps / 10.0, 0, 6)  # spread
        base -= clamp(safe_float(infer.get("expected_vol")) * 200, 0, 8)  # volatility risk
        base -= clamp(safe_float(infer.get("uncertainty")) * 15, 0, 10)  # model disagreement
        base -= clamp(corr_exposure * 20, 0, 8)  # correlation risk
        base -= clamp(safe_float(self.config.get("execution_risk_penalty", 0)) * 5, 0, 5)
        stars = safe_float((self.config.get("priorities") or {}).get(market, 0), 0)
        base += stars * 1.0
        score = clamp(base, 0, 100)

        evald = strat_result.get("evald", {})
        best_strat = strat_result.get("best") or (
            max(evald.items(), key=lambda kv: kv[1]["fit"] * (0.5 + abs(kv[1]["score"])))[0]
            if evald
            else ""
        )

        reasons = [
            f"Direction model: {infer['direction']} (p={infer['p_long']}, mode={infer['mode']})",
            f"Regime: {regime_info['regime']} ({regime_info['confidence']})",
            f"Best strategy fit: {best_strat}",
            f"Expected move {pct(infer['expected_return'])}% vs cost {pct(cost)}% "
            f"→ net {pct(exp_net)}%",
            f"Pre-trade simulation: {simulation['verdict']}",
        ]
        if infer.get("mode") == "heuristic-composite-LABELED":
            reasons.append(
                "⚠ heuristic composite (models untrained, non-strict mode) — "
                "labeled, not model output"
            )
        if infer.get("mode") == "untrained-no-signal":
            reasons.append(
                "AI models not trained on real data yet — no signal emitted " "(strict mode)"
            )
        if f.data_stale:
            reasons.append("⚠ market data stale")

        opp = Opportunity(
            symbol=market,
            venue=venue,
            direction=infer["direction"],
            score=round(score, 1),
            confidence=safe_float(infer.get("confidence")),
            quality=safe_float(infer.get("quality")),
            regime=regime_info["regime"],
            strategy=best_strat,
            expected_return=safe_float(infer.get("expected_return")),
            expected_net=round(exp_net, 5),
            cost_pct=round(cost, 5),
            price=f.price,
            reasons=reasons,
            machine={
                "p_long": infer.get("p_long"),
                "exp_vol": infer.get("expected_vol"),
                "exp_mae": infer.get("expected_mae"),
                "exp_mfe": infer.get("expected_mfe"),
                "model_version": infer.get("model_version"),
                "trained": infer.get("model_trained"),
                "mode": infer.get("mode"),
                "uncertainty": infer.get("uncertainty"),
                "simulation": simulation,
            },
        )
        return opp, simulation

    @staticmethod
    def rank(opps: list[Opportunity]) -> list[Opportunity]:
        return sorted(opps, key=lambda o: o.score, reverse=True)
