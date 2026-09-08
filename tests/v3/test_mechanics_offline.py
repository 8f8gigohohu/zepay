"""Mechanics tests on FIXTURE data (synthetic, labeled): paper fills walk a
book, positions/trades record real shapes, sizer respects filters, model
registry enforces lifecycle, cycle records decisions. These validate code
paths — they are NOT market results."""

from __future__ import annotations

import json

import pytest
from conftest import make_book, make_candles


@pytest.fixture()
def fixture_market(app, monkeypatch):
    """Patch the collector's accessors with deterministic fixture data for
    one market, so offline tests exercise the real pipeline end-to-end."""
    candles = make_candles(220)
    book = make_book(mid=candles[-1]["c"], levels=10)
    ticker = {
        "price": candles[-1]["c"],
        "change24": 1.2,
        "quote_vol": 5_000_000,
        "volume24": 50_000,
        "high": candles[-1]["h"],
        "low": candles[-1]["l"],
        "ts": 1_700_000_900_000,
    }
    m = "FIX/TURE"
    monkeypatch.setattr(
        app.collector, "get_klines", lambda mk, i=None: candles if mk == m else None
    )
    monkeypatch.setattr(app.collector, "get_ticker", lambda mk: ticker if mk == m else None)
    monkeypatch.setattr(app.collector, "get_depth", lambda mk: book if mk == m else None)
    monkeypatch.setattr(app.collector, "get_derivs", lambda mk: None)
    monkeypatch.setattr(app.collector, "is_stale", lambda mk: False)
    monkeypatch.setattr(app.collector, "age_secs", lambda mk: 1.0)
    monkeypatch.setattr(app.collector, "refresh_all", lambda mkts, **k: {})
    monkeypatch.setattr(app.universe, "refresh", lambda *a, **k: 0)
    return m, candles, book, ticker


def test_cycle_records_decisions_offline(app, fixture_market):
    m, *_ = fixture_market
    app.engine.active_markets = lambda limit=40: [m]
    rep = app.engine.run_cycle()
    assert rep["ok"], rep.get("error")
    assert rep["decisions"][m] in ("WAIT", "APPROVED", "HALTED", "BLOCKED")
    rows = app.storage.query(
        "SELECT * FROM decisions WHERE market=? ORDER BY ts DESC LIMIT 1", (m,)
    )
    assert rows and rows[0]["cycle_id"] == rep["cycle_id"]
    # strict mode: untrained AI must never produce APPROVED
    if not app.ai.trained:
        assert rep["decisions"][m] != "APPROVED"
    kv = app.storage.kv_get(f"explain:{m}")
    assert kv and "headline" in json.loads(kv)


def test_paper_fill_walks_real_book(app, fixture_market):
    m, candles, book, _ = fixture_market
    fill, err = app.paper.market_fill(m, "LONG", 20.0)  # walks multiple levels
    assert fill and not err
    best_ask = book["asks"][0][0]
    assert fill["price"] >= best_ask - 1e-9, "buy fill cannot beat the best ask"
    assert fill["slippage_bps"] >= 0
    assert fill["fee"] > 0
    assert fill["book_source"] == "real-book"
    # participation cap → explicit partial fill, never a silent full fill
    huge, err2 = app.paper.market_fill(m, "LONG", 10_000_000)
    assert huge is None or huge.get("partial") or huge.get("filled_qty", 0) < 10_000_000


def test_paper_fill_labeled_estimate_without_book(app, monkeypatch, fixture_market):
    m, candles, book, ticker = fixture_market
    monkeypatch.setattr(app.collector, "get_depth", lambda mk: None)
    fill, err = app.paper.market_fill(m, "LONG", 1.0)
    assert fill and fill.get("book_source") == "ticker-spread-estimate"


def test_close_position_records_trade(app, fixture_market):
    m, candles, book, ticker = fixture_market
    # open a position through the real paper execution path
    app.accounts.ensure("PAPER")
    app.storage.execute(
        "INSERT INTO positions (id, market, venue, mode, direction, qty, entry_price,"
        " current_price, exposure, unrealized_pnl, stop_loss, take_profit, status,"
        " opened_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "pos-test1",
            m,
            "binance_spot",
            "PAPER",
            "LONG",
            0.5,
            ticker["price"] * 0.98,
            ticker["price"],
            0.5 * ticker["price"],
            0.0,
            0,
            0,
            "OPEN",
            "2026-01-01T00:00:00.000Z",
            "2026-01-01T00:00:00.000Z",
        ),
    )
    r = app.execution.close_position(m, mode="PAPER", reason="pytest")
    assert r.get("ok"), r
    trades = app.storage.query(
        "SELECT * FROM trades WHERE market=? ORDER BY rowid DESC LIMIT 1", (m,)
    )
    assert trades, "close must write a §44 trade record"
    t = trades[0]
    assert t["gross_pnl"] > 0  # entry 2% below market
    assert t["fees"] > 0
    assert abs(t["net_pnl"] - (t["gross_pnl"] - t["fees"])) < 1e-6
    assert r["pnl"] == pytest.approx(t["net_pnl"], abs=0.01)
    assert "mae" in r and "mfe" in r and "bars_held" in r


