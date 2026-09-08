"""Hyperopt / optimization engine (§25, freqtrade-hyperopt-inspired).

Methods: grid, random, and a compact GP-UCB Bayesian optimizer (numpy RBF
kernel — no external ML dependency).

The objective is NOT historical profit (§25/§69). It is a penalized composite:

    obj = net_return
        − 1.0 × max_drawdown
        − 0.7 × IS/OOS performance gap        (overfitting)
        − 0.5 × fold instability
        − 0.3 × low-trade-count penalty
        − 0.2 × excessive-turnover penalty

Evaluated on REAL candles only: in-sample slice for search, held-out
out-of-sample slice to measure the gap. Insufficient data → honest failure.
"""

from __future__ import annotations

import json
import logging
import math
import random
import statistics
import time
from typing import Any

import numpy as np

from zepay.core.ids import new_id
from zepay.core.util import clamp, utcnow_iso

log = logging.getLogger("zepay.ml.hyperopt")

PARAM_SPACE = {
    "entry_threshold": (0.10, 0.60),
    "atr_stop_mult": (1.0, 3.0),
    "atr_tp_mult": (2.0, 6.0),
    "max_bars_held": (5, 30),
    "position_pct": (0.05, 0.30),
}
INT_PARAMS = {"max_bars_held"}


def sample_params(rng: random.Random) -> dict:
    p = {}
    for k, (lo, hi) in PARAM_SPACE.items():
        v = rng.uniform(lo, hi)
        p[k] = round(v) if k in INT_PARAMS else round(v, 3)
    return p


def grid_points(levels: int = 3) -> list[dict]:
    keys = list(PARAM_SPACE)
    vals: list[list[Any]] = []
    for k in keys:
        lo, hi = PARAM_SPACE[k]
        if k in INT_PARAMS:
            vals.append([round(float(x)) for x in np.linspace(lo, hi, levels)])
        else:
            vals.append([round(float(x), 3) for x in np.linspace(lo, hi, levels)])
    out = []

    def rec(i, acc):
        if i == len(keys):
            out.append(dict(acc))
            return
        for v in vals[i]:
            acc[keys[i]] = v
            rec(i + 1, acc)

    rec(0, {})
    return out


# ---------------------------------------------------------------------------
# Compact GP-UCB Bayesian optimizer (RBF kernel, numpy only)
# ---------------------------------------------------------------------------
class MiniGP:
    def __init__(self, length_scale: float = 0.4, noise: float = 1e-3):
        self.ls = length_scale
        self.noise = noise
        self.X: list[list[float]] = []
        self.y: list[float] = []

    def _k(self, A: np.ndarray, B: np.ndarray) -> np.ndarray:
        d2 = ((A[:, None, :] - B[None, :, :]) ** 2).sum(-1)
        return np.exp(-d2 / (2 * self.ls**2))

    def add(self, x: list[float], y: float) -> None:
        self.X.append(x)
        self.y.append(y)

    def predict(self, x: list[float]) -> tuple[float, float]:
        if len(self.X) < 2:
            return (statistics.fmean(self.y) if self.y else 0.0), 1.0
        X = np.asarray(self.X)
        y = np.asarray(self.y)
        mu_y = y.mean()
        yc = y - mu_y
        K = self._k(X, X) + self.noise * np.eye(len(X))
        try:
            Kinv_y = np.linalg.solve(K, yc)
            kx = self._k(np.asarray([x]), X)[0]
            mu = float(kx @ Kinv_y + mu_y)
            v = np.linalg.solve(K, kx)
            var = max(1.0 - float(kx @ v), 1e-6)
            return mu, math.sqrt(var)
        except np.linalg.LinAlgError:
            return mu_y, 1.0


def _encode(p: dict) -> list[float]:
    out = []
    for k, (lo, hi) in PARAM_SPACE.items():
        out.append((p[k] - lo) / (hi - lo))
    return out


