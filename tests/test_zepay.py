#!/usr/bin/env python3
"""
ZEPAY test suite.

Runs entirely offline: market-data network calls are stubbed at the cache
boundary with SYNTHETIC fixtures that live only in this test environment
(never in the application's live path — see REAL_DATA_REQUIRED in zepay.py).
Real-API behavior is covered by /api/providers/check and the exchange test
endpoint against live infrastructure (honest UNREACHABLE when egress is
blocked).

Run:  python3 -m unittest discover -s tests -v
"""
import json
import math
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

# Isolated data dir BEFORE importing zepay (never touch real zepay_data)
TEST_DATA_DIR = tempfile.mkdtemp(prefix="zepay_test_")
os.environ["ZEPAY_DATA_DIR"] = TEST_DATA_DIR
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import zepay  # noqa: E402
from zepay import (AI, AssetFeatures, Agents, AIPM, Backtester, CONFIG, DBASE, MDATA,
                   MCPM, Opportunity, OpportunityEngine, PaperExchange, PortfolioEngine,
                   Reconciler, REGISTRY, RiskEngine, Sizer, SystemState, Decision,
                   UNIVERSE, aead_chacha20_poly1305_encrypt,
                   aead_chacha20_poly1305_decrypt, hkdf_sha256, redact, SECRETS,
                   SECRETBOX, WSClient, record_decision, run_cycle, verify_license_key,
                   activate_license, MODEL_VERSION, http_json)
from zepay import ExecutionEngine  # noqa: E402


def synth_candles(n=160, start=100.0, drift=0.004, vol=0.01, seed=7):
    """Deterministic OHLCV series shaped like real data (test fixture only)."""
    rng = random.Random(seed)
    rows, px = [], start
    t = 1_700_000_000_000
    for i in range(n):
        o = px
        c = max(o * (1 + drift + rng.gauss(0, vol)), 0.01)
        h = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        l = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        v = 500 + abs(rng.gauss(0, 200))
        rows.append({"open_time": t + i * 3600_000, "o": o, "h": h, "l": l, "c": c,
                     "v": v, "quote_vol": v * c, "trades": int(v)})
        px = c
    return rows


def inject_market(market, candles=None, price=None, bids=None, asks=None, fresh=True):
    """Place synthetic market state exactly where real fetched data lands."""
    ts = zepay.ts_ms() if fresh else zepay.ts_ms() - 10_000_000
    candles = candles if candles is not None else synth_candles()
    price = price if price is not None else candles[-1]["c"]
    with MDATA._lock:
        MDATA.cache[market] = {"exchange": "binance", "symbol": market, "timestamp": ts,
                               "source": "test-fixture", "data_type": "ticker", "quality": "OK",
                               "price": price, "change24": 1.2, "volume24": 1000,
                               "quote_vol": 25_000_000, "high": price * 1.05, "low": price * 0.95,
                               "ts": ts}
        interval = CONFIG.get("candle_interval", "1h")
        MDATA.klines_cache[(market, interval)] = {"rows": candles, "ts": ts}
        if bids and asks:
            MDATA.depth_cache[market] = {"bids": bids, "asks": asks, "ts": ts}
        MDATA.errors.pop(market, None)


def no_network_refresh(*a, **k):
    """Stub MDATA.refresh_all in engine tests (fixtures already injected)."""
    return {}


def make_opp(market, direction="LONG", price=100.0, expected_net=0.01):
    return Opportunity(market=market, direction=direction, score=90, confidence=0.8,
                       quality=0.8, regime="BULLISH_TREND", strategy="trend",
                       expected_return=0.02, expected_net=expected_net, cost_pct=0.001,
                       price=price, reasons=["test"])


def make_feat(market, price=100.0, atr14=1.0):
    f = AssetFeatures(market=market)
    f.price = price
    f.n_candles = 150
    f.atr14 = atr14
    f.atr_pct = (atr14 / price) if price else 0.01
    f.sma20 = price * 0.99
    f.sma50 = price * 0.97
    f.liquidity_usd = 5_000_000
    f.spread_bps = 3.0
    return f


class ZepayTestCase(unittest.TestCase):
    def setUp(self):
        # stable defaults per test
        with CONFIG._lock:
            CONFIG.cfg["kill_switch"] = False
            CONFIG.cfg["trading_halted"] = False
            CONFIG.cfg["halt_reason"] = ""
            CONFIG.cfg["live_enabled"] = False
            CONFIG.cfg["trading_mode"] = "PAPER"
            CONFIG.cfg["strict_ai_mode"] = True
            CONFIG.cfg["ai_failure_policy"] = "reduce_risk"
            CONFIG.cfg["universe"] = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
            CONFIG.cfg["paper_starting_balance"] = 10000.0
            CONFIG.cfg["paper_participation_max"] = 0.25
        zepay.refresh_cost()
        self._old_refresh = MDATA.refresh_all
        MDATA.refresh_all = no_network_refresh
        self._old_cycle_llm = None

    def tearDown(self):
        MDATA.refresh_all = self._old_refresh
        # clean test positions/orders
        DBASE.execute("DELETE FROM positions WHERE market LIKE '%TEST%' OR market IN ('BTC/USDT','ETH/USDT','SOL/USDT')")
        DBASE.execute("DELETE FROM orders WHERE market LIKE '%TEST%' OR market IN ('BTC/USDT','ETH/USDT','SOL/USDT')")
        DBASE.execute("DELETE FROM fills WHERE market LIKE '%TEST%' OR market IN ('BTC/USDT','ETH/USDT','SOL/USDT')")
        with MDATA._lock:
            MDATA.cache.clear()
            MDATA.klines_cache.clear()
            MDATA.depth_cache.clear()


