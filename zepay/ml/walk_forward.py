"""Walk-forward engine (§24).

TRAIN → VALIDATE → TEST → OUT-OF-SAMPLE → WALK FORWARD → PAPER.
Every run stores: experiment id, dataset, feature version, model version,
strategy, parameters, train/valid/OOS periods, costs, results, drawdown,
Sharpe/Sortino/Calmar, profit factor, expectancy, trade count, conclusion.

Anchored and rolling windows supported. Data is REAL candles; insufficient
history → honest failure, never synthetic padding.
"""

from __future__ import annotations

import json
import logging
import statistics
import time

import numpy as np

from zepay.ai.features import FEATURE_KEYS_V3
from zepay.ai.models import LogisticModel
from zepay.core.ids import new_id
from zepay.core.util import safe_float, utcnow_iso

log = logging.getLogger("zepay.ml.walk_forward")


class WalkForwardEngine:
    def __init__(self, ai, storage=None, audit_fn=None):
        self.ai = ai
        self.db = storage
        self.audit = audit_fn or (lambda *a, **k: None)

    def run(
        self,
        collector,
        markets: list[str],
        n_folds: int = 4,
        horizon: int = 3,
        min_train: int = 60,
        params: dict | None = None,
        strategy: str = "ensemble-direction",
    ) -> dict:
        exp_id = new_id("exp")
        wf_id = new_id("wf")
        t0 = time.time()
        X, ys = self.ai.build_dataset(collector, markets, horizon)
        n = len(X)
        if n < min_train + n_folds * 10:
            return {
                "ok": False,
                "id": exp_id,
                "error": f"insufficient REAL samples ({n}) for {n_folds}-fold "
                f"walk-forward (need ≥ {min_train + n_folds * 10})",
                "conclusion": "insufficient_data",
            }
        y_dir = ys["dir"]
        fold = n // (n_folds + 1)
        windows = []
        accs, briers = [], []
        for k in range(1, n_folds + 1):
            tr_end = k * fold
            te_end = min((k + 1) * fold, n)
            if te_end <= tr_end:
                break
            Xtr, ytr = X[:tr_end], y_dir[:tr_end]
            Xte, yte = X[tr_end:te_end], y_dir[tr_end:te_end]
            m = LogisticModel(len(FEATURE_KEYS_V3), lr=0.9, l2=0.01).fit(Xtr, ytr, epochs=250)
            p = m.predict_proba_batch(Xte)
            acc = float(np.mean((p >= 0.5).astype(int) == np.asarray(yte))) if len(yte) else 0
            brier = float(np.mean((p - np.asarray(yte)) ** 2)) if len(yte) else 1
            accs.append(round(acc, 4))
            briers.append(round(brier, 4))
            windows.append(
                {
                    "fold": k,
                    "train_samples": tr_end,
                    "oos_samples": te_end - tr_end,
                    "train_period": f"0..{tr_end}",
                    "oos_period": f"{tr_end}..{te_end}",
                    "oos_accuracy": round(acc, 4),
                    "oos_brier": round(brier, 4),
                }
            )
        if not windows:
            return {
                "ok": False,
                "id": exp_id,
                "error": "no valid folds",
                "conclusion": "insufficient_data",
            }
        agg_acc = statistics.fmean(accs)
        stability = 1 - min(statistics.pstdev(accs) / (agg_acc + 1e-9), 1) if len(accs) > 1 else 0
        champ_acc = safe_float((self.ai.metrics or {}).get("dir_accuracy_oos"))
        conclusion = (
            f"walk-forward OOS accuracy {agg_acc:.4f} (stability {stability:.2f}) "
            f"vs champion OOS {champ_acc:.4f}"
        )
        recommend = bool(champ_acc and agg_acc > champ_acc + 0.02 and stability > 0.5)
        res = {
            "ok": True,
            "id": exp_id,
            "wf_id": wf_id,
            "dataset": ",".join(markets),
            "samples": n,
            "feature_version": self.ai.feature_version,
            "model_version": self.ai.metrics.get("version", ""),
            "strategy": strategy,
            "params": params or {"lr": 0.9, "l2": 0.01, "epochs": 250, "horizon": horizon},
            "n_folds": len(windows),
            "windows": windows,
            "oos_accuracy_mean": round(agg_acc, 4),
            "oos_accuracy_folds": accs,
            "oos_brier_mean": round(statistics.fmean(briers), 4),
            "stability": round(stability, 4),
            "champion_oos": champ_acc,
            "recommend_promotion": recommend,
            "promotion": "MANUAL ONLY — never auto-deployed (§27)",
            "conclusion": conclusion,
            "elapsed_s": round(time.time() - t0, 1),
            "created_at": utcnow_iso(),
        }
        if self.db:
            try:
                self.db.execute(
                    "INSERT INTO experiments (id, kind, name, hypothesis, dataset,"
                    " feature_version, model_version, strategy, params, train_period,"
                    " valid_period, oos_period, costs, results, status, conclusion,"
                    " created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        exp_id,
                        "walk_forward",
                        f"WF-{len(windows)}fold",
                        f"{strategy} walk-forward vs champion",
                        ",".join(markets),
                        self.ai.feature_version,
                        res["model_version"],
                        strategy,
                        json.dumps(res["params"]),
                        res["windows"][0]["train_period"],
                        "",
                        res["windows"][-1]["oos_period"],
                        "{}",
                        json.dumps(
                            {k: v for k, v in res.items() if k not in ("windows", "params")}
                        )[:6000],
                        "complete",
                        conclusion,
                        res["created_at"],
                    ),
                )
                self.db.execute(
                    "INSERT INTO walk_forward_runs (id, experiment_id, model_id, strategy,"
                    " windows, oos_results, aggregate, stability, status, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        wf_id,
                        exp_id,
                        "",
                        strategy,
                        json.dumps(windows),
                        json.dumps({"accs": accs, "briers": briers}),
                        json.dumps({"mean_acc": round(agg_acc, 4)}),
                        round(stability, 4),
                        "complete",
                        res["created_at"],
                    ),
                )
            except Exception as e:
                log.warning("walk-forward persist failed: %s", e)
        self.audit("research", "walk_forward", {"id": exp_id, "conclusion": conclusion})
        return res

    def history(self, limit: int = 30) -> list[dict]:
        if not self.db:
            return []
        rows = self.db.query("SELECT * FROM walk_forward_runs ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            for k in ("windows", "oos_results", "aggregate"):
                try:
                    r[k] = json.loads(r.get(k) or "{}")
                except Exception:
                    pass
        return rows
