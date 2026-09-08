"""Order state machine (§33).

CREATED → VALIDATED → SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED
                                     │                │
                                     ├→ CANCEL_REQUESTED → CANCELLED
                                     ├→ REJECTED / FAILED
                                     └→ UNKNOWN  ⚠ dangerous:
                                        never blindly resubmit; reconcile first.

Every transition is validated and persisted (order_transitions table) — an
illegal transition raises OrderStateError instead of corrupting state.
"""

from __future__ import annotations

import logging

from zepay.core.domain import OrderStatus
from zepay.core.errors import OrderStateError, UnknownOrderStateError
from zepay.core.util import utcnow_iso

log = logging.getLogger("zepay.execution.state")

S = OrderStatus

LEGAL: dict[str, set[str]] = {
    S.CREATED.value: {S.VALIDATED.value, S.REJECTED.value, S.FAILED.value},
    S.VALIDATED.value: {
        S.SUBMITTED.value,
        S.REJECTED.value,
        S.FAILED.value,
        S.CANCEL_REQUESTED.value,
    },
    S.SUBMITTED.value: {
        S.ACKNOWLEDGED.value,
        S.PARTIALLY_FILLED.value,
        S.FILLED.value,
        S.REJECTED.value,
        S.FAILED.value,
        S.UNKNOWN.value,
        S.CANCEL_REQUESTED.value,
        S.CANCELLED.value,
    },
    S.ACKNOWLEDGED.value: {
        S.PARTIALLY_FILLED.value,
        S.FILLED.value,
        S.CANCELLED.value,
        S.CANCEL_REQUESTED.value,
        S.FAILED.value,
        S.UNKNOWN.value,
        S.REJECTED.value,
    },
    S.PARTIALLY_FILLED.value: {
        S.PARTIALLY_FILLED.value,
        S.FILLED.value,
        S.CANCEL_REQUESTED.value,
        S.CANCELLED.value,
        S.UNKNOWN.value,
        S.FAILED.value,
    },
    S.CANCEL_REQUESTED.value: {
        S.CANCELLED.value,
        S.PARTIALLY_FILLED.value,
        S.FILLED.value,
        S.UNKNOWN.value,
        S.FAILED.value,
    },
    S.UNKNOWN.value: {
        S.ACKNOWLEDGED.value,
        S.PARTIALLY_FILLED.value,
        S.FILLED.value,
        S.CANCELLED.value,
        S.REJECTED.value,
        S.FAILED.value,
    },
    # terminal states
    S.FILLED.value: set(),
    S.CANCELLED.value: set(),
    S.REJECTED.value: set(),
    S.FAILED.value: set(),
}

TERMINAL = {S.FILLED.value, S.CANCELLED.value, S.REJECTED.value, S.FAILED.value}


def is_terminal(status: str) -> bool:
    return status in TERMINAL


def validate_transition(src: str, dst: str) -> bool:
    return dst in LEGAL.get(src, set())


class OrderStateMachine:
    """Applies + records transitions for one storage backend."""

    def __init__(self, storage, audit_fn=None):
        self.db = storage
        self.audit = audit_fn or (lambda *a, **k: None)

    def transition(
        self,
        order_id: str,
        dst: str,
        reason: str = "",
        venue: str = "",
        force: bool = False,
        extra_sets: dict | None = None,
    ) -> dict:
        row = self.db.query_one("SELECT id, status, venue FROM orders WHERE id=?", (order_id,))
        if not row:
            raise OrderStateError(f"order {order_id} not found")
        src = row["status"]
        if src == dst:
            return row
        if not validate_transition(src, dst) and not force:
            raise OrderStateError(f"illegal transition {src} → {dst} for {order_id}")
        if dst == S.UNKNOWN.value:
            log.warning(
                "order %s entered UNKNOWN state (%s) — reconciliation required "
                "before any resubmission",
                order_id,
                reason,
            )
        sets = ["status=?", "updated_at=?"]
        params: list = [dst, utcnow_iso()]
        for k, v in (extra_sets or {}).items():
            if k in ("filled_qty", "avg_fill_price", "exchange_order_id", "reject_reason", "price"):
                sets.append(f"{k}=?")
                params.append(v)
        params.append(order_id)
        self.db.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id=?", tuple(params))
        self.db.execute(
            "INSERT INTO order_transitions (order_id, from_status, to_status, reason,"
            " venue, ts) VALUES (?,?,?,?,?,?)",
            (order_id, src, dst, str(reason)[:500], venue or row.get("venue", ""), utcnow_iso()),
        )
        if dst == S.UNKNOWN.value:
            self.audit("execution", "order_unknown_state", {"order": order_id, "reason": reason})
        row["status"] = dst
        return row

    def history(self, order_id: str) -> list[dict]:
        return self.db.query(
            "SELECT * FROM order_transitions WHERE order_id=? ORDER BY id", (order_id,)
        )

    def unknown_orders(self) -> list[dict]:
        return self.db.query("SELECT * FROM orders WHERE status='UNKNOWN'")

    def guard_resubmit(self, order_id: str) -> None:
        """§33: never blindly resubmit an UNKNOWN order."""
        row = self.db.query_one("SELECT status FROM orders WHERE id=?", (order_id,))
        if row and row["status"] == S.UNKNOWN.value:
            raise UnknownOrderStateError(
                f"order {order_id} is UNKNOWN — reconcile with the venue first; "
                "blind resubmission is forbidden"
            )
