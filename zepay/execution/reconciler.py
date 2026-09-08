"""Reconciliation engine (v2 semantics, multi-mode).

ZEPAY state vs venue state. Runs at startup and periodically.
  * paper bookkeeping consistency (equity = start + realized + unrealized)
  * every OPEN position must have an entry fill
  * LIVE: real exchange balances vs local positions; withdrawal permission
    → refused; UNKNOWN orders → live stays blocked until resolved.
Mismatch BLOCKS live trading — never papered over.
"""

from __future__ import annotations

import logging

from zepay.core.util import safe_float, utcnow_iso

log = logging.getLogger("zepay.execution.reconciler")


class Reconciler:
    def __init__(
        self,
        storage,
        config,
        accounts,
        portfolio,
        execution,
        registry,
        universe,
        audit_fn,
        alerts=None,
    ):
        self.db = storage
        self.config = config
        self.accounts = accounts
        self.portfolio = portfolio
        self.execution = execution
        self.registry = registry
        self.universe = universe
        self.audit = audit_fn
        self.alerts = alerts

    def paper_check(self) -> dict:
        issues = []
        acct = self.accounts.get("PAPER")
        if not acct:
            issues.append("paper account missing")
        else:
            start = safe_float(self.config.get("paper_starting_balance", 10000.0))
            realized = safe_float(acct.get("realized_pnl"))
            unreal = safe_float(acct.get("unrealized_pnl"))
            expect = start + realized + unreal
            actual = safe_float(acct.get("equity"))
            if abs(expect - actual) > 0.02:
                issues.append(f"equity mismatch: ledger {expect:.2f} vs account {actual:.2f}")
        for p in self.portfolio.positions("PAPER"):
            side = "BUY" if p["direction"] == "LONG" else "SELL"
            fills = self.db.query(
                "SELECT SUM(qty) AS q FROM fills WHERE market=? AND side=? AND order_id IN"
                " (SELECT id FROM orders WHERE mode='PAPER')",
                (p["market"], side),
            )
            bought = safe_float((fills or [{}])[0].get("q"))
            if bought <= 0:
                issues.append(f"open position {p['market']} has no entry fill")
        open_lim = self.db.query(
            "SELECT COUNT(*) AS n FROM orders WHERE status IN ('ACKNOWLEDGED',"
            "'PARTIALLY_FILLED','SUBMITTED','CREATED','VALIDATED')"
        )
        ok = not issues
        report = {
            "ok": ok,
            "mode": "PAPER",
            "issues": issues,
            "checked_at": utcnow_iso(),
            "open_orders": open_lim[0]["n"] if open_lim else 0,
        }
        if not ok:
            if self.alerts:
                self.alerts.notify("WARN", "Paper reconciliation issues", "; ".join(issues))
            self.audit("reconciliation", "paper_mismatch", {"issues": issues})
        else:
            self.audit("reconciliation", "paper_ok", {})
        return report

    def live_check(self) -> dict:
        venue = self.config.get("exchange") or "binance_spot"
        adapter = self.registry.get(venue)
        if not adapter or not adapter.has_credentials():
            return {
                "ok": False,
                "mode": "LIVE",
                "venue": venue,
                "issues": ["exchange credentials not configured"],
                "checked_at": utcnow_iso(),
            }
        issues = []
        try:
            acct = adapter.account(timeout=12)
        except Exception as e:
            return {
                "ok": False,
                "mode": "LIVE",
                "venue": venue,
                "issues": [f"cannot read exchange account: {e}"[:200]],
                "checked_at": utcnow_iso(),
            }
        if acct.get("canWithdraw"):
            issues.append("API key has withdrawal permission — refused (§51)")
        ex_bal = {
            b["asset"]: safe_float(b["free"]) + safe_float(b["locked"])
            for b in acct.get("balances", [])
        }
        for p in self.portfolio.positions("LIVE"):
            inst = self.universe.get(p["market"])
            base = inst.base_asset if inst else p["market"].split("/")[0]
            have = ex_bal.get(base, 0.0)
            if have + 1e-9 < safe_float(p["qty"]):
                issues.append(
                    f"{p['market']}: exchange balance {have} < position qty " f"{p['qty']}"
                )
        unknown = self.db.query("SELECT id FROM orders WHERE mode='LIVE' AND status='UNKNOWN'")
        for u in unknown:
            issues.append(f"order {u['id']} outcome unresolved (§33 UNKNOWN)")
        ok = not issues
        report = {
            "ok": ok,
            "mode": "LIVE",
            "venue": venue,
            "issues": issues,
            "checked_at": utcnow_iso(),
            "exchange_balances": {k: v for k, v in list(ex_bal.items())[:20] if v > 0},
        }
        if not ok:
            self.execution.set_live_block("; ".join(issues)[:500])
            if self.alerts:
                self.alerts.notify(
                    "CRITICAL",
                    "LIVE reconciliation mismatch — live trading blocked",
                    "; ".join(issues)[:500],
                )
            self.audit("reconciliation", "live_mismatch", {"issues": issues})
        else:
            self.execution.clear_live_block()
            self.audit("reconciliation", "live_ok", {})
        return report

    def resolve_unknown_orders(self) -> list[dict]:
        """Try to resolve every UNKNOWN live order against the venue (§33:
        reconcile first, never blind resubmission)."""
        resolved = []
        venue = self.config.get("exchange") or "binance_spot"
        adapter = self.registry.get(venue)
        for o in self.db.query("SELECT * FROM orders WHERE status='UNKNOWN' AND mode='LIVE'"):
            entry = {"order": o["id"], "resolved": False}
            if adapter and adapter.has_credentials():
                try:
                    sym = self.universe.exchange_symbol(o["market"])
                    q = adapter.get_order(
                        sym,
                        order_id=o.get("exchange_order_id"),
                        client_order_id=o.get("client_order_id"),
                    )
                    status = str(q.get("status", ""))
                    executed = safe_float(q.get("executedQty"))
                    mapping = {
                        "NEW": "ACKNOWLEDGED",
                        "PARTIALLY_FILLED": "PARTIALLY_FILLED",
                        "FILLED": "FILLED",
                        "CANCELED": "CANCELLED",
                        "REJECTED": "REJECTED",
                        "EXPIRED": "CANCELLED",
                    }
                    if status in mapping:
                        self.execution.sm.transition(
                            o["id"],
                            mapping[status],
                            f"reconciled: venue says {status}",
                            venue,
                            force=True,
                            extra_sets={
                                "filled_qty": executed,
                                "avg_fill_price": (
                                    (safe_float(q.get("cummulativeQuoteQty")) / executed)
                                    if executed
                                    else None
                                ),
                            },
                        )
                        entry["resolved"] = True
                        entry["venue_status"] = status
                except Exception as e:
                    entry["error"] = str(e)[:160]
            else:
                entry["error"] = "no credentials — cannot query venue; order stays UNKNOWN"
            resolved.append(entry)
        if all(r["resolved"] for r in resolved) and resolved:
            remaining = self.db.query_one(
                "SELECT COUNT(*) AS n FROM orders WHERE status='UNKNOWN' AND mode='LIVE'"
            )
            if remaining and remaining["n"] == 0:
                self.execution.clear_live_block()
                self.audit("reconciliation", "unknown_orders_resolved", {"count": len(resolved)})
        return resolved

    def startup(self) -> dict:
        report = {"paper": self.paper_check(), "unknown_orders": self.resolve_unknown_orders()}
        if self.config.get("live_enabled"):
            report["live"] = self.live_check()
        self.audit("reconciliation", "startup", {"components": list(report.keys())})
        return report
