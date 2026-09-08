"""AI engine V3 (§15-16): real quant models + calibrated ensemble.

Members (each produces direction probability / confidence / expected return /
expected volatility / trade quality / regime compatibility):

  1. direction   — logistic model trained on real candles (v2 lineage)
  2. return      — ridge model → expected return over horizon
  3. volatility  — ridge model → expected realized vol over horizon
  4. quality     — logistic model → forward-path trade quality (MFE vs MAE)
  5. mae / mfe   — ridge models → expected adverse/favorable excursion
  6. strategies  — orchestrator aggregate mapped to probability space
  7. momentum    — normalized 10/20-bar momentum member
  8. meanrev     — %B/RSI contrarian member
  9. crossasset  — BTC momentum + relative strength member

Fusion:  ENSEMBLE → CALIBRATION (Platt, fitted on OOS) → CONFIDENCE
         → EXPECTED NET EDGE (opportunity layer) → RISK ADJUSTMENT (risk layer)
Weights: equal | weighted | regime_weighted (config `ensemble_mode`).
Disagreement between members raises uncertainty and lowers confidence —
simple averaging alone is never the whole story (§16).

STRICT MODE (default): untrained/degraded models → WAIT with an honest mode
label. The engine NEVER fabricates a prediction and presents it as model
output. The regime model is the rule-based RegimeEngine (documented as such —
it IS a model of observed features, not a placeholder).
"""

from __future__ import annotations

import logging
import statistics
import threading

import numpy as np

from zepay.ai.calibration import PlattCalibrator, brier_score, calibration_error
from zepay.ai.features import FEATURE_KEYS_V3, feature_vector
from zepay.ai.models import LogisticModel, RidgeModel, feature_importance, model_from_dict
from zepay.core.events import BUS, E
from zepay.core.util import clamp, safe_float, utcnow_iso
from zepay.features.engine import AssetFeatures
from zepay.features.indicators import (
    atr,
    bollinger,
    ema,
    macd,
    market_structure,
    rsi,
    sma,
    supertrend,
)

log = logging.getLogger("zepay.ai.engine")

MODEL_VERSION = "quant-ensemble-3.0.0"
MIN_TRAIN_SAMPLES = 60

# Regime-dependent member weights (§16/§29). Rows sum-normalize at fusion time.
REGIME_MODEL_WEIGHTS = {
    #                dir  strat mom  meanrev cross
    "BULLISH_TREND": [1.0, 0.8, 0.9, 0.2, 0.6],
    "BEARISH_TREND": [1.0, 0.8, 0.9, 0.2, 0.6],
    "SIDEWAYS": [0.7, 0.6, 0.4, 1.0, 0.5],
    "MEAN_REVERSION": [0.6, 0.6, 0.3, 1.0, 0.4],
    "HIGH_VOL": [0.8, 0.7, 0.6, 0.5, 0.6],
    "LOW_VOL": [0.7, 0.6, 0.5, 0.7, 0.5],
    "COMPRESSION": [0.6, 0.7, 0.5, 0.6, 0.5],
    "BREAKOUT": [0.9, 0.9, 1.0, 0.2, 0.6],
    "RECOVERY": [0.8, 0.8, 0.8, 0.4, 0.7],
    "PANIC": [0.4, 0.3, 0.3, 0.3, 0.5],
    "UNSTABLE": [0.3, 0.3, 0.3, 0.3, 0.3],
}
DEFAULT_WEIGHTS = [0.8, 0.6, 0.6, 0.5, 0.5]
EQUAL_WEIGHTS = [1.0, 1.0, 1.0, 1.0, 1.0]