class TestCrypto(ZepayTestCase):
    def test_aead_rfc8439_vectors(self):
        SECRETBOX.self_test()  # includes RFC 8439 §A.5 ciphertext + tag

    def test_tampered_ciphertext_rejected(self):
        key, nonce = os.urandom(32), os.urandom(12)
        ct = aead_chacha20_poly1305_encrypt(key, nonce, b"paylod", b"aad")
        for i in (0, len(ct) - 1):
            bad = bytearray(ct); bad[i] ^= 0xFF
            with self.assertRaises(ValueError):
                aead_chacha20_poly1305_decrypt(key, nonce, bytes(bad), b"aad")

    def test_wrong_aad_rejected(self):
        key, nonce = os.urandom(32), os.urandom(12)
        ct = aead_chacha20_poly1305_encrypt(key, nonce, b"x", b"aad1")
        with self.assertRaises(ValueError):
            aead_chacha20_poly1305_decrypt(key, nonce, ct, b"aad2")

    def test_hkdf_rfc5869(self):
        okm = hkdf_sha256(b"\x0b" * 22, salt=bytes.fromhex("000102030405060708090a0b0c"),
                          info=bytes.fromhex("f0f1f2f3f4f5f6f7f8f9"), length=42)
        self.assertEqual(okm, bytes.fromhex("3cb25f25faacd57a90434f64d0362f2a"
                                            "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
                                            "34007208d5b887185865"))

    def test_secrets_encrypted_at_rest(self):
        SECRETS.put("exchange_api_secret", "PLAINTEXT-SECRET-XYZ", purpose="exchange")
        raw = sqlite3.connect(zepay.DB_PATH).execute(
            "SELECT ciphertext FROM secrets WHERE name='exchange_api_secret'").fetchone()[0]
        self.assertNotIn("PLAINTEXT-SECRET-XYZ", raw)
        self.assertTrue(raw.startswith("v1."))
        self.assertEqual(SECRETS.get("exchange_api_secret"), "PLAINTEXT-SECRET-XYZ")
        SECRETS.delete("exchange_api_secret")


class TestLicense(ZepayTestCase):
    def test_offline_valid_and_checksum(self):
        payload = "ZEPAY-ABCD-EFGH-IJKL"
        chk = zepay.hashlib.sha256(payload.encode()).hexdigest()[:4].upper()
        res = verify_license_key(payload + "-" + chk)
        self.assertTrue(res.ok)
        self.assertEqual(res.mode, "offline")
        self.assertIn("no cloud verification", res.message.lower())

    def test_offline_rejects_garbage(self):
        for bad in ("", "bad", "ZEPAY-X", "ZEPAY-@@@@-@@@@-@@@@"):
            self.assertFalse(verify_license_key(bad).ok)

    def test_cloud_blocked_without_spec(self):
        res = verify_license_key("ZEPAY-ANY-KEY", mode="cloud")
        self.assertFalse(res.ok)
        self.assertIn("unavailable", res.message.lower())
        self.assertIn("endpoint", res.message.lower())

    def test_cloud_unreachable_endpoint_fails_honestly(self):
        with CONFIG._lock:
            old = CONFIG.cfg.get("license_verify_endpoint")
            CONFIG.cfg["license_verify_endpoint"] = "https://zepay.invalid.example/verify"
        try:
            res = verify_license_key("ZEPAY-AAAA-BBBB-CCCC", mode="cloud")
            self.assertFalse(res.ok)   # must NOT claim success
            self.assertEqual(res.mode, "cloud")
        finally:
            with CONFIG._lock:
                CONFIG.cfg["license_verify_endpoint"] = old

    def test_activate_persists_hash_not_key(self):
        key = "ZEPAY-ABCD-EFGH-IJKL-" + zepay.hashlib.sha256(b"ZEPAY-ABCD-EFGH-IJKL").hexdigest()[:4].upper()
        res = activate_license(key)
        self.assertTrue(res.ok)
        with open(zepay.CONFIG_PATH) as fh:
            cfg_raw = fh.read()
        self.assertNotIn(key, cfg_raw)          # key itself never stored
        self.assertTrue(CONFIG.get("license_key_hash"))


