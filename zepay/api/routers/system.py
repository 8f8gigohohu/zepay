"""System router: health, status, config, stage gate, kill switch, backups,
audit, alerts, venues/credentials, SSE event stream."""

from __future__ import annotations

import json
import queue

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from zepay.api.deps import guard_mutate, zapp_of
from zepay.core.config import PRIVILEGED_KEYS
from zepay.core.events import BUS, E
from zepay.security import license as lic

router = APIRouter(prefix="/api", tags=["system"])

SSE_EVENTS = [
    E.RISK_DECISION,
    E.ORDER_FILLED,
    E.ORDER_REJECTED,
    E.ORDER_CREATED,
    E.KILL_SWITCH_ACTIVATED,
    E.MODEL_DEGRADED,
    E.MODEL_UPDATED,
    E.DATA_QUALITY,
    E.CYCLE_ENDED,
    E.SYSTEM_EVENT,
    E.EXCHANGE_DISCONNECTED,
    E.POSITION_CHANGED,
]


# ---------------------------------------------------------------------------
@router.get("/health")
def health(request: Request) -> dict:
    return zapp_of(request).sys_health.check()


@router.get("/status")
def status(request: Request) -> dict:
    z = zapp_of(request)
    return {
        "engine": z.engine.status(),
        "execution": z.execution.status(),
        "stage_gate": z.gate.status(),
        "kill_switch": z.kill.status(),
        "venues": {
            vid: {**v, "enabled": bool((z.config.get("venues_enabled") or {}).get(vid))}
            for vid, v in z.registry.health_all().items()
        },
        "ws": z.ws.status(),
        "quality": z.quality.summary(),
        "metrics": z.metrics.snapshot(),
    }


class ConfigPatch(BaseModel):
    patch: dict


@router.get("/config")
def get_config(request: Request, full: bool = False) -> dict:
    z = zapp_of(request)
    return z.config.all(public_only=not full)


@router.post("/config", dependencies=[Depends(guard_mutate)])
def set_config(body: ConfigPatch, request: Request) -> dict:
    z = zapp_of(request)
    blocked = [k for k in body.patch if k in PRIVILEGED_KEYS]
    if blocked:
        raise HTTPException(
            403,
            f"Privileged keys refused via /config: {blocked}. "
            "Use the stage gate / kill switch / venues endpoints.",
        )
    changed = z.config.update(body.patch, actor="api")
    return {"ok": True, "changed": list(changed.keys())}


# ---- connectivity diagnostics (real probes — explains WHY data is down) ----
@router.get("/diagnostics/connectivity")
def diagnostics_connectivity(request: Request) -> dict:
    """Real network probes against every venue's public endpoint. Honest by
    construction: no venue is ever reported reachable unless it actually
    answered. Distinguishes environment blocks (all fail) from per-venue
    problems (geo-block 451, auth, etc.)."""
    import time as _t

    z = zapp_of(request)
    from zepay.exchanges.binance_futures import FAPI_BASES
    from zepay.exchanges.binance_spot import PUBLIC_BASES

    hosts = {
        "binance_spot": PUBLIC_BASES[0] if PUBLIC_BASES else "",
        "binance_futures": FAPI_BASES[0] if FAPI_BASES else "",
    }
    rows = []
    for vid, adapter in z.registry.all().items():
        row = {
            "venue": vid,
            "host": hosts.get(vid) or "",
            "status": None,
            "latency_ms": None,
            "error": "",
        }
        t0 = _t.monotonic()
        try:
            adapter.ping()
            row["status"] = "REACHABLE"
        except Exception as e:
            msg = str(e)
            row["status"] = (
                "GEO_BLOCKED"
                if "451" in msg
                else ("NOT_CONFIGURED" if "NOT_CONFIGURED" in msg.upper() else "UNREACHABLE")
            )
            row["error"] = msg[:180]
        row["latency_ms"] = round((_t.monotonic() - t0) * 1000)
        rows.append(row)
    reachable = [r for r in rows if r["status"] == "REACHABLE"]
    data_venues = [r for r in rows if r["venue"] in ("binance_spot", "binance_futures")]
    if reachable or any(r["status"] == "REACHABLE" for r in data_venues):
        verdict = "Exchange APIs reachable — data should flow. Check credentials/keys if trading still fails."
    elif data_venues and all(r["status"] == "GEO_BLOCKED" for r in data_venues):
        verdict = (
            "Exchange APIs answer HTTP 451 — your network/region is geo-blocked by the exchange. "
            "Trading cannot run from this network (ZEPAY will not fake data)."
        )
    else:
        verdict = (
            "This machine/network cannot reach ANY exchange API (connection blocked or dropped). "
            "This is an environment limitation, not an app bug — ZEPAY refuses to trade without "
            "real data. Run ZEPAY where exchange APIs are reachable (your own machine/VPS with "
            "open egress) and the same UI will trade."
        )
    z.audit(
        "system",
        "connectivity_check",
        {"reachable": len(reachable), "probed": len(rows)},
        actor="api",
    )
    return {"probes": rows, "reachable_count": len(reachable), "verdict": verdict}


