"""Confidence calibration (§16).

Platt scaling (logistic recalibration of raw scores) fitted on out-of-sample
predictions, plus reliability measurement (Brier score + binned calibration
error). An uncalibrated model is reported as such — calibration is measured,
never assumed.
"""

from __future__ import annotations

import numpy as np

from zepay.ai.models import sigmoid


class PlattCalibrator:
    """p_cal = sigmoid(a * logit(p_raw) + b), fitted by GD on real OOS pairs."""

    def __init__(self, a: float = 1.0, b: float = 0.0, fitted: bool = False):
        self.a = a
        self.b = b
        self.fitted = fitted

    @staticmethod
    def _logit(p, eps: float = 1e-6):
        p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
        return np.log(p / (1 - p))

    def fit(self, p_raw, y_true, epochs: int = 400, lr: float = 0.05) -> PlattCalibrator:
        z = self._logit(p_raw)
        y = np.asarray(y_true, dtype=float)
        if len(z) < 20 or len(z) != len(y):
            return self  # not enough OOS data — stay identity, report unfitted
        a, b = 1.0, 0.0
        n = len(z)
        for _ in range(epochs):
            p = sigmoid(a * z + b)
            err = p - y
            ga = float((err * z).mean())
            gb = float(err.mean())
            a -= lr * n * ga / max(n, 1) * 2
            b -= lr * n * gb / max(n, 1) * 2
        self.a, self.b, self.fitted = a, b, True
        return self

    def calibrate(self, p_raw: float) -> float:
        if not self.fitted:
            return float(p_raw)
        z = float(self._logit([p_raw])[0])
        return float(sigmoid(self.a * z + self.b))

    def to_dict(self) -> dict:
        return {
            "a": round(self.a, 6),
            "b": round(self.b, 6),
            "fitted": self.fitted,
            "kind": "platt",
        }

    @classmethod
    def from_dict(cls, d: dict) -> PlattCalibrator:
        return cls(float(d.get("a", 1.0)), float(d.get("b", 0.0)), bool(d.get("fitted")))


def brier_score(p, y) -> float:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(p) == 0 or len(p) != len(y):
        return 1.0
    return float(np.mean((p - y) ** 2))


def reliability_curve(p, y, bins: int = 10) -> list[dict]:
    """Binned predicted-vs-actual — the calibration curve shown in the UI."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    out: list[dict] = []
    if len(p) == 0:
        return out
    edges = np.linspace(0, 1, bins + 1)
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & (p < hi) if i < bins - 1 else (p >= lo) & (p <= hi)
        n = int(mask.sum())
        out.append(
            {
                "bin_lo": round(float(lo), 2),
                "bin_hi": round(float(hi), 2),
                "n": n,
                "pred_mean": round(float(p[mask].mean()), 4) if n else None,
                "actual_mean": round(float(y[mask].mean()), 4) if n else None,
            }
        )
    return out


def calibration_error(p, y, bins: int = 10) -> float:
    """Sample-weighted mean |predicted − actual| across bins (ECE)."""
    curve = reliability_curve(p, y, bins)
    tot = sum(c["n"] for c in curve) or 1
    ece = 0.0
    for c in curve:
        if c["n"] and c["pred_mean"] is not None and c["actual_mean"] is not None:
            ece += c["n"] / tot * abs(c["pred_mean"] - c["actual_mean"])
    return round(ece, 4)