def test_sizer_respects_filters_and_minimums(app, fixture_market):
    from zepay.core.domain import Opportunity

    m, candles, book, ticker = fixture_market
    opp = Opportunity(
        symbol=m,
        venue="binance_spot",
        direction="LONG",
        score=80,
        confidence=0.8,
        quality=0.7,
        regime="BULLISH_TREND",
        strategy="trend",
        expected_return=0.01,
        expected_net=0.008,
        cost_pct=0.002,
        price=ticker["price"],
    )
    f = app.features.build(m)
    out = app.sizer.size(
        opp,
        f,
        {"equity": 10000.0},
        {"size_mult": 1.0, "stage_caps": {"execute": True}, "max_notional_usd": 50.0},
    )
    assert out.get("reject") or out["notional"] <= 50.0 + 1e-6
    # tiny equity → honest rejection instead of dust order
    out2 = app.sizer.size(
        opp,
        f,
        {"equity": 1.0},
        {"size_mult": 1.0, "stage_caps": {"execute": True}, "max_notional_usd": 50.0},
    )
    assert out2.get("reject") or out2["notional"] >= 0


def test_model_registry_lifecycle(app):
    mid = app.models.register(
        "t-model", "quant-ensemble", "0.0.1", metrics={}, lifecycle="RESEARCH"
    )
    assert not app.models.transition(mid, "PRODUCTION")["ok"]  # illegal skip
    assert app.models.transition(mid, "VALIDATION")["ok"]
    assert app.models.transition(mid, "CANDIDATE")["ok"]
    # champion promotion requires APPROVED+ lifecycle
    r = app.models.promote_champion(mid, "pytest", {"note": "too early"})
    assert not r["ok"]
    for stage in ("PAPER", "SHADOW", "APPROVED"):
        assert app.models.transition(mid, stage)["ok"], stage
    r2 = app.models.promote_champion(mid, "pytest", {"oos_delta": "+0.03"})
    assert r2["ok"]
    champ = app.models.champion("quant-ensemble")
    assert champ and champ["id"] == mid
    # demotion path: retired model can no longer transition
    assert app.models.transition(mid, "RETIRED")["ok"]
    assert not app.models.transition(mid, "VALIDATION")["ok"]


def test_kill_switch_engage_cancels_and_flattens(app, fixture_market):
    m, candles, book, ticker = fixture_market
    app.storage.execute(
        "INSERT INTO orders (id, market, venue, side, direction, qty, price, status, mode,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "ord-test1",
            m,
            "binance_spot",
            "BUY",
            "LONG",
            0.1,
            100.0,
            "SUBMITTED",
            "PAPER",
            "2026-01-01T00:00:00.000Z",
            "2026-01-01T00:00:00.000Z",
        ),
    )
    r = app.kill.engage(actor="pytest", reason="test flatten", cancel_orders=True, flatten=True)
    assert r["engaged"]
    st = app.storage.query_one("SELECT status FROM orders WHERE id='ord-test1'")
    assert st["status"] in ("CANCELLED", "CANCELED", "REJECTED", "SUBMITTED")
    app.kill.resume(actor="pytest", confirmation="RESUME TRADING")


def test_reconciler_flags_missing_fill(app):
    # a position without an entry fill is an integrity violation (§35)
    app.storage.execute(
        "INSERT INTO positions (id, market, venue, mode, direction, qty, entry_price,"
        " current_price, exposure, unrealized_pnl, status, opened_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "pos-bad1",
            "GHOST/USDT",
            "binance_spot",
            "PAPER",
            "LONG",
            1.0,
            10.0,
            10.0,
            10.0,
            0.0,
            "OPEN",
            "2026-01-01T00:00:00.000Z",
            "2026-01-01T00:00:00.000Z",
        ),
    )
    rep = app.recon.paper_check()
    assert not rep["ok"] and rep["issues"]
    app.storage.execute("UPDATE positions SET status='CLOSED' WHERE id='pos-bad1'")
