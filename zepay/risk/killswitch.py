"""Emergency kill switch (§39).

* stops new entries IMMEDIATELY (config flag read by the Risk Engine)
* optionally cancels open orders
* optionally flattens positions per configured emergency policy
* persists state (survives restart — never auto-resumes)
* creates an audit event + alert
* RESUMING requires an explicit separate human action with typed
  confirmation — the kill switch itself never resumes anything.
"""

from __future__ import annotations

import logging
from typing import Any

from zepay.core.events import BUS, E
from zepay.core.util import utcnow_iso

log = logging.getLogger("zepay.risk.killswitch")


class KillSwitch:
    def __init__(self, config, storage, audit_fn, alerts=None, execution=None):
        self.config = config
        self.db = storage
        self.audit = audit_fn
        self.alerts = alerts
        self.execution = execution  # set post-construction (circular ref avoided)

    @property
    def engaged(self) -> bool:
        return bool(self.config.get("kill_switch"))

    def engage(
        self,
        actor: str = "user",
        reason: str = "",
        cancel_orders: bool | None = None,
        flatten: bool = False,
    ) -> dict:
        cancel = (
            self.config.get("kill_switch_cancel_orders", True)
            if cancel_orders is None
            else cancel_orders
        )
        self.config.update(
            {
                "kill_switch": True,
                "trading_halted": True,
                "halt_reason": reason or "Kill switch engaged",
                "kill_switch_engaged_at": utcnow_iso(),
            },
            actor=actor,
        )
        self.audit(
            "risk",
            "kill_switch_engaged",
            {"actor": actor, "reason": reason, "cancel_orders": cancel, "flatten": flatten},
        )
        if self.alerts:
            self.alerts.notify(
                "CRITICAL",
                "KILL SWITCH ENGAGED",
                f"actor={actor} reason={reason or 'manual'} — new entries "
                "stopped; state persists until manual resume",
            )
        BUS.publish(
            E.KILL_SWITCH_ACTIVATED, {"actor": actor, "reason": reason}, source="killswitch"
        )
        result: dict[str, Any] = {"engaged": True, "cancelled": 0, "flattened": 0, "errors": []}
        if self.execution is not None:
            if cancel:
                try:
                    result["cancelled"] = self.execution.cancel_all_open("KILL_SWITCH")
                except Exception as e:
                    result["errors"].append(f"cancel_all: {e}")
            policy = "flatten" if flatten else self.config.get("kill_switch_flatten_policy", "none")
            if policy in ("flatten", "reduce"):
                try:
                    result["flattened"] = self.execution.emergency_flatten(policy)
                except Exception as e:
                    result["errors"].append(f"flatten: {e}")
        return result

    def status(self) -> dict:
        return {
            "engaged": self.engaged,
            "halt_reason": self.config.get("halt_reason", ""),
            "engaged_at": self.config.get("kill_switch_engaged_at"),
            "flatten_policy": self.config.get("kill_switch_flatten_policy", "none"),
            "cancel_orders_on_engage": bool(self.config.get("kill_switch_cancel_orders", True)),
            "note": "Kill switch NEVER auto-resumes. Resume requires explicit "
            "human action with typed confirmation.",
        }

    def resume(self, actor: str, confirmation: str) -> dict:
        """Explicit human resume. Requires typing RESUME TRADING."""
        if confirmation != "RESUME TRADING":
            return {"ok": False, "error": "Resume requires typing exactly: RESUME TRADING"}
        self.config.update(
            {
                "kill_switch": False,
                "trading_halted": False,
                "halt_reason": "",
                "kill_switch_engaged_at": None,
            },
            actor=actor,
        )
        self.audit("risk", "kill_switch_resumed", {"actor": actor}, actor=actor)
        if self.alerts:
            self.alerts.notify(
                "WARN",
                "Kill switch released",
                f"by {actor} — trading gates re-evaluated from scratch",
            )
        return {"ok": True, "engaged": False}
