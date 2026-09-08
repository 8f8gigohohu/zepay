"""ZEPAY V3 server entrypoint: FastAPI + bundled frontend + engine loop.

Run:  python -m zepay.apps.server
Env:  ZEPAY_HOST / ZEPAY_PORT / ZEPAY_DATA_DIR / ZEPAY_AUTOSTART / ZEPAY_DB_BACKEND
"""

from __future__ import annotations

import logging
import os
import sys

from zepay.api.app import make_app
from zepay.apps.compose import ZepayApp
from zepay.core.config import Settings

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND_DIR = os.path.join(REPO_ROOT, "frontend")


def setup_logging(settings: Settings) -> None:
    level = getattr(logging, str(settings.log_level).upper(), logging.INFO)
    fmt = (
        "%(asctime)s %(levelname)s %(name)s %(message)s"
        if not settings.log_json
        else '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s",'
        '"msg":"%(message)s"}'
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(settings.log_path))
    except Exception:
        pass
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)


def build_server(settings: Settings | None = None, start_engine: bool | None = None):
    from contextlib import asynccontextmanager

    settings = settings or Settings()
    setup_logging(settings)
    zapp = ZepayApp(settings).build()
    out = zapp.startup(start_ws=True, start_engine=start_engine)
    logging.getLogger("zepay.server").info("startup: %s", out)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        zapp.shutdown()

    fast = make_app(zapp, lifespan=lifespan)
    if os.path.isdir(FRONTEND_DIR):
        from fastapi.staticfiles import StaticFiles

        fast.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return fast, zapp


def main() -> None:
    import uvicorn

    settings = Settings()
    fast, _zapp = build_server(settings)
    uvicorn.run(fast, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
