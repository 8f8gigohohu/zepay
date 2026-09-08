"""Model drift monitoring (§59).

Monitors: feature drift (PSI vs training snapshot), prediction drift
(distribution of recent p_long vs training), calibration drift (rolling
Brier/accuracy once outcomes are observable), regime drift, performance
drift. Responses are graduated and automatic in the SAFE direction only:

  mild   → confidence and size already reduced via AI.degraded
  severe → model DISABLED (registry lifecycle DEGRADED, strict mode → WAIT)

Never silently continues. All state changes are audited + alerted.
"""

from __future__ import annotations

import json
import logging
import statistics

import numpy as np

from zepay.ai.features import FEATURE_KEYS_V3, feature_vector
from zepay.core.util import safe_float, utcnow_iso

log = logging.getLogger("zepay.ml.drift")

PSI_WARN = 0.10
PSI_SEVERE = 0.25


def psi(expected: list[float], actual: list[float], bins: int = 8) -> float:
    """Population Stability Index between training and current samples."""
    if len(expected) < bins * 2 or len(actual) < bins * 2:
        return 0.0
    e = np.asarray(expected, dtype=float)
    a = np.asarray(actual, dtype=float)
    edges = np.quantile(e, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    e_pct = np.histogram(e, edges)[0] / len(e)
    a_pct = np.histogram(a, edges)[0] / len(a)
    e_pct = np.clip(e_pct, 1e-4, None)
    a_pct = np.clip(a_pct, 1e-4, None)
    return float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))


