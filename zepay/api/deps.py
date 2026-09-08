"""Shared FastAPI dependencies: app access + optional token guard."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from zepay.apps.compose import ZepayApp


def zapp_of(request: Request) -> ZepayApp:
    return request.app.state.zapp


def guard_mutate(request: Request, authorization: str | None = Header(default=None)) -> ZepayApp:
    """Every mutating endpoint depends on this. Enforces the optional bearer
    token (config `api_token`; empty = local-open, documented limitation)."""
    zapp = zapp_of(request)
    want = str(zapp.config.get("api_token") or "").strip()
    if want:
        got = (authorization or "").replace("Bearer ", "").strip()
        if got != want:
            raise HTTPException(status_code=401, detail="invalid or missing API token")
    return zapp
