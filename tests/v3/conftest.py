"""Shared fixtures. All offline tests run on a temp data dir with no network;
live-network tests are gated behind ZEPAY_LIVE_TESTS=1.

Synthetic candles used here are FIXTURES FOR MECHANICS TESTING (fill walking,
state machines, record shapes). They are never presented as market results.
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("ZEPAY_AUTOSTART", "0")
os.environ.setdefault("ZEPAY_LOG_LEVEL", "ERROR")

LIVE = os.environ.get("ZEPAY_LIVE_TESTS") == "1"


@pytest.fixture(scope="session")
def app():
    from zepay.apps.compose import ZepayApp
    from zepay.core.config import Settings

    tmp = tempfile.mkdtemp(prefix="zepay-test-")
    settings = Settings(data_dir=tmp)
    settings.engine_autostart = False
    zapp = ZepayApp(settings).build()
    zapp.startup(start_ws=False, start_engine=False)
    yield zapp
    zapp.shutdown()


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    from zepay.api.app import make_app

    return TestClient(make_app(app))


def make_candles(n: int = 220, start: float = 100.0, seed: int = 7) -> list[dict]:
    """Deterministic fixture candles (mechanics only — labeled synthetic)."""
    import math
    import random

    rng = random.Random(seed)
    out = []
    px = start
    t0 = 1_700_000_000_000
    for i in range(n):
        drift = math.sin(i / 9.0) * 0.4 + rng.gauss(0, 0.35)
        o = px
        c = max(1.0, px + drift)
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.004)))
        l = min(o, c) * (1 - abs(rng.gauss(0, 0.004)))
        v = 1000 + rng.random() * 500
        out.append(
            {
                "open_time": t0 + i * 3_600_000,
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v,
                "quote_vol": v * c,
                "trades": 100 + i,
                "interval": "1h",
                "closed": True,
            }
        )
        px = c
    return out


def make_book(mid: float = 100.0, levels: int = 8, step: float = 0.02, qty: float = 5.0) -> dict:
    return {
        "bids": [[mid - step * (i + 1), qty * (1 + i * 0.1)] for i in range(levels)],
        "asks": [[mid + step * (i + 1), qty * (1 + i * 0.1)] for i in range(levels)],
        "ts": 1_700_000_000_000,
    }