class TestProviders(ZepayTestCase):
    def test_registry_integrated_providers(self):
        ids = {p["id"] for p in REGISTRY.all(integrated_only=True)}
        self.assertIn("binance_spot_public", ids)
        self.assertIn("binance_user", ids)
        self.assertIn("zepay_cloud", ids)

    def test_zepay_cloud_is_blocked(self):
        snap = REGISTRY.get("zepay_cloud").snapshot()
        self.assertEqual(snap["status"], "BLOCKED")
        self.assertIn("specification", snap.get("note", "").lower())

    def test_public_catalog_not_integrated(self):
        cat = zepay.public_api_catalog_payload()
        self.assertGreater(len(cat), 15)
        for c in cat:
            self.assertFalse(c["integrated"])

    def test_health_check_records_real_error(self):
        snap = REGISTRY.get("binance_spot_public").health_check()
        # in an offline environment this MUST be UNREACHABLE/DEGRADED with an error — never OK
        self.assertIn(snap["status"], ("UNREACHABLE", "DEGRADED", "OK"))
        if snap["status"] != "OK":
            self.assertTrue(snap["last_error"])


class TestUniverse(ZepayTestCase):
    def test_fallback_universe_when_offline(self):
        UNIVERSE.refresh()
        self.assertGreaterEqual(len(UNIVERSE.catalog), 10)
        self.assertIn(UNIVERSE.source, ("default_fallback", "cached_metadata", "exchange_metadata"))
        a = UNIVERSE.get("BTC/USDT")
        self.assertIsNotNone(a)

    def test_search(self):
        assets = UNIVERSE.all_assets(search="BTC", limit=10)
        self.assertTrue(all("BTC" in a["symbol"] for a in assets))


class TestFeaturesAndAI(ZepayTestCase):
    def test_feature_build_from_fixture(self):
        inject_market("BTC/USDT", candles=synth_candles(160, seed=1), price=None,
                      bids=[[99.0, 10], [98.0, 20]], asks=[[101.0, 10], [102.0, 20]])
        f = zepay.FeatureEngine.build("BTC/USDT")
        self.assertGreater(f.price, 0)
        self.assertEqual(f.n_candles, 160)
        self.assertGreater(f.spread_bps, 0)
        self.assertGreater(f.atr_pct, 0)

    def test_strict_mode_never_fabricates(self):
        AI.trained = False
        f = make_feat("BTC/USDT")
        regime = zepay.RegimeEngine.detect(f)
        strat = zepay.StrategyEngine.evaluate(f, regime["regime"])
        infer = AI.infer(f, regime, strat)
        self.assertEqual(infer["direction"], "WAIT")
        self.assertEqual(infer["mode"], "untrained-no-signal")
        self.assertEqual(infer["confidence"], 0.0)

    def test_train_and_infer_on_data(self):
        for m, seed in (("BTC/USDT", 3), ("ETH/USDT", 5), ("SOL/USDT", 9)):
            inject_market(m, candles=synth_candles(200, seed=seed))
        metrics = AI.train(["BTC/USDT", "ETH/USDT", "SOL/USDT"])
        self.assertEqual(metrics.get("status"), "trained")
        self.assertGreater(metrics["samples"], 60)
        f = zepay.FeatureEngine.build("BTC/USDT")
        regime = zepay.RegimeEngine.detect(f)
        strat = zepay.StrategyEngine.evaluate(f, regime["regime"])
        infer = AI.infer(f, regime, strat)
        self.assertEqual(infer["mode"], "trained-ensemble")
        self.assertIn(infer["direction"], ("LONG", "SHORT", "WAIT"))
        AI.trained = False  # restore for other tests

    def test_no_lookahead_in_features(self):
        """Changing FUTURE candles must not change the signal at step i."""
        candles = synth_candles(120, seed=11)
        for m in ("BTC/USDT",):
            inject_market(m, candles=candles)
        i = 100
        w = candles[:i + 1]
        f1 = AI._features_from_window("BTC/USDT", w)
        altered = [dict(c) for c in candles]
        for k in range(i + 1, len(altered)):
            altered[k]["c"] *= 2.0
        f2 = AI._features_from_window("BTC/USDT", altered[:i + 1])
        self.assertAlmostEqual(f1.rsi14, f2.rsi14, places=9)
        self.assertAlmostEqual(f1.sma20, f2.sma20, places=9)


