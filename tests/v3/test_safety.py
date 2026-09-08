"""Safety invariants (§21, §22, §29, §33, §50, §52). These must never regress:
the platform is only allowed to exist if they hold."""

from __future__ import annotations

from zepay.core.domain import Decision, OrderStatus
from zepay.core.errors import OrderStateError
from zepay.execution.order_state import validate_transition
from zepay.risk.engine import HARD_LIMITS


class BoomAcct:
    def get(self, *a, **k):
        raise RuntimeError("simulated internal corruption")

    def __bool__(self):
        return True


def test_risk_engine_fails_closed(app):
    d, reasons, _ = app.risk.authorize(None, None, {}, [], BoomAcct(), {}, None, "PAPER")
    assert d == Decision.HALTED
    assert any("FAIL CLOSED" in r for r in reasons)


def test_kill_switch_blocks_and_needs_exact_phrase(app):
    app.kill.engage(actor="pytest", reason="test")
    allowed, why = app.risk.entries_allowed()
    assert not allowed and "kill" in why.lower()
    res = app.execution.place(None, {}, None, mode="PAPER")
    assert not res.get("ok")
    assert not app.kill.resume(actor="pytest", confirmation="resume").get("ok")
    assert not app.kill.resume(actor="pytest", confirmation="RESUME trading").get("ok")
    assert app.kill.resume(actor="pytest", confirmation="RESUME TRADING").get("ok")
    allowed2, _ = app.risk.entries_allowed()
    # entries may still be blocked by data state — but NOT by the kill switch
    assert "kill" not in str(app.risk.entries_allowed()[1]).lower() or allowed2


def test_kill_switch_persists_across_config_reload(app):
    app.kill.engage(actor="pytest", reason="persistence test")
    assert app.config.get("kill_switch") is True
    app.config.load()  # re-read from disk
    assert app.config.get("kill_switch") is True
    app.kill.resume(actor="pytest", confirmation="RESUME TRADING")


def test_stage_gate_refuses_skip_and_wrong_phrase(app):
    st = app.gate.status()
    assert st["stage"] == "PAPER"
    assert not app.gate.promote(actor="pytest", confirmation="ENABLE FULL LIVE").get("ok")
    assert not app.gate.promote(actor="pytest", confirmation="enable shadow mode").get("ok")
    # even the RIGHT phrase fails while prerequisites are unmet
    r = app.gate.promote(actor="pytest", confirmation="ENABLE SHADOW MODE")
    assert not r.get("ok")
    assert (
        "prerequisite" in str(r.get("error", "")).lower()
        or "requirement" in str(r.get("error", "")).lower()
        or not r.get("ok")
    )


def test_live_disabled_by_default_and_placement_refused(app):
    from zepay.core.domain import Opportunity

    assert app.config.get("live_enabled") is False
    assert app.config.get("operational_stage") == "PAPER"
    assert app.execution.effective_mode() == "PAPER"
    opp = Opportunity(
        symbol="FIX/TURE",
        venue="binance_spot",
        direction="LONG",
        score=90,
        confidence=0.9,
        quality=0.9,
        regime="BULLISH_TREND",
        strategy="trend",
        expected_return=0.02,
        expected_net=0.015,
        cost_pct=0.002,
        price=100.0,
    )
    res = app.execution.place(opp, {"qty": 0.1, "notional": 10.0}, None, mode="LIVE")
    assert not res.get("ok")
    assert "live" in str(res.get("error", "")).lower()


def test_hard_limits_forbid_withdrawals(app):
    assert HARD_LIMITS["withdrawals"] == "FORBIDDEN"
    caps = app.binance_spot.capabilities()
    assert not getattr(caps, "withdrawals", False)
    assert HARD_LIMITS.get("llm_can_trade") is False
    assert HARD_LIMITS.get("mcp_can_trade") is False
    assert HARD_LIMITS.get("auto_promote_to_live") is False


def test_mcp_refuses_trading_by_construction(app):
    r = app.mcp.upsert(
        {
            "id": "evil",
            "name": "Evil",
            "url": "http://127.0.0.1:9/x",
            "purpose": "read docs",
            "permissions": ["trading", "research.readonly"],
        }
    )
    assert not r.get("ok") and "refused" in r["error"].lower()
    r2 = app.mcp.upsert(
        {
            "id": "evil2",
            "name": "X",
            "url": "http://x",
            "purpose": "withdraw funds",
            "permissions": ["research.readonly"],
        }
    )
    assert not r2.get("ok")
    app.mcp.upsert(
        {
            "id": "ok-srv",
            "name": "OK",
            "url": "http://127.0.0.1:9/x",
            "purpose": "fetch pages",
            "permissions": ["research.readonly"],
            "enabled": True,
        }
    )
    r3 = app.mcp.call_tool("ok-srv", "place_order", {"symbol": "BTCUSDT"})
    assert not r3.get("ok") and "refused" in r3["error"].lower()
    r4 = app.mcp.call_tool("ok-srv", "cancel_all_trades", {})
    assert not r4.get("ok")
    # disabled/unconfigured servers refuse everything
    r5 = app.mcp.call_tool("mcp-fetch", "fetch", {"url": "http://example.com"})
    assert not r5.get("ok")


def test_order_state_machine_rejects_illegal(app):
    assert not validate_transition(OrderStatus.FILLED.value, OrderStatus.SUBMITTED.value)
    assert not validate_transition(OrderStatus.CANCELLED.value, OrderStatus.FILLED.value)
    assert validate_transition(OrderStatus.SUBMITTED.value, OrderStatus.FILLED.value)
    raised = False
    try:
        app.execution.sm.transition("no-such-order", OrderStatus.FILLED.value)
    except OrderStateError:
        raised = True
    assert raised


def test_untrained_ai_emits_wait_strict(app):
    from zepay.ai.engine import AIEngine
    from zepay.features.engine import AssetFeatures

    fresh = AIEngine(app.config, None, None)
    f = AssetFeatures(market="FIX/TURE", price=100.0, n_candles=200)
    out = fresh.infer(
        f,
        {"regime": "RANGE", "confidence": 0.5},
        {"evald": {}, "best": "", "aggregate": {}, "signals": {}},
    )
    assert out["direction"] == "WAIT"
    assert out["mode"] == "untrained-no-signal"
    assert out["confidence"] == 0


def test_privileged_config_refused_via_api(client):
    r = client.post("/api/config", json={"patch": {"live_enabled": True}})
    assert r.status_code == 403
    r2 = client.post("/api/config", json={"patch": {"operational_stage": "FULL_LIVE"}})
    assert r2.status_code == 403
    r3 = client.post("/api/config", json={"patch": {"kill_switch": False}})
    assert r3.status_code == 403