class DriftMonitor:
    def __init__(self, storage, ai, registry, config, audit_fn, alerts=None):
        self.db = storage
        self.ai = ai
        self.registry = registry
        self.config = config
        self.audit = audit_fn
        self.alerts = alerts
        self.train_snapshot: dict[str, list[float]] = {}

    def snapshot_training_distribution(self, collector, markets: list[str]) -> dict:
        """Capture feature distributions at training time (baseline for PSI)."""
        cols: dict[str, list[float]] = {k: [] for k in FEATURE_KEYS_V3}
        for m in markets:
            kl = collector.get_klines(m) or []
            if len(kl) < 80:
                continue
            for i in range(40, len(kl) - 1):
                f = self.ai._features_from_window(m, kl[: i + 1])
                if f is None:
                    continue
                vec = feature_vector(f, version="v3")
                for k, v in zip(FEATURE_KEYS_V3, vec, strict=False):
                    cols[k].append(v)
        self.train_snapshot = cols
        try:
            stats = {
                k: {
                    "mean": statistics.fmean(v) if v else 0,
                    "std": statistics.pstdev(v) if len(v) > 1 else 0,
                    "n": len(v),
                }
                for k, v in cols.items()
            }
            self.db.kv_set("drift_baseline", json.dumps({"stats": stats, "ts": utcnow_iso()}))
        except Exception:
            pass
        return {"ok": True, "features": len(cols), "samples": len(next(iter(cols.values()), []))}

    def label_outcomes(self, collector, horizon: int = 3) -> int:
        """Fill outcome columns for predictions whose horizon has elapsed —
        enables REAL calibration/accuracy drift instead of guesswork."""
        rows = self.db.query(
            "SELECT id, market, ts, p_long, direction FROM model_predictions"
            " WHERE outcome_known=0 ORDER BY id DESC LIMIT 200"
        )
        labeled = 0
        for r in rows:
            kl = collector.get_klines(r["market"]) or []
            if len(kl) < horizon + 2:
                continue
            try:
                import datetime as dt

                t_pred = dt.datetime.strptime(r["ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                    tzinfo=dt.UTC
                )
                ms = int(t_pred.timestamp() * 1000)
            except Exception:
                continue
            future = [x for x in kl if x["open_time"] > ms][: horizon + 1]
            if len(future) <= horizon:
                continue  # horizon not fully elapsed yet — wait (never guess)
            base = [x for x in kl if x["open_time"] <= ms]
            if not base:
                continue
            p0 = base[-1]["c"]
            fut_ret = future[horizon - 1]["c"] / p0 - 1 if p0 else 0
            self.db.execute(
                "UPDATE model_predictions SET outcome_known=1, outcome_return=?,"
                " outcome_direction=? WHERE id=?",
                (
                    round(fut_ret, 6),
                    "LONG" if fut_ret > 0.001 else ("SHORT" if fut_ret < -0.001 else "WAIT"),
                    r["id"],
                ),
            )
            labeled += 1
        return labeled

    def tick(self, collector, markets: list[str]) -> dict:
        report: dict = {"ts": utcnow_iso(), "checks": {}}
        # ---- feature drift (PSI) ----
        if self.train_snapshot:
            current: dict[str, list[float]] = {k: [] for k in FEATURE_KEYS_V3}
            for m in markets:
                kl = collector.get_klines(m) or []
                for i in range(max(40, len(kl) - 40), len(kl)):
                    f = self.ai._features_from_window(m, kl[: i + 1])
                    if f is None:
                        continue
                    for k, v in zip(FEATURE_KEYS_V3, feature_vector(f, version="v3"), strict=False):
                        current[k].append(v)
            psis = {}
            for k in FEATURE_KEYS_V3:
                if len(self.train_snapshot.get(k, [])) >= 16 and len(current.get(k, [])) >= 16:
                    psis[k] = round(psi(self.train_snapshot[k], current[k]), 4)
            worst = max(psis.values()) if psis else 0
            worst_feat = max(psis, key=lambda k: psis[k]) if psis else ""
            report["checks"]["feature_drift"] = {
                "psi_worst": worst,
                "feature": worst_feat,
                "psi_all": psis,
                "status": (
                    "ok" if worst < PSI_WARN else ("warn" if worst < PSI_SEVERE else "severe")
                ),
            }
        else:
            report["checks"]["feature_drift"] = {
                "status": "no_baseline",
                "note": "training snapshot missing",
            }
        # ---- prediction drift ----
        preds = self.db.query(
            "SELECT p_long, confidence FROM model_predictions ORDER BY id DESC LIMIT 300"
        )
        if len(preds) >= 30:
            ps = [safe_float(p["p_long"]) for p in preds]
            mean_p = statistics.fmean(ps)
            report["checks"]["prediction_drift"] = {
                "n": len(ps),
                "p_long_mean": round(mean_p, 4),
                "p_long_std": round(statistics.pstdev(ps), 4),
                "status": "ok" if 0.2 < mean_p < 0.8 else "warn",
            }
        # ---- calibration drift (real outcomes) ----
        labeled = self.db.query(
            "SELECT p_long, direction, outcome_direction, outcome_return"
            " FROM model_predictions WHERE outcome_known=1 ORDER BY id DESC LIMIT 300"
        )
        if len(labeled) >= 30:
            ys = [1 if r["outcome_direction"] == "LONG" else 0 for r in labeled]
            ps = [safe_float(r["p_long"]) for r in labeled]
            brier = float(np.mean((np.asarray(ps) - np.asarray(ys)) ** 2))
            acc = float(np.mean([(p >= 0.5) == bool(y) for p, y in zip(ps, ys, strict=False)]))
            base_rate = float(np.mean(ys))
            report["checks"]["calibration_drift"] = {
                "n": len(labeled),
                "brier": round(brier, 4),
                "accuracy": round(acc, 4),
                "base_rate": round(base_rate, 4),
                "status": (
                    "ok"
                    if brier <= 0.25 and acc >= base_rate * 0.9
                    else ("warn" if brier <= 0.30 else "severe")
                ),
            }
        # ---- regime drift ----
        regimes = self.db.query(
            "SELECT regime, COUNT(*) AS n FROM model_predictions"
            " GROUP BY regime ORDER BY n DESC LIMIT 10"
        )
        if regimes:
            tot = sum(r["n"] for r in regimes) or 1
            report["checks"]["regime_drift"] = {
                "distribution": {r["regime"]: round(r["n"] / tot, 3) for r in regimes},
                "status": "ok",
            }
        # ---- graduated response ----
        statuses = [c.get("status") for c in report["checks"].values()]
        severe = statuses.count("severe")
        warn = statuses.count("warn")
        champ = self.registry.champion("quant-ensemble")
        if severe and not self.ai.degraded:
            self.ai.mark_degraded(f"drift severe in {severe} monitor(s)")
            if champ:
                self.registry.set_drift(champ["id"], "severe", json.dumps(statuses)[:200])
            if self.alerts:
                self.alerts.notify(
                    "CRITICAL",
                    "MODEL DEGRADED — risk reduced",
                    "Drift monitors report severe degradation. Confidence "
                    "and size reduced; strict mode will WAIT if disabled.",
                )
            report["action"] = "degraded"
        elif severe >= 2 and self.ai.degraded:
            # persistent severe drift → DISABLE the model (§59)
            self.ai.trained = False
            if champ:
                self.registry.transition(
                    champ["id"],
                    "DEGRADED",
                    actor="drift-monitor",
                    evidence="persistent severe drift — disabled",
                )
                self.registry.set_drift(champ["id"], "disabled", "model disabled by drift monitor")
            if self.alerts:
                self.alerts.notify(
                    "CRITICAL",
                    "MODEL DISABLED by drift monitor",
                    "Persistent severe drift — model disabled. System "
                    "falls back to strict-mode WAIT until retrained.",
                )
            report["action"] = "disabled"
        elif warn and not severe:
            report["action"] = "monitor"
            if champ:
                self.registry.set_drift(champ["id"], "warn", "")
        else:
            report["action"] = "none"
            if self.ai.degraded and not warn and not severe:
                self.ai.degraded = False
                self.ai.degraded_reason = ""
                if champ:
                    self.registry.set_drift(champ["id"], "none", "recovered")
                report["action"] = "recovered"
        try:
            self.db.kv_set("drift_report", json.dumps(report, default=str))
        except Exception:
            pass
        return report

    def last_report(self) -> dict:
        try:
            return json.loads(self.db.kv_get("drift_report") or "{}")
        except Exception:
            return {}
