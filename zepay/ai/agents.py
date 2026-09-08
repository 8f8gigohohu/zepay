"""Research agents (§15) — TradingAgents-inspired pipeline, ported from v2.

Guardrails (architectural, not aspirational):
  * agents receive ONLY real system data passed explicitly as arguments
  * deterministic analysts are cheap, auditable, offline
  * the optional LLM debate is schema-validated; invalid output is DISCARDED
  * agents have NO reference to execution, risk limits or credentials
  * recommendations annotate the decision ledger — they never auto-execute
"""

from __future__ import annotations

import json
import logging
import re

from zepay.core.util import clamp, jdump, pct, safe_float
from zepay.exchanges.http import http_error_str

log = logging.getLogger("zepay.ai.agents")

AGENT_GUARDRAILS = [
    "Agents receive real market data and engine state only.",
    "LLM agent output is schema-validated; failures are discarded, not guessed.",
    "Agents have NO access to ExecutionEngine, RiskEngine limits or credentials.",
    "Agent recommendations never auto-execute; they only annotate the ledger.",
]


class Agents:
    """Deterministic analyst agents — every verdict cites measured evidence."""

    @staticmethod
    def market_analyst(f):
        ev = []
        trend = (
            "UP"
            if f.price > f.sma20 > f.sma50
            else ("DOWN" if f.price < f.sma20 < f.sma50 else "MIXED")
        )
        ev.append(
            f"Structure {trend}: price {f.price:.6g} vs SMA20 {f.sma20:.6g} / "
            f"SMA50 {f.sma50:.6g}"
        )
        ev.append(
            f"Momentum 10/20-bar: {pct(f.mom_10)}% / {pct(f.mom_20)}%; " f"24h {f.change24:+.2f}%"
        )
        ev.append(f"Volume ratio vs 20-bar mean: {f.vol_ratio:.2f}x")
        verdict = (
            "BULLISH"
            if (trend == "UP" and f.mom_10 > 0)
            else ("BEARISH" if trend == "DOWN" else "NEUTRAL")
        )
        return {"agent": "MarketAnalyst", "verdict": verdict, "confidence": 0.6, "evidence": ev}

    @staticmethod
    def technical_analyst(f):
        ev = [
            f"RSI14 {f.rsi14:.1f}",
            f"MACD hist {f.macd_hist:.6g}",
            f"Bollinger %B {f.bb_pctb:.2f} (width {pct(f.bb_width)}%)",
            f"ATR14 {pct(f.atr_pct)}% of price",
        ]
        if f.rsi14 > 70:
            v, c = "BEARISH", 0.55
        elif f.rsi14 < 30:
            v, c = "BULLISH", 0.55
        elif f.macd_hist > 0 and f.bb_pctb > 0.5:
            v, c = "BULLISH", 0.5
        elif f.macd_hist < 0 and f.bb_pctb < 0.5:
            v, c = "BEARISH", 0.5
        else:
            v, c = "NEUTRAL", 0.45
        return {"agent": "TechnicalAnalyst", "verdict": v, "confidence": c, "evidence": ev}

    @staticmethod
    def quant_analyst(f, infer):
        ev = [
            f"Model p(long) {infer['p_long']} mode={infer['mode']}",
            f"Expected return {pct(infer['expected_return'])}% over horizon",
            f"Expected vol {pct(infer['expected_vol'])}%",
            f"Expected MAE/MFE {pct(infer.get('expected_mae', 0))}% / "
            f"{pct(infer.get('expected_mfe', 0))}%",
            f"Trade quality score {infer['quality']}",
        ]
        v = (
            "BULLISH"
            if infer["direction"] == "LONG"
            else ("BEARISH" if infer["direction"] == "SHORT" else "NEUTRAL")
        )
        return {
            "agent": "QuantAnalyst",
            "verdict": v,
            "confidence": infer["confidence"],
            "evidence": ev,
        }

    @staticmethod
    def regime_analyst(f, regime_info):
        ev = [f"Regime {regime_info['regime']} (conf {regime_info['confidence']})"] + regime_info[
            "reasons"
        ]
        v = (
            "BULLISH"
            if regime_info["regime"] in ("BULLISH_TREND", "BREAKOUT", "RECOVERY")
            else ("BEARISH" if regime_info["regime"] in ("BEARISH_TREND", "PANIC") else "NEUTRAL")
        )
        return {
            "agent": "RegimeAnalyst",
            "verdict": v,
            "confidence": regime_info["confidence"],
            "evidence": ev,
        }

    @staticmethod
    def derivatives_analyst(f):
        if not f.derivs_available:
            return {
                "agent": "DerivativesAnalyst",
                "verdict": "NO_DATA",
                "confidence": 0,
                "evidence": [
                    "Futures data (funding/OI) UNAVAILABLE from provider — " "no assumptions made"
                ],
            }
        ev = [
            f"Funding rate {pct(f.funding_rate, 4)}%/8h",
            f"Open interest {f.open_interest:,.0f} contracts",
        ]
        if f.funding_rate > 0.0005:
            ev.append("Elevated positive funding — longs pay; crowded long positioning")
            return {
                "agent": "DerivativesAnalyst",
                "verdict": "BEARISH",
                "confidence": 0.5,
                "evidence": ev,
            }
        if f.funding_rate < -0.0005:
            ev.append("Negative funding — shorts pay; crowded short positioning")
            return {
                "agent": "DerivativesAnalyst",
                "verdict": "BULLISH",
                "confidence": 0.5,
                "evidence": ev,
            }
        return {
            "agent": "DerivativesAnalyst",
            "verdict": "NEUTRAL",
            "confidence": 0.4,
            "evidence": ev,
        }

    @staticmethod
    def cross_asset_analyst(f):
        ev = [
            f"BTC 20-bar momentum {pct(f.btc_mom)}%",
            f"Market momentum {pct(f.market_mom)}% (breadth {pct(f.market_breadth)}% up)",
            f"Correlation to BTC {f.corr_btc:+.2f}",
            f"Relative strength vs market {pct(f.rel_strength)}%",
        ]
        v = (
            "BULLISH"
            if (f.market_mom > 0 and f.rel_strength > 0)
            else ("BEARISH" if (f.market_mom < 0 and f.rel_strength < 0) else "NEUTRAL")
        )
        return {"agent": "CrossAssetAnalyst", "verdict": v, "confidence": 0.5, "evidence": ev}

    @staticmethod
    def risk_analyst(f):
        ev = [
            f"Spread {f.spread_bps:.1f} bps",
            f"Visible depth ${f.depth_usd:,.0f}",
            f"ATR {pct(f.atr_pct)}%",
            f"Liquidity 24h ${f.liquidity_usd:,.0f}",
        ]
        risky = (
            f.spread_bps > 15 or f.atr_pct > 0.05 or (f.liquidity_usd and f.liquidity_usd < 500000)
        )
        return {
            "agent": "RiskAnalyst",
            "verdict": "CAUTION" if risky else "OK",
            "confidence": 0.6,
            "evidence": ev,
        }

    @staticmethod
    def portfolio_analyst(exposure, positions):
        ev = [
            f"Exposure {pct(exposure.get('total_pct', 0))}% "
            f"(long {pct(exposure.get('long_pct', 0))}% / short {pct(exposure.get('short_pct', 0))}%)",
            f"Open positions {len(positions)}",
        ]
        for m, p in (exposure.get("per_asset") or {}).items():
            ev.append(f"{m}: {pct(p)}% of equity")
        return {
            "agent": "PortfolioAnalyst",
            "verdict": "OK" if exposure.get("total_pct", 0) < 0.5 else "CAUTION",
            "confidence": 0.5,
            "evidence": ev,
        }

    @staticmethod
    def execution_analyst(f, cost_model):
        cost = cost_model.round_trip_pct(f)
        ev = [
            f"Round-trip cost estimate {pct(cost)}% (fees+spread+slippage+funding est.)",
            f"Spread {f.spread_bps:.1f} bps",
        ]
        return {
            "agent": "ExecutionAnalyst",
            "verdict": "OK" if cost < 0.004 else "EXPENSIVE",
            "confidence": 0.5,
            "evidence": ev,
        }

    @classmethod
    def run_deterministic(cls, f, regime_info, infer, exposure, positions, cost_model):
        return [
            cls.market_analyst(f),
            cls.technical_analyst(f),
            cls.quant_analyst(f, infer),
            cls.regime_analyst(f, regime_info),
            cls.derivatives_analyst(f),
            cls.cross_asset_analyst(f),
            cls.risk_analyst(f),
            cls.portfolio_analyst(exposure, positions),
            cls.execution_analyst(f, cost_model),
        ]


