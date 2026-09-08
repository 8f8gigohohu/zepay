"""API contract tests (offline): every endpoint answers with REAL state or an
honest empty/unavailable shape — never a 500, never invented numbers."""

from __future__ import annotations


def test_readonly_endpoints_200(client):
    for path in (
        "/api/ping",
        "/api/health",
        "/api/status",
        "/api/config",
        "/api/universe?limit=5",
        "/api/markets",
        "/api/quality",
        "/api/decisions?limit=5",
        "/api/orders?limit=5",
        "/api/positions",
        "/api/trades?limit=5",
        "/api/account?mode=PAPER",
        "/api/portfolio?mode=PAPER",
        "/api/risk",
        "/api/execution-quality",
        "/api/ai/status",
        "/api/models",
        "/api/experiments",
        "/api/stage",
        "/api/mcp",
        "/api/backup/list",
        "/api/audit?limit=5",
        "/api/alerts?limit=5",
        "/api/system-events?limit=5",
        "/api/venues",
    ):
        r = client.get(path)
        assert r.status_code == 200, f"{path} → {r.status_code}: {r.text[:120]}"


def test_risk_endpoint_exposes_hard_limits(client):
    r = client.get("/api/risk").json()
    assert "hard_limits" in r and "self_test" in r and "stage_caps" in r
    assert r["system_state"] in ("NORMAL", "CAUTION", "DEFENSIVE", "HALTED")


def test_mcp_registry_ships_disabled(client):
    r = client.get("/api/mcp").json()
    assert len(r["servers"]) >= 4
    for s in r["servers"]:
        if s["id"].startswith("mcp-"):
            assert s["enabled"] is False
            assert s["configured"] is False
            assert s["status"] == "DISABLED"
    assert "trading" in r["forbidden_classes"]


def test_config_public_view_masks_privileged(client):
    cfg = client.get("/api/config").json()
    assert cfg.get("license_key_hash") is None or "license_key_hash" not in cfg


def test_backup_run_sqlite_integrity(client):
    r = client.post("/api/backup/run").json()
    assert r["ok"] and r["backend"] == "sqlite" and r["integrity"] == "ok"
    lst = client.get("/api/backup/list").json()
    assert len(lst["backups"]) >= 1


def test_cycle_run_endpoint_offline(client, monkeypatch):
    # force the data layer empty → every market must honestly report NO_DATA
    zapp = client.app.state.zapp
    monkeypatch.setattr(zapp.engine, "active_markets", lambda limit=40: ["GHOST/USDT"])
    monkeypatch.setattr(zapp.collector, "refresh_all", lambda mkts, **k: {})
    monkeypatch.setattr(zapp.collector, "get_klines", lambda m, i=None: None)
    monkeypatch.setattr(zapp.universe, "refresh", lambda *a, **k: 0)
    r = client.post("/api/cycle/run")
    assert r.status_code == 200
    rep = r.json()
    assert rep["ok"] and rep["mode"] == "PAPER"
    assert rep["markets"][0]["decision"] == "NO_DATA"


def test_train_endpoint_honest_without_data(client, monkeypatch):
    # no data → train must report insufficient_data, not fake metrics
    zapp = client.app.state.zapp
    monkeypatch.setattr(zapp.engine, "active_markets", lambda limit=40: ["GHOST/USDT"])
    monkeypatch.setattr(zapp.engine, "refresh_data", lambda mkts: {})
    monkeypatch.setattr(zapp.collector, "get_klines", lambda m, i=None: None)
    r = client.post("/api/ai/train", json={"markets": ["GHOST/USDT"], "horizon": 3}).json()
    assert r["status"] == "insufficient_data"
    assert zapp.ai.trained is False


def test_sse_event_bus_wiring(client):
    """SSE streaming itself is verified against the live server (curl); here we
    prove the bus→handler wiring the endpoint relies on (TestClient cannot
    safely consume an infinite stream)."""
    from zepay.core.events import BUS, E

    got = []
    BUS.subscribe(E.SYSTEM_EVENT, lambda ev: got.append(ev.payload))
    BUS.publish(E.SYSTEM_EVENT, {"probe": True}, source="pytest")
    assert got and got[0]["probe"] is True
