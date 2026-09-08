"""Model registry (§26) + champion/challenger (§27).

Lifecycle: RESEARCH → VALIDATION → CANDIDATE → PAPER → SHADOW → APPROVED →
LIMITED → PRODUCTION → DEGRADED → RETIRED. Transitions are validated and
audited. Promotion to champion is ALWAYS manual with recorded evidence —
the production model is never replaced automatically.
"""

from __future__ import annotations

import json
import logging

from zepay.core.domain import ModelLifecycle
from zepay.core.events import BUS, E
from zepay.core.ids import new_id
from zepay.core.util import safe_float, utcnow_iso

log = logging.getLogger("zepay.ml.registry")

L = ModelLifecycle

LEGAL_TRANSITIONS: dict[str, set[str]] = {
    L.RESEARCH.value: {L.VALIDATION.value, L.RETIRED.value},
    L.VALIDATION.value: {L.CANDIDATE.value, L.RESEARCH.value, L.RETIRED.value},
    L.CANDIDATE.value: {L.PAPER.value, L.VALIDATION.value, L.RETIRED.value},
    L.PAPER.value: {L.SHADOW.value, L.CANDIDATE.value, L.DEGRADED.value, L.RETIRED.value},
    L.SHADOW.value: {L.APPROVED.value, L.PAPER.value, L.DEGRADED.value, L.RETIRED.value},
    L.APPROVED.value: {L.LIMITED.value, L.SHADOW.value, L.RETIRED.value},
    L.LIMITED.value: {L.PRODUCTION.value, L.APPROVED.value, L.DEGRADED.value, L.RETIRED.value},
    L.PRODUCTION.value: {L.DEGRADED.value, L.RETIRED.value},
    L.DEGRADED.value: {L.VALIDATION.value, L.RETIRED.value, L.PRODUCTION.value},
    L.RETIRED.value: set(),
}