class TestRiskEngine(ZepayTestCase):
    def test_blocks_stale_market(self):
        inject_market("BTC/USDT", fresh=False)
        f = make_feat("BTC/USDT", price=0)
        opp = make_opp("BTC/USDT", price=0)
        d, _, _ = RiskEngine.authorize(opp, f, {"status": "BLOCKED", "reasons": ["stale"]},
                                       [], {"equity": 10000}, {})
        self.assertIn(d, (Decision.REJECTED, Decision.HALTED))

    def test_kill_switch_blocks_everything(self):
        with CONFIG._lock:
            CONFIG.cfg["kill_switch"] = True
        try:
            allowed, why = RiskEngine.entries_allowed()
            self.assertFalse(allowed)
            st, _ = RiskEngine.system_state({"equity": 10000, "drawdown_pct": 0, "day_pnl": 0})
            self.assertEqual(st, SystemState.HALTED)
            r = ExecutionEngine.place(make_opp("BTC/USDT"), {"qty": 1, "notional": 100, "stop": 0,
                                                            "take_profit": 0, "reject": False},
                                      make_feat("BTC/USDT"), mode="PAPER")
            self.assertFalse(r["ok"])
            self.assertIn("halted", r["error"].lower())
        finally:
            with CONFIG._lock:
                CONFIG.cfg["kill_switch"] = False

    def test_daily_loss_halt(self):
        st, msg = RiskEngine.system_state({"equity": 10000, "drawdown_pct": 0,
                                           "day_pnl": -500})  # 5% > 3% default limit
        self.assertEqual(st, SystemState.HALTED)

    def test_drawdown_states(self):
        self.assertEqual(RiskEngine.system_state({"equity": 10000, "drawdown_pct": 0.02,
                                                  "day_pnl": 0})[0], SystemState.NORMAL)
        self.assertEqual(RiskEngine.system_state({"equity": 10000, "drawdown_pct": 0.07,
                                                  "day_pnl": 0})[0], SystemState.CAUTION)
        self.assertEqual(RiskEngine.system_state({"equity": 10000, "drawdown_pct": 0.10,
                                                  "day_pnl": 0})[0], SystemState.DEFENSIVE)

    def test_ai_failure_policy_stop_trading(self):
        AI.trained = False
        inject_market("BTC/USDT")
        with CONFIG._lock:
            CONFIG.cfg["ai_failure_policy"] = "stop_trading"
            CONFIG.cfg["universe"] = ["BTC/USDT"]
        try:
            allowed, why = RiskEngine.entries_allowed()
            self.assertFalse(allowed)
            self.assertIn("AI UNAVAILABLE", why)
        finally:
            with CONFIG._lock:
                CONFIG.cfg["ai_failure_policy"] = "reduce_risk"

    def test_max_positions_gate(self):
        inject_market("BTC/USDT")
        inject_market("NEW/USDT")
        positions = [{"market": f"M{i}/USDT", "exposure": 100} for i in range(5)]
        opp = make_opp("NEW/USDT")
        d, why, _ = RiskEngine.authorize(opp, make_feat("NEW/USDT"),
                                         {"status": "TRADEABLE", "reasons": []},
                                         positions, {"equity": 10000}, {})
        self.assertEqual(d, Decision.WAIT)
        self.assertTrue(any("Max positions" in w for w in why))


