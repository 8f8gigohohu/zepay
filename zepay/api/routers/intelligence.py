"""Intelligence router: AI models, registry lifecycle (manual promotion),
research pipeline, backtests, walk-forward, hyperopt, MCP tools."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from zepay.api.deps import guard_mutate, zapp_of

router = APIRouter(prefix="/api", tags=["intelligence"])


# ---- AI engine ----
@router.get("/ai/status")
def ai_status(request: Request) -> dict:
    z = zapp_of(request)
    champ = z.models.champion("quant-ensemble")
    return {
        "trained": z.ai.trained,
        "degraded": z.ai.degraded,
        "degraded_reason": z.ai.degraded_reason,
        "feature_version": z.ai.feature_version,
        "metrics": z.ai.metrics,
        "importances": z.ai.importances() if z.ai.trained else {},
        "strict_mode": bool(z.config.get("strict_ai_mode")),
        "ensemble_mode": z.config.get("ensemble_mode"),
        "champion": (
            {
                k: champ.get(k)
                for k in ("id", "name", "version", "lifecycle", "drift_status", "metrics")
            }
            if champ
            else None
        ),
        "drift": z.drift.last_report(),
        "llm": z.llm.status() if hasattr(z.llm, "status") else {"configured": False},
    }


class TrainBody(BaseModel):
    markets: list[str] | None = None
    horizon: int = 3


@router.post("/ai/train", dependencies=[Depends(guard_mutate)])
def ai_train(body: TrainBody, request: Request) -> dict:
    z = zapp_of(request)
    markets = body.markets or z.engine.active_markets(limit=10)
    z.engine.refresh_data(markets)  # real data first — never train on nothing
    res = z.ai.train(z.collector, markets, horizon=body.horizon)
    z.models.ensure_champion_from_production()
    try:
        z.drift.snapshot_training_distribution(z.collector, markets)
    except Exception:
        pass
    return res


# ---- model registry (§26/§27: promotion is manual, evidenced) ----
@router.get("/models")
def models(request: Request, lifecycle: str | None = None) -> dict:
    z = zapp_of(request)
    return {
        "models": z.models.list_all(lifecycle=lifecycle),
        "champion": z.models.champion("quant-ensemble"),
        "challengers": z.models.challengers(),
    }


class PromoteBody(BaseModel):
    actor: str
    evidence: dict


@router.post("/models/{model_id}/promote", dependencies=[Depends(guard_mutate)])
def model_promote(model_id: str, body: PromoteBody, request: Request) -> dict:
    return zapp_of(request).models.promote_champion(model_id, body.actor, body.evidence)


class TransitionBody(BaseModel):
    to_lifecycle: str
    actor: str = "api"
    evidence: str = ""


@router.post("/models/{model_id}/transition", dependencies=[Depends(guard_mutate)])
def model_transition(model_id: str, body: TransitionBody, request: Request) -> dict:
    return zapp_of(request).models.transition(
        model_id, body.to_lifecycle, actor=body.actor, evidence=body.evidence
    )


@router.get("/models/compare")
def model_compare(request: Request, a: str, b: str) -> dict:
    return zapp_of(request).models.compare(a, b)


# ---- experiments & research (§24/§25/§28) ----
@router.get("/experiments")
def experiments(request: Request, limit: int = 30) -> list:
    z = zapp_of(request)
    return z.storage.query(
        "SELECT id, kind, name, status, conclusion, created_at FROM experiments"
        " ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )


class ResearchBody(BaseModel):
    horizon: int = 3
    l2: list[float] = [0.01, 0.05]


@router.post("/research/run", dependencies=[Depends(guard_mutate)])
def research_run(body: ResearchBody, request: Request) -> dict:
    z = zapp_of(request)
    markets = z.engine.active_markets(limit=6)
    z.engine.refresh_data(markets)
    hyp = {
        "name": "api-research",
        "description": "operator-triggered research cycle",
        "params": {"l2": body.l2, "horizon": body.horizon},
    }
    return z.research.run(hyp)


class WFBody(BaseModel):
    markets: list[str] | None = None
    n_folds: int = 4
    horizon: int = 3


@router.post("/research/walk-forward", dependencies=[Depends(guard_mutate)])
def walk_forward(body: WFBody, request: Request) -> dict:
    z = zapp_of(request)
    markets = body.markets or z.engine.active_markets(limit=6)
    z.engine.refresh_data(markets)
    return z.walk_forward.run(z.collector, markets, n_folds=body.n_folds, horizon=body.horizon)


@router.get("/research/walk-forward/history")
def wf_history(request: Request) -> list:
    return zapp_of(request).walk_forward.history()


class HyperoptBody(BaseModel):
    market: str
    method: str = "random"  # grid | random | bayes | evolutionary
    n_trials: int = 12


@router.post("/research/hyperopt", dependencies=[Depends(guard_mutate)])
def hyperopt(body: HyperoptBody, request: Request) -> dict:
    z = zapp_of(request)
    if body.method not in ("grid", "random", "bayes", "evolutionary"):
        raise HTTPException(400, "method must be grid|random|bayes|evolutionary")
    z.engine.refresh_data([body.market])
    return z.hyperopt.run(body.market, method=body.method, n_trials=body.n_trials)


# ---- backtesting (§23) ----
class BacktestBody(BaseModel):
    market: str | None = None
    markets: list[str] | None = None
    starting: float = 10000.0
    interval: str | None = None
    params: dict | None = None


@router.post("/backtest/run", dependencies=[Depends(guard_mutate)])
def backtest_run(body: BacktestBody, request: Request) -> dict:
    z = zapp_of(request)
    if body.markets:
        z.engine.refresh_data(body.markets)
        return z.backtester.run_multi(
            body.markets, starting=body.starting, interval=body.interval, params=body.params
        )
    if not body.market:
        raise HTTPException(400, "provide market or markets")
    z.engine.refresh_data([body.market])
    return z.backtester.run_single(
        body.market, starting=body.starting, interval=body.interval, params=body.params
    )


@router.get("/backtest/history")
def backtest_history(request: Request, limit: int = 50) -> list:
    return zapp_of(request).backtester.history(limit=limit)


# ---- MCP (§50: research tools only, trading refused by construction) ----
@router.get("/mcp")
def mcp_registry(request: Request) -> dict:
    z = zapp_of(request)
    return {
        "servers": z.mcp.registry(),
        "permission_classes": __import__(
            "zepay.mcp.manager", fromlist=["MCP_PERMISSION_CLASSES"]
        ).MCP_PERMISSION_CLASSES,
        "forbidden_classes": __import__(
            "zepay.mcp.manager", fromlist=["MCP_FORBIDDEN_CLASSES"]
        ).MCP_FORBIDDEN_CLASSES,
    }


@router.post("/mcp/servers", dependencies=[Depends(guard_mutate)])
def mcp_upsert(body: dict, request: Request) -> dict:
    return zapp_of(request).mcp.upsert(body)


@router.delete("/mcp/servers/{sid}", dependencies=[Depends(guard_mutate)])
def mcp_remove(sid: str, request: Request) -> dict:
    return zapp_of(request).mcp.remove(sid)


class MCPToolBody(BaseModel):
    server: str
    tool: str
    args: dict = {}


@router.post("/mcp/call", dependencies=[Depends(guard_mutate)])
def mcp_call(body: MCPToolBody, request: Request) -> dict:
    return zapp_of(request).mcp.call_tool(body.server, body.tool, body.args)


@router.get("/mcp/calls")
def mcp_calls(request: Request, limit: int = 50) -> list:
    return zapp_of(request).mcp.recent_calls(limit=limit)


# ---- drift monitor (§59) ----
@router.post("/ai/drift-check", dependencies=[Depends(guard_mutate)])
def drift_check(request: Request) -> dict:
    z = zapp_of(request)
    markets = z.engine.active_markets(limit=6)
    return z.drift.tick(z.collector, markets)
