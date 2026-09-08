"""Quant models (§4: trained from scratch on REAL data — never pretrained
myths, never fabricated predictions).

Vectorized successors of the v2 LogisticModel/RidgeModel with identical
interfaces (predict_proba / predict / fit) plus serialization so the Model
Registry can persist and reload champion weights.

  * LogisticModel — full-batch gradient descent with L2 on mean gradient
  * RidgeModel    — exact closed-form ridge least squares (deterministic)

Both train ONLY on caller-supplied real datasets; an unfitted model is
explicitly `fitted=False` and the strict-mode AI engine refuses to emit
signals from it.
"""

from __future__ import annotations

import numpy as np


def sigmoid(x):
    x = np.clip(np.asarray(x, dtype=float), -30, 30)
    return 1.0 / (1.0 + np.exp(-x))


class LogisticModel:
    """Binary logistic regression: GD on the mean gradient with L2."""

    kind = "logistic"

    def __init__(self, n_feat: int, lr: float = 0.9, l2: float = 0.01):
        self.n_feat = n_feat
        self.lr = lr
        self.l2 = l2
        self.w = np.zeros(n_feat)
        self.b = 0.0
        self.fitted = False

    def predict_proba(self, x) -> float:
        xv = np.asarray(x, dtype=float)
        return float(sigmoid(np.dot(self.w, xv) + self.b))

    def predict_proba_batch(self, X) -> np.ndarray:
        Xv = np.asarray(X, dtype=float)
        return sigmoid(Xv @ self.w + self.b)

    def fit(self, X, y, epochs: int = 300) -> LogisticModel:
        Xv = np.asarray(X, dtype=float)
        yv = np.asarray(y, dtype=float)
        if Xv.size == 0 or len(Xv) != len(yv):
            return self
        n = len(Xv)
        for _ in range(epochs):
            p = sigmoid(Xv @ self.w + self.b)
            err = p - yv
            grad_w = (Xv.T @ err) / n + self.l2 * self.w
            grad_b = float(err.mean())
            self.w -= self.lr * grad_w
            self.b -= self.lr * grad_b
        self.fitted = True
        return self

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "n_feat": self.n_feat,
            "lr": self.lr,
            "l2": self.l2,
            "w": self.w.tolist(),
            "b": self.b,
            "fitted": self.fitted,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LogisticModel:
        m = cls(int(d["n_feat"]), float(d.get("lr", 0.9)), float(d.get("l2", 0.01)))
        m.w = np.asarray(d["w"], dtype=float)
        m.b = float(d["b"])
        m.fitted = bool(d.get("fitted"))
        return m


class RidgeModel:
    """Ridge regression solved in closed form: w = (XᵀX + λI)⁻¹Xᵀy."""

    kind = "ridge"

    def __init__(self, n_feat: int, lr: float = 0.0, l2: float = 1.0):
        self.n_feat = n_feat
        self.lr = lr  # unused (closed form); kept for interface parity
        self.l2 = l2  # λ scale: λ = l2 * n
        self.w = np.zeros(n_feat)
        self.b = 0.0
        self.fitted = False

    def predict(self, x) -> float:
        xv = np.asarray(x, dtype=float)
        return float(np.dot(self.w, xv) + self.b)

    def predict_batch(self, X) -> np.ndarray:
        Xv = np.asarray(X, dtype=float)
        return Xv @ self.w + self.b

    def fit(self, X, y, epochs: int = 0) -> RidgeModel:
        Xv = np.asarray(X, dtype=float)
        yv = np.asarray(y, dtype=float)
        if Xv.size == 0 or len(Xv) != len(yv):
            return self
        n = len(Xv)
        # center y on the bias via augmented column
        A = np.hstack([Xv, np.ones((n, 1))])
        lam = max(self.l2, 1e-6)
        reg = np.eye(A.shape[1]) * lam
        reg[-1, -1] = 1e-8  # do not regularize the intercept
        try:
            theta = np.linalg.solve(A.T @ A + reg * max(n, 1) / 100.0, A.T @ yv)
        except np.linalg.LinAlgError:
            theta = np.linalg.lstsq(A, yv, rcond=None)[0]
        self.w = theta[:-1]
        self.b = float(theta[-1])
        self.fitted = True
        return self

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "n_feat": self.n_feat,
            "lr": self.lr,
            "l2": self.l2,
            "w": self.w.tolist(),
            "b": self.b,
            "fitted": self.fitted,
        }

    @classmethod
    def from_dict(cls, d: dict) -> RidgeModel:
        m = cls(int(d["n_feat"]), float(d.get("lr", 0.0)), float(d.get("l2", 1.0)))
        m.w = np.asarray(d["w"], dtype=float)
        m.b = float(d["b"])
        m.fitted = bool(d.get("fitted"))
        return m


def model_from_dict(d: dict):
    kind = d.get("kind")
    if kind == LogisticModel.kind:
        return LogisticModel.from_dict(d)
    if kind == RidgeModel.kind:
        return RidgeModel.from_dict(d)
    raise ValueError(f"unknown model kind: {kind}")


def feature_importance(model, keys: list[str]) -> list[dict]:
    """|weight| normalized — honest importance for linear models."""
    w = np.abs(np.asarray(model.w, dtype=float))
    tot = float(w.sum()) or 1.0
    rows = [
        {
            "feature": k,
            "weight": round(float(model.w[i]), 6),
            "importance": round(float(w[i] / tot), 4),
        }
        for i, k in enumerate(keys)
        if i < len(model.w)
    ]
    return sorted(rows, key=lambda r: -r["importance"])