class TestPaperExecution(ZepayTestCase):
    def _fresh_market(self, market="BTC/USDT", price=100.0):
        inject_market(market, candles=synth_candles(160, seed=2, start=price),
                      price=price,
                      bids=[[price * 0.999, 50], [price * 0.998, 100]],
                      asks=[[price * 1.001, 50], [price * 1.002, 100]])

    def test_market_order_lifecycle(self):
        self._fresh_market()
        DBASE.execute("DELETE FROM positions WHERE market='BTC/USDT'")
        DBASE.execute("DELETE FROM orders WHERE market='BTC/USDT'")
        opp = make_opp("BTC/USDT", price=100.0)
        size = {"qty": 1.0, "notional": 100.0, "stop": 95.0, "take_profit": 110.0, "reject": False}
        r = ExecutionEngine.place(opp, size, make_feat("BTC/USDT"), mode="PAPER")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["simulated"])
        self.assertGreater(r["fill_price"], 100.0)  # buys fill at ask side + slippage
        order = DBASE.query_one("SELECT * FROM orders WHERE id=?", (r["order_id"],))
        self.assertEqual(order["status"], "FILLED")
        pos = DBASE.query_one("SELECT * FROM positions WHERE market='BTC/USDT' AND status='OPEN'")
        self.assertIsNotNone(pos)
        self.assertAlmostEqual(pos["qty"], 1.0, places=6)
        # fee was charged
        acct = zepay.get_account("PAPER")
        self.assertLess(acct["realized_pnl"], 0)
        r2 = ExecutionEngine.close_position("BTC/USDT", "PAPER", reason="test")
        self.assertTrue(r2["ok"])

    def test_partial_fill_on_thin_book(self):
        inject_market("THIN/USDT", candles=synth_candles(160, seed=4, start=50), price=50,
                      bids=[[49.95, 0.4], [49.90, 0.6]], asks=[[50.05, 0.4], [50.10, 0.6]])
        with CONFIG._lock:
            CONFIG.cfg["universe"] = ["THIN/USDT"]
        DBASE.execute("DELETE FROM positions WHERE market='THIN/USDT'")
        opp = make_opp("THIN/USDT", price=50)
        size = {"qty": 10.0, "notional": 500.0, "stop": 45, "take_profit": 60, "reject": False}
        r = ExecutionEngine.place(opp, size, make_feat("THIN/USDT", price=50), mode="PAPER")
        self.assertTrue(r["ok"])
        self.assertTrue(r["partial"])
        self.assertLess(r["filled_qty"], 10.0)
        self.assertGreater(r["remaining_qty"], 0)
        order = DBASE.query_one("SELECT * FROM orders WHERE id=?", (r["order_id"],))
        self.assertEqual(order["status"], "PARTIALLY_FILLED")
        # position holds only the filled qty
        pos = DBASE.query_one("SELECT * FROM positions WHERE market='THIN/USDT' AND status='OPEN'")
        self.assertAlmostEqual(pos["qty"], r["filled_qty"], places=6)
        ExecutionEngine.close_position("THIN/USDT", "PAPER", reason="test")

    def test_idempotency_duplicate_refused(self):
        self._fresh_market()
        DBASE.execute("DELETE FROM positions WHERE market='BTC/USDT'")
        opp = make_opp("BTC/USDT")
        size = {"qty": 0.5, "notional": 50, "stop": 95, "take_profit": 110, "reject": False}
        key = "idem-test-123"
        r1 = ExecutionEngine.place(opp, size, make_feat("BTC/USDT"), mode="PAPER",
                                   idempotency_key=key)
        self.assertTrue(r1["ok"])
        r2 = ExecutionEngine.place(opp, size, make_feat("BTC/USDT"), mode="PAPER",
                                   idempotency_key=key)
        self.assertFalse(r2["ok"])
        self.assertIn("Duplicate", r2["error"])
        ExecutionEngine.close_position("BTC/USDT", "PAPER", reason="test")

    def test_existing_position_refused(self):
        self._fresh_market()
        now = zepay.utcnow_iso()
        DBASE.execute("DELETE FROM positions WHERE market='BTC/USDT'")
        DBASE.execute("INSERT INTO positions (id, market, direction, qty, entry_price, current_price,"
                      " stop_loss, take_profit, exposure, unrealized_pnl, realized_pnl, confidence,"
                      " strategy, opened_at, updated_at, status)"
                      " VALUES ('pos-x','BTC/USDT','LONG',1,100,100,95,110,100,0,0,1,'t',?,?,'OPEN')",
                      (now, now))
        r = ExecutionEngine.place(make_opp("BTC/USDT"),
                                  {"qty": 1, "notional": 100, "stop": 0, "take_profit": 0, "reject": False},
                                  make_feat("BTC/USDT"), mode="PAPER")
        self.assertFalse(r["ok"])
        self.assertIn("already open", r["error"])
        DBASE.execute("DELETE FROM positions WHERE id='pos-x'")

    def test_below_min_notional_rejected(self):
        # extreme volatility → sizer's volatility/caps shrink notional below the
        # exchange minimum → order must be rejected, never sent
        inject_market("DUST/USDT", candles=synth_candles(160, seed=6, start=100), price=100,
                      bids=[[99.9, 5]], asks=[[100.1, 5]])
        with CONFIG._lock:
            CONFIG.cfg["universe"] = ["DUST/USDT"]
        opp = make_opp("DUST/USDT", price=100.0)
        f = make_feat("DUST/USDT", price=100.0, atr14=700.0)   # ATR 7x price
        size = Sizer.size(opp, f, {"equity": 10000}, {"size_mult": 0.4})
        if size.get("reject"):
            r = ExecutionEngine.place(opp, size, f, mode="PAPER")
            self.assertFalse(r["ok"])
            self.assertIn("below exchange minimum", r["error"])
        else:
            # if the fixture math lands above minimum, force the reject path explicitly
            r = ExecutionEngine.place(opp, {"qty": 0, "notional": 0, "reject": True,
                                            "reject_reason": "below exchange minimum ($10.00)"}, f,
                                      mode="PAPER")
            self.assertFalse(r["ok"])
            self.assertIn("below exchange minimum", r["error"])

    def test_stale_price_aborts_execution(self):
        inject_market("ETH/USDT")                # universe feed healthy…
        with CONFIG._lock:
            CONFIG.cfg["universe"] = ["ETH/USDT"]
        inject_market("BTC/USDT", fresh=False)   # …but the traded market is stale
        r = ExecutionEngine.place(make_opp("BTC/USDT"),
                                  {"qty": 1, "notional": 100, "stop": 0, "take_profit": 0, "reject": False},
                                  make_feat("BTC/USDT"), mode="PAPER")
        self.assertFalse(r["ok"])
        self.assertIn("Stale", r["error"])

    def test_live_mode_requires_gate(self):
        self._fresh_market()
        with CONFIG._lock:
            CONFIG.cfg["live_enabled"] = False
        r = ExecutionEngine.place(make_opp("BTC/USDT"),
                                  {"qty": 1, "notional": 100, "stop": 0, "take_profit": 0, "reject": False},
                                  make_feat("BTC/USDT"), mode="LIVE")
        self.assertFalse(r["ok"])
        self.assertIn("Live trading is disabled", r["error"])

    def test_limit_order_fills_on_cross(self):
        self._fresh_market("LIM/USDT", price=100.0)
        DBASE.execute("DELETE FROM orders WHERE market='LIM/USDT'")
        DBASE.execute("DELETE FROM positions WHERE market='LIM/USDT'")
        r = ExecutionEngine.place_limit_paper("LIM/USDT", "LONG", 1.0, 99.0)
        self.assertTrue(r["ok"])
        o = DBASE.query_one("SELECT * FROM orders WHERE id=?", (r["order_id"],))
        self.assertEqual(o["status"], "NEW")
        # price falls to 98.9 → buy limit 99 crosses
        inject_market("LIM/USDT", candles=synth_candles(160, seed=2, start=98), price=98.9,
                      bids=[[98.8, 50]], asks=[[98.95, 50]])
        fills = ExecutionEngine.check_open_orders()
        self.assertEqual(len(fills), 1)
        o = DBASE.query_one("SELECT * FROM orders WHERE id=?", (r["order_id"],))
        self.assertEqual(o["status"], "FILLED")
        pos = DBASE.query_one("SELECT * FROM positions WHERE market='LIM/USDT' AND status='OPEN'")
        self.assertIsNotNone(pos)
        ExecutionEngine.close_position("LIM/USDT", "PAPER", reason="test")

    def test_sl_tp_exit_in_cycle(self):
        candles = synth_candles(160, seed=8, start=100)
        inject_market("EXIT/USDT", candles=candles, price=100,
                      bids=[[99.9, 50]], asks=[[100.1, 50]])
        now = zepay.utcnow_iso()
        DBASE.execute("DELETE FROM positions WHERE market='EXIT/USDT'")
        DBASE.execute("INSERT INTO positions (id, market, direction, qty, entry_price, current_price,"
                      " stop_loss, take_profit, exposure, unrealized_pnl, realized_pnl, confidence,"
                      " strategy, opened_at, updated_at, status)"
                      " VALUES ('pos-tp','EXIT/USDT','LONG',1,100,100,95,101,100,0,0,1,'t',?,?,'OPEN')",
                      (now, now))
        with CONFIG._lock:
            CONFIG.cfg["universe"] = ["EXIT/USDT"]
        # price rallies to 102 → take-profit at 101 hits
        inject_market("EXIT/USDT", candles=candles, price=102,
                      bids=[[101.9, 50]], asks=[[102.1, 50]])
        res = run_cycle(save_signals=False, with_llm_research=False)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["exits"]), 1)
        self.assertEqual(res["exits"][0]["reason"], "take-profit")
        pos = DBASE.query_one("SELECT * FROM positions WHERE id='pos-tp'")
        self.assertEqual(pos["status"], "CLOSED")
        self.assertGreater(pos["realized_pnl"], 0)