class ModelRegistry:
    def __init__(self, storage, audit_fn):
        self.db = storage
        self.audit = audit_fn

    # ---- registration ----
    def register(
        self,
        name: str,
        kind: str,
        version: str,
        *,
        features: list | None = None,
        dataset: str = "",
        train_period: str = "",
        valid_period: str = "",
        oos_period: str = "",
        metrics: dict | None = None,
        calibration: dict | None = None,
        feature_importance: list | None = None,
        params_blob: str = "",
        samples: int = 0,
        lifecycle: str = L.RESEARCH.value,
    ) -> str:
        mid = new_id("mdl")
        now = utcnow_iso()
        self.db.execute(
            "INSERT INTO models (id, name, version, kind, lifecycle, status, champion,"
            " features, dataset, train_period, valid_period, oos_period, metrics,"
            " calibration, feature_importance, drift_status, params_blob, samples,"
            " trained_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                mid,
                name,
                version,
                kind,
                lifecycle,
                lifecycle,
                0,
                json.dumps(features or []),
                dataset,
                train_period,
                valid_period,
                oos_period,
                json.dumps(metrics or {}),
                json.dumps(calibration or {}),
                json.dumps(feature_importance or []),
                "none",
                params_blob,
                samples,
                now,
                now,
                now,
            ),
        )
        self.audit(
            "ml",
            "model_registered",
            {"id": mid, "name": name, "version": version, "lifecycle": lifecycle},
        )
        BUS.publish(
            E.MODEL_UPDATED,
            {"model_id": mid, "name": name, "lifecycle": lifecycle},
            source="registry",
        )
        return mid

    def get(self, model_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM models WHERE id=?", (model_id,))
        if row:
            for k in ("features", "metrics", "calibration", "feature_importance"):
                try:
                    row[k] = json.loads(
                        row.get(k) or "{}"
                        if k != "features" and k != "feature_importance"
                        else row.get(k) or "[]"
                    )
                except Exception:
                    pass
        return row

    def list_all(self, lifecycle: str | None = None, limit: int = 100) -> list[dict]:
        """All registered models (newest first)."""
        q = (
            "SELECT id,name,version,kind,lifecycle,status,champion,drift_status,samples,"
            "metrics,trained_at FROM models"
        )
        params: tuple = ()
        if lifecycle:
            q += " WHERE lifecycle=?"
            params = (lifecycle,)
        q += " ORDER BY id DESC LIMIT ?"
        rows = self.db.query(q, (*params, limit))
        for r in rows:
            try:
                r["metrics"] = json.loads(r.get("metrics") or "{}")
            except Exception:
                r["metrics"] = {}
        return rows

    # ---- lifecycle ----
    def transition(
        self, model_id: str, to_lifecycle: str, actor: str = "system", evidence: str = ""
    ) -> dict:
        row = self.db.query_one("SELECT id, lifecycle FROM models WHERE id=?", (model_id,))
        if not row:
            return {"ok": False, "error": "model not found"}
        src = row["lifecycle"]
        if to_lifecycle not in LEGAL_TRANSITIONS.get(src, set()):
            return {"ok": False, "error": f"illegal lifecycle transition {src} → {to_lifecycle}"}
        self.db.execute(
            "UPDATE models SET lifecycle=?, status=?, updated_at=? WHERE id=?",
            (to_lifecycle, to_lifecycle, utcnow_iso(), model_id),
        )
        self.db.execute(
            "INSERT INTO model_promotions (model_id, from_lifecycle, to_lifecycle,"
            " champion, approved_by, evidence, created_at) VALUES (?,?,?,?,?,?,?)",
            (model_id, src, to_lifecycle, 0, actor, str(evidence)[:2000], utcnow_iso()),
        )
        self.audit(
            "ml",
            "lifecycle_transition",
            {"id": model_id, "from": src, "to": to_lifecycle, "actor": actor},
        )
        return {"ok": True, "from": src, "to": to_lifecycle}

    # ---- champion / challenger (§27) ----
    def champion(self, kind: str = "") -> dict | None:
        q = "SELECT * FROM models WHERE champion=1"
        params: tuple = ()
        if kind:
            q += " AND kind=?"
            params = (kind,)
        row = self.db.query_one(q + " ORDER BY updated_at DESC LIMIT 1", params)
        return self.get(row["id"]) if row else None

    def challengers(self) -> list[dict]:
        return self.db.query(
            "SELECT id,name,version,kind,lifecycle,metrics FROM models WHERE champion=0"
            " AND lifecycle IN ('CANDIDATE','PAPER','SHADOW','APPROVED','LIMITED')"
            " ORDER BY id DESC LIMIT 20"
        )

    def promote_champion(self, model_id: str, actor: str, evidence: dict) -> dict:
        """MANUAL ONLY. Evidence (OOS metrics comparison) is recorded."""
        row = self.get(model_id)
        if not row:
            return {"ok": False, "error": "model not found"}
        if row["lifecycle"] not in (
            L.APPROVED.value,
            L.LIMITED.value,
            L.PRODUCTION.value,
            L.SHADOW.value,
        ):
            return {
                "ok": False,
                "error": f"model must be APPROVED+ to champion (is {row['lifecycle']})",
            }
        prev = self.champion(row["kind"])
        self.db.execute("UPDATE models SET champion=0 WHERE champion=1 AND kind=?", (row["kind"],))
        self.db.execute(
            "UPDATE models SET champion=1, lifecycle='PRODUCTION', status='active',"
            " updated_at=? WHERE id=?",
            (utcnow_iso(), model_id),
        )
        if prev:
            self.db.execute(
                "UPDATE models SET lifecycle='RETIRED', updated_at=? WHERE id=?",
                (utcnow_iso(), prev["id"]),
            )
        self.db.execute(
            "INSERT INTO model_promotions (model_id, from_lifecycle, to_lifecycle,"
            " champion, approved_by, evidence, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                model_id,
                row["lifecycle"],
                L.PRODUCTION.value,
                1,
                actor,
                json.dumps(evidence, default=str)[:4000],
                utcnow_iso(),
            ),
        )
        self.audit(
            "ml",
            "champion_promoted",
            {"id": model_id, "replaced": prev["id"] if prev else None, "actor": actor},
            actor=actor,
        )
        return {"ok": True, "champion": model_id, "retired_previous": prev["id"] if prev else None}

    def compare(self, model_a: str, model_b: str) -> dict:
        """Side-by-side evidence table for promotion decisions."""
        a, b = self.get(model_a), self.get(model_b)
        if not a or not b:
            return {"ok": False, "error": "model(s) not found"}
        keys = [
            "dir_accuracy_oos",
            "dir_brier_oos",
            "dir_brier_calibrated_oos",
            "dir_ece_oos",
            "return_mae_oos",
            "samples",
        ]
        rows = []
        for k in keys:
            va = safe_float((a.get("metrics") or {}).get(k))
            vb = safe_float((b.get("metrics") or {}).get(k))
            rows.append(
                {
                    "metric": k,
                    "a": va,
                    "b": vb,
                    "better": (
                        "a"
                        if (va and (not vb or self._better(k, va, vb)))
                        else ("b" if vb else "n/a")
                    ),
                }
            )
        return {
            "ok": True,
            "a": {
                "id": a["id"],
                "name": a["name"],
                "version": a["version"],
                "lifecycle": a["lifecycle"],
            },
            "b": {
                "id": b["id"],
                "name": b["name"],
                "version": b["version"],
                "lifecycle": b["lifecycle"],
            },
            "metrics": rows,
            "note": "Comparison is evidence — promotion still requires explicit "
            "human approval (§27).",
        }

    @staticmethod
    def _better(metric: str, va: float, vb: float) -> bool:
        lower_is_better = ("brier", "ece", "mae")
        if any(x in metric for x in lower_is_better):
            return va < vb
        return va > vb

    # ---- drift status ----
    def ensure_champion_from_production(self) -> None:
        """If the live AI engine has a trained ensemble persisted as PRODUCTION
        but no champion flag exists, reflect reality: mark the newest
        PRODUCTION quant-ensemble row as champion. This is bookkeeping for the
        CURRENTLY RUNNING model — challenger promotion stays manual (§27)."""
        if self.champion("quant-ensemble"):
            return
        row = self.db.query_one(
            "SELECT id FROM models WHERE kind='quant-ensemble' AND lifecycle='PRODUCTION'"
            " ORDER BY id DESC LIMIT 1"
        )
        if row:
            self.db.execute(
                "UPDATE models SET champion=1, updated_at=? WHERE id=?", (utcnow_iso(), row["id"])
            )
            self.audit("ml", "champion_flag_synced", {"id": row["id"]})

    def set_drift(self, model_id: str, drift_status: str, detail: str = "") -> None:
        self.db.execute(
            "UPDATE models SET drift_status=?, updated_at=? WHERE id=?",
            (drift_status, utcnow_iso(), model_id),
        )
        if drift_status == "severe":
            self.audit("ml", "model_drift_severe", {"id": model_id, "detail": detail})

    def record_prediction(
        self, model_id: str, market: str, infer: dict, cycle_id: str = ""
    ) -> None:
        try:
            self.db.execute(
                "INSERT INTO model_predictions (model_id, market, ts, direction, p_long,"
                " expected_return, expected_vol, confidence, quality, regime, cycle_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    model_id,
                    market,
                    utcnow_iso(),
                    infer.get("direction"),
                    safe_float(infer.get("p_long")),
                    safe_float(infer.get("expected_return")),
                    safe_float(infer.get("expected_vol")),
                    safe_float(infer.get("confidence")),
                    safe_float(infer.get("quality")),
                    infer.get("regime", ""),
                    cycle_id,
                ),
            )
        except Exception as e:
            log.debug("prediction record failed: %s", e)
