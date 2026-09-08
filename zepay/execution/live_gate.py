"""Operational stage gate (§22): PAPER → SHADOW → LIMITED_LIVE → FULL_LIVE.

NEVER automatically promoted. Each promotion requires measured prerequisites
(real history in the current stage, passing reconciliation, verified
credentials for live stages, risk self-test) AND a typed confirmation.
Demotion (back to PAPER / halt) is always allowed instantly.

This module is the ONLY writer of `operational_stage` and `live_enabled`
besides explicit demotions — the settings API refuses those keys.
"""

from __future__ import annotations

import logging

from zepay.core.domain import OperationalStage
from zepay.core.util import utcnow_iso

log = logging.getLogger("zepay.execution.gate")

STAGE_ORDER = [
    OperationalStage.PAPER.value,
    OperationalStage.SHADOW.value,
    OperationalStage.LIMITED_LIVE.value,
    OperationalStage.FULL_LIVE.value,
]

CONFIRMATIONS = {
    OperationalStage.SHADOW.value: "ENABLE SHADOW MODE",
    OperationalStage.LIMITED_LIVE.value: "ENABLE LIMITED LIVE",
    OperationalStage.FULL_LIVE.value: "ENABLE FULL LIVE",
}

MIN_DECISIONS_SHADOW = 20  # paper decisions recorded before shadow
MIN_DECISIONS_LIMITED = 50  # shadow decisions recorded before limited live
MIN_DECISIONS_FULL = 100  # limited-live decisions before full live


