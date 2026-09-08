"""Trading router: decisions, orders, positions, trades, account, portfolio,
risk state, execution quality, manual interventions (close/cancel/flatten).

Manual interventions run through the SAME risk/execution path as the engine —
no side doors (§21).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from zepay.api.deps import guard_mutate, zapp_of

router = APIRouter(prefix="/api", tags=["trading"])


@router.get("/decisions")
def decisions(request: Request, limit: int = 100, market: str | None = None) -> list:
    z = zapp_of(request)
    q = "SELECT * FROM decisions"
    p: tuple = ()
    if market:
        q += " WHERE market=?"
        p = (market,)
    q += " ORDER BY ts DESC LIMIT ?"
    return z.storage.query(q, (*p, limit))


@router.get("/orders")
def orders(request: Request, limit: int = 100, open_only: bool = False) -> list:
    z = zapp_of(request)
    q = "SELECT * FROM orders"
    if open_only:
        q += " WHERE status IN ('CREATED','VALIDATED','SUBMITTED','PARTIALLY_FILLED','NEW')"
    q += " ORDER BY rowid DESC LIMIT ?"
    return z.storage.query(q, (limit,))


@router.get("/positions")
def positions(request: Request, mode: str | None = None) -> list:
    z = zapp_of(request)
    return z.portfolio.positions(mode)


@router.get("/trades")
def trades(request: Request, limit: int = 100) -> list:
    z = zapp_of(request)
    return z.storage.query("SELECT * FROM trades ORDER BY rowid DESC LIMIT ?", (limit,))


@router.get("/account")
def account(request: Request, mode: str = "PAPER") -> dict:
    z = zapp_of(request)
    acct = z.accounts.get(mode)
    if not acct:
        raise HTTPException(404, f"no account for mode {mode}")
    return acct


@router.get("/portfolio")
def portfolio(request: Request, mode: str = "PAPER") -> dict:
    z = zapp_of(request)
    return z.portfolio.snapshot(mode)


@router.get("/risk")
def risk(request: Request) -> dict:
    z = zapp_of(request)
    state, msg = z.risk.system_state(None)
    allowed, why = z.risk.entries_allowed()
    return {
        "system_state": state.value,
        "state_message": msg,
        "entries_allowed": allowed,
        "entries_reason": why,
        "stage_caps": z.risk.stage_caps(),
        "consecutive_losses": z.risk.consecutive_losses(),
        "hard_limits": __import__("zepay.risk.engine", fromlist=["HARD_LIMITS"]).HARD_LIMITS,
        "self_test": z.risk.self_test(),
        "events": z.storage.query("SELECT * FROM risk_events ORDER BY id DESC LIMIT 50"),
    }


@router.get("/execution-quality")
def execution_quality(request: Request) -> dict:
    z = zapp_of(request)
    return {
        "venues": z.exec_quality.all_venues(),
        "slippage": z.exec_quality.slippage_distribution(),
    }


# ---- manual interventions ----
class CloseBody(BaseModel):
    mode: str = "PAPER"
    reason: str = "manual"
    qty: float | None = None


@router.post("/positions/{market:path}/close", dependencies=[Depends(guard_mutate)])
def close_position(market: str, body: CloseBody, request: Request) -> dict:
    z = zapp_of(request)
    return z.execution.close_position(
        market, mode=body.mode, reason=body.reason, qty_override=body.qty
    )


@router.post("/orders/{order_id}/cancel", dependencies=[Depends(guard_mutate)])
def cancel_order(order_id: str, request: Request, mode: str = "PAPER") -> dict:
    z = zapp_of(request)
    return z.execution.cancel_order(order_id, mode=mode)


class CancelAllBody(BaseModel):
    reason: str = "manual"


@router.post("/orders/cancel-all", dependencies=[Depends(guard_mutate)])
def cancel_all(body: CancelAllBody, request: Request) -> dict:
    z = zapp_of(request)
    n = z.execution.cancel_all_open(reason=body.reason)
    return {"ok": True, "cancelled": n}


class FlattenBody(BaseModel):
    policy: str = "flatten"  # flatten | reduce


@router.post("/emergency/flatten", dependencies=[Depends(guard_mutate)])
def flatten(body: FlattenBody, request: Request) -> dict:
    z = zapp_of(request)
    if body.policy not in ("flatten", "reduce"):
        raise HTTPException(400, "policy must be flatten|reduce")
    n = z.execution.emergency_flatten(policy=body.policy)
    return {"ok": True, "actions": n, "policy": body.policy}


@router.post("/cycle/run", dependencies=[Depends(guard_mutate)])
def run_cycle(request: Request) -> dict:
    """Run one engine cycle synchronously (UI 'Run cycle now')."""
    z = zapp_of(request)
    rep = z.engine.run_cycle()
    z.metrics.observe_cycle(rep.get("elapsed_s", 0))
    if not rep.get("ok"):
        z.metrics.observe_error()
    return rep


@router.get("/cycle/last")
def last_cycle(request: Request) -> dict:
    return zapp_of(request).engine.last_report()


@router.post("/reconcile", dependencies=[Depends(guard_mutate)])
def reconcile(request: Request) -> dict:
    z = zapp_of(request)
    out: dict[str, Any] = {"paper": z.recon.paper_check()}
    if z.execution.effective_mode() == "LIVE":
        out["live"] = z.recon.live_check()
        out["unknown"] = z.recon.resolve_unknown_orders()
    return out
