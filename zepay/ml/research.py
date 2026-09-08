"""Continuous research pipeline (§28).

DISCOVER → HYPOTHESIS → DATASET → FEATURE ENGINEERING → TRAIN → BACKTEST →
WALK FORWARD → COST TEST → ROBUSTNESS TEST → COMPARE WITH CHAMPION →
PAPER TEST → REPORT → HUMAN APPROVAL.

Never automatically deploys: the output is a registered CANDIDATE model plus
an evidence report. Promotion goes through the registry's manual
champion/challenger flow (§27).
"""

from __future__ import annotations

import json
import logging
import time

from zepay.ai.features import FEATURE_KEYS_V3
from zepay.ai.models import LogisticModel
from zepay.backtesting.analysis import parameter_robustness
from zepay.core.ids import new_id
from zepay.core.util import safe_float, utcnow_iso

log = logging.getLogger("zepay.ml.research")


class ResearchPipeline:
    def __init__(
        self,
        ai,
        collector,
        universe,
        config,
        registry,
        walk_forward,
        backtester,
        storage=None,
        audit_fn=None,
    ):
        self.ai = ai
        self.collector = collector
        self.universe = universe
        self.config = config
        self.registry = registry
        self.wf = walk_forward
        self.bt = backtester
        self.db = storage
        self.audit = audit_fn or (lambda *a, **k: None)

    # ---- stage 1: discovery (real universe, real liquidity) ----
    def discover(self, limit: int = 8) -> list[str]:
        assets = self.universe.all_assets(limit=60)
        picked = [a["symbol"] for a in assets if safe_float(a.get("volume24h_usd")) > 1_000_000][
            :limit
        ]
        universe = self.config.get("universe") or []
        for m in universe:
            if m not in picked:
                picked.append(m)
        return picked[: limit + len(universe)]

    # ---- full pipeline ----
    def run(self, hypothesis: dict | None = None) -> dict:
        exp_id = new_id("exp")
        t0 = time.time()
        report: dict = {"id": exp_id, "ok": False, "stages": {}, "created_at": utcnow_iso()}
        hyp = hypothesis or {
            "name": "regularization-scan",
            "description": "challenger logistic with stronger L2 and " "longer horizon vs champion",
            "params": {"l2": [0.01, 0.05, 0.2], "horizon": 3},
        }
        report["hypothesis"] = hyp
        markets = self.discover()
        report["stages"]["discover"] = {"markets": markets, "ok": bool(markets)}
        if not markets:
            report["conclusion"] = "no markets discovered — real universe empty/unreachable"
            self._persist(report)
            return report

        # ---- dataset + feature engineering (shared, versioned) ----
        horizon = int(hyp["params"].get("horizon", 3))
        X, ys = self.ai.build_dataset(self.collector, markets, horizon)
        n = len(X)
        report["stages"]["dataset"] = {
            "samples": n,
            "feature_version": self.ai.feature_version,
            "features": len(FEATURE_KEYS_V3),
            "ok": n >= 120,
        }
        if n < 120:
            report["conclusion"] = f"insufficient REAL samples ({n}) — no training attempted"
            self._persist(report)
            return report

        # ---- train challengers ----
        cut = int(n * 0.8)
        Xtr, Xva = X[:cut], X[cut:]
        ytr, yva = ys["dir"][:cut], ys["dir"][cut:]
        import numpy as np

        variants = []
        for l2 in hyp["params"].get("l2", [0.01]):
            m = LogisticModel(len(FEATURE_KEYS_V3), lr=0.9, l2=float(l2)).fit(Xtr, ytr, epochs=300)
            p = m.predict_proba_batch(Xva)
            acc = float(np.mean((p >= 0.5).astype(int) == np.asarray(yva)))
            brier = float(np.mean((p - np.asarray(yva)) ** 2))
            variants.append(
                {"l2": l2, "oos_accuracy": round(acc, 4), "oos_brier": round(brier, 4), "model": m}
            )
        best_variant = max(variants, key=lambda v: v["oos_accuracy"] - v["oos_brier"])
        report["stages"]["train"] = {
            "variants": [{k: v for k, v in vv.items() if k != "model"} for vv in variants],
            "best": {k: v for k, v in best_variant.items() if k != "model"},
            "ok": True,
        }

        # ---- walk-forward on the same real data ----
        wf = self.wf.run(self.collector, markets, n_folds=4, horizon=horizon)
        report["stages"]["walk_forward"] = {
            "ok": wf.get("ok", False),
            "oos_accuracy_mean": wf.get("oos_accuracy_mean"),
            "stability": wf.get("stability"),
            "conclusion": wf.get("conclusion", wf.get("error", "")),
        }

        # ---- cost test: backtest under stressed costs (real candles) ----
        base_market = markets[0]
        cost_test = {}
        for mult, label in ((1.0, "base"), (1.5, "stress-1.5x"), (2.0, "stress-2x")):
            res = self.bt.run_single(
                base_market,
                persist=False,
                fee_bps=safe_float(self.config.get("fee_bps", 10)) * mult,
                slip_bps=safe_float(self.config.get("slippage_bps", 5)) * mult,
            )
            cost_test[label] = {
                "ok": res.get("ok"),
                "net_return_pct": res.get("net_return_pct"),
                "max_dd_pct": res.get("max_dd_pct"),
                "trades": res.get("trades"),
            }
        report["stages"]["cost_test"] = {
            "market": base_market,
            "results": cost_test,
            "ok": cost_test["base"].get("ok", False),
        }

        # ---- robustness: parameter neighborhood ----
        neigh = {}
        for thr in (0.20, 0.25, 0.30, 0.35):
            res = self.bt.run_single(base_market, persist=False, params={"entry_threshold": thr})
            neigh[f"entry_threshold={thr}"] = res
        robust = parameter_robustness(neigh)
        report["stages"]["robustness"] = robust

        # ---- compare with champion (§27) ----
        champ = self.registry.champion("quant-ensemble")
        champ_acc = safe_float((self.ai.metrics or {}).get("dir_accuracy_oos"))
        challenger_acc = best_variant["oos_accuracy"]
        comparison = {
            "champion": champ["id"] if champ else None,
            "champion_oos_accuracy": champ_acc,
            "challenger_oos_accuracy": challenger_acc,
            "delta": round(challenger_acc - champ_acc, 4) if champ_acc else None,
            "recommend_promotion": bool(
                champ_acc
                and challenger_acc > champ_acc + 0.02
                and safe_float(wf.get("stability"), 0) > 0.5
            ),
        }
        report["stages"]["compare_with_champion"] = comparison

        # ---- register challenger as CANDIDATE (never auto-promote) ----
        model_id = self.registry.register(
            name="research-challenger",
            kind="quant-ensemble",
            version=f"research-{exp_id[-8:]}",
            features=FEATURE_KEYS_V3,
            dataset=",".join(markets),
            metrics={
                "dir_accuracy_oos": challenger_acc,
                "dir_brier_oos": best_variant["oos_brier"],
                "l2": best_variant["l2"],
                "horizon": horizon,
                "samples": n,
            },
            params_blob=json.dumps(best_variant["model"].to_dict()),
            samples=n,
            lifecycle="CANDIDATE",
        )
        report["stages"]["register"] = {"model_id": model_id, "lifecycle": "CANDIDATE", "ok": True}
        report["model_id"] = model_id
        report["ok"] = True
        report["conclusion"] = (
            f"challenger OOS acc {challenger_acc:.4f} vs champion {champ_acc or 'n/a'}; "
            f"WF stability {wf.get('stability')}; robustness {robust.get('verdict', 'n/a')}; "
            f"promotion recommended: {comparison['recommend_promotion']} — "
            "HUMAN APPROVAL REQUIRED (§27/§28)"
        )
        report["elapsed_s"] = round(time.time() - t0, 1)
        report["next_step"] = (
            "PAPER TEST the candidate via registry transition "
            "CANDIDATE→PAPER, then manual champion promotion."
        )
        self._persist(report)
        self.audit(
            "research", "pipeline_run", {"id": exp_id, "conclusion": report["conclusion"][:300]}
        )
        return report

    def _persist(self, report: dict) -> None:
        if not self.db:
            return
        try:
            stages = {
                k: {kk: vv for kk, vv in v.items() if kk != "model"}
                for k, v in report.get("stages", {}).items()
                if isinstance(v, dict)
            }
            self.db.execute(
                "INSERT INTO experiments (id, kind, name, hypothesis, dataset, results,"
                " status, conclusion, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    report["id"],
                    "research_pipeline",
                    (report.get("hypothesis") or {}).get("name", "research"),
                    json.dumps(report.get("hypothesis") or {}),
                    ",".join((report.get("stages", {}).get("discover") or {}).get("markets", [])),
                    json.dumps({**report, "stages": stages}, default=str)[:7000],
                    "complete" if report.get("ok") else "failed",
                    str(report.get("conclusion", ""))[:1000],
                    report.get("created_at", ""),
                ),
            )
            self.db.execute(
                "INSERT INTO research_reports (id, ts, cycle_id, kind, agents, summary,"
                " created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    new_id("rep"),
                    utcnow_iso(),
                    "",
                    "auto-research",
                    "{}",
                    json.dumps(report, default=str)[:6000],
                    utcnow_iso(),
                ),
            )
        except Exception as e:
            log.warning("research persist failed: %s", e)

    def history(self, limit: int = 20) -> list[dict]:
        if not self.db:
            return []
        rows = self.db.query(
            "SELECT id, kind, name, status, conclusion, created_at FROM experiments"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return rows
