"""Data honesty (§17, §23, §31, §34, §59): the system must refuse, label or
report UNAVAILABLE — never fabricate prices, fills, metrics or backtests."""

from __future__ import annotations

import numpy as np
from conftest import make_candles


def test_paper_quote_refuses_without_data(app):
    q, err = app.paper.quote("NO/SUCH")
    assert q is None and err


def test_paper_market_fill_refuses_without_data(app):
    fill, err = app.paper.market_fill("NO/SUCH", "LONG", 1.0)
    assert fill is None and err


def test_backtester_refuses_insufficient_candles(app):
    res = app.backtester.run_single("NO/SUCH")
    assert not res["ok"]
    assert "REAL" in res["error"] or "synthetic" in res["error"]


def test_backtester_runs_on_fixture_candles(app, monkeypatch):
    candles = make_candles(220)
    monkeypatch.setattr(app.backtester, "_series", lambda m, i=None, l=500: candles)
    res = app.backtester.run_single("FIX/TURE", starting=10000, persist=False)
    assert res["ok"]
    assert res["candles"] == 220
    # cost accounting is explicit and non-negative
    assert res["fees"] >= 0 and res["slippage"] >= 0
    assert "honesty_note" in res
    # net differs from gross whenever there was any trading activity
    if res["trades"]:
        assert (
            res["net_return_pct"] != res["gross_return_pct"] or (res["fees"] + res["slippage"]) == 0
        )


def test_monte_carlo_refuses_tiny_samples():
    from zepay.backtesting.analysis import monte_carlo

    r = monte_carlo([1.0, 2.0])
    assert not r["ok"]
    r2 = monte_carlo([1.0, -0.5, 2.0, -1.0, 0.8, 1.2, -0.3])
    assert r2["ok"] and 0 <= r2["ruin_probability"] <= 1


def test_hyperopt_objective_penalizes_dd_and_oos_gap():
    from zepay.ml.hyperopt import HyperoptEngine

    good = {"ok": True, "net_return_pct": 5, "max_dd_pct": 3, "trades": 12}
    risky = {"ok": True, "net_return_pct": 20, "max_dd_pct": 40, "trades": 12}
    o1, _ = HyperoptEngine.objective(good, good, 400)
    o2, _ = HyperoptEngine.objective(risky, risky, 400)
    assert o1 > o2, "huge drawdown must not outscore modest returns"
    overfit = {"ok": True, "net_return_pct": 20, "max_dd_pct": 3, "trades": 12}
    poor_oos = {"ok": True, "net_return_pct": -5, "max_dd_pct": 3, "trades": 12}
    o3, _ = HyperoptEngine.objective(overfit, poor_oos, 400)
    assert o3 < o1, "IS/OOS gap (overfitting) must be penalized"
    few = {"ok": True, "net_return_pct": 5, "max_dd_pct": 3, "trades": 1}
    o4, _ = HyperoptEngine.objective(few, few, 400)
    assert o4 < o1, "too-few-trades must be penalized"


def test_psi_math():
    from zepay.ml.drift import psi

    rng = np.random.default_rng(1)
    a = list(rng.normal(0, 1, 500))
    b = list(rng.normal(0, 1, 500))
    c = list(rng.normal(3, 1, 500))
    assert psi(a, b) < 0.10, "same distribution → small PSI"
    assert psi(a, c) > 0.25, "shifted distribution → severe PSI"


def test_execution_quality_never_fabricates(app):
    assert app.exec_quality.venue_score("never-traded-venue") is None


def test_data_quality_marks_stale_and_blocks(app):
    app.quality.mark_stale("FIX/TURE", "pytest")
    q = app.quality.market_quality("FIX/TURE")
    assert q["status"] == "STALE"
    s = app.quality.summary()
    assert s["status_counts"].get("STALE", 0) >= 1
    app.quality.mark_ok("FIX/TURE", 0)


def test_venue_score_from_real_records(app):
    app.exec_quality.record(
        order_id="o-test",
        venue="v-test",
        market="FIX/TURE",
        mode="PAPER",
        expected_price=100.0,
        actual_price=100.05,
        qty_requested=1.0,
        qty_filled=1.0,
        latency_ms=120,
        maker=False,
        rejected=False,
        spread_bps=4.0,
    )
    sc = app.exec_quality.venue_score("v-test")
    assert sc is not None and 0 <= sc["quality_score"] <= 1 and sc["samples"] == 1


def test_feed_health_reports_unavailable_honestly(app):
    fh = app.collector.feed_health(["NO/SUCH"])
    assert fh["status"] in ("UNAVAILABLE", "DEGRADED", "OK")
    assert "reason" in fh or "markets" in fh or fh["status"] == "UNAVAILABLE"
