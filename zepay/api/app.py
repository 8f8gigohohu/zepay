"""FastAPI application factory (§49).

One app, mounted routers, CORS for the bundled frontend, optional bearer
token on mutating endpoints (config `api_token` — empty = local open, which
is documented as a limitation, not hidden).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from zepay.apps.compose import ZepayApp

log = logging.getLogger("zepay.api")


def make_app(zapp: ZepayApp, lifespan=None) -> FastAPI:
    fast = FastAPI(
        title="ZEPAY V3",
        version="3.0.0",
        description="Production-grade multi-asset AI-assisted trading "
        "platform. Real data only — unavailable states are "
        "reported honestly.",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    fast.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    fast.state.zapp = zapp

    from zepay.api.routers import intelligence, markets, system, trading

    for mod in (system, markets, trading, intelligence):
        fast.include_router(mod.router)

    @fast.get("/api/ping")
    def ping() -> dict:
        return {"ok": True, "service": "zepay-v3", "stage": zapp.config.get("operational_stage")}

    return fast
