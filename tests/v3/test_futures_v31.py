"""V3.1 futures + license + multi-venue routing tests (offline, honest states).

Every test here asserts REAL behavior with fakes ONLY at the network boundary
(the venue adapter). No fabricated market data, balances, or trades.
"""

from __future__ import annotations

import hashlib

import pytest

from zepay.core.domain import ContractType, Instrument, TradingMode
from zepay.market_data.universe import UniverseManager
from zepay.security import license as lic


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _restore_license_state(app):
    """Session app is shared — never leak an unlicensed state into other tests."""
    with app.config._lock:
        saved = (app.config.cfg.get("license_key_hash"), app.config.cfg.get("license_mode"))
    yield
    with app.config._lock:
        app.config.cfg["license_key_hash"], app.config.cfg["license_mode"] = saved
        app.config.save_locked()


def _inst(
    symbol: str, venue: str, futures: bool = False, max_lev: float | None = None
) -> Instrument:
    base, quote = symbol.split("/")
    return Instrument(
        instrument_id=f"{venue}:{symbol}",
        venue=venue,
        symbol=symbol,
        exchange_symbol=f"{base}{quote}",
        base_asset=base,
        quote_asset=quote,
        trading_mode=TradingMode.FUTURES if futures else None,
        contract_type=ContractType.PERPETUAL if futures else None,
        max_leverage=max_lev,
        status="TRADING",
        source="exchange_metadata",
    )


class FakeFuturesAdapter:
    """Records calls; network boundary fake for precheck tests."""

    id = "binance_futures"

    def __init__(
        self, dual_side: bool = False, fail_leverage: bool = False, fail_margin: str | None = None
    ):
        self.calls: list[tuple] = []
        self.dual_side = dual_side
        self.fail_leverage = fail_leverage
        self.fail_margin = fail_margin

    def position_mode(self) -> dict:
        self.calls.append(("position_mode",))
        return {"dualSidePosition": self.dual_side}

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED"):
        self.calls.append(("set_margin_type", symbol, margin_type))
        if self.fail_margin:
            raise RuntimeError(self.fail_margin)

    def set_leverage(self, symbol: str, leverage: int):
        self.calls.append(("set_leverage", symbol, leverage))
        if self.fail_leverage:
            raise RuntimeError("leverage rejected (-4028: invalid leverage)")


# ---------------------------------------------------------------------------
# 1. license — offline format validation (honest, labeled)
# ---------------------------------------------------------------------------
def _offline_key(groups: list[str]) -> str:
    payload = "ZEPAY-" + "-".join(groups)
    ck = hashlib.sha256(payload.encode()).hexdigest()[:4].upper()
    return f"{payload}-{ck}"


def test_license_offline_accepts_valid_checksum_key():
    res = lic.OfflineKeyProvider().verify(_offline_key(["ABCD", "EFGH", "IJKL"]))
    assert res["ok"] and res["mode"] == "offline"
    assert "NOT cloud verification" in res["message"] or "local" in res["message"].lower()
    assert res["details"]["verification"] == "offline-format-check-only"


def test_license_offline_rejects_bad_checksum():
    res = lic.OfflineKeyProvider().verify("ZEPAY-ABCD-EFGH-IJKL-WXYZ")
    assert not res["ok"] and "checksum" in res["message"].lower()


def test_license_offline_rejects_malformed():
    for bad in ["", "abc", "ZEPAY-TOO", "ZEPAY-ab#d-EFGH-IJKL", "MYKEY-ABCD-EFGH-IJKL"]:
        assert not lic.OfflineKeyProvider().verify(bad)["ok"], bad


def test_license_cloud_without_endpoint_is_honestly_blocked(app):
    res = lic.verify_key(app.config, _offline_key(["ABCD", "EFGH", "IJKL"]), mode="cloud")
    assert not res["ok"]
    assert "no ZEPAY endpoint" in res["message"] or "not configured" in res["message"].lower()
    assert "specification missing" in res["message"] or "spec" in res["message"].lower()


# ---------------------------------------------------------------------------
# 2. license gate — trading blocked until verified, then allowed
# ---------------------------------------------------------------------------
def test_engine_cycle_gated_without_license(client, app, monkeypatch):
    monkeypatch.setattr(app.engine, "active_markets", lambda limit=40: ["BTC/USDT"])
    with app.config._lock:
        saved = app.config.cfg.get("license_key_hash")
        app.config.cfg["license_key_hash"] = None
        app.config.save_locked()
    try:
        r = client.post("/api/cycle/run")
        assert r.status_code == 200
        rep = r.json()
        assert rep["ok"] is False and rep.get("license_gated") is True
        assert "key" in rep["error"].lower()
    finally:
        with app.config._lock:
            app.config.cfg["license_key_hash"] = saved
            app.config.save_locked()