class ResearchEngineLLM:
    """Optional LLM research layer (advisory). Bull/bear debate using ONLY the
    supplied real data; output must match the schema or it is DISCARDED."""

    SCHEMA_HINT = (
        '{"bull_case": ["..."], "bear_case": ["..."], '
        '"bias": "LONG"|"SHORT"|"WAIT", "confidence": 0.0-1.0, '
        '"key_risks": ["..."], "summary": "..."}'
    )

    def __init__(self, config, llm_manager, audit_fn):
        self.config = config
        self.llm = llm_manager
        self.audit = audit_fn

    def build_prompt(self, market, f, regime_info, infer, agents_out):
        data = {
            "market": market,
            "price": f.price,
            "change24h_pct": f.change24,
            "rsi14": round(f.rsi14, 1),
            "atr_pct": round(f.atr_pct * 100, 3),
            "momentum_10": round(f.mom_10 * 100, 3),
            "momentum_20": round(f.mom_20 * 100, 3),
            "volume_ratio": round(f.vol_ratio, 2),
            "spread_bps": round(f.spread_bps, 1),
            "funding_rate": f.funding_rate if f.derivs_available else None,
            "regime": regime_info["regime"],
            "quant_model": {
                "p_long": infer["p_long"],
                "direction": infer["direction"],
                "confidence": infer["confidence"],
                "mode": infer["mode"],
                "disagreement": infer.get("disagreement"),
            },
            "analysts": [{"agent": a["agent"], "verdict": a["verdict"]} for a in agents_out],
        }
        system = (
            "You are a crypto research analyst on a trading desk. Use ONLY the data "
            "provided. Do not invent prices, news or on-chain facts. If data is "
            "missing, say so. Respond with a single JSON object, no markdown, "
            "matching exactly this schema:\n"
            + self.SCHEMA_HINT
            + "\nThis is research only — you cannot place orders."
        )
        user = "Market data and engine state:\n" + jdump(data)
        return system, user

    @staticmethod
    def validate(text):
        try:
            t = (text or "").strip()
            if t.startswith("```"):
                t = re.sub(r"^```[a-z]*\n?|```$", "", t).strip()
            start, end = t.find("{"), t.rfind("}")
            if start < 0 or end <= start:
                return None
            d = json.loads(t[start : end + 1])
            if not isinstance(d, dict):
                return None
            bias = str(d.get("bias", "WAIT")).upper()
            if bias not in ("LONG", "SHORT", "WAIT"):
                bias = "WAIT"
            return {
                "bull_case": [str(x)[:300] for x in (d.get("bull_case") or [])][:8],
                "bear_case": [str(x)[:300] for x in (d.get("bear_case") or [])][:8],
                "key_risks": [str(x)[:300] for x in (d.get("key_risks") or [])][:8],
                "bias": bias,
                "confidence": clamp(safe_float(d.get("confidence"), 0), 0, 1),
                "summary": str(d.get("summary", ""))[:1000],
            }
        except Exception:
            return None

    def run(self, market, f, regime_info, infer, agents_out):
        if not self.config.get("research_llm_enabled"):
            return {
                "available": False,
                "reason": "LLM research layer disabled (quant ensemble only)",
            }
        if not self.llm.active_provider():
            return {"available": False, "reason": "no AI provider configured"}
        system, user = self.build_prompt(market, f, regime_info, infer, agents_out)
        try:
            r = self.llm.chat(system, user, max_tokens=900, timeout=60)
        except Exception as e:
            self.audit("ai", "llm_research_failed", {"market": market, "error": http_error_str(e)})
            return {"available": False, "reason": f"LLM call failed: {http_error_str(e)}"}
        parsed = self.validate(r["text"])
        if parsed is None:
            self.audit("ai", "llm_research_invalid_output", {"market": market})
            return {"available": False, "reason": "LLM output failed schema validation — discarded"}
        parsed.update(
            {
                "available": True,
                "provider": r["provider"],
                "model": r["model"],
                "local": r["local"],
                "latency_ms": r["latency_ms"],
                "advisory_only": True,
            }
        )
        return parsed
