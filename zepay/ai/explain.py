"""AI explanation builder (§40).

Every explanation references ACTUAL observed feature values — the strings are
generated FROM the data, so a reason can never appear without the measurement
behind it. If data is missing the explanation says so (UNAVAILABLE), it does
not invent a reason.
"""

from __future__ import annotations

from zepay.core.util import pct, safe_float
from zepay.features.engine import AssetFeatures


def build_explanation(
    f: AssetFeatures,
    regime_info: dict,
    infer: dict,
    strat_result: dict,
    decision: str,
    decision_reasons: list,
    costs: dict,
    portfolio: dict | None = None,
) -> dict:
    reasons: list[str] = []
    risks: list[str] = []
    unavailable: list[str] = []

    # ---- trend / structure (observed) ----
    if f.n_candles >= 50 and f.sma20 and f.sma50:
        if f.price > f.sma20 > f.sma50:
            reasons.append(
                f"bullish structure: price {f.price:.6g} > SMA20 {f.sma20:.6g} "
                f"> SMA50 {f.sma50:.6g}"
            )
        elif f.price < f.sma20 < f.sma50:
            risks.append(
                f"bearish structure: price below SMA20/SMA50 " f"({f.sma20:.6g}/{f.sma50:.6g})"
            )
    if f.supertrend_dir:
        reasons.append(f"supertrend pointing {'up' if f.supertrend_dir > 0 else 'down'}")
    if f.structure and f.structure != "UNKNOWN":
        reasons.append(f"market structure: {f.structure.replace('_', ' ').lower()}")

    # ---- momentum / volume (observed) ----
    if abs(f.mom_10) > 0.005:
        reasons.append(f"10-bar momentum {pct(f.mom_10)}%")
    if f.vol_ratio > 1.3:
        reasons.append(f"volume {f.vol_ratio:.2f}x its 20-bar mean")
    elif f.vol_ratio < 0.7:
        risks.append(f"participation weak: volume {f.vol_ratio:.2f}x mean")

    # ---- order flow (observed; only when a real book exists) ----
    if f.depth_usd > 0:
        if abs(f.ob_imbalance) > 0.1:
            side = "bid" if f.ob_imbalance > 0 else "ask"
            reasons.append(
                f"order-book imbalance {f.ob_imbalance:+.2f} ({side}-heavy, "
                f"${f.depth_usd:,.0f} visible depth)"
            )
    else:
        unavailable.append("order book")

    # ---- volatility ----
    if f.atr_pct:
        (risks if f.atr_pct > 0.05 else reasons).append(
            f"ATR {pct(f.atr_pct)}% of price ({'elevated' if f.atr_pct > 0.05 else 'acceptable'} volatility)"
        )

    # ---- levels ----
    if f.resistance and f.price and min(f.resistance) < f.price * 1.01:
        risks.append(f"resistance nearby at {min(f.resistance):.6g}")
    if f.support and f.price and max(f.support) > f.price * 0.99:
        risks.append(f"support close below at {max(f.support):.6g}")

    # ---- derivatives context (real or UNAVAILABLE — never assumed) ----
    if f.derivs_available:
        if abs(f.funding_rate) > 0.0005:
            who = "longs pay" if f.funding_rate > 0 else "shorts pay"
            risks.append(f"funding {f.funding_rate * 100:+.4f}%/8h ({who})")
        if f.open_interest:
            reasons.append(f"open interest {f.open_interest:,.0f} (context)")
    else:
        unavailable.append("funding/OI (derivatives API unavailable or blocked)")

    # ---- model & ensemble (measured) ----
    reasons.append(
        f"ensemble p(long)={infer.get('p_long')} "
        f"({'calibrated' if infer.get('calibrated') else 'uncalibrated'}, "
        f"mode={infer.get('mode')}, disagreement={infer.get('disagreement')})"
    )
    reasons.append(
        f"expected return {pct(infer.get('expected_return', 0))}% vs expected vol "
        f"{pct(infer.get('expected_vol', 0))}%; expected MAE "
        f"{pct(infer.get('expected_mae', 0))}% / MFE {pct(infer.get('expected_mfe', 0))}%"
    )
    if infer.get("mode") == "untrained-no-signal":
        risks.append("AI models NOT trained on real data yet — strict mode emits WAIT")
    if infer.get("model_degraded"):
        risks.append(f"model DEGRADED: {infer.get('degraded_reason')}")

    # ---- costs (measured/estimated — labeled) ----
    net = safe_float(costs.get("expected_net"))
    reasons.append(
        f"costs: spread {safe_float(costs.get('spread_bps')):.1f}bps + fees "
        f"{safe_float(costs.get('fee_bps')):.1f}bps + slippage est. "
        f"{safe_float(costs.get('slip_bps')):.1f}bps ⇒ expected net edge "
        f"{pct(net)}%"
    )
    if net <= 0:
        risks.append("expected net edge does not exceed costs")

    # ---- portfolio context ----
    if portfolio:
        corr_exp = safe_float(portfolio.get("correlated_exposure"))
        if corr_exp > 0.2:
            risks.append(f"correlated portfolio exposure already {pct(corr_exp)}% of equity")
        tot_exp = safe_float(portfolio.get("total_exposure"))
        if tot_exp > 0.4:
            risks.append(f"total portfolio exposure {pct(tot_exp)}%")

    # ---- data quality ----
    if f.data_stale:
        risks.append("market data STALE — trading gates will block/reduce")
    if unavailable:
        risks.append("missing inputs (honest): " + ", ".join(unavailable))

    return {
        "headline": f"{infer.get('direction', 'WAIT')} {f.market}",
        "confidence": infer.get("confidence", 0),
        "regime": f"{regime_info.get('regime')} (conf {regime_info.get('confidence')})",
        "reasons": reasons,
        "risks": risks,
        "decision": decision,
        "decision_reasons": decision_reasons,
        "strategy": strat_result.get("best", ""),
        "model_version": infer.get("model_version", ""),
        "honesty_note": "Every line above is generated from measured values. "
        "Missing data is listed as missing — never invented.",
    }