# ---------------------------------------------------------------------------
class HyperoptEngine:
    def __init__(self, backtester, collector, config, storage=None, audit_fn=None):
        self.bt = backtester
        self.collector = collector
        self.config = config
        self.db = storage
        self.audit = audit_fn or (lambda *a, **k: None)

    # ---- penalized objective (§25) ----
    @staticmethod
    def objective(res_is: dict, res_oos: dict, n_bars: int) -> tuple[float, dict]:
        if not res_is.get("ok") or not res_oos.get("ok"):
            return -9.99, {"error": "backtest failed on real data"}
        net_is = res_is["net_return_pct"] / 100
        net_oos = res_oos["net_return_pct"] / 100
        dd_is = res_is["max_dd_pct"] / 100
        dd_oos = res_oos["max_dd_pct"] / 100
        trades = res_is["trades"] + res_oos["trades"]
        obj = (
            0.5 * net_is
            + 0.5 * net_oos
            - 1.0 * (0.5 * dd_is + 0.5 * dd_oos)
            - 0.7 * max(0.0, net_is - net_oos)  # overfit gap
            - 0.5 * abs(net_is - net_oos)  # instability
            - 0.3 * max(0.0, (10 - trades) / 10.0)  # too few trades
            - 0.2 * max(0.0, (trades / max(n_bars, 1)) - 0.15)
        )  # churn
        detail = {
            "net_is": round(net_is, 4),
            "net_oos": round(net_oos, 4),
            "dd_is": round(dd_is, 4),
            "dd_oos": round(dd_oos, 4),
            "trades": trades,
            "oos_gap": round(net_is - net_oos, 4),
            "obj": round(obj, 4),
        }
        return round(obj, 4), detail

    def _evaluate(
        self, market: str, rows_is: list, rows_oos: list, params: dict, starting: float
    ) -> tuple[float, dict, dict, dict]:
        res_is = self.bt.run_single(
            market, starting=starting, params=params, persist=False, rows_override=rows_is
        )
        res_oos = self.bt.run_single(
            market, starting=starting, params=params, persist=False, rows_override=rows_oos
        )
        obj, detail = self.objective(res_is, res_oos, len(rows_is) + len(rows_oos))
        return obj, detail, res_is, res_oos

    # ---- driver ----
    def run(
        self,
        market: str,
        method: str = "random",
        n_trials: int = 12,
        starting: float = 10000.0,
        seed: int | None = None,
    ) -> dict:
        exp_id = new_id("hyp")
        t0 = time.time()
        rows = self.bt._series(market, self.config.get("candle_interval", "1h"), 500)
        if len(rows) < 200:
            return {
                "ok": False,
                "id": exp_id,
                "error": f"need ≥200 REAL candles for hyperopt ({market}: {len(rows)})",
            }
        split = int(len(rows) * 0.7)
        rows_is, rows_oos = rows[:split], rows[split - 50 :]
        rng = random.Random(seed)
        trials: list[dict] = []
        gp = MiniGP()
        best: dict | None = None

        def consider(params: dict, tag: str):
            nonlocal best
            obj, detail, _res_is, _res_oos = self._evaluate(
                market, rows_is, rows_oos, params, starting
            )
            entry = {"params": params, "obj": obj, "method": tag, **detail}
            trials.append(entry)
            gp.add(_encode(params), obj)
            if best is None or obj > best["obj"]:
                best = entry

        if method == "grid":
            for p in grid_points(3):
                consider(p, "grid")
        elif method == "random":
            for _ in range(n_trials):
                consider(sample_params(rng), "random")
        elif method == "bayes":
            for _ in range(min(4, n_trials)):  # seed randomly
                consider(sample_params(rng), "random-seed")
            for it in range(max(1, n_trials - 4)):  # GP-UCB acquisition
                cands = [sample_params(rng) for _ in range(24)]
                scored = []
                for c in cands:
                    mu, sd = gp.predict(_encode(c))
                    scored.append((mu + 1.5 * sd, c))
                scored.sort(key=lambda x: -x[0])
                consider(scored[0][1], f"bayes-{it}")
        elif method == "evolutionary":
            pop = [sample_params(rng) for _ in range(6)]
            for p in pop:
                consider(p, "evo-gen0")
            for gen in range(max(1, n_trials // 6)):
                pop.sort(
                    key=lambda p: next(
                        (t["obj"] for t in reversed(trials) if t["params"] == p), -9.99
                    ),
                    reverse=True,
                )
                survivors = pop[:3]
                children: list[dict] = []
                while len(children) < 3:
                    a, b = rng.choice(survivors), rng.choice(survivors)
                    child = {k: (a[k] if rng.random() < 0.5 else b[k]) for k in PARAM_SPACE}
                    if rng.random() < 0.3:  # mutate
                        k = rng.choice(list(PARAM_SPACE))
                        lo, hi = PARAM_SPACE[k]
                        child[k] = (
                            round(clamp(child[k] + rng.gauss(0, (hi - lo) * 0.15), lo, hi))
                            if k in INT_PARAMS
                            else round(clamp(child[k] + rng.gauss(0, (hi - lo) * 0.15), lo, hi), 3)
                        )
                    children.append(child)
                pop = survivors + children
                for p in children:
                    consider(p, f"evo-gen{gen + 1}")
        else:
            return {
                "ok": False,
                "id": exp_id,
                "error": f"unknown method {method} (grid|random|bayes|evolutionary)",
            }

        result = {
            "ok": True,
            "id": exp_id,
            "market": market,
            "method": method,
            "trials": len(trials),
            "best": best,
            "history": trials[-40:],
            "data": {
                "candles": len(rows),
                "is_bars": len(rows_is),
                "oos_bars": len(rows_oos),
                "split": "70/30 chronological",
            },
            "objective_note": "penalized: drawdown, IS/OOS gap, instability, low trade "
            "count, churn — NOT raw profit (§25/§69)",
            "elapsed_s": round(time.time() - t0, 1),
            "created_at": utcnow_iso(),
            "promotion": "Parameters are CANDIDATES — adopt via config change with "
            "human review; never auto-applied.",
        }
        if self.db:
            try:
                self.db.execute(
                    "INSERT INTO experiments (id, kind, name, hypothesis, dataset,"
                    " feature_version, model_version, strategy, params, results, status,"
                    " conclusion, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        exp_id,
                        "hyperopt",
                        f"hyperopt-{method}-{market}",
                        f"penalized objective search ({method})",
                        market,
                        self.bt.ai.feature_version,
                        "",
                        "ensemble-backtest",
                        json.dumps(best["params"] if best else {}),
                        json.dumps({k: v for k, v in result.items() if k not in ("history",)})[
                            :6000
                        ],
                        "complete",
                        f"best obj {best['obj'] if best else 'n/a'}",
                        result["created_at"],
                    ),
                )
            except Exception as e:
                log.warning("hyperopt persist failed: %s", e)
        self.audit(
            "research",
            "hyperopt",
            {
                "id": exp_id,
                "market": market,
                "method": method,
                "best_obj": best["obj"] if best else None,
            },
        )
        return result