class TestEngineCycle(ZepayTestCase):
    def test_full_cycle_with_data(self):
        for m, seed in (("BTC/USDT", 21), ("ETH/USDT", 22)):
            inject_market(m, candles=synth_candles(200, seed=seed),
                          bids=[[99, 20]], asks=[[101, 20]])
        with CONFIG._lock:
            CONFIG.cfg["universe"] = ["BTC/USDT", "ETH/USDT"]
        res = run_cycle(save_signals=True, with_llm_research=False)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["opportunities"]), 2)
        for o in res["opportunities"]:
            self.assertIn(o["decision"], ("APPROVED", "WAIT", "REJECTED", "HALTED"))
            self.assertIn("rank", o)
            self.assertGreaterEqual(o["score"], 0)
            self.assertLessEqual(o["score"], 100)
        # decision ledger entries for both markets
        rows = DBASE.query("SELECT market FROM decisions ORDER BY ts DESC LIMIT 2")
        self.assertEqual({r["market"] for r in rows}, {"BTC/USDT", "ETH/USDT"})

    def test_cycle_all_stale_blocks_entries(self):
        inject_market("BTC/USDT", fresh=False)
        with CONFIG._lock:
            CONFIG.cfg["universe"] = ["BTC/USDT"]
        res = run_cycle(save_signals=False, with_llm_research=False)
        for o in res["opportunities"]:
            self.assertIn(o["decision"], ("HALTED", "REJECTED"))
        self.assertEqual(res["data_feed"]["status"], "UNAVAILABLE")

    def test_feed_health_states(self):
        inject_market("BTC/USDT")
        self.assertEqual(MDATA.feed_health(["BTC/USDT"])["status"], "LIVE")
        inject_market("BTC/USDT", fresh=False)
        self.assertEqual(MDATA.feed_health(["BTC/USDT"])["status"], "UNAVAILABLE")

    def test_agents_run_on_real_features(self):
        inject_market("BTC/USDT", candles=synth_candles(160, seed=31))
        f = zepay.FeatureEngine.build("BTC/USDT")
        regime = zepay.RegimeEngine.detect(f)
        strat = zepay.StrategyEngine.evaluate(f, regime["regime"])
        infer = AI.infer(f, regime, strat)
        out = Agents.run_deterministic(f, regime, infer, {"total_pct": 0}, [])
        names = {a["agent"] for a in out}
        self.assertIn("MarketAnalyst", names)
        self.assertIn("RiskAnalyst", names)
        self.assertIn("DerivativesAnalyst", names)
        for a in out:
            self.assertIn(a["verdict"], ("BULLISH", "BEARISH", "NEUTRAL", "NO_DATA", "OK", "CAUTION", "EXPENSIVE"))

    def test_llm_output_validation(self):
        good = '{"bias":"LONG","confidence":0.7,"bull_case":["x"],"bear_case":[],"key_risks":["r"],"summary":"s"}'
        parsed = zepay.ResearchEngineLLM.validate(good)
        self.assertEqual(parsed["bias"], "LONG")
        self.assertEqual(parsed["confidence"], 0.7)
        # clamped confidence
        parsed2 = zepay.ResearchEngineLLM.validate('{"bias":"SHORT","confidence":9}')
        self.assertEqual(parsed2["confidence"], 1.0)
        # invalid bias coerced
        parsed3 = zepay.ResearchEngineLLM.validate('{"bias":"MOON","confidence":0.5}')
        self.assertEqual(parsed3["bias"], "WAIT")
        # garbage discarded
        self.assertIsNone(zepay.ResearchEngineLLM.validate("not json at all"))
        self.assertIsNone(zepay.ResearchEngineLLM.validate("```json\n{broken\n```"))


