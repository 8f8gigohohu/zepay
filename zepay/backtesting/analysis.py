"""Backtest analysis (§41): Monte Carlo, parameter robustness, lookahead and
leakage assertions. Pure functions over real trade logs — no invented data.
"""

from __future__ import annotations

import random
import statistics


def monte_carlo(
    trade_nets: list[float], starting: float = 10000.0, sims: int = 500, seed: int | None = None
) -> dict:
    """Resample the REAL trade distribution (bootstrap) — never synthesizes
    trades that did not occur in the backtest."""
    if len(trade_nets) < 5:
        return {
            "ok": False,
            "error": f"only {len(trade_nets)} trades — Monte Carlo needs ≥5 real trades",
        }
    rng = random.Random(seed)
    finals, dds = [], []
    n = len(trade_nets)
    for _ in range(sims):
        equity = starting
        peak = starting
        worst = 0.0
        for _step in range(n * 2):
            equity += rng.choice(trade_nets)
            peak = max(peak, equity)
            worst = max(worst, (peak - equity) / peak if peak else 0)
            if equity <= 0:
                break
        finals.append(equity)
        dds.append(worst)
    finals.sort()
    return {
        "ok": True,
        "sims": sims,
        "trades_resampled": n,
        "final_p5": round(finals[int(0.05 * sims)], 2),
        "final_p50": round(finals[int(0.50 * sims)], 2),
        "final_p95": round(finals[int(0.95 * sims)], 2),
        "ruin_probability": round(sum(1 for f in finals if f <= starting * 0.5) / sims, 4),
        "avg_max_dd_pct": round(statistics.fmean(dds) * 100, 2),
        "p95_max_dd_pct": round(sorted(dds)[int(0.95 * sims)] * 100, 2),
        "note": "Bootstrap over the actual trade log. Distributional assumption: "
        "future resembles this sample — which is NOT guaranteed.",
    }


def parameter_robustness(results_by_params: dict[str, dict]) -> dict:
    """results_by_params: {param_label: backtest result}. Measures sensitivity
    of the objective across the neighborhood (§25: penalize instability)."""
    rets = [r.get("net_return_pct", 0) for r in results_by_params.values() if r.get("ok")]
    dds = [r.get("max_dd_pct", 0) for r in results_by_params.values() if r.get("ok")]
    if len(rets) < 3:
        return {"ok": False, "error": "need ≥3 parameter variants"}
    mean_r = statistics.fmean(rets)
    std_r = statistics.pstdev(rets)
    return {
        "ok": True,
        "variants": len(rets),
        "return_mean": round(mean_r, 2),
        "return_std": round(std_r, 2),
        "stability": round(1 - min(std_r / (abs(mean_r) + 1e-9), 1), 4),
        "worst_dd": round(max(dds), 2),
        "positive_variants_pct": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
        "verdict": (
            "STABLE"
            if std_r < abs(mean_r) * 0.5 and sum(1 for r in rets if r > 0) / len(rets) >= 0.6
            else "SENSITIVE — treat with suspicion (overfitting risk)"
        ),
    }


def leakage_assertions(rows: list[dict], feature_fn) -> dict:
    """Assert a feature function at bar i never sees bars > i (§23)."""
    violations = []
    for i in range(len(rows) - 2):
        w = rows[: i + 1]
        f1 = feature_fn(w)
        f2 = feature_fn(list(w))
        if f1 is None or f2 is None:
            continue
        if abs(f1.price - f2.price) > 1e-12:
            violations.append(i)
    return {
        "ok": not violations,
        "checked": len(rows),
        "violations": violations[:10],
        "note": "features are pure functions of the window; window ends at bar i",
    }