class AIEngine:
    def __init__(self, config, storage=None, audit_fn=None):
        self.config = config
        self.db = storage
        self.audit = audit_fn or (lambda *a, **k: None)
        n = len(FEATURE_KEYS_V3)
        self.dir_model = LogisticModel(n)
        self.ret_model = RidgeModel(n)
        self.vol_model = RidgeModel(n)
        self.qual_model = LogisticModel(n)
        self.mae_model = RidgeModel(n)
        self.mfe_model = RidgeModel(n)
        self.calibrator = PlattCalibrator()
        self.trained = False
        self.degraded = False
        self.degraded_reason = ""
        self.train_samples = 0
        self.metrics: dict = {}
        self.feature_version = "features-3.0.0"
        self.last_inference: str | None = None
        self.inference_count = 0
        self._recent_pred: list[tuple[float, int]] = []  # (p, label) ring for drift
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Dataset construction from REAL candles (no lookahead: label windows
    # use only bars strictly after the feature window)
    # ------------------------------------------------------------------
    def build_dataset(self, collector, markets: list[str], horizon: int = 3):
        X, y_dir, y_ret, y_vol, y_qual, y_mae, y_mfe = [], [], [], [], [], [], []
        for m in markets:
            kl = collector.get_klines(m) or []
            if len(kl) < 80:
                continue
            closes = [r["c"] for r in kl]
            for i in range(40, len(closes) - horizon - 1):
                window = kl[: i + 1]
                f = self._features_from_window(m, window)
                if f is None:
                    continue
                fut = closes[i + horizon] / closes[i] - 1 if closes[i] else 0
                fwd_rets = [
                    (closes[j] / closes[j - 1] - 1)
                    for j in range(i + 1, min(i + horizon + 1, len(closes)))
                    if closes[j - 1]
                ]
                fwd_vol = statistics.pstdev(fwd_rets) if len(fwd_rets) > 1 else 0.0
                fwd_highs = [r["h"] for r in kl[i + 1 : i + 1 + horizon]]
                fwd_lows = [r["l"] for r in kl[i + 1 : i + 1 + horizon]]
                mfe = (max(fwd_highs) / closes[i] - 1) if fwd_highs and closes[i] else 0
                mae = (min(fwd_lows) / closes[i] - 1) if fwd_lows and closes[i] else 0
                X.append(feature_vector(f, version="v3"))
                y_dir.append(1 if fut > 0.001 else 0)
                y_ret.append(clamp(fut, -0.1, 0.1))
                y_vol.append(clamp(fwd_vol, 0, 0.2))
                y_qual.append(1 if mfe > abs(mae) and mfe > 0.0015 else 0)
                y_mae.append(clamp(mae, -0.1, 0))
                y_mfe.append(clamp(mfe, 0, 0.1))
        return X, {
            "dir": y_dir,
            "ret": y_ret,
            "vol": y_vol,
            "qual": y_qual,
            "mae": y_mae,
            "mfe": y_mfe,
        }

    @staticmethod
    def _features_from_window(market: str, window: list[dict]) -> AssetFeatures | None:
        """Features from a candle window ONLY (used by training and the
        backtester — the look-ahead guard lives in the caller's slicing)."""
        try:
            closes = [r["c"] for r in window]
            highs = [r["h"] for r in window]
            lows = [r["l"] for r in window]
            vols = [r["v"] for r in window]
            f = AssetFeatures(market=market)
            f.price = closes[-1]
            f.n_candles = len(closes)
            s20 = sma(closes, 20)
            s50 = sma(closes, 50)
            e12 = ema(closes, 12)
            e26 = ema(closes, 26)
            f.sma20 = s20[-1]
            f.sma50 = s50[-1]
            f.ema12 = e12[-1]
            f.ema26 = e26[-1]
            f.ema9 = ema(closes, 9)[-1]
            f.ema21 = ema(closes, 21)[-1]
            f.ema50 = ema(closes, 50)[-1]
            f.rsi14 = rsi(closes)
            f.macd_hist = macd(closes)["hist"]
            bb = bollinger(closes)
            f.bb_pctb = bb["pctb"]
            f.bb_width = bb["width"]
            a = atr(highs, lows, closes)
            f.atr14 = a
            f.atr_pct = (a / closes[-1]) if closes[-1] else 0
            f.mom_10 = (closes[-1] / closes[-11] - 1) if len(closes) > 11 and closes[-11] else 0
            f.mom_20 = (closes[-1] / closes[-21] - 1) if len(closes) > 21 and closes[-21] else 0
            if len(vols) >= 20:
                base = statistics.fmean(vols[-20:-1])
                f.vol_ratio = (vols[-1] / base) if base else 1.0
            st = supertrend(highs, lows, closes)
            f.supertrend_dir = st["direction"]
            f.structure = market_structure(highs, lows, closes)["structure"]
            hw = max(highs[-20:]) if len(highs) >= 20 else max(highs)
            lw = min(lows[-20:]) if len(lows) >= 20 else min(lows)
            f.high_20 = hw
            f.low_20 = lw
            f.breakout_up = ((closes[-1] - hw) / closes[-1]) if closes[-1] else 0
            f.breakout_dn = ((closes[-1] - lw) / closes[-1]) if closes[-1] else 0
            f.data_quality = "OK"
            return f
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Training (real data only; chronological split; calibration on OOS)
    # ------------------------------------------------------------------
    def train(self, collector, markets: list[str], horizon: int = 3) -> dict:
        with self._lock:
            X, ys = self.build_dataset(collector, markets, horizon)
            n = len(X)
            if n < MIN_TRAIN_SAMPLES:
                self.metrics = {"status": "insufficient_data", "samples": n}
                self.trained = False
                self._register("untrained")
                return self.metrics
            cut = int(n * 0.8)  # chronological — no leakage
            Xtr, Xva = X[:cut], X[cut:]
            n_feat = len(FEATURE_KEYS_V3)
            self.dir_model = LogisticModel(n_feat).fit(Xtr, ys["dir"][:cut], epochs=300)
            self.ret_model = RidgeModel(n_feat).fit(Xtr, ys["ret"][:cut])
            self.vol_model = RidgeModel(n_feat).fit(Xtr, ys["vol"][:cut])
            self.qual_model = LogisticModel(n_feat).fit(Xtr, ys["qual"][:cut], epochs=300)
            self.mae_model = RidgeModel(n_feat).fit(Xtr, ys["mae"][:cut])
            self.mfe_model = RidgeModel(n_feat).fit(Xtr, ys["mfe"][:cut])
            # calibration fitted on the OOS split (never on training data)
            p_va = self.dir_model.predict_proba_batch(Xva)
            self.calibrator = PlattCalibrator().fit(p_va, ys["dir"][cut:])
            p_cal = np.array([self.calibrator.calibrate(p) for p in p_va])
            yte = ys["dir"][cut:]
            acc = float(np.mean((p_va >= 0.5).astype(int) == np.asarray(yte))) if len(yte) else 0
            preds = self.ret_model.predict_batch(Xva)
            mae_ret = float(np.mean(np.abs(preds - np.asarray(ys["ret"][cut:])))) if len(Xva) else 0
            self.train_samples = n
            self.trained = True
            self.degraded = False
            self.degraded_reason = ""
            self.metrics = {
                "status": "trained",
                "samples": n,
                "horizon": horizon,
                "dir_accuracy_oos": round(acc, 4),
                "dir_brier_oos": round(brier_score(p_va, yte), 4),
                "dir_brier_calibrated_oos": round(brier_score(p_cal, yte), 4),
                "dir_ece_oos": calibration_error(p_cal, yte),
                "return_mae_oos": round(mae_ret, 5),
                "feature_version": self.feature_version,
                "features": FEATURE_KEYS_V3,
                "version": MODEL_VERSION,
                "trained_at": utcnow_iso(),
                "split": {"train": cut, "oos": n - cut},
                "calibration": self.calibrator.to_dict(),
            }
            self._register("active")
            BUS.publish(
                E.MODEL_UPDATED,
                {"model": "ensemble", "version": MODEL_VERSION, "samples": n},
                source="ai",
            )
            self.audit("ml", "model_trained", self.metrics)
            return self.metrics

    def _register(self, status: str) -> None:
        if self.db is None:
            return
        try:
            import json

            blob = json.dumps(self.export_models())
            self.db.execute(
                "INSERT INTO models (id, name, version, kind, lifecycle, status, features,"
                " metrics, calibration, params_blob, samples, trained_at, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"mdl-ensemble-{utcnow_iso()[:19]}",
                    "ensemble",
                    MODEL_VERSION,
                    "quant-ensemble",
                    "PRODUCTION" if status == "active" else "RESEARCH",
                    status,
                    json.dumps(FEATURE_KEYS_V3),
                    json.dumps(self.metrics),
                    json.dumps(self.calibrator.to_dict()),
                    blob,
                    self.train_samples,
                    self.metrics.get("trained_at", ""),
                    utcnow_iso(),
                    utcnow_iso(),
                ),
            )
        except Exception as e:
            log.warning("model registration failed: %s", e)

    # ------------------------------------------------------------------
    # Persistence (model registry reload)
    # ------------------------------------------------------------------
    def export_models(self) -> dict:
        return {
            "dir": self.dir_model.to_dict(),
            "ret": self.ret_model.to_dict(),
            "vol": self.vol_model.to_dict(),
            "qual": self.qual_model.to_dict(),
            "mae": self.mae_model.to_dict(),
            "mfe": self.mfe_model.to_dict(),
            "calibrator": self.calibrator.to_dict(),
            "feature_version": self.feature_version,
            "model_version": MODEL_VERSION,
        }

    def import_models(self, blob: dict) -> bool:
        try:
            with self._lock:
                self.dir_model = model_from_dict(blob["dir"])
                self.ret_model = model_from_dict(blob["ret"])
                self.vol_model = model_from_dict(blob["vol"])
                self.qual_model = model_from_dict(blob["qual"])
                self.mae_model = model_from_dict(blob["mae"])
                self.mfe_model = model_from_dict(blob["mfe"])
                self.calibrator = PlattCalibrator.from_dict(blob["calibrator"])
                self.trained = all(
                    m.fitted
                    for m in (self.dir_model, self.ret_model, self.vol_model, self.qual_model)
                )
            return self.trained
        except Exception as e:
            log.error("model import failed: %s", e)
            return False

    def importances(self) -> dict:
        return {
            "direction": feature_importance(self.dir_model, FEATURE_KEYS_V3)[:8],
            "return": feature_importance(self.ret_model, FEATURE_KEYS_V3)[:8],
            "quality": feature_importance(self.qual_model, FEATURE_KEYS_V3)[:8],
        }

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------
    def healthy(self) -> bool:
        return self.trained and not self.degraded

    def mark_degraded(self, reason: str) -> None:
        with self._lock:
            self.degraded = True
            self.degraded_reason = reason
        BUS.publish(E.MODEL_DEGRADED, {"model": "ensemble", "reason": reason}, source="ai")
        self.audit("ml", "model_degraded", {"reason": reason})

    # ------------------------------------------------------------------
    # Ensemble members
    # ------------------------------------------------------------------
    def _members(self, f: AssetFeatures, strat_result: dict) -> list[dict]:
        agg = safe_float(strat_result.get("aggregate"), 0)
        mom = clamp(
            0.5 + (clamp(f.mom_10 * 8, -1, 1) * 0.6 + clamp(f.mom_20 * 4, -1, 1) * 0.4) * 0.35,
            0.02,
            0.98,
        )
        mr = clamp(
            0.5
            + clamp((0.5 - f.bb_pctb) * 2, -1, 1)
            * (1 if (f.rsi14 < 45 or f.rsi14 > 55) else 0.3)
            * 0.35,
            0.02,
            0.98,
        )
        xa = clamp(
            0.5
            + (clamp(f.btc_mom * 5, -1, 1) * 0.5 + clamp(f.rel_strength * 5, -1, 1) * 0.5) * 0.3,
            0.05,
            0.95,
        )
        strat_p = clamp(0.5 + agg * 0.35, 0.05, 0.95)
        members = [
            {"name": "direction_model", "p": None},  # filled when trained
            {"name": "strategies", "p": strat_p},
            {"name": "momentum", "p": mom},
            {"name": "mean_reversion", "p": mr},
            {"name": "cross_asset", "p": xa},
        ]
        return members

    def _weights(self, regime: str) -> list[float]:
        mode = self.config.get("ensemble_mode", "regime_weighted")
        if mode == "equal":
            return list(EQUAL_WEIGHTS)
        if mode == "weighted":
            return list(DEFAULT_WEIGHTS)
        return list(REGIME_MODEL_WEIGHTS.get(regime, DEFAULT_WEIGHTS))

    # ------------------------------------------------------------------
    # Inference (§17: answers the 25 decision questions' AI portions)
    # ------------------------------------------------------------------
    def infer(self, f: AssetFeatures, regime_info: dict, strat_result: dict) -> dict:
        with self._lock:
            x = feature_vector(f, version="v3")
            strict = bool(self.config.get("strict_ai_mode", True))
            regime = regime_info.get("regime", "UNSTABLE")
            members = self._members(f, strat_result)
            used_model = heuristic = False
            p_model = None
            if self.trained and not self.degraded:
                p_model = self.dir_model.predict_proba(x)
                members[0]["p"] = p_model
                used_model = True
            elif not strict:
                agg = safe_float(strat_result.get("aggregate"), 0)
                p_model = clamp(0.5 + agg * 0.35, 0.05, 0.95)
                members[0]["p"] = p_model
                heuristic = True
            else:
                members[0]["p"] = None  # strict: untrained member contributes nothing

            # ---- ensemble fusion ----
            weights = self._weights(regime)
            active = [(m, w) for m, w in zip(members, weights, strict=False) if m["p"] is not None]
            if active:
                tot_w = sum(w for _, w in active) or 1.0
                p_ens = sum(m["p"] * w for m, w in active) / tot_w
                disagreement = (
                    float(np.std([m["p"] for m, _ in active])) if len(active) > 1 else 0.0
                )
            else:
                p_ens, disagreement = 0.5, 0.0
            p_cal = (
                self.calibrator.calibrate(p_ens)
                if (used_model and self.calibrator.fitted)
                else p_ens
            )
            uncertainty = clamp(disagreement * 2, 0, 1)

            # ---- per-model outputs (trained path) ----
            if used_model:
                exp_ret = float(self.ret_model.predict(x))
                exp_vol_m = max(float(self.vol_model.predict(x)), f.atr_pct * 0.5, 0.002)
                p_qual = float(self.qual_model.predict_proba(x))
                exp_mae = float(self.mae_model.predict(x))
                exp_mfe = float(self.mfe_model.predict(x))
            else:
                exp_ret = clamp(
                    safe_float(strat_result.get("aggregate"), 0) * max(f.atr_pct, 0.005) * 1.2,
                    -0.05,
                    0.05,
                )
                exp_vol_m = max(f.atr_pct * 1.1, 0.002)
                p_qual = 0.5
                exp_mae = -max(f.atr_pct, 0.004)
                exp_mfe = max(f.atr_pct, 0.004)
            exp_vol = max(f.atr_pct * 1.1, exp_vol_m, 0.002)

            # ---- trade quality composite (v2 formula, kept) ----
            evald = strat_result.get("evald", {})
            scores = [
                v.get("score", 0.0)
                for v in evald.values()
                if v.get("weighted") != 0 or v.get("score", 0) != 0
            ]
            agree = 1 - (statistics.pstdev(scores) if len(scores) > 1 else 1)
            best_fit = max((v.get("fit", 0.5) for v in evald.values()), default=0.5)
            data_q = 1.0 if f.n_candles >= 120 else (0.6 if f.n_candles >= 60 else 0.3)
            if f.data_stale:
                data_q *= 0.5
            quality = clamp(0.3 * agree + 0.25 * best_fit + 0.2 * data_q + 0.25 * p_qual, 0, 1)

            # ---- direction & confidence ----
            if used_model:
                direction = "LONG" if p_cal >= 0.56 else "SHORT" if p_cal <= 0.44 else "WAIT"
                confidence = round(abs(p_cal - 0.5) * 2 * (1 - 0.5 * uncertainty), 4)
                mode = "trained-ensemble"
            elif heuristic:
                direction = "LONG" if p_cal >= 0.56 else "SHORT" if p_cal <= 0.44 else "WAIT"
                confidence = round(abs(p_cal - 0.5) * 2 * (1 - 0.5 * uncertainty), 4)
                mode = "heuristic-composite-LABELED"
            else:
                direction, confidence, mode = "WAIT", 0.0, "untrained-no-signal"

            out = {
                "p_long": round(float(p_cal), 4),
                "p_raw": round(float(p_ens), 4),
                "direction": direction,
                "expected_return": round(float(exp_ret), 5),
                "expected_vol": round(float(exp_vol), 5),
                "expected_mae": round(float(exp_mae), 5),
                "expected_mfe": round(float(exp_mfe), 5),
                "trade_quality_p": round(float(p_qual), 4),
                "confidence": confidence,
                "quality": round(quality, 4),
                "disagreement": round(disagreement, 4),
                "uncertainty": round(uncertainty, 4),
                "calibrated": bool(used_model and self.calibrator.fitted),
                "ensemble_mode": self.config.get("ensemble_mode", "regime_weighted"),
                "members": [
                    {
                        "name": m["name"],
                        "p": (round(m["p"], 4) if m["p"] is not None else None),
                        "weight": w,
                    }
                    for m, w in zip(members, weights, strict=False)
                ],
                "model_version": MODEL_VERSION,
                "model_trained": self.trained,
                "model_degraded": self.degraded,
                "degraded_reason": self.degraded_reason,
                "mode": mode,
                "regime": regime,
                "regime_conf": regime_info.get("confidence", 0),
            }
            self.last_inference = utcnow_iso()
            self.inference_count += 1
            # ring buffer for drift monitoring (labels filled by ml/drift.py
            # when the horizon outcome becomes observable)
            self._recent_pred.append((float(p_cal), 0))
            if len(self._recent_pred) > 2000:
                del self._recent_pred[:500]
            return out

    def recent_predictions(self) -> list[tuple[float, int]]:
        with self._lock:
            return list(self._recent_pred)
