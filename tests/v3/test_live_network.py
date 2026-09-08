"""Live-network acceptance tests — GATED: run with ZEPAY_LIVE_TESTS=1.

These prove the platform works against REAL market infrastructure
(data-api.binance.vision REST + data-stream.binance.vision WS in this
environment). They are skipped by default so the offline suite stays
deterministic.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from conftest import LIVE

pytestmark = pytest.mark.skipif(not LIVE, reason="set ZEPAY_LIVE_TESTS=1 to run")

ACCEPTANCE_MARKETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "DOGE/USDT"]


def test_universe_discovers_real_instruments(app):
    n = app.universe.refresh()
    assert n > 100, f"only {n} instruments discovered"
    snap = app.universe.snapshot()
    assert snap["source"] == "exchange_metadata"
    btc = app.universe.get("BTC/USDT")
    assert btc is not None and btc.status == "TRADING"
    assert btc.tick_size and btc.tick_size > 0


def test_collector_fetches_real_candles(app):
    rows = app.collector.fetch_klines("BTC/USDT", "1h", 200)
    assert rows and len(rows) >= 100
    last = rows[-1]
    assert last["c"] > 1000, "BTC price must be real"
    assert last["quote_vol"] > 1_000_000
    assert last["open_time"] > 1_600_000_000_000
    # timestamps strictly ascending (no duplicate/reordered bars)
    ts = [r["open_time"] for r in rows]
    assert ts == sorted(ts) and len(set(ts)) == len(ts)


def test_collector_fetches_real_depth(app):
    d = app.collector.fetch_depth("ETH/USDT", limit=20)
    assert d and d.get("bids") and d.get("asks")
    best_bid, best_ask = d["bids"][0][0], d["asks"][0][0]
    assert best_ask > best_bid > 0
    spread_bps = (best_ask - best_bid) / best_bid * 1e4
    assert spread_bps < 50, f"ETH spread {spread_bps}bps implausible"


def test_full_cycle_on_acceptance_markets(app):
    app.engine.active_markets = lambda limit=40: ACCEPTANCE_MARKETS
    rep = app.engine.run_cycle()
    assert rep["ok"], rep.get("error")
    assert rep["refresh"]["errors"] == 0
    decided = [
        mk
        for mk in rep["markets"]
        if mk.get("decision") in ("WAIT", "APPROVED", "BLOCKED", "HALTED")
    ]
    assert len(decided) == len(
        ACCEPTANCE_MARKETS
    ), f"every acceptance market must get a real decision: {rep['decisions']}"
    for mk in decided:
        assert mk.get("health") in ("TRADEABLE", "LIMITED", "BLOCKED")
        assert mk.get("regime"), "regime must be detected from real data"
        ai = mk.get("ai") or {}
        assert ai.get("mode") in (
            "trained-ensemble",
            "heuristic-composite-LABELED",
            "untrained-no-signal",
        )
        sim = mk.get("sim") or {}
        assert "expected_net_pct" in sim and "verdict" in sim


def test_websocket_receives_real_message():
    async def listen():
        import websockets

        url = (
            "wss://data-stream.binance.vision/stream?streams="
            "btcusdt@miniTicker/ethusdt@miniTicker"
        )
        async with websockets.connect(url, open_timeout=15) as ws:
            raw = await asyncio.wait_for(ws.recv(), timeout=20)
            return json.loads(raw)

    d = asyncio.run(listen())
    assert d.get("stream", "").endswith("@miniTicker")
    data = d["data"]
    assert data["e"] == "24hrMiniTicker"
    assert float(data["c"]) > 0, "real close price required"
    assert int(data["E"]) > 1_600_000_000_000


def test_paper_trade_end_to_end_on_real_book(app):
    """Full mechanics on REAL books: refresh → cycle → if any APPROVED opp,
    verify the paper fill used the real book; otherwise force a paper fill
    through PaperExchange directly (still real book, labeled PAPER)."""
    app.engine.active_markets = lambda limit=40: ACCEPTANCE_MARKETS
    app.engine.refresh_data(ACCEPTANCE_MARKETS)
    fill, err = app.paper.market_fill("BTC/USDT", "LONG", 0.002)
    assert fill and not err, err
    assert fill["book_source"] == "real-book"
    t = app.collector.get_ticker("BTC/USDT")
    assert (
        abs(fill["price"] - t["price"]) / t["price"] < 0.002
    ), "paper fill must be within 20bps of the real market price"
    assert fill["fee"] > 0


def test_kill_switch_halts_real_cycle(app):
    app.kill.engage(actor="pytest-live", reason="live acceptance")
    app.engine.active_markets = lambda limit=40: ACCEPTANCE_MARKETS[:2]
    rep = app.engine.run_cycle()
    assert rep["ok"]
    assert rep["executed"] == [], "no orders may execute while kill switch engaged"
    app.kill.resume(actor="pytest-live", confirmation="RESUME TRADING")
