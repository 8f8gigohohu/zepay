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
        "venues": z.registry.health_all(),
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