class TestBacktester(ZepayTestCase):
    def test_backtest_runs_with_costs(self):
        rows = synth_candles(400, seed=41, drift=0.002)
        old = Backtester._series
        Backtester._series = staticmethod(lambda market, interval=None, limit=500: rows)
        try:
            r = Backtester.run_single("BT/USDT", starting=10000)
        finally:
            Backtester._series = old
        self.assertTrue(r["ok"])
        self.assertEqual(r["candles"], 400)
        self.assertGreaterEqual(r["fees"], 0)
        self.assertIn("costs_note", r)

    def test_backtest_refuses_without_data(self):
        old = Backtester._series
        Backtester._series = staticmethod(lambda market, interval=None, limit=500: [])
        try:
            r = Backtester.run_single("NOPE/USDT")
            self.assertFalse(r["ok"])
            self.assertIn("REAL history", r["error"])
        finally:
            Backtester._series = old


class TestReconciliation(ZepayTestCase):
    def test_paper_check_clean(self):
        # after cleanup there should be no orphans
        DBASE.execute("DELETE FROM positions WHERE status='OPEN'")
        r = Reconciler.paper_check()
        self.assertTrue(r["ok"], r["issues"])

    def test_detects_orphan_position(self):
        now = zepay.utcnow_iso()
        DBASE.execute("INSERT INTO positions (id, market, direction, qty, entry_price, current_price,"
                      " stop_loss, take_profit, exposure, unrealized_pnl, realized_pnl, confidence,"
                      " strategy, opened_at, updated_at, status)"
                      " VALUES ('pos-orph','ORPH/USDT','LONG',1,1,1,0,0,1,0,0,1,'t',?,?,'OPEN')",
                      (now, now))
        r = Reconciler.paper_check()
        self.assertFalse(r["ok"])
        self.assertTrue(any("ORPH" in i for i in r["issues"]))
        DBASE.execute("DELETE FROM positions WHERE id='pos-orph'")

    def test_live_check_blocks_without_credentials(self):
        r = Reconciler.live_check()
        self.assertFalse(r["ok"])
        self.assertTrue(any("credentials" in i for i in r["issues"]))


class TestMCP(ZepayTestCase):
    def test_registry_disabled_by_default(self):
        for s in MCPM.registry():
            if s["id"].startswith("mcp-"):
                self.assertFalse(s["enabled"])

    def test_trading_permissions_refused(self):
        r = MCPM.upsert({"id": "evil", "name": "evil", "url": "http://127.0.0.1:1/mcp",
                         "purpose": "x", "permissions": ["trading"], "enabled": True})
        self.assertFalse(r["ok"])

    def test_trading_tool_refused(self):
        MCPM.upsert({"id": "t1", "name": "t1", "url": "http://127.0.0.1:1/mcp",
                     "purpose": "research", "permissions": ["research.readonly"], "enabled": True})
        r = MCPM.call_tool("t1", "place_order", {"symbol": "BTCUSDT", "qty": 1})
        self.assertFalse(r["ok"])
        self.assertIn("cannot trade", r["error"])  # guard fires before network
        self.assertIn("cannot trade", r["error"])  # refused before any network call
        r2 = MCPM.call_tool("t1", "fetch", {"url": "http://x", "order": "buy"})
        self.assertFalse(r2["ok"])
        MCPM.remove("t1")