# ---- ZEPAY license (§6: honest offline/cloud key verification) ----
@router.get("/license/status")
def license_status(request: Request) -> dict:
    z = zapp_of(request)
    return lic.license_status(z.config)


class LicenseVerifyBody(BaseModel):
    key: str
    mode: str | None = None  # None → auto (cloud if endpoint set, else offline)


@router.post("/license/verify", dependencies=[Depends(guard_mutate)])
def license_verify(body: LicenseVerifyBody, request: Request) -> dict:
    z = zapp_of(request)
    res = lic.activate_key(z.config, z.audit, body.key, mode=body.mode)
    return dict(res)


@router.post("/license/clear", dependencies=[Depends(guard_mutate)])
def license_clear(request: Request) -> dict:
    z = zapp_of(request)
    lic.clear_key(z.config, z.audit)
    return {"ok": True, "status": lic.license_status(z.config)}


class LicenseEndpointBody(BaseModel):
    endpoint: str


@router.post("/license/endpoint", dependencies=[Depends(guard_mutate)])
def license_endpoint(body: LicenseEndpointBody, request: Request) -> dict:
    z = zapp_of(request)
    ep = (body.endpoint or "").strip()
    if ep and not ep.startswith("https://"):
        raise HTTPException(400, "ZEPAY cloud endpoint must be an https:// URL")
    with z.config._lock:
        z.config.cfg["license_verify_endpoint"] = ep or None
        z.config.save_locked()
    z.audit("auth", "license_endpoint_set", {"endpoint": ep or "(cleared)"}, actor="api")
    return {"ok": True, "status": lic.license_status(z.config)}


# ---- kill switch (§29: engage is instant, resume needs typed phrase) ----
class KillBody(BaseModel):
    reason: str = ""
    cancel_orders: bool = True
    flatten: bool = False


@router.post("/kill-switch/engage", dependencies=[Depends(guard_mutate)])
def kill_engage(body: KillBody, request: Request) -> dict:
    return zapp_of(request).kill.engage(
        actor="api", reason=body.reason, cancel_orders=body.cancel_orders, flatten=body.flatten
    )


class ResumeBody(BaseModel):
    confirmation: str


@router.post("/kill-switch/resume", dependencies=[Depends(guard_mutate)])
def kill_resume(body: ResumeBody, request: Request) -> dict:
    return zapp_of(request).kill.resume(actor="api", confirmation=body.confirmation)


# ---- stage gate (§22: manual promotion with typed confirmation) ----
class StageBody(BaseModel):
    confirmation: str
    actor: str = "api"


@router.get("/stage")
def stage_status(request: Request) -> dict:
    return zapp_of(request).gate.status()


@router.post("/stage/promote", dependencies=[Depends(guard_mutate)])
def stage_promote(body: StageBody, request: Request) -> dict:
    return zapp_of(request).gate.promote(actor=body.actor, confirmation=body.confirmation)


class DemoteBody(BaseModel):
    reason: str = ""
    actor: str = "api"


@router.post("/stage/demote", dependencies=[Depends(guard_mutate)])
def stage_demote(body: DemoteBody, request: Request) -> dict:
    return zapp_of(request).gate.demote(actor=body.actor, reason=body.reason)


# ---- backups (§56) ----
@router.post("/backup/run", dependencies=[Depends(guard_mutate)])
def backup_run(request: Request) -> dict:
    return zapp_of(request).backups.run(label="api")


@router.get("/backup/list")
def backup_list(request: Request) -> dict:
    z = zapp_of(request)
    return {"backups": z.backups.list(), "restore": z.backups.restore_instructions()}


# ---- audit & alerts (§48: immutable trail) ----
@router.get("/audit")
def audit_log(request: Request, limit: int = 100) -> list:
    return zapp_of(request).auditlog.recent_audit(limit=limit)


@router.get("/system-events")
def system_events(request: Request, limit: int = 100) -> list:
    return zapp_of(request).auditlog.recent_events(limit=limit)


@router.get("/alerts")
def alerts(request: Request, limit: int = 50) -> list:
    return zapp_of(request).alerts.recent(limit=limit)