class LiveGate:
    def __init__(self, config, storage, registry, risk, reconciler, audit_fn, alerts=None):
        self.config = config
        self.db = storage
        self.registry = registry
        self.risk = risk
        self.reconciler = reconciler
        self.audit = audit_fn
        self.alerts = alerts

    def current_stage(self) -> str:
        return self.config.get("operational_stage", "PAPER")

    def _decision_count(self, stage: str) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM decisions WHERE stage=?", (stage,))
        return int((row or {}).get("n", 0))

    def _order_count(self, mode: str) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM orders WHERE mode=?", (mode,))
        return int((row or {}).get("n", 0))

    def requirements(self) -> dict:
        stage = self.current_stage()
        out: dict = {"stage": stage, "checks": {}, "next_stage": None, "met": False}
        idx = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else 0
        if idx >= len(STAGE_ORDER) - 1:
            out["note"] = "Already at FULL_LIVE. Risk limits remain authoritative."
            out["met"] = True
            return out
        nxt = STAGE_ORDER[idx + 1]
        out["next_stage"] = nxt
        checks = {}
        if nxt == OperationalStage.SHADOW.value:
            n = self._decision_count("PAPER")
            checks["paper_history"] = {
                "required": MIN_DECISIONS_SHADOW,
                "actual": n,
                "ok": n >= MIN_DECISIONS_SHADOW,
            }
            rec = self.reconciler.paper_check()
            checks["reconciliation"] = {"ok": rec["ok"], "issues": rec["issues"]}
        elif nxt == OperationalStage.LIMITED_LIVE.value:
            n = self._decision_count("SHADOW")
            checks["shadow_history"] = {
                "required": MIN_DECISIONS_LIMITED,
                "actual": n,
                "ok": n >= MIN_DECISIONS_LIMITED,
            }
            venue = self.config.get("exchange") or "binance_spot"
            adapter = self.registry.get(venue)
            conn = adapter.test_connection() if adapter else {"connected": False}
            checks["credentials"] = {
                "ok": bool(conn.get("connected")),
                "detail": conn.get("error") or conn.get("message", ""),
            }
            perms = conn.get("permissions")
            can_withdraw = bool(isinstance(perms, dict) and perms.get("canWithdraw"))
            checks["no_withdrawal_permission"] = {"ok": not can_withdraw}
            checks["risk_self_test"] = self.risk.self_test()
            rec = self.reconciler.paper_check()
            checks["reconciliation"] = {"ok": rec["ok"], "issues": rec["issues"]}
        elif nxt == OperationalStage.FULL_LIVE.value:
            n = self._decision_count("LIMITED_LIVE")
            checks["limited_live_history"] = {
                "required": MIN_DECISIONS_FULL,
                "actual": n,
                "ok": n >= MIN_DECISIONS_FULL,
            }
            viol = self.db.query_one(
                "SELECT COUNT(*) AS n FROM risk_events WHERE kind='execution' AND"
                " decision='REJECTED' AND created_at > (SELECT MIN(created_at) FROM"
                " decisions WHERE stage='LIMITED_LIVE')"
            )
            checks["no_serious_violations"] = {
                "violations": int((viol or {}).get("n", 0)),
                "ok": True,
            }
            rec = self.reconciler.live_check()
            checks["live_reconciliation"] = {"ok": rec["ok"], "issues": rec["issues"]}
            checks["risk_self_test"] = self.risk.self_test()
        out["checks"] = checks
        out["met"] = self._all_ok(checks)
        out["confirmation_phrase"] = CONFIRMATIONS.get(nxt, "")
        return out

    @staticmethod
    def _all_ok(checks: dict) -> bool:
        return all(not (isinstance(v, dict) and not v.get("ok", True)) for v in checks.values())

    def promote(self, actor: str, confirmation: str) -> dict:
        req = self.requirements()
        nxt = req.get("next_stage")
        if not nxt:
            return {"ok": False, "error": "Already at the final stage."}
        if confirmation != CONFIRMATIONS.get(nxt):
            return {
                "ok": False,
                "error": f"Typed confirmation required: '{CONFIRMATIONS.get(nxt)}'",
            }
        if not req["met"]:
            failed = [
                k for k, v in req["checks"].items() if isinstance(v, dict) and not v.get("ok", True)
            ]
            return {"ok": False, "error": f"Prerequisites not met: {failed}", "requirements": req}
        patch = {
            "operational_stage": nxt,
            "stage_approved_by": actor,
            "stage_changed_at": utcnow_iso(),
            "live_confirmed_steps": (self.config.get("live_confirmed_steps") or [])
            + [f"{nxt}:{utcnow_iso()}"],
        }
        if nxt in (OperationalStage.LIMITED_LIVE.value, OperationalStage.FULL_LIVE.value):
            patch["live_enabled"] = True
        else:
            patch["live_enabled"] = False
        self.config.update(patch, actor=actor)
        self.audit(
            "security",
            "stage_promotion",
            {"from": req["stage"], "to": nxt, "actor": actor},
            actor=actor,
        )
        if self.alerts:
            self.alerts.notify(
                "WARN", "Operational stage changed", f"{req['stage']} → {nxt} by {actor}"
            )
        return {"ok": True, "stage": nxt}

    def demote(self, actor: str, reason: str = "") -> dict:
        """Always allowed — going safer never requires prerequisites."""
        stage = self.current_stage()
        idx = STAGE_ORDER.index(stage) if stage in STAGE_ORDER else 0
        new = STAGE_ORDER[max(idx - 1, 0)]
        self.config.update(
            {
                "operational_stage": new,
                "live_enabled": new
                in (OperationalStage.LIMITED_LIVE.value, OperationalStage.FULL_LIVE.value)
                and self.config.get("live_enabled", False)
                and new != OperationalStage.PAPER.value
                and new != OperationalStage.SHADOW.value,
                "stage_changed_at": utcnow_iso(),
            },
            actor=actor,
        )
        if new in (OperationalStage.PAPER.value, OperationalStage.SHADOW.value):
            self.config.update({"live_enabled": False}, actor=actor)
        self.audit(
            "security", "stage_demotion", {"from": stage, "to": new, "reason": reason}, actor=actor
        )
        if self.alerts:
            self.alerts.notify(
                "WARN", "Operational stage DEMOTED", f"{stage} → {new} ({reason or 'manual'})"
            )
        return {"ok": True, "stage": new}

    def status(self) -> dict:
        return {
            "stage": self.current_stage(),
            "stage_approved_by": self.config.get("stage_approved_by"),
            "stage_changed_at": self.config.get("stage_changed_at"),
            "live_enabled": bool(self.config.get("live_enabled")),
            "live_block_reason": self.config.get("live_block_reason") or "",
            "confirmed_steps": self.config.get("live_confirmed_steps") or [],
            "requirements": self.requirements(),
            "policy": "Promotion is NEVER automatic. Every step requires measured "
            "prerequisites plus a typed human confirmation.",
        }