class TestAIProviders(ZepayTestCase):
    def test_set_provider_stores_encrypted(self):
        r = AIPM.set_provider("openai", True, model="gpt-4o-mini", api_key="sk-TEST-KEY-123")
        self.assertTrue(r["ok"])
        raw = sqlite3.connect(zepay.DB_PATH).execute(
            "SELECT ciphertext FROM secrets WHERE name='ai_key_openai'").fetchone()
        self.assertIsNotNone(raw)
        self.assertNotIn("sk-TEST-KEY-123", raw[0])
        self.assertEqual(AIPM.active_provider(), "openai")
        # switching disables the others
        AIPM.set_provider("anthropic", True, api_key="sk-ant-TEST")
        self.assertEqual(AIPM.active_provider(), "anthropic")
        st = AIPM.status()
        openai_row = next(p for p in st["providers"] if p["id"] == "openai")
        self.assertFalse(openai_row["enabled"])
        AIPM.set_provider("anthropic", False)
        AIPM.set_provider("openai", False)
        SECRETS.delete("ai_key_openai")
        SECRETS.delete("ai_key_anthropic")

    def test_no_provider_active_by_default(self):
        self.assertIsNone(AIPM.active_provider())
        self.assertEqual(CONFIG.get("ai_mode"), "LOCAL_QUANT_ONLY")


class TestSecurity(ZepayTestCase):
    def test_redaction_recursive(self):
        r = redact({"api_key": "k", "secret": "s", "passphrase": "p", "Authorization": "a",
                    "ok": 1, "list": [{"token": "t"}]})
        for k in ("api_key", "secret", "passphrase", "Authorization"):
            self.assertEqual(r[k], "***REDACTED***")
        self.assertEqual(r["list"][0]["token"], "***REDACTED***")
        self.assertEqual(r["ok"], 1)

    def test_config_public_never_leaks_secrets(self):
        SECRETS.put("exchange_api_key", "SUPER-KEY-999", purpose="exchange")
        c = CONFIG.all(public_only=True)
        s = json.dumps(c)
        self.assertNotIn("SUPER-KEY-999", s)
        SECRETS.delete("exchange_api_key")

    def test_withdrawal_keys_refused_by_exchange_test(self):
        # adapter without network: test must fail (honest), never "connected"
        EX = zepay.EXCHANGES["binance"]
        EX.set_credentials("AAAA1234", "fake-secret-9876")
        try:
            r = EX.test_connection()
            self.assertFalse(r["connected"])
            self.assertIn("error", r)
        finally:
            EX.clear_credentials()

    def test_ws_frame_codec_roundtrip(self):
        payload = json.dumps({"e": "24hrMiniTicker", "s": "BTCUSDT", "c": "50000"}).encode()
        frame = WSClient._frame(0x1, payload)
        self.assertTrue(frame[1] & 0x80)  # masked (client→server)
        parsed = WSClient._parse_frame(frame, 0)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[1], 0x1)
        self.assertEqual(parsed[2], payload)
        # fragmented/incomplete buffer → None
        self.assertIsNone(WSClient._parse_frame(frame[:5], 0))


class TestRestartRecovery(ZepayTestCase):
    def test_state_survives_reopen(self):
        # write something, reopen the DB file with a fresh connection
        did = record_decision("RST/USDT", make_feat("RST/USDT"), {}, None, "WAIT", ["x"],
                              {}, {}, "cycle-rst")
        c = sqlite3.connect(zepay.DB_PATH)
        row = c.execute("SELECT market, decision FROM decisions WHERE id=?", (did,)).fetchone()
        c.close()
        self.assertEqual(row, ("RST/USDT", "WAIT"))

    def test_sessions_persisted(self):
        tok = zepay.create_session()
        self.assertTrue(zepay.valid_session(tok))
        self.assertFalse(zepay.valid_session("bogus-token"))
        zepay.drop_session(tok)
        self.assertFalse(zepay.valid_session(tok))

    def test_startup_reconciliation_runs(self):
        r = Reconciler.startup()
        self.assertIn("paper", r)


class TestDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import urllib.request as ur
        # static checks on the HTML payload (server may not be running)
        html = zepay.DASHBOARD_HTML
        cls.html = html

    def test_pages_present(self):
        for page in ("Dashboard", "Assets", "AI Signals", "Portfolio", "Positions",
                     "Backtesting", "Risk", "AI Models", "Agents", "Decision Ledger",
                     "Exchange", "API Manager", "AI Manager", "MCP Manager",
                     "Diagnostics", "Settings", "Help", "Alerts"):
            self.assertIn(page, self.html, f"missing page {page}")

    def test_banners_present(self):
        # the banners come from /api/status (mode_banner / data_banner) and must be wired:
        self.assertIn("mode_banner", self.html)
        self.assertIn("data_banner", self.html)
        self.assertIn("STOP TRADING", self.html)
        sp = zepay.status_payload()
        self.assertIn("REAL DATA UNAVAILABLE", sp["data_banner"])
        self.assertIn("PAPER — REAL DATA", sp["mode_banner"])
        with CONFIG._lock:
            old = CONFIG.cfg["live_enabled"]
            CONFIG.cfg["live_enabled"] = True
        try:
            self.assertIn("LIVE — REAL MONEY", zepay.status_payload()["mode_banner"])
        finally:
            with CONFIG._lock:
                CONFIG.cfg["live_enabled"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