# ---- venues & credentials (§13/§52: withdrawal keys refused) ----
@router.get("/venues")
def venues(request: Request) -> dict:
    z = zapp_of(request)
    out = {}
    for vid, a in z.registry.all().items():
        try:
            out[vid] = {
                "status": a.status(),
                "capabilities": a.capabilities(),
                "health": a.health(),
            }
        except Exception as e:
            out[vid] = {"status": "ERROR", "error": str(e)}
    return out


class TestBody(BaseModel):
    venue: str


@router.post("/venues/test", dependencies=[Depends(guard_mutate)])
def venue_test(body: TestBody, request: Request) -> dict:
    z = zapp_of(request)
    a = z.registry.get(body.venue)
    if a is None:
        raise HTTPException(404, f"unknown venue {body.venue}")
    try:
        return a.test_connection()
    except Exception as e:
        return {"connected": False, "venue": body.venue, "error": str(e)}


class CredentialsBody(BaseModel):
    venue: str
    api_key: str
    api_secret: str


@router.post("/venues/credentials", dependencies=[Depends(guard_mutate)])
def venue_credentials(body: CredentialsBody, request: Request) -> dict:
    z = zapp_of(request)
    a = z.registry.get(body.venue)
    if a is None or not hasattr(a, "set_credentials"):
        raise HTTPException(400, f"venue {body.venue} does not accept API credentials")
    a.set_credentials(body.api_key, body.api_secret)
    return {
        "ok": True,
        "venue": body.venue,
        "note": "credentials encrypted at rest; withdrawal permission is "
        "verified OFF at connection test",
    }


class VenueEnableBody(BaseModel):
    confirm: str  # typed phrase: "ENABLE <VENUE_ID>"


@router.post("/venues/{venue}/enable", dependencies=[Depends(guard_mutate)])
def venue_enable(venue: str, body: VenueEnableBody, request: Request) -> dict:
    """Explicit, typed-confirmation venue activation (futures requires this)."""
    z = zapp_of(request)
    a = z.registry.get(venue)
    if a is None:
        raise HTTPException(404, f"unknown venue {venue}")
    if body.confirm.strip().upper() != f"ENABLE {venue.upper()}":
        raise HTTPException(400, f'Type the exact phrase "ENABLE {venue.upper()}" to confirm.')
    with z.config._lock:
        flags = dict(z.config.cfg.get("venues_enabled") or {})
        flags[venue] = True
        z.config.cfg["venues_enabled"] = flags
        z.config.save_locked()
    z.audit("exchange", "venue_enabled", {"venue": venue}, actor="api")
    if z.alerts:
        z.alerts.notify(
            "WARN",
            f"Venue {venue} enabled",
            "The venue is now eligible for routing. Live trading still requires "
            "the stage gate; risk limits remain authoritative.",
        )
    return {
        "ok": True,
        "venues_enabled": flags,
        "note": "venue routing enabled — live orders still require stage gate + credentials",
    }


@router.post("/venues/{venue}/disable", dependencies=[Depends(guard_mutate)])
def venue_disable(venue: str, request: Request) -> dict:
    z = zapp_of(request)
    with z.config._lock:
        flags = dict(z.config.cfg.get("venues_enabled") or {})
        flags[venue] = False
        z.config.cfg["venues_enabled"] = flags
        z.config.save_locked()
    z.audit("exchange", "venue_disabled", {"venue": venue}, actor="api")
    return {"ok": True, "venues_enabled": flags}


@router.delete("/venues/credentials/{venue}", dependencies=[Depends(guard_mutate)])
def venue_credentials_clear(venue: str, request: Request) -> dict:
    z = zapp_of(request)
    a = z.registry.get(venue)
    if a is None or not hasattr(a, "clear_credentials"):
        raise HTTPException(400, f"venue {venue} has no credentials to clear")
    return a.clear_credentials()


# ---- SSE live event stream (§55 bus → browser) ----
@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    z = zapp_of(request)
    q: queue.Queue = queue.Queue(maxsize=200)

    def handler(ev) -> None:
        try:
            q.put_nowait(
                {"event": ev.name, "payload": ev.payload, "ts": ev.ts, "source": ev.source}
            )
        except queue.Full:
            try:
                q.get_nowait()
                q.put_nowait(
                    {"event": ev.name, "payload": ev.payload, "ts": ev.ts, "source": ev.source}
                )
            except Exception:
                pass

    for name in SSE_EVENTS:
        BUS.subscribe(name, handler)

    async def gen():
        yield f"data: {json.dumps({'event': 'connected', 'payload': {'stage': z.config.get('operational_stage')}})}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = q.get(timeout=1.0)
                    yield f"data: {json.dumps(item, default=str)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            for name in SSE_EVENTS:
                BUS.unsubscribe(name, handler)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