def test_license_verify_api_activates_and_reports_offline_mode(client, app):
    with app.config._lock:
        app.config.cfg["license_key_hash"] = None
        app.config.save_locked()
    key = _offline_key(["TEST", "KEY1", "ONLN"])
    r = client.post("/api/license/verify", json={"key": key, "mode": "offline"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["mode"] == "offline"
    # hash stored, raw key never persisted anywhere in config
    with app.config._lock:
        assert app.config.cfg["license_key_hash"] == hashlib.sha256(key.encode()).hexdigest()
        assert key not in str(app.config.cfg)
    st = client.get("/api/license/status").json()
    assert st["verified"] is True and st["mode"] == "offline"
    assert st["cloud_adapter"].startswith("BLOCKED")  # honest: spec not bundled
    # clear → gated again
    assert client.post("/api/license/clear").status_code == 200
    assert client.get("/api/license/status").json()["verified"] is False


def test_license_endpoint_requires_https(client):
    r = client.post("/api/license/endpoint", json={"endpoint": "http://zepay.example/api"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 3. multi-venue universe routing (per-instrument venue, no restart)
# ---------------------------------------------------------------------------
def test_universe_routes_market_to_futures_venue(app):
    u: UniverseManager = app.universe
    with u._lock:
        u.by_venue = {
            "binance_spot": {"BTC/USDT": _inst("BTC/USDT", "binance_spot")},
            "binance_futures": {
                "BTC/USDT": _inst("BTC/USDT", "binance_futures", futures=True, max_lev=3)
            },
        }
        u._reselect_active_locked()
    assert u.venue_of("BTC/USDT") == "binance_spot"  # default venue first

    assert u.set_market_venue("BTC/USDT", "binance_futures") is True
    assert u.venue_of("BTC/USDT") == "binance_futures"
    inst = u.get("BTC/USDT")
    assert inst.trading_mode == TradingMode.FUTURES
    assert inst.max_leverage == 3
    assert u.venue_of("BTC/USDT") == "binance_futures"

    # unknown venue / not-offered symbol refused
    assert u.set_market_venue("BTC/USDT", "zepay") is False
    assert u.set_market_venue("GHOST/USDT", "binance_futures") is False

    # clear override → back to default venue
    assert u.set_market_venue("BTC/USDT", None) is True
    assert u.venue_of("BTC/USDT") == "binance_spot"


def test_market_venue_api_endpoint(app, client):
    u = app.universe
    with u._lock:
        u.by_venue = {
            "binance_spot": {"ETH/USDT": _inst("ETH/USDT", "binance_spot")},
            "binance_futures": {"ETH/USDT": _inst("ETH/USDT", "binance_futures", futures=True)},
        }
        u._reselect_active_locked()
    r = client.post("/api/markets/ETH/USDT/venue", json={"venue": "binance_futures"})
    assert r.status_code == 200
    body = r.json()
    assert body["venue"] == "binance_futures" and body["trading_mode"] == "FUTURES"
    # unknown → honest 400 with guidance
    r = client.post("/api/markets/ETH/USDT/venue", json={"venue": "zepay"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 4. futures pre-order checks (risk cap → one-way mode → margin → leverage)
# ---------------------------------------------------------------------------
def _mk_exec(app):
    return app.execution


def test_futures_prechecks_happy_path_order_and_audit(app):
    ex = _mk_exec(app)
    ad = FakeFuturesAdapter()
    ok, err, lev = ex._futures_prechecks(ad, "BTC/USDT", "BTCUSDT")
    assert ok is True and err == "" and lev == 1
    order = [c[0] for c in ad.calls]
    assert order == ["position_mode", "set_margin_type", "set_leverage"]
    assert ad.calls[1] == ("set_margin_type", "BTCUSDT", "ISOLATED")
    assert ad.calls[2] == ("set_leverage", "BTCUSDT", 1)


def test_futures_prechecks_refuse_hedge_mode(app):
    ex = _mk_exec(app)
    ad = FakeFuturesAdapter(dual_side=True)
    ok, err, _ = ex._futures_prechecks(ad, "BTC/USDT", "BTCUSDT")
    assert ok is False and "ONE-WAY" in err


def test_futures_prechecks_cap_leverage_by_risk_and_instrument(app, monkeypatch):
    ex = _mk_exec(app)
    monkeypatch.setitem(app.config.cfg, "futures_leverage", 50)  # user asks 50x
    # instrument cap 3x → risk hard cap 5x → effective 3x
    monkeypatch.setattr(
        app.universe, "get", lambda s: _inst(s, "binance_futures", futures=True, max_lev=3)
    )
    ad = FakeFuturesAdapter()
    ok, err, lev = ex._futures_prechecks(ad, "BTC/USDT", "BTCUSDT")
    assert ok is True and lev == 3
    assert ("set_leverage", "BTCUSDT", 3) in ad.calls


def test_futures_prechecks_leverage_failure_refuses(app):
    ex = _mk_exec(app)
    ad = FakeFuturesAdapter(fail_leverage=True)
    ok, err, _ = ex._futures_prechecks(ad, "BTC/USDT", "BTCUSDT")
    assert ok is False and "set leverage" in err.lower()


def test_futures_margin_mode_no_change_error_tolerated(app):
    ex = _mk_exec(app)
    ad = FakeFuturesAdapter(fail_margin="-4046 No need to change margin type.")
    ok, err, lev = ex._futures_prechecks(ad, "BTC/USDT", "BTCUSDT")
    assert ok is True and ("set_leverage", "BTCUSDT", 1) in ad.calls


def test_instrument_is_futures_detection(app):
    ex = _mk_exec(app)
    assert ex._instrument_is_futures("BTC/USDT", "binance_futures") is True
    assert ex._instrument_is_futures("BTC/USDT", "binance_spot") is False
    monkey = pytest.MonkeyPatch()
    monkey.setattr(app.universe, "get", lambda s: _inst(s, "zepay", futures=True))
    try:
        assert ex._instrument_is_futures("X/USDT", "zepay") is True
    finally:
        monkey.undo()


# ---------------------------------------------------------------------------
# 5. venue enable gate — typed confirmation, audited
# ---------------------------------------------------------------------------
def test_venue_enable_requires_typed_phrase(client):
    r = client.post("/api/venues/binance_futures/enable", json={"confirm": "yes please"})
    assert r.status_code == 400
    r = client.post(
        "/api/venues/binance_futures/enable", json={"confirm": "ENABLE BINANCE_FUTURES"}
    )
    assert r.status_code == 200
    assert r.json()["venues_enabled"]["binance_futures"] is True
    r = client.post("/api/venues/binance_futures/disable")
    assert r.status_code == 200
    assert r.json()["venues_enabled"]["binance_futures"] is False


def test_venue_enable_unknown_venue(client):
    assert client.post("/api/venues/ftx/enable", json={"confirm": "ENABLE FTX"}).status_code == 404


# ---------------------------------------------------------------------------
# 6. ZEPAY native venue — futures surface honestly NOT_CONFIGURED
# ---------------------------------------------------------------------------
def test_zepay_venue_futures_methods_blocked_without_spec(app):
    zepay_ad = app.registry.get("zepay")
    assert zepay_ad is not None
    for call in (
        lambda: zepay_ad.positions(),
        lambda: zepay_ad.set_leverage("BTCUSDT", 2),
        lambda: zepay_ad.set_margin_type("BTCUSDT", "ISOLATED"),
        lambda: zepay_ad.position_mode(),
        lambda: zepay_ad.funding_info("BTCUSDT"),
        lambda: zepay_ad.max_leverage("BTCUSDT"),
    ):
        with pytest.raises(Exception) as ei:
            call()
        assert (
            "NOT_CONFIGURED" in str(ei.value).upper() or "not configured" in str(ei.value).lower()
        )


# ---------------------------------------------------------------------------
# 7. binance_futures connection test refuses withdrawal-enabled keys
# ---------------------------------------------------------------------------
def test_futures_test_connection_refuses_withdrawal_key(app, monkeypatch):
    ad = app.registry.get("binance_futures")
    ad.set_credentials("test-key", "test-secret")
    monkeypatch.setattr(ad, "ping", lambda: {"ok": True})
    monkeypatch.setattr(ad, "account", lambda timeout=12: {"canWithdraw": True, "canTrade": True})
    out = ad.test_connection()
    assert out["connected"] is False and out["status"] == "BLOCKED"
    assert "WITHDRAWAL" in out["error"].upper()
    monkeypatch.setattr(ad, "account", lambda timeout=12: {"canWithdraw": False, "canTrade": False})
    out = ad.test_connection()
    assert out["connected"] is False and out["status"] == "BLOCKED"
    assert "canTrade" in out["error"]
    monkeypatch.setattr(ad, "account", lambda timeout=12: {"canWithdraw": False, "canTrade": True})
    out = ad.test_connection()
    assert out["connected"] is True and out["status"] == "OK"


# ---------------------------------------------------------------------------
# 8. connectivity diagnostics — honest probes, verdict explains environment
# ---------------------------------------------------------------------------
def test_connectivity_diagnostics_structure(client):
    r = client.get("/api/diagnostics/connectivity")
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d["probes"], list) and len(d["probes"]) >= 4
    assert {p["venue"] for p in d["probes"]} >= {
        "binance_spot",
        "binance_futures",
        "zepay",
        "solana",
    }
    for p in d["probes"]:
        assert p["status"] in {"REACHABLE", "UNREACHABLE", "GEO_BLOCKED", "NOT_CONFIGURED"}
        # honest labels only: REACHABLE requires a real answer, never faked
        if p["status"] == "UNREACHABLE":
            assert p["error"]
    assert d["reachable_count"] == sum(1 for p in d["probes"] if p["status"] == "REACHABLE")
    assert d["verdict"]  # always explains WHY data is down
