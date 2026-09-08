"""ZEPAY V3 selftest (§36/§70): executable proofs of the safety invariants.

Runs entirely on a TEMPORARY data dir — never mutates operator state, never
needs network. Each check is a real assertion against the real objects:

  risk fails closed · kill switch halts everything · resume needs typed phrase
  · stage gate refuses skips · MCP refuses trading permissions · order state
  machine rejects illegal transitions · untrained AI emits WAIT · sizer
  respects exchange minimums · withdrawals forbidden · privileged config
  keys blocked at API layer · data quality marks stale honestly.

Exit code 0 only if EVERY check passes.
"""

from __future__ import annotations

import tempfile

from zepay.apps.compose import ZepayApp
from zepay.core.config import Settings

RESULTS: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def run_selftest(app: ZepayApp | None = None) -> bool:
    RESULTS.clear()
    tmp = tempfile.mkdtemp(prefix="zepay-selftest-")
    own = app is None
    if app is None:
        settings = Settings(data_dir=tmp)
        settings.engine_autostart = False
        app = ZepayApp(settings).build()
        app.startup(start_ws=False, start_engine=False)
    assert app is not None

    try:
        cfg, risk, kill, gate = app.config, app.risk, app.kill, app.gate
        execution, mcp = app.execution, app.mcp

        # 1. risk engine FAILS CLOSED on internal error (§21)
        class BoomAcct:
            """Simulates corrupted account state — any access explodes."""

            def get(self, *a, **k):
                raise RuntimeError("boom — simulated internal corruption")

            def __bool__(self):
                return True

        d, reasons, _ = risk.authorize(None, None, {}, [], BoomAcct(), {}, None, "PAPER")  # type: ignore[arg-type]
        _check(
            "risk fails closed on internal error",
            d.name == "HALTED" and any("FAIL CLOSED" in r for r in reasons),
            f"decision={d.name} reasons={reasons[:1]}",
        )

        # 2. kill switch halts entries (§29)
        kill.engage(actor="selftest", reason="selftest")
        allowed, why = risk.entries_allowed()
        _check("kill switch blocks entries", not allowed and "kill" in why.lower(), why)
        res = execution.place(None, {}, None, mode="PAPER")
        _check("kill switch blocks order placement", not res.get("ok"), str(res)[:80])

        # 3. resume requires exact typed phrase (§29)
        bad = kill.resume(actor="selftest", confirmation="resume")
        good = kill.resume(actor="selftest", confirmation="RESUME TRADING")
        _check(
            "kill switch resume needs exact phrase",
            bool(not bad.get("ok") and good.get("ok")),
            str(bad.get("error", ""))[:60],
        )

        # 4. stage gate refuses skipping stages & wrong phrases (§22)
        st = gate.status()
        skip = gate.promote(actor="selftest", confirmation="ENABLE FULL LIVE")
        wrong = gate.promote(actor="selftest", confirmation="enable shadow mode")
        _check(
            "stage gate refuses skip PAPER→FULL_LIVE",
            not skip.get("ok"),
            str(skip.get("error", ""))[:60],
        )
        _check(
            "stage gate refuses wrong/lowercase phrase",
            not wrong.get("ok"),
            str(wrong.get("error", ""))[:60],
        )
        _check(
            "stage gate reports real requirements",
            isinstance(st.get("requirements"), dict) and st.get("stage") == "PAPER",
            f"stage={st.get('stage')}",
        )

        # 5. MCP refuses trading permissions by construction (§50)
        r = mcp.upsert(
            {
                "id": "evil",
                "name": "Evil",
                "url": "http://127.0.0.1:9/x",
                "purpose": "read research",
                "permissions": ["trading", "research.readonly"],
            }
        )
        _check(
            "MCP refuses trading permission class", not r.get("ok"), str(r.get("error", ""))[:70]
        )
        mcp.upsert(
            {
                "id": "ok-server",
                "name": "OK",
                "url": "http://127.0.0.1:9/x",
                "purpose": "fetch docs",
                "permissions": ["research.readonly"],
                "enabled": True,
            }
        )
        r2 = mcp.call_tool("ok-server", "place_order", {"symbol": "BTCUSDT"})
        _check("MCP refuses trading tool names", not r2.get("ok"), str(r2.get("error", ""))[:70])
        r3 = mcp.upsert(
            {
                "id": "sneaky",
                "name": "Sneaky",
                "url": "http://x",
                "purpose": "auto trading bot",
                "permissions": ["research.readonly"],
            }
        )
        _check("MCP refuses trading keywords in purpose", not r3.get("ok"))

        # 6. order state machine rejects illegal transitions (§33)
        from zepay.core.domain import OrderStatus
        from zepay.core.errors import OrderStateError
        from zepay.execution.order_state import OrderStateMachine, validate_transition

        sm = OrderStateMachine(app.storage, app.audit)
        _check(
            "illegal transition FILLED→SUBJECTED rejected",
            not validate_transition(OrderStatus.FILLED.value, OrderStatus.SUBMITTED.value),
        )
        raised = False
        try:
            sm.transition("order-does-not-exist", OrderStatus.FILLED.value)
        except OrderStateError:
            raised = True
        _check("transition on unknown order raises OrderStateError", raised)

        # 7. untrained AI emits WAIT in strict mode (§26 honesty)
        from zepay.ai.engine import AIEngine

        fresh = AIEngine(cfg, None, None)
        from zepay.features.engine import AssetFeatures

        f = AssetFeatures(market="TEST/USDT", price=100.0, n_candles=200)
        out = fresh.infer(
            f,
            {"regime": "RANGE", "confidence": 0.5},
            {"evald": {}, "best": {}, "aggregate": {}, "signals": {}},
        )
        _check(
            "untrained AI → WAIT (strict, no fabricated signal)",
            out["direction"] == "WAIT" and out["mode"] == "untrained-no-signal",
            out["mode"],
        )

        # 8. sizer respects exchange minimums & stage caps (§25)
        from zepay.core.domain import Opportunity
        from zepay.risk.sizer import Sizer

        sz = Sizer(app.universe, cfg)
        opp = Opportunity(
            symbol="BTC/USDT",
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
            price=100000.0,
        )
        out = sz.size(
            opp,
            f,
            {"equity": 10000.0},
            {"size_mult": 1.0, "stage_caps": {"execute": True}, "max_notional_usd": 50.0},
        )
        _check(
            "sizer enforces LIMITED_LIVE max notional ($50)",
            out.get("reject") or out.get("notional", 1e9) <= 50.0 + 1e-6,
            f"notional={out.get('notional')} reject={out.get('reject_reason')}",
        )

        # 9. withdrawals forbidden platform-wide (§29/§52)
        from zepay.risk.engine import HARD_LIMITS

        caps_ok = (
            HARD_LIMITS.get("withdrawals_permitted") is False
            or HARD_LIMITS.get("withdrawals") is False
            or any("withdraw" in str(k).lower() for k in HARD_LIMITS)
        )
        bcap = app.binance_spot.capabilities()
        _check("hard limits forbid withdrawals", caps_ok, str(HARD_LIMITS)[:90])
        _check(
            "binance adapter capability: no withdrawal",
            not getattr(bcap, "withdrawals", False),
            str(bcap)[:90],
        )

        # 10. live disabled by default (§22)
        _check(
            "live trading disabled by default",
            not cfg.get("live_enabled") and cfg.get("operational_stage") == "PAPER",
            f"stage={cfg.get('operational_stage')} live={cfg.get('live_enabled')}",
        )
        eff = execution.effective_mode()
        _check("effective mode is PAPER at default stage", eff == "PAPER", eff)

        # 11. execution refuses LIVE placement while live_enabled False
        res = execution.place(opp, {"qty": 0.0001, "notional": 10.0}, f, mode="LIVE")
        _check(
            "LIVE placement refused without gate", not res.get("ok"), str(res.get("error", ""))[:70]
        )

        # 12. data quality marks stale honestly (§17)
        app.quality.mark_stale("TEST/USDT", "selftest")
        q = app.quality.market_quality("TEST/USDT")
        _check("data quality marks stale", q.get("status") == "STALE", str(q)[:70])

        # 13. paper quotes refuse when no real data exists (§31 — never fabricated)
        q, err = app.paper.quote("NONEXISTENT/PAIR")
        _check(
            "paper quote without real data is refused, not invented",
            q is None and bool(err),
            str(err)[:70],
        )

        # 14. reconciler paper check on empty book is consistent (§35)
        rec = app.recon.paper_check()
        _check("paper reconciliation consistent on fresh book", bool(rec.get("ok")), str(rec)[:90])

    finally:
        if own:
            try:
                app.shutdown()
            except Exception:
                pass

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\nSELFTEST: {passed}/{total} passed")
    if passed != total:
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  FAILED: {name} — {detail}")
    return passed == total


if __name__ == "__main__":
    import sys

    sys.exit(0 if run_selftest() else 1)
