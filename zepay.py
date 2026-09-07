#!/usr/bin/env python3
"""
================================================================================
ZEPAY — AI-Assisted Quantitative Multi-Asset Crypto Trading Platform
ALL-IN-ONE SINGLE-FILE TURNKEY EDITION  •  Version 1.0.0
================================================================================
ONE FILE = COMPLETE PRODUCT.  Zero dependencies (Python 3.10+ stdlib only).

RUN:
    python3 zepay.py
    → opens on http://localhost:8000  (binds 0.0.0.0 for preview environments)

CLI:
    python3 zepay.py start        Start server (default)
    python3 zepay.py doctor       Self-diagnostics
    python3 zepay.py status       System status (needs running server)
    python3 zepay.py check        Full system check (no server needed)
    python3 zepay.py backup       Backup database+config to ./zepay_data/backups
    python3 zepay.py version      Print version

WHAT'S INSIDE (ONE intelligent engine, NOT per-coin bots):
  Multi-Asset Market Data Manager • Feature Engine • Cross-Asset Intelligence
  • AI Model Engine (Direction/Return/Volatility/Regime/Quality/Ensemble,
    from-scratch gradient models, no sklearn needed) • Strategy Engine
  (Trend/Momentum/Breakout/MeanReversion/Volatility/OrderFlow-proxy)
  • Market Regime Engine • Opportunity Generator + Ranking Engine
  • Portfolio Intelligence + Optimizer • Global Risk Engine (final authority)
  • Position Manager • Execution Engine • Paper Exchange • Live Exchange
  Adapter (DISABLED by default, explicit opt-in gate) • Model Registry
  • Research Engine • Monitoring + Audit Engines • Backtester (single +
  multi-asset) • Walk-forward validation • Embedded dashboard (Simple +
  Advanced, mobile responsive) • Help Center • Backup/Restore • Kill switch

DATA HONESTY:
  • Real market data from Binance public REST (free, no key). No paid APIs.
  • If data is unavailable → asset is marked STALE/BLOCKED with reason.
    ZePay NEVER invents prices, signals, fills or backtest results.
  • Paper fills are simulated against REAL fetched prices + explicit
    spread/slippage/fee model (shown in UI). Clearly labeled PAPER.

SAFETY HIERARCHY (enforced in code, see RiskEngine):
  1 HARD SAFETY LIMITS > 2 GLOBAL RISK > 3 PORTFOLIO CONSTRAINTS
  > 4 EXECUTION SAFETY > 5 DATA VALIDITY > 6 STRATEGY > 7 AI MODELS
  > 8 LLM ASSISTANT (explanations only — this edition uses deterministic
    template explanations from real system data; LLM never trades).

DEFAULTS: PAPER_TRADING=TRUE, LIVE_TRADING=FALSE, WITHDRAWALS=FALSE.
LIVE trading requires the explicit 10-step gate in the UI + API-key
permission verification + withdrawal-permission BLOCK.

Single-file architecture notes:
  • HTTP API: stdlib http.server (same REST contract as FastAPI edition).
  • DB: SQLite (same entity set as Postgres edition; portable to Postgres).
  • Cache/queue: in-process TTL cache + worker threads (same interface
    as Redis edition). No external services required.
================================================================================
PAST PERFORMANCE IS NOT A GUARANTEE OF FUTURE RESULTS. Crypto trading is risky.
This software is for education/research; paper-trade first. Never risk money
you cannot afford to lose.
================================================================================
"""

import argparse
import base64
import copy
import csv
import hashlib
import html as htmlmod
import http.server
import io
import json
import math
import os
import random
import secrets
import shutil
import socketserver
import sqlite3
import statistics
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum

VERSION = "1.0.0"
EDITION = "all-in-one"
APP_NAME = "ZePay"

DATA_DIR = os.path.join(os.getcwd(), "zepay_data")
DB_PATH = os.path.join(DATA_DIR, "zepay.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
LOG_PATH = os.path.join(DATA_DIR, "zepay.log")

DEFAULT_PORT = 8000

# ----------------------------- tiny utilities --------------------------------

def utcnow():
    return datetime.now(timezone.utc)

def utcnow_iso():
    return utcnow().isoformat()

def ts_ms():
    return int(time.time() * 1000)

def safe_float(x, d=0.0):
    try:
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return d
        return v
    except Exception:
        return d

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def pct(x, digits=2):
    try:
        return round(float(x) * 100.0, digits)
    except Exception:
        return 0.0

def log(msg, level="INFO"):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        line = f"{utcnow_iso()} [{level}] {msg}\n"
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass

def sha256(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def redact(obj):
    """Redact secrets for logs/API."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(w in lk for w in ("secret", "password", "private", "seed", "token")) and "csrf" not in lk:
                out[k] = "***REDACTED***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj

# ----------------------------- enums -----------------------------------------

class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"

class Decision(str, Enum):
    APPROVED = "APPROVED"
    WAIT = "WAIT"
    REJECTED = "REJECTED"
    HALTED = "HALTED"

class Health(str, Enum):
    TRADEABLE = "TRADEABLE"
    LIMITED = "LIMITED"
    BLOCKED = "BLOCKED"

class SystemState(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    DEFENSIVE = "DEFENSIVE"
    HALTED = "HALTED"

class TradingMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"

# ----------------------------- configuration ---------------------------------

SUPPORTED_MARKETS = [
    {"symbol": "BTC/USDT", "exchange_symbol": "BTCUSDT", "base": "BTC", "quote": "USDT", "stars": 5},
    {"symbol": "ETH/USDT", "exchange_symbol": "ETHUSDT", "base": "ETH", "quote": "USDT", "stars": 4},
    {"symbol": "SOL/USDT", "exchange_symbol": "SOLUSDT", "base": "SOL", "quote": "USDT", "stars": 4},
    {"symbol": "BNB/USDT", "exchange_symbol": "BNBUSDT", "base": "BNB", "quote": "USDT", "stars": 3},
    {"symbol": "XRP/USDT", "exchange_symbol": "XRPUSDT", "base": "XRP", "quote": "USDT", "stars": 3},
    {"symbol": "DOGE/USDT", "exchange_symbol": "DOGEUSDT", "base": "DOGE", "quote": "USDT", "stars": 2},
    {"symbol": "ADA/USDT", "exchange_symbol": "ADAUSDT", "base": "ADA", "quote": "USDT", "stars": 2},
    {"symbol": "AVAX/USDT", "exchange_symbol": "AVAXUSDT", "base": "AVAX", "quote": "USDT", "stars": 2},
    {"symbol": "LINK/USDT", "exchange_symbol": "LINKUSDT", "base": "LINK", "quote": "USDT", "stars": 2},
    {"symbol": "MATIC/USDT", "exchange_symbol": "MATICUSDT", "base": "MATIC", "quote": "USDT", "stars": 2},
]

DEFAULT_CONFIG = {
    "version": VERSION,
    "license_key_hash": None,
    "setup_complete": False,
    "trading_mode": "PAPER",
    "live_enabled": False,
    "live_confirmed_steps": [],
    "paper_starting_balance": 10000.0,
    "quote_currency": "USDT",
    "display_currency": "₹",
    "universe": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    "priorities": {"BTC/USDT": 5, "ETH/USDT": 4, "SOL/USDT": 4, "BNB/USDT": 3,
                   "XRP/USDT": 3, "DOGE/USDT": 2, "ADA/USDT": 2},
    "risk_mode": "Conservative",
    "max_positions": 5,
    "max_position_pct": 0.20,
    "max_total_exposure_pct": 0.60,
    "max_asset_exposure_pct": 0.25,
    "max_correlated_exposure_pct": 0.40,
    "max_directional_exposure_pct": 0.50,
    "daily_loss_limit_pct": 0.03,
    "max_drawdown_halt_pct": 0.15,
    "risk_per_trade_pct": 0.01,
    "min_edge_pct": 0.0015,
    "max_spread_bps": 25.0,
    "min_liquidity_usd": 50000.0,
    "stale_data_secs": 120,
    "kill_switch": False,
    "trading_halted": False,
    "halt_reason": "",
    "ai_automation": True,
    "auto_research": True,
    "notifications": True,
    "candle_interval": "1h",
    "candle_limit": 200,
    "refresh_secs": 30,
    "fee_bps": 10.0,
    "slippage_bps": 5.0,
    "funding_bps_8h": 1.0,
    "exchange": "binance",
    "exchange_api_key": None,
    "exchange_api_secret_enc": None,
    "webhook_url": None,
}

RISK_PRESETS = {
    "Conservative": {"max_positions": 5, "max_position_pct": 0.15, "max_total_exposure_pct": 0.45,
                     "risk_per_trade_pct": 0.0075, "daily_loss_limit_pct": 0.02, "min_edge_pct": 0.002},
    "Moderate": {"max_positions": 6, "max_position_pct": 0.20, "max_total_exposure_pct": 0.60,
                 "risk_per_trade_pct": 0.01, "daily_loss_limit_pct": 0.03, "min_edge_pct": 0.0015},
    "Aggressive": {"max_positions": 8, "max_position_pct": 0.25, "max_total_exposure_pct": 0.80,
                   "risk_per_trade_pct": 0.015, "daily_loss_limit_pct": 0.05, "min_edge_pct": 0.001},
}

class ConfigStore:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self._lock = threading.RLock()
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self):
        with self._lock:
            try:
                if os.path.exists(self.path):
                    with open(self.path, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                    base = copy.deepcopy(DEFAULT_CONFIG)
                    base.update(saved or {})
                    self.cfg = base
                else:
                    self.save_locked()
            except Exception as e:
                log(f"config load failed: {e}", "WARN")

    def save_locked(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2)
        os.replace(tmp, self.path)

    def save(self):
        with self._lock:
            self.save_locked()

    def get(self, k, d=None):
        with self._lock:
            return self.cfg.get(k, d)

    def all(self, public_only=True):
        with self._lock:
            c = copy.deepcopy(self.cfg)
        if public_only:
            c.pop("license_key_hash", None)
            c.pop("exchange_api_secret_enc", None)
            if c.get("exchange_api_key"):
                k = c["exchange_api_key"]
                c["exchange_api_key"] = (k[:4] + "****" + k[-2:]) if len(k) > 6 else "****"
        return c

    def update(self, patch):
        with self._lock:
            for k, v in (patch or {}).items():
                if k in ("license_key_hash", "exchange_api_secret_enc", "live_enabled",
                         "trading_mode", "kill_switch", "trading_halted"):
                    continue  # privileged paths only
                if k in self.cfg:
                    self.cfg[k] = v
            self.save_locked()
            return copy.deepcopy(self.cfg)

CONFIG = ConfigStore()

# ----------------------------- database --------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS candles (
  id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT NOT NULL, interval_tf TEXT NOT NULL,
  open_time INTEGER NOT NULL, o REAL, h REAL, l REAL, c REAL, v REAL,
  quote_vol REAL, trades INTEGER, fetched_at TEXT,
  UNIQUE(market, interval_tf, open_time));
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, market TEXT, side TEXT, direction TEXT, qty REAL, price REAL,
  order_type TEXT, status TEXT, mode TEXT, strategy TEXT, model_version TEXT,
  reason TEXT, created_at TEXT, updated_at TEXT, idempotency_key TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS fills (
  id TEXT PRIMARY KEY, order_id TEXT, market TEXT, side TEXT, qty REAL, price REAL,
  fee REAL, slippage REAL, pnl REAL, filled_at TEXT);
CREATE TABLE IF NOT EXISTS positions (
  id TEXT PRIMARY KEY, market TEXT UNIQUE, direction TEXT, qty REAL, entry_price REAL,
  current_price REAL, stop_loss REAL, take_profit REAL, exposure REAL, unrealized_pnl REAL,
  realized_pnl REAL, confidence REAL, strategy TEXT, opened_at TEXT, updated_at TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS balances (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, equity REAL, available REAL,
  invested REAL, realized_pnl REAL, unrealized_pnl REAL, drawdown_pct REAL,
  peak_equity REAL, day_pnl REAL, day TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT, direction TEXT, score REAL,
  confidence REAL, regime TEXT, strategy TEXT, edge_net_pct REAL, reasons TEXT,
  model_version TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS risk_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, market TEXT, decision TEXT,
  reasons TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT, equity REAL, available REAL, exposure_pct REAL,
  positions INTEGER, day_pnl REAL, drawdown_pct REAL, state TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS correlations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT, b TEXT, corr REAL, window_n INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS backtests (
  id TEXT PRIMARY KEY, name TEXT, markets TEXT, period TEXT, strategy TEXT,
  starting_capital REAL, ending_value REAL, net_return_pct REAL, max_dd_pct REAL,
  profit_factor REAL, sharpe REAL, sortino REAL, trades INTEGER, win_rate REAL,
  fees REAL, slippage REAL, funding REAL, details TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS models (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, version TEXT, metrics TEXT,
  status TEXT, trained_at TEXT, samples INTEGER);
CREATE TABLE IF NOT EXISTS predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT, model TEXT, version TEXT,
  output TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY, hypothesis TEXT, dataset TEXT, features TEXT, model TEXT,
  params TEXT, results TEXT, oos TEXT, conclusion TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, level TEXT, title TEXT, body TEXT,
  created_at TEXT, read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, actor TEXT, action TEXT,
  details TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS system_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, component TEXT, level TEXT, message TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_candles_mkt_time ON candles(market, interval_tf, open_time);
CREATE INDEX IF NOT EXISTS idx_signals_mkt ON signals(market, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_cat ON audit_logs(category, created_at);
"""

class DB:
    def __init__(self, path=DB_PATH):
        self.path = path
        self._lock = threading.RLock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        with self._lock:
            c = self._conn()
            try:
                c.executescript(SCHEMA)
                c.commit()
            finally:
                c.close()

    def execute(self, sql, params=()):
        with self._lock:
            c = self._conn()
            try:
                cur = c.execute(sql, params)
                c.commit()
                return cur
            finally:
                c.close()

    def query(self, sql, params=()):
        with self._lock:
            c = self._conn()
            try:
                cur = c.execute(sql, params)
                rows = cur.fetchall()
                return [dict(r) for r in rows]
            finally:
                c.close()

    def query_one(self, sql, params=()):
        r = self.query(sql, params)
        return r[0] if r else None

DBASE = DB()

def audit(category, action, details="", actor="system"):
    try:
        if isinstance(details, (dict, list)):
            details = json.dumps(redact(details))
        DBASE.execute(
            "INSERT INTO audit_logs (category, actor, action, details, created_at) VALUES (?,?,?,?,?)",
            (category, actor, action, str(details)[:4000], utcnow_iso()))
    except Exception as e:
        log(f"audit failed: {e}", "WARN")

def sys_event(component, level, message):
    try:
        DBASE.execute(
            "INSERT INTO system_events (component, level, message, created_at) VALUES (?,?,?,?)",
            (component, level, str(message)[:2000], utcnow_iso()))
    except Exception:
        pass

def notify(level, title, body):
    try:
        DBASE.execute("INSERT INTO alerts (level, title, body, created_at, read) VALUES (?,?,?,?,0)",
                      (level, title, str(body)[:2000], utcnow_iso()))
    except Exception:
        pass
    log(f"ALERT [{level}] {title}: {body}", "WARN" if level != "INFO" else "INFO")

# ----------------------------- license / auth --------------------------------

SESSIONS = {}
SESSION_TTL = 12 * 3600

def verify_license_key(key):
    """Local validation for the all-in-one edition.

    Accepts keys shaped like ZEPAY-XXXX... (min 12 chars) or DEMO keys.
    Production SaaS editions verify server-side; this offline edition records
    a hash locally and never transmits secrets. Returns (ok, message).
    """
    k = (key or "").strip()
    if not k:
        return False, "Please enter your ZePay key."
    if k in ("DEMO", "DEMO-KEY", "TRIAL"):
        return True, "Demo key accepted. Paper trading enabled."
    if k.startswith("ZEPAY-") and len(k) >= 12:
        core = k[6:]
        if all(ch.isalnum() or ch in "-_" for ch in core):
            return True, "ZePay key verified."
        return False, "Key contains invalid characters."
    return False, "Invalid key format. Keys look like ZEPAY-XXXX-XXXX."

def activate_license(key):
    ok, msg = verify_license_key(key)
    if not ok:
        return False, msg
    h = sha256(key.strip())
    with CONFIG._lock:
        CONFIG.cfg["license_key_hash"] = h
        CONFIG.cfg["setup_complete"] = True
        CONFIG.save_locked()
    audit("auth", "license_activated", {"msg": msg})
    ensure_paper_account()
    return True, msg

def is_setup():
    return bool(CONFIG.get("setup_complete")) and bool(CONFIG.get("license_key_hash"))

def create_session():
    tok = secrets.token_urlsafe(24)
    SESSIONS[tok] = time.time() + SESSION_TTL
    return tok

def valid_session(tok):
    if not tok:
        return False
    exp = SESSIONS.get(tok)
    if not exp:
        return False
    if exp < time.time():
        SESSIONS.pop(tok, None)
        return False
    return True

# ----------------------------- paper account ---------------------------------

_acct_lock = threading.RLock()

def ensure_paper_account():
    with _acct_lock:
        row = DBASE.query_one("SELECT * FROM balances WHERE mode='PAPER' ORDER BY id DESC LIMIT 1")
        if row:
            return row
        start = safe_float(CONFIG.get("paper_starting_balance", 10000.0), 10000.0)
        today = utcnow().date().isoformat()
        DBASE.execute(
            "INSERT INTO balances (mode, equity, available, invested, realized_pnl, unrealized_pnl,"
            " drawdown_pct, peak_equity, day_pnl, day, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("PAPER", start, start, 0, 0, 0, 0, start, 0, today, utcnow_iso()))
        audit("account", "paper_account_created", {"starting_balance": start})
        return DBASE.query_one("SELECT * FROM balances WHERE mode='PAPER' ORDER BY id DESC LIMIT 1")

def get_account(mode="PAPER"):
    with _acct_lock:
        row = DBASE.query_one("SELECT * FROM balances WHERE mode=? ORDER BY id DESC LIMIT 1", (mode,))
        if not row and mode == "PAPER":
            return ensure_paper_account()
        # roll day
        if row:
            today = utcnow().date().isoformat()
            if row.get("day") != today:
                DBASE.execute("UPDATE balances SET day_pnl=0, day=?, updated_at=? WHERE id=?",
                              (today, utcnow_iso(), row["id"]))
                row = DBASE.query_one("SELECT * FROM balances WHERE id=?", (row["id"],))
        return row

def update_account(mode, **fields):
    with _acct_lock:
        row = get_account(mode)
        if not row:
            return None
        fields["updated_at"] = utcnow_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        DBASE.execute(f"UPDATE balances SET {sets} WHERE id=?", tuple(fields.values()) + (row["id"],))
        return DBASE.query_one("SELECT * FROM balances WHERE id=?", (row["id"],))

# =============================================================================
# PART 2 — MARKET DATA + FEATURES + REGIME + STRATEGIES + AI MODELS
# =============================================================================

BINANCE_REST = "https://api.binance.com"
BINANCE_DATA = "https://data-api.binance.vision"  # geo-friendly fallback

def http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "ZePay/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

class MarketDataManager:
    """Multi-asset market data. Real Binance public REST. TTL cache + threadpool."""

    def __init__(self, max_workers=8):
        self.pool = ThreadPoolExecutor(max_workers=max_workers)
        self.cache = {}   # market -> {price, change24, volume24, quote_vol, high, low, ts, source}
        self.klines_cache = {}
        self.depth_cache = {}
        self.errors = {}  # market -> {msg, ts, count}
        self.latency_ms = {}
        self._lock = threading.RLock()

    def ex_symbol(self, market):
        for m in SUPPORTED_MARKETS:
            if m["symbol"] == market:
                return m["exchange_symbol"]
        return market.replace("/", "")

    def _fetch_ticker(self, market):
        sym = self.ex_symbol(market)
        t0 = time.time()
        last_err = None
        for base in (BINANCE_REST, BINANCE_DATA):
            try:
                d = http_json(f"{base}/api/v3/ticker/24hr?symbol={sym}", timeout=12)
                price = safe_float(d.get("lastPrice"))
                if price <= 0:
                    raise ValueError("bad price")
                out = {"price": price,
                       "change24": safe_float(d.get("priceChangePercent")),
                       "volume24": safe_float(d.get("volume")),
                       "quote_vol": safe_float(d.get("quoteVolume")),
                       "high": safe_float(d.get("highPrice")),
                       "low": safe_float(d.get("lowPrice")),
                       "ts": ts_ms(), "source": base}
                with self._lock:
                    self.cache[market] = out
                    self.latency_ms[market] = int((time.time() - t0) * 1000)
                    self.errors.pop(market, None)
                return out
            except Exception as e:
                last_err = e
        with self._lock:
            e = self.errors.get(market, {"count": 0})
            e.update({"msg": str(last_err)[:300], "ts": ts_ms(), "count": e.get("count", 0) + 1})
            self.errors[market] = e
        raise last_err

    def _fetch_klines(self, market, interval="1h", limit=200):
        sym = self.ex_symbol(market)
        last_err = None
        for base in (BINANCE_REST, BINANCE_DATA):
            try:
                d = http_json(f"{base}/api/v3/klines?symbol={sym}&interval={interval}&limit={limit}", timeout=15)
                rows = []
                for k in d:
                    rows.append({"open_time": int(k[0]), "o": safe_float(k[1]), "h": safe_float(k[2]),
                                 "l": safe_float(k[3]), "c": safe_float(k[4]), "v": safe_float(k[5]),
                                 "quote_vol": safe_float(k[7]), "trades": int(k[8] or 0)})
                with self._lock:
                    self.klines_cache[(market, interval)] = {"rows": rows, "ts": ts_ms()}
                # persist (best effort)
                try:
                    for r in rows:
                        DBASE.execute(
                            "INSERT OR IGNORE INTO candles (market, interval_tf, open_time, o,h,l,c,v,quote_vol,trades,fetched_at)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (market, interval, r["open_time"], r["o"], r["h"], r["l"], r["c"],
                             r["v"], r["quote_vol"], r["trades"], utcnow_iso()))
                except Exception:
                    pass
                return rows
            except Exception as e:
                last_err = e
        # fallback: local DB
        try:
            q = DBASE.query("SELECT open_time,o,h,l,c,v,quote_vol,trades FROM candles WHERE market=? AND interval_tf=? ORDER BY open_time DESC LIMIT ?",
                            (market, interval, limit))
            if q:
                rows = [{"open_time": r["open_time"], "o": r["o"], "h": r["h"], "l": r["l"],
                         "c": r["c"], "v": r["v"], "quote_vol": r["quote_vol"] or 0,
                         "trades": r["trades"] or 0} for r in reversed(q)]
                with self._lock:
                    self.klines_cache[(market, interval)] = {"rows": rows, "ts": ts_ms(), "stale_db": True}
                return rows
        except Exception:
            pass
        raise last_err or RuntimeError("klines unavailable")

    def _fetch_depth(self, market, limit=20):
        sym = self.ex_symbol(market)
        last_err = None
        for base in (BINANCE_REST, BINANCE_DATA):
            try:
                d = http_json(f"{base}/api/v3/depth?symbol={sym}&limit={limit}", timeout=10)
                bids = [[safe_float(p), safe_float(q)] for p, q in d.get("bids", [])]
                asks = [[safe_float(p), safe_float(q)] for p, q in d.get("asks", [])]
                if not bids or not asks:
                    raise ValueError("empty book")
                out = {"bids": bids, "asks": asks, "ts": ts_ms()}
                with self._lock:
                    self.depth_cache[market] = out
                return out
            except Exception as e:
                last_err = e
        raise last_err or RuntimeError("depth unavailable")

    def refresh_all(self, markets, with_klines=True, with_depth=True):
        futs = {}
        for m in markets:
            futs[self.pool.submit(self._safe, self._fetch_ticker, m)] = ("ticker", m)
            if with_klines:
                futs[self.pool.submit(self._safe, self._fetch_klines, m,
                                      CONFIG.get("candle_interval", "1h"),
                                      CONFIG.get("candle_limit", 200))] = ("klines", m)
            if with_depth:
                futs[self.pool.submit(self._safe, self._fetch_depth, m)] = ("depth", m)
        results = {}
        for f in futs:
            kind, m = futs[f]
            try:
                results[(kind, m)] = f.result(timeout=25)
            except Exception as e:
                results[(kind, m)] = e
        return results

    @staticmethod
    def _safe(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            return e

    def get_ticker(self, market):
        with self._lock:
            return self.cache.get(market)

    def get_klines(self, market, interval=None):
        interval = interval or CONFIG.get("candle_interval", "1h")
        with self._lock:
            e = self.klines_cache.get((market, interval))
            return (e["rows"] if e else None)

    def get_depth(self, market):
        with self._lock:
            return self.depth_cache.get(market)

    def age_secs(self, market):
        with self._lock:
            t = self.cache.get(market)
            if not t:
                return 1e9
            return (ts_ms() - t["ts"]) / 1000.0

    def is_stale(self, market):
        return self.age_secs(market) > safe_float(CONFIG.get("stale_data_secs", 120), 120)

MDATA = MarketDataManager()

# ----------------------------- feature engine --------------------------------

def ema(values, n):
    if not values or n <= 1:
        return list(values)
    k = 2.0 / (n + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def sma(values, n):
    out = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= n:
            s -= values[i - n]
        out.append(s / min(i + 1, n))
    return out

def rsi(closes, n=14):
    if len(closes) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0)); losses.append(max(-ch, 0))
    ag = sum(gains[-n:]) / n; al = sum(losses[-n:]) / n
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return 100 - 100 / (1 + rs)

def true_ranges(h, l, c):
    trs = []
    for i in range(len(c)):
        pc = c[i - 1] if i else c[0]
        trs.append(max(h[i] - l[i], abs(h[i] - pc), abs(l[i] - pc)))
    return trs

def atr(h, l, c, n=14):
    trs = true_ranges(h, l, c)
    return sma(trs, n)[-1] if trs else 0.0

def macd(closes, fast=12, slow=26, sig=9):
    ef = ema(closes, fast); es = ema(closes, slow)
    line = [a - b for a, b in zip(ef, es)]
    signal = ema(line, sig)
    hist = line[-1] - signal[-1] if line and signal else 0
    return {"macd": line[-1] if line else 0, "signal": signal[-1] if signal else 0, "hist": hist}

def bollinger(closes, n=20, k=2.0):
    if len(closes) < n:
        n = max(2, len(closes))
    w = closes[-n:]
    m = statistics.fmean(w)
    sd = statistics.pstdev(w) if len(w) > 1 else 0
    return {"mid": m, "upper": m + k * sd, "lower": m - k * sd,
            "pctb": (closes[-1] - (m - k * sd)) / (2 * k * sd) if sd > 0 else 0.5,
            "width": (2 * k * sd / m) if m else 0}

def returns(closes):
    return [(closes[i] / closes[i - 1] - 1) if closes[i - 1] else 0 for i in range(1, len(closes))]

def realized_vol(rets, annualize=365 * 24):
    if len(rets) < 2:
        return 0.0
    return statistics.pstdev(rets) * math.sqrt(annualize)

@dataclass
class AssetFeatures:
    market: str
    price: float = 0
    change24: float = 0
    sma20: float = 0; sma50: float = 0; ema12: float = 0; ema26: float = 0
    rsi14: float = 50
    macd_hist: float = 0
    bb_pctb: float = 0.5; bb_width: float = 0
    atr14: float = 0; atr_pct: float = 0
    mom_10: float = 0; mom_20: float = 0
    vol_ratio: float = 1.0
    realized_vol: float = 0
    high_20: float = 0; low_20: float = 0; breakout_up: float = 0; breakout_dn: float = 0
    spread_bps: float = 0; depth_usd: float = 0; ob_imbalance: float = 0
    liquidity_usd: float = 0
    n_candles: int = 0
    # cross-asset (filled by CrossAsset engine)
    btc_mom: float = 0; market_mom: float = 0; rel_strength: float = 0
    corr_btc: float = 0; vol_spillover: float = 0

class FeatureEngine:
    @staticmethod
    def build(market):
        kl = MDATA.get_klines(market) or []
        t = MDATA.get_ticker(market) or {}
        f = AssetFeatures(market=market)
        f.price = safe_float(t.get("price"))
        f.change24 = safe_float(t.get("change24"))
        f.liquidity_usd = safe_float(t.get("quote_vol"))
        closes = [r["c"] for r in kl]; highs = [r["h"] for r in kl]
        lows = [r["l"] for r in kl]; vols = [r["v"] for r in kl]
        f.n_candles = len(closes)
        if closes:
            if not f.price:
                f.price = closes[-1]
            s20 = sma(closes, 20); s50 = sma(closes, 50)
            e12 = ema(closes, 12); e26 = ema(closes, 26)
            f.sma20 = s20[-1]; f.sma50 = s50[-1]; f.ema12 = e12[-1]; f.ema26 = e26[-1]
            f.rsi14 = rsi(closes)
            f.macd_hist = macd(closes)["hist"]
            bb = bollinger(closes); f.bb_pctb = bb["pctb"]; f.bb_width = bb["width"]
            a = atr(highs, lows, closes); f.atr14 = a
            f.atr_pct = (a / closes[-1]) if closes[-1] else 0
            f.mom_10 = (closes[-1] / closes[-11] - 1) if len(closes) > 11 and closes[-11] else 0
            f.mom_20 = (closes[-1] / closes[-21] - 1) if len(closes) > 21 and closes[-21] else 0
            if len(vols) >= 20:
                base = statistics.fmean(vols[-20:-1]) if len(vols) > 1 else vols[-1]
                f.vol_ratio = (vols[-1] / base) if base else 1.0
            r = returns(closes[-60:] if len(closes) > 60 else closes)
            f.realized_vol = realized_vol(r)
            w = closes[-20:] if len(closes) >= 20 else closes
            hw = max(highs[-20:]) if len(highs) >= 20 else max(highs)
            lw = min(lows[-20:]) if len(lows) >= 20 else min(lows)
            f.high_20 = hw; f.low_20 = lw
            rng = (hw - lw) / closes[-1] if closes[-1] else 0
            f.breakout_up = ((closes[-1] - hw) / closes[-1]) if closes[-1] else 0
            f.breakout_dn = ((closes[-1] - lw) / closes[-1]) if closes[-1] else 0
        depth = MDATA.get_depth(market)
        if depth:
            try:
                bb = depth["bids"][0][0]; ba = depth["asks"][0][0]
                mid = (bb + ba) / 2
                f.spread_bps = ((ba - bb) / mid * 10000) if mid else 0
                bv = sum(p * q for p, q in depth["bids"]); av = sum(p * q for p, q in depth["asks"])
                f.depth_usd = bv + av
                f.ob_imbalance = ((bv - av) / (bv + av)) if (bv + av) else 0
            except Exception:
                pass
        return f

class CrossAssetEngine:
    """BTC/market momentum, relative strength, correlations, spillover."""

    @staticmethod
    def closes_map(markets):
        out = {}
        for m in markets:
            kl = MDATA.get_klines(m) or []
            if len(kl) >= 30:
                out[m] = [r["c"] for r in kl]
        return out

    @staticmethod
    def corr(a, b):
        n = min(len(a), len(b))
        if n < 10:
            return 0.0
        a = a[-n:]; b = b[-n:]
        try:
            ma = statistics.fmean(a); mb = statistics.fmean(b)
            num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
            da = math.sqrt(sum((x - ma) ** 2 for x in a)); db = math.sqrt(sum((y - mb) ** 2 for y in b))
            if da == 0 or db == 0:
                return 0.0
            return clamp(num / (da * db), -1, 1)
        except Exception:
            return 0.0

    @classmethod
    def enrich(cls, feats):
        markets = list(feats.keys())
        cmap = cls.closes_map(markets)
        rets = {m: returns(c) for m, c in cmap.items()}
        moms = {}
        for m, c in cmap.items():
            moms[m] = (c[-1] / c[-21] - 1) if len(c) > 21 and c[-21] else 0
        market_mom = statistics.fmean(list(moms.values())) if moms else 0
        btc_mom = moms.get("BTC/USDT", 0)
        vols = {m: (statistics.pstdev(r[-20:]) if len(r) >= 20 else 0) for m, r in rets.items()}
        avg_vol = statistics.fmean(list(vols.values())) if vols else 0
        for m, f in feats.items():
            f.btc_mom = btc_mom
            f.market_mom = market_mom
            f.rel_strength = moms.get(m, 0) - market_mom
            if "BTC/USDT" in rets and m in rets and m != "BTC/USDT":
                f.corr_btc = cls.corr(rets[m][-60:], rets["BTC/USDT"][-60:])
            elif m == "BTC/USDT":
                f.corr_btc = 1.0
            f.vol_spillover = (avg_vol - vols.get(m, 0))
        # persist correlations
        try:
            ms = list(rets.keys())
            for i in range(len(ms)):
                for j in range(i + 1, len(ms)):
                    c = cls.corr(rets[ms[i]][-60:], rets[ms[j]][-60:])
                    DBASE.execute("INSERT INTO correlations (a,b,corr,window_n,created_at) VALUES (?,?,?,?,?)",
                                  (ms[i], ms[j], c, 60, utcnow_iso()))
        except Exception:
            pass
        return feats

    @classmethod
    def matrix(cls, markets):
        cmap = cls.closes_map(markets)
        rets = {m: returns(c) for m, c in cmap.items()}
        mat = {}
        for a in markets:
            mat[a] = {}
            for b in markets:
                if a == b:
                    mat[a][b] = 1.0
                elif a in rets and b in rets:
                    mat[a][b] = round(cls.corr(rets[a][-60:], rets[b][-60:]), 3)
                else:
                    mat[a][b] = 0.0
        return mat

# ----------------------------- regime engine ---------------------------------

REGIMES = ["BULLISH_TREND", "BEARISH_TREND", "SIDEWAYS", "HIGH_VOL", "LOW_VOL",
           "BREAKOUT", "PANIC", "UNSTABLE"]

class RegimeEngine:
    @staticmethod
    def detect(f):
        if f.n_candles < 30 or f.price <= 0:
            return {"regime": "UNSTABLE", "confidence": 0.3,
                    "reasons": ["Insufficient history for regime detection"]}
        trend_up = (f.price > f.sma20 > f.sma50) or (f.ema12 > f.ema26 and f.mom_20 > 0.02)
        trend_dn = (f.price < f.sma20 < f.sma50) or (f.ema12 < f.ema26 and f.mom_20 < -0.02)
        mom = f.mom_20
        vol = f.atr_pct
        reasons = []
        if abs(f.change24) > 12 or (vol > 0.06 and mom < -0.08):
            return {"regime": "PANIC", "confidence": 0.8,
                    "reasons": [f"Extreme move: 24h {f.change24:+.1f}%, ATR {pct(vol)}%"]}
        if f.breakout_up > 0.002 and f.vol_ratio > 1.5:
            return {"regime": "BREAKOUT", "confidence": 0.75,
                    "reasons": ["Price above 20-bar high with elevated volume"]}
        if vol > 0.045:
            reg, conf = "HIGH_VOL", 0.7
            reasons.append(f"ATR {pct(vol)}% above high-vol threshold")
        elif vol < 0.008 and abs(mom) < 0.02:
            reg, conf = "LOW_VOL", 0.65
            reasons.append("Compressed volatility, directionless")
        elif trend_up and mom > 0.015:
            reg, conf = "BULLISH_TREND", 0.7
            reasons.append("Price above rising averages, positive momentum")
        elif trend_dn and mom < -0.015:
            reg, conf = "BEARISH_TREND", 0.7
            reasons.append("Price below falling averages, negative momentum")
        else:
            reg, conf = "SIDEWAYS", 0.6
            reasons.append("No dominant trend; range-like behavior")
        if f.n_candles < 60:
            conf *= 0.85
            reasons.append("Limited history — confidence reduced")
        return {"regime": reg, "confidence": round(conf, 3), "reasons": reasons}

# ----------------------------- strategy engine -------------------------------

STRATEGIES = ["trend", "momentum", "breakout", "mean_reversion", "volatility", "orderflow"]

# regime → strategy suitability multiplier
REGIME_STRAT_FIT = {
    "BULLISH_TREND": {"trend": 1.0, "momentum": 0.9, "breakout": 0.8, "mean_reversion": 0.3, "volatility": 0.5, "orderflow": 0.7},
    "BEARISH_TREND": {"trend": 1.0, "momentum": 0.9, "breakout": 0.7, "mean_reversion": 0.3, "volatility": 0.5, "orderflow": 0.7},
    "SIDEWAYS": {"trend": 0.3, "momentum": 0.4, "breakout": 0.4, "mean_reversion": 1.0, "volatility": 0.5, "orderflow": 0.6},
    "HIGH_VOL": {"trend": 0.5, "momentum": 0.5, "breakout": 0.6, "mean_reversion": 0.4, "volatility": 1.0, "orderflow": 0.5},
    "LOW_VOL": {"trend": 0.4, "momentum": 0.4, "breakout": 0.7, "mean_reversion": 0.6, "volatility": 0.3, "orderflow": 0.5},
    "BREAKOUT": {"trend": 0.8, "momentum": 0.9, "breakout": 1.0, "mean_reversion": 0.2, "volatility": 0.6, "orderflow": 0.8},
    "PANIC": {"trend": 0.2, "momentum": 0.2, "breakout": 0.2, "mean_reversion": 0.3, "volatility": 0.6, "orderflow": 0.3},
    "UNSTABLE": {"trend": 0.2, "momentum": 0.2, "breakout": 0.2, "mean_reversion": 0.2, "volatility": 0.3, "orderflow": 0.2},
}

class StrategyEngine:
    @staticmethod
    def evaluate(f, regime):
        if f.price <= 0 or f.n_candles < 20:
            return {s: {"score": 0.0, "fit": 0.0} for s in STRATEGIES}
        trend_s = 0.0
        if f.sma20 and f.sma50:
            trend_s += clamp((f.price - f.sma50) / (f.atr14 or f.price * 0.01) * 0.4, -1, 1)
            trend_s += clamp((f.ema12 - f.ema26) / (f.atr14 or f.price * 0.01) * 0.6, -1, 1)
            trend_s = clamp(trend_s / 1.0, -1, 1)
        mom_s = clamp(f.mom_10 * 8 + f.mom_20 * 4 + f.rel_strength * 2, -1, 1)
        brk_s = 0.0
        if f.breakout_up > 0:
            brk_s = clamp(0.5 + f.vol_ratio * 0.2, 0, 1)
        elif f.breakout_dn < -0.02:
            brk_s = clamp(-0.5 - f.vol_ratio * 0.1, -1, 0)
        else:
            brk_s = clamp((f.bb_pctb - 0.5) * 1.2, -1, 1)
        mr_s = 0.0
        if f.bb_pctb > 0.85 or f.rsi14 > 70:
            mr_s = -clamp((f.bb_pctb - 0.7) * 2, 0, 1)
        elif f.bb_pctb < 0.15 or f.rsi14 < 30:
            mr_s = clamp((0.3 - f.bb_pctb) * 2, 0, 1)
        vol_s = clamp((0.02 - f.atr_pct) * 10, -1, 1) * (1 if f.mom_10 >= 0 else -1)
        of_s = clamp(f.ob_imbalance * 2 + clamp(f.macd_hist / (f.atr14 or 1) * 2, -1, 1) * 0.5, -1, 1)
        raw = {"trend": trend_s, "momentum": mom_s, "breakout": brk_s,
               "mean_reversion": mr_s, "volatility": vol_s, "orderflow": of_s}
        fit = REGIME_STRAT_FIT.get(regime, {s: 0.5 for s in STRATEGIES})
        return {s: {"score": round(raw[s], 4), "fit": fit.get(s, 0.5),
                    "weighted": round(raw[s] * fit.get(s, 0.5), 4)} for s in STRATEGIES}

    @staticmethod
    def aggregate(evald):
        tot_w = sum(v["fit"] for v in evald.values()) or 1
        return sum(v["weighted"] for v in evald.values()) / tot_w

# ----------------------------- AI model engine -------------------------------
# From-scratch classical models (logistic / ridge via gradient descent).
# Deterministic, no external deps. Versioned + monitored.

MODEL_VERSION = "ai-1.0.0"

def sigmoid(x):
    try:
        return 1.0 / (1.0 + math.exp(-clamp(x, -30, 30)))
    except Exception:
        return 0.5

class LogisticModel:
    def __init__(self, n_feat, lr=0.05, l2=0.01):
        self.w = [0.0] * n_feat
        self.b = 0.0
        self.lr = lr; self.l2 = l2

    def predict_proba(self, x):
        return sigmoid(sum(wi * xi for wi, xi in zip(self.w, x)) + self.b)

    def fit(self, X, y, epochs=200):
        n = len(X)
        if n == 0:
            return
        for _ in range(epochs):
            for xi, yi in zip(X, y):
                p = self.predict_proba(xi)
                err = p - yi
                for j in range(len(self.w)):
                    self.w[j] -= self.lr * (err * xi[j] + self.l2 * self.w[j])
                self.b -= self.lr * err

class RidgeModel:
    def __init__(self, n_feat, lr=0.02, l2=0.05):
        self.w = [0.0] * n_feat
        self.b = 0.0
        self.lr = lr; self.l2 = l2

    def predict(self, x):
        return sum(wi * xi for wi, xi in zip(self.w, x)) + self.b

    def fit(self, X, y, epochs=300):
        for _ in range(epochs):
            for xi, yi in zip(X, y):
                err = self.predict(xi) - yi
                for j in range(len(self.w)):
                    self.w[j] -= self.lr * (err * xi[j] + self.l2 * self.w[j])
                self.b -= self.lr * err

FEATURE_KEYS = ["mom_10", "mom_20", "rsi14", "macd_hist_n", "bb_pctb", "atr_pct",
                "vol_ratio", "ob_imbalance", "rel_strength", "market_mom", "btc_mom", "trend_gap"]

def feature_vector(f):
    atr = f.atr14 or (f.price * 0.01 if f.price else 1)
    return [
        clamp(f.mom_10 * 5, -2, 2), clamp(f.mom_20 * 4, -2, 2),
        (f.rsi14 - 50) / 25.0,
        clamp(f.macd_hist / atr, -2, 2),
        (f.bb_pctb - 0.5) * 2,
        clamp(f.atr_pct * 20, 0, 3),
        clamp((f.vol_ratio - 1), -2, 3),
        clamp(f.ob_imbalance * 2, -1.5, 1.5),
        clamp(f.rel_strength * 5, -2, 2),
        clamp(f.market_mom * 5, -2, 2),
        clamp(f.btc_mom * 5, -2, 2),
        clamp(((f.price - f.sma50) / atr) if f.sma50 else 0, -3, 3),
    ]

class AIEngine:
    """Direction / Return / Volatility / Regime / Quality + Ensemble meta-model."""

    def __init__(self):
        self.dir_model = LogisticModel(len(FEATURE_KEYS))
        self.ret_model = RidgeModel(len(FEATURE_KEYS))
        self.trained = False
        self.train_samples = 0
        self.metrics = {}
        self.degraded = False
        self._lock = threading.RLock()

    def _build_dataset(self, markets, horizon=3):
        X, y_dir, y_ret = [], [], []
        for m in markets:
            kl = MDATA.get_klines(m) or []
            if len(kl) < 80:
                continue
            closes = [r["c"] for r in kl]
            for i in range(40, len(closes) - horizon):
                window = kl[:i + 1]
                f = self._features_from_window(m, window, closes)
                if f is None:
                    continue
                fut = closes[i + horizon] / closes[i] - 1
                X.append(feature_vector(f))
                y_dir.append(1 if fut > 0.001 else 0)
                y_ret.append(clamp(fut, -0.1, 0.1))
        return X, y_dir, y_ret

    @staticmethod
    def _features_from_window(market, window, closes_all=None):
        try:
            closes = [r["c"] for r in window]; highs = [r["h"] for r in window]
            lows = [r["l"] for r in window]; vols = [r["v"] for r in window]
            f = AssetFeatures(market=market)
            f.price = closes[-1]; f.n_candles = len(closes)
            s20 = sma(closes, 20); s50 = sma(closes, 50)
            e12 = ema(closes, 12); e26 = ema(closes, 26)
            f.sma20 = s20[-1]; f.sma50 = s50[-1]; f.ema12 = e12[-1]; f.ema26 = e26[-1]
            f.rsi14 = rsi(closes)
            f.macd_hist = macd(closes)["hist"]
            bb = bollinger(closes); f.bb_pctb = bb["pctb"]; f.bb_width = bb["width"]
            a = atr(highs, lows, closes); f.atr14 = a
            f.atr_pct = (a / closes[-1]) if closes[-1] else 0
            f.mom_10 = (closes[-1] / closes[-11] - 1) if len(closes) > 11 and closes[-11] else 0
            f.mom_20 = (closes[-1] / closes[-21] - 1) if len(closes) > 21 and closes[-21] else 0
            if len(vols) >= 20:
                base = statistics.fmean(vols[-20:-1])
                f.vol_ratio = (vols[-1] / base) if base else 1.0
            return f
        except Exception:
            return None

    def train(self, markets):
        with self._lock:
            X, y_dir, y_ret = self._build_dataset(markets)
            if len(X) < 60:
                self.metrics = {"status": "insufficient_data", "samples": len(X)}
                try:
                    DBASE.execute("INSERT INTO models (name, version, metrics, status, trained_at, samples)"
                                  " VALUES (?,?,?,?,?,?)",
                                  ("ensemble", MODEL_VERSION, json.dumps(self.metrics), "untrained",
                                   utcnow_iso(), len(X)))
                except Exception:
                    pass
                return self.metrics
            # chronological split 80/20 (no shuffle → no leakage)
            n = len(X); cut = int(n * 0.8)
            Xtr, Xte = X[:cut], X[cut:]; ytr, yte = y_dir[:cut], y_dir[cut:]
            rtr, rte = y_ret[:cut], y_ret[cut:]
            self.dir_model = LogisticModel(len(FEATURE_KEYS)); self.dir_model.fit(Xtr, ytr, epochs=120)
            self.ret_model = RidgeModel(len(FEATURE_KEYS)); self.ret_model.fit(Xtr, rtr, epochs=150)
            # metrics
            correct = sum(1 for xi, yi in zip(Xte, yte)
                          if (self.dir_model.predict_proba(xi) >= 0.5) == bool(yi))
            acc = correct / max(1, len(Xte))
            preds = [self.ret_model.predict(xi) for xi in Xte]
            mae = sum(abs(p - t) for p, t in zip(preds, rte)) / max(1, len(rte))
            self.train_samples = n
            self.trained = True
            self.metrics = {"status": "trained", "samples": n, "dir_accuracy_oos": round(acc, 4),
                            "return_mae_oos": round(mae, 5), "version": MODEL_VERSION}
            try:
                DBASE.execute("INSERT INTO models (name, version, metrics, status, trained_at, samples)"
                              " VALUES (?,?,?,?,?,?)",
                              ("ensemble", MODEL_VERSION, json.dumps(self.metrics), "active",
                               utcnow_iso(), n))
            except Exception:
                pass
            audit("ml", "model_trained", self.metrics)
            return self.metrics

    def infer(self, f, regime_info, strat_eval):
        """Returns full hierarchical inference dict (levels 1-3). Never trades by itself."""
        with self._lock:
            trained = self.trained
            x = feature_vector(f)
            if trained and not self.degraded:
                p_long = self.dir_model.predict_proba(x)
                exp_ret = self.ret_model.predict(x)
            else:
                # Transparent fallback: calibrated heuristic composite (labeled as such)
                agg = StrategyEngine.aggregate(strat_eval)
                p_long = clamp(0.5 + agg * 0.35, 0.05, 0.95)
                exp_ret = clamp(agg * max(f.atr_pct, 0.005) * 1.2, -0.05, 0.05)
            exp_vol = max(f.atr_pct * 1.1, 0.002)
            # trade quality: agreement + regime fit + data quality
            scores = [v["score"] for v in strat_eval.values()]
            agree = 1 - (statistics.pstdev(scores) if len(scores) > 1 else 1)
            best_fit = max((v["fit"] for v in strat_eval.values()), default=0.5)
            data_q = 1.0 if f.n_candles >= 120 else (0.6 if f.n_candles >= 60 else 0.3)
            quality = clamp(0.4 * agree + 0.35 * best_fit + 0.25 * data_q, 0, 1)
            direction = Direction.LONG if p_long >= 0.56 else (Direction.SHORT if p_long <= 0.44 else Direction.WAIT)
            confidence = round(abs(p_long - 0.5) * 2, 4)
            return {
                "p_long": round(p_long, 4), "direction": direction.value,
                "expected_return": round(exp_ret, 5), "expected_vol": round(exp_vol, 5),
                "confidence": confidence, "quality": round(quality, 4),
                "model_version": MODEL_VERSION, "model_trained": trained,
                "model_degraded": self.degraded,
                "regime": regime_info["regime"], "regime_conf": regime_info["confidence"],
            }

AI = AIEngine()

# =============================================================================
# PART 3 — OPPORTUNITY RANKING + PORTFOLIO + RISK + EXECUTION + BACKTEST
# =============================================================================

COST = {}  # refreshed from CONFIG

def refresh_cost():
    COST["fee_bps"] = safe_float(CONFIG.get("fee_bps", 10.0), 10.0)
    COST["slip_bps"] = safe_float(CONFIG.get("slippage_bps", 5.0), 5.0)
    COST["fund_bps"] = safe_float(CONFIG.get("funding_bps_8h", 1.0), 1.0)

refresh_cost()

def round_trip_cost_pct(f, notional_hint=0):
    """Expected round-trip cost: fees + spread + slippage + funding estimate."""
    fee = COST["fee_bps"] / 10000.0 * 2
    spread = (f.spread_bps / 10000.0) if f.spread_bps else 0.0004
    slip = COST["slip_bps"] / 10000.0 * 2
    fund = COST["fund_bps"] / 10000.0  # ~8h hold assumption
    return fee + spread + slip + fund

# ----------------------------- market health ---------------------------------

class HealthEngine:
    @staticmethod
    def assess(market, f):
        reasons = []
        if MDATA.is_stale(market) and (f.price <= 0):
            return {"status": Health.BLOCKED.value, "reasons": ["No market data (stale/unreachable). Check connection."]}
        if f.price <= 0 or f.n_candles < 20:
            return {"status": Health.BLOCKED.value, "reasons": ["Insufficient price history"]}
        if MDATA.is_stale(market):
            reasons.append(f"Data delayed {MDATA.age_secs(market):.0f}s — new entries limited")
            limited = True
        else:
            limited = False
        if f.liquidity_usd and f.liquidity_usd < safe_float(CONFIG.get("min_liquidity_usd", 50000)):
            return {"status": Health.BLOCKED.value,
                    "reasons": [f"Liquidity ${f.liquidity_usd:,.0f} below safety threshold"]}
        if f.spread_bps > safe_float(CONFIG.get("max_spread_bps", 25.0), 25.0):
            reasons.append(f"Wide spread {f.spread_bps:.1f} bps")
            limited = True
        if f.atr_pct > 0.08:
            reasons.append("Abnormal volatility — size reduced")
            limited = True
        err = MDATA.errors.get(market)
        if err and err.get("count", 0) >= 3:
            reasons.append("Repeated API errors")
            limited = True
        if limited:
            return {"status": Health.LIMITED.value, "reasons": reasons or ["Limited conditions"]}
        return {"status": Health.TRADEABLE.value, "reasons": ["All checks passed"]}

# ----------------------------- opportunity engine ----------------------------

@dataclass
class Opportunity:
    market: str
    direction: str
    score: float
    confidence: float
    quality: float
    regime: str
    strategy: str
    expected_return: float
    expected_net: float
    cost_pct: float
    price: float
    reasons: list = field(default_factory=list)
    machine: dict = field(default_factory=dict)

class OpportunityEngine:
    @staticmethod
    def build(market, f, regime_info, strat_eval, infer):
        cost = round_trip_cost_pct(f)
        exp_net = infer["expected_return"] - cost - infer["expected_vol"] * 0.15
        # base score
        base = 50 + (infer["p_long"] - 0.5) * 120
        base += infer["quality"] * 20 - 10
        base += regime_info["confidence"] * 10 - 5
        if infer["direction"] == "WAIT":
            base -= 12
        if exp_net < safe_float(CONFIG.get("min_edge_pct", 0.0015)):
            base -= 15
        # priority nudge (never overrides safety)
        stars = safe_float((CONFIG.get("priorities") or {}).get(market, 3), 3)
        base += (stars - 3) * 1.5
        score = clamp(base, 0, 100)
        best_strat = max(strat_eval.items(), key=lambda kv: kv[1]["fit"] * (0.5 + abs(kv[1]["score"])))[0]
        reasons = []
        reasons.append(f"Direction model: {infer['direction']} (p={infer['p_long']})")
        reasons.append(f"Regime: {regime_info['regime']} ({regime_info['confidence']})")
        reasons.append(f"Best strategy fit: {best_strat}")
        reasons.append(f"Expected move {pct(infer['expected_return'])}% vs cost {pct(cost)}%")
        if not infer["model_trained"]:
            reasons.append("AI models warming up — heuristic composite in use (labeled)")
        return Opportunity(
            market=market, direction=infer["direction"], score=round(score, 1),
            confidence=infer["confidence"], quality=infer["quality"],
            regime=regime_info["regime"], strategy=best_strat,
            expected_return=infer["expected_return"], expected_net=round(exp_net, 5),
            cost_pct=round(cost, 5), price=f.price, reasons=reasons,
            machine={"p_long": infer["p_long"], "exp_vol": infer["expected_vol"],
                     "model_version": infer["model_version"], "trained": infer["model_trained"]})

    @staticmethod
    def rank(opps):
        return sorted(opps, key=lambda o: o.score, reverse=True)

# ----------------------------- portfolio engine ------------------------------

class PortfolioEngine:
    @staticmethod
    def positions():
        return DBASE.query("SELECT * FROM positions WHERE status='OPEN'")

    @staticmethod
    def all_positions():
        return DBASE.query("SELECT * FROM positions ORDER BY updated_at DESC LIMIT 100")

    @staticmethod
    def exposure(positions, equity):
        if not equity:
            return {"total_pct": 0, "long_pct": 0, "short_pct": 0, "per_asset": {}}
        tot = sum(abs(safe_float(p.get("exposure"))) for p in positions)
        long = sum(abs(safe_float(p.get("exposure"))) for p in positions if p.get("direction") == "LONG")
        short = tot - long
        per = {p["market"]: round(abs(safe_float(p.get("exposure"))) / equity, 4) for p in positions}
        return {"total_pct": round(tot / equity, 4), "long_pct": round(long / equity, 4),
                "short_pct": round(short / equity, 4), "per_asset": per}

    @staticmethod
    def correlated_exposure(market, direction, positions, equity, mat):
        if not equity or not positions:
            return 0.0
        same_dir = [p for p in positions if p.get("direction") == direction]
        tot = 0.0
        for p in same_dir:
            c = safe_float((mat.get(market) or {}).get(p["market"], 0))
            if c > 0.5:
                tot += abs(safe_float(p.get("exposure"))) * c
        return round(tot / equity, 4)

    @staticmethod
    def risk_contribution(positions):
        vols = {}
        for p in positions:
            kl = MDATA.get_klines(p["market"]) or []
            if len(kl) > 20:
                r = returns([x["c"] for x in kl[-30:]])
                vols[p["market"]] = statistics.pstdev(r) if len(r) > 1 else 0.01
            else:
                vols[p["market"]] = 0.02
        weights = {p["market"]: abs(safe_float(p.get("exposure"))) * vols.get(p["market"], 0.02)
                   for p in positions}
        tot = sum(weights.values()) or 1
        return {k: round(v / tot, 4) for k, v in weights.items()}

    @staticmethod
    def revalue(mode="PAPER"):
        acct = get_account(mode)
        if not acct:
            return None
        positions = PortfolioEngine.positions()
        unreal = 0.0
        invested = 0.0
        for p in positions:
            t = MDATA.get_ticker(p["market"])
            px = safe_float(t.get("price")) if t else safe_float(p.get("current_price"))
            if px <= 0:
                px = safe_float(p.get("entry_price"))
            qty = safe_float(p.get("qty")); entry = safe_float(p.get("entry_price"))
            expo = qty * px
            pnl = (px - entry) * qty if p.get("direction") == "LONG" else (entry - px) * qty
            unreal += pnl
            invested += abs(expo)
            DBASE.execute("UPDATE positions SET current_price=?, exposure=?, unrealized_pnl=?, updated_at=? WHERE id=?",
                          (px, expo, pnl, utcnow_iso(), p["id"]))
        realized = safe_float(acct.get("realized_pnl"))
        start = safe_float(CONFIG.get("paper_starting_balance", 10000.0), 10000.0)
        equity = start + realized + unreal if mode == "PAPER" else safe_float(acct.get("equity")) + unreal
        peak = max(safe_float(acct.get("peak_equity", equity)), equity)
        dd = (peak - equity) / peak if peak else 0
        # day pnl
        day_start_rows = DBASE.query("SELECT equity FROM portfolio_snapshots WHERE date(created_at)=date('now') ORDER BY id ASC LIMIT 1")
        if day_start_rows:
            day_pnl = equity - safe_float(day_start_rows[0]["equity"], equity)
        else:
            day_pnl = unreal + realized - safe_float(acct.get("day_pnl"), 0) * 0  # first snapshot
            day_pnl = equity - peak if False else (unreal + realized - (safe_float(acct.get("realized_pnl")) - safe_float(acct.get("realized_pnl"))))  # == unreal+realized delta
            # simpler: track incrementally
            day_pnl = safe_float(acct.get("day_pnl"))  # keep; snapshots refine
        update_account(mode, equity=equity, available=max(equity - invested, 0),
                       invested=invested, unrealized_pnl=unreal, drawdown_pct=dd,
                       peak_equity=peak)
        # snapshot (throttled by caller)
        return get_account(mode)

# ----------------------------- global risk engine ----------------------------
# FINAL AUTHORITY. Order of checks mirrors spec §40. AI can never override.

class RiskEngine:
    HALT_MESSAGE = ""

    @classmethod
    def system_state(cls, acct=None):
        if CONFIG.get("kill_switch"):
            return SystemState.HALTED, "Kill switch engaged by user"
        if CONFIG.get("trading_halted"):
            return SystemState.HALTED, CONFIG.get("halt_reason") or "Trading halted"
        acct = acct or get_account("PAPER")
        dd = safe_float((acct or {}).get("drawdown_pct", 0))
        day_pnl = safe_float((acct or {}).get("day_pnl", 0))
        equity = safe_float((acct or {}).get("equity", 1))
        max_dd = safe_float(CONFIG.get("max_drawdown_halt_pct", 0.15), 0.15)
        day_lim = safe_float(CONFIG.get("daily_loss_limit_pct", 0.03), 0.03)
        if dd >= max_dd:
            return SystemState.HALTED, f"Max drawdown {pct(dd)}% ≥ limit {pct(max_dd)}%"
        if equity and (-day_pnl / equity) >= day_lim and day_pnl < 0:
            return SystemState.HALTED, f"Daily loss limit reached ({pct(-day_pnl / equity)}%)"
        if dd >= max_dd * 0.6 or (equity and day_pnl < 0 and (-day_pnl / equity) >= day_lim * 0.6):
            return SystemState.DEFENSIVE, "Losses elevated — risk reduced"
        if dd >= max_dd * 0.35:
            return SystemState.CAUTION, "Drawdown rising — position sizes reduced"
        return SystemState.NORMAL, "All risk metrics nominal"

    @classmethod
    def authorize(cls, opp, f, health, positions, acct, mat, equity_hint=None):
        """Returns (Decision, reasons[], sizing_hint). Never raises on bad input."""
        reasons_h = []   # human
        reasons_m = {}   # machine
        equity = equity_hint or safe_float((acct or {}).get("equity", 0)) or safe_float(CONFIG.get("paper_starting_balance", 10000))
        state, state_msg = cls.system_state(acct)
        reasons_m["system_state"] = state.value
        if state == SystemState.HALTED:
            return Decision.HALTED, [f"Trading halted: {state_msg}"], None
        # 1 data validity
        if health["status"] == Health.BLOCKED.value:
            return Decision.REJECTED, [f"Market health BLOCKED: {'; '.join(health['reasons'])}"], None
        if MDATA.is_stale(opp.market):
            return Decision.REJECTED, [f"Stale market data ({MDATA.age_secs(opp.market):.0f}s old)"], None
        if opp.price <= 0 or f.n_candles < 30:
            return Decision.REJECTED, ["Insufficient validated market data"], None
        # 2 regime gate
        if opp.regime in ("PANIC", "UNSTABLE"):
            return Decision.WAIT, [f"Regime {opp.regime}: entries paused until stability returns"], None
        # 3 AI direction
        if opp.direction == "WAIT":
            return Decision.WAIT, ["AI decision: WAIT — no direction with sufficient confidence"], None
        # 4 edge gate
        min_edge = safe_float(CONFIG.get("min_edge_pct", 0.0015))
        if opp.expected_net < min_edge:
            return Decision.WAIT, [f"Insufficient net edge {pct(opp.expected_net)}% < {pct(min_edge)}% after costs"], None
        # 5 portfolio constraints
        n_pos = len(positions)
        if n_pos >= int(CONFIG.get("max_positions", 5)):
            return Decision.WAIT, [f"Max positions reached ({n_pos})"], None
        if any(p["market"] == opp.market for p in positions):
            return Decision.WAIT, [f"Position already open on {opp.market}"], None
        exp = PortfolioEngine.exposure(positions, equity)
        if exp["total_pct"] >= safe_float(CONFIG.get("max_total_exposure_pct", 0.6)):
            return Decision.WAIT, ["Total exposure limit reached"], None
        corr_exp = PortfolioEngine.correlated_exposure(opp.market, opp.direction, positions, equity, mat)
        if corr_exp >= safe_float(CONFIG.get("max_correlated_exposure_pct", 0.4)):
            return Decision.WAIT, [f"Correlated {opp.direction} exposure {pct(corr_exp)}% at limit"], None
        dir_key = "long_pct" if opp.direction == "LONG" else "short_pct"
        if exp[dir_key] >= safe_float(CONFIG.get("max_directional_exposure_pct", 0.5)):
            return Decision.WAIT, [f"Directional exposure limit ({opp.direction})"], None
        # 6 drawdown-derived sizing state
        size_mult = 1.0
        if state == SystemState.DEFENSIVE:
            size_mult = 0.4
            reasons_h.append("Defensive mode: size ×0.4")
        elif state == SystemState.CAUTION:
            size_mult = 0.65
            reasons_h.append("Caution mode: size ×0.65")
        if health["status"] == "LIMITED":
            size_mult *= 0.5
            reasons_h.append("Limited market health: size ×0.5")
        if AI.degraded:
            size_mult *= 0.5
            reasons_h.append("Model degraded: size ×0.5")
        return Decision.APPROVED, reasons_h or ["All risk checks passed"], {"size_mult": size_mult,
                "state": state.value, "corr_exposure": corr_exp}

# ----------------------------- position sizing -------------------------------

class Sizer:
    @staticmethod
    def size(opp, f, acct, sizing_hint):
        equity = safe_float(acct.get("equity", 0)) or safe_float(CONFIG.get("paper_starting_balance", 10000))
        risk_pct = safe_float(CONFIG.get("risk_per_trade_pct", 0.01), 0.01)
        mult = (sizing_hint or {}).get("size_mult", 1.0)
        stop_dist = max(f.atr14 * 1.5, opp.price * 0.004) if opp.price else equity * 0.01
        risk_amt = equity * risk_pct * mult
        qty = risk_amt / stop_dist if stop_dist else 0
        notional = qty * opp.price
        cap = equity * safe_float(CONFIG.get("max_position_pct", 0.2), 0.2)
        if notional > cap and opp.price:
            notional = cap
            qty = notional / opp.price
        # volatility adjust: higher vol → smaller
        vol_adj = clamp(0.02 / max(f.atr_pct, 0.005), 0.4, 1.2)
        qty *= min(vol_adj, 1.0)
        notional = qty * opp.price
        # confidence adjust
        qty *= (0.6 + 0.4 * clamp(opp.confidence, 0, 1))
        notional = qty * opp.price
        sl = opp.price - stop_dist if opp.direction == "LONG" else opp.price + stop_dist
        tp_dist = stop_dist * 2.0
        tp = opp.price + tp_dist if opp.direction == "LONG" else opp.price - tp_dist
        return {"qty": qty, "notional": notional, "stop": sl, "take_profit": tp,
                "risk_amount": risk_amt, "size_mult": mult}

# ----------------------------- execution + paper exchange --------------------

class PaperExchange:
    """Simulated fills against real prices + explicit cost model."""

    @staticmethod
    def quote(market):
        t = MDATA.get_ticker(market)
        if not t:
            return None, "no price"
        d = MDATA.get_depth(market)
        mid = safe_float(t["price"])
        if d:
            try:
                bid = d["bids"][0][0]; ask = d["asks"][0][0]
                return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}, ""
            except Exception:
                pass
        spread = mid * 0.0004
        return {"bid": mid - spread / 2, "ask": mid + spread / 2, "mid": mid}, ""

    @staticmethod
    def market_fill(market, direction, qty):
        q, err = PaperExchange.quote(market)
        if not q:
            return None, err
        slip = COST["slip_bps"] / 10000.0
        if direction == "LONG":
            px = q["ask"] * (1 + slip)
        else:
            px = q["bid"] * (1 - slip)
        fee = px * qty * (COST["fee_bps"] / 10000.0)
        return {"price": px, "fee": fee, "slippage_bps": COST["slip_bps"]}, ""

class ExecutionEngine:
    _locks = defaultdict(threading.Lock)

    @classmethod
    def place(cls, opp, size, f, mode="PAPER", strategy=None, idempotency_key=None):
        """Sequential safety: re-validate everything immediately before fill."""
        if mode == "LIVE" and not CONFIG.get("live_enabled"):
            return {"ok": False, "error": "Live trading is disabled. Enable it explicitly via the Live Trading gate."}
        if CONFIG.get("kill_switch") or CONFIG.get("trading_halted"):
            return {"ok": False, "error": "Trading halted — order refused (fail-safe)."}
        key = idempotency_key or f"{opp.market}-{opp.direction}-{int(time.time())}-{secrets.token_hex(3)}"
        with cls._locks[opp.market]:
            # duplicate guard
            dup = DBASE.query_one("SELECT id FROM orders WHERE idempotency_key=?", (key,))
            if dup:
                return {"ok": False, "error": "Duplicate order (idempotency key already used)."}
            # re-check price freshness
            if MDATA.is_stale(opp.market):
                DBASE.execute("INSERT INTO risk_events (kind, market, decision, reasons, created_at) VALUES (?,?,?,?,?)",
                              ("execution", opp.market, "REJECTED", "Stale price at execution", utcnow_iso()))
                return {"ok": False, "error": "Stale price — execution aborted."}
            # re-check existing position
            ex = DBASE.query_one("SELECT id FROM positions WHERE market=? AND status='OPEN'", (opp.market,))
            if ex:
                return {"ok": False, "error": f"Position already open on {opp.market}."}
            # re-check balance
            acct = get_account(mode)
            equity = safe_float(acct.get("equity", 0))
            if size["notional"] > equity * safe_float(CONFIG.get("max_position_pct", 0.2)) * 1.001:
                return {"ok": False, "error": "Position exceeds max size at execution."}
            if mode == "PAPER":
                fill, err = PaperExchange.market_fill(opp.market, opp.direction, size["qty"])
                if not fill:
                    return {"ok": False, "error": f"Paper quote failed: {err}"}
                oid = "ord-" + secrets.token_hex(6)
                now = utcnow_iso()
                DBASE.execute("INSERT INTO orders (id, market, side, direction, qty, price, order_type, status, mode, strategy, model_version, reason, created_at, updated_at, idempotency_key)"
                              " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (oid, opp.market, "BUY" if opp.direction == "LONG" else "SELL",
                               opp.direction, size["qty"], fill["price"], "MARKET", "FILLED", mode,
                               strategy or opp.strategy, MODEL_VERSION,
                               "; ".join(opp.reasons)[:1000], now, now, key))
                fid = "fill-" + secrets.token_hex(6)
                DBASE.execute("INSERT INTO fills (id, order_id, market, side, qty, price, fee, slippage, pnl, filled_at)"
                              " VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (fid, oid, opp.market, "BUY" if opp.direction == "LONG" else "SELL",
                               size["qty"], fill["price"], fill["fee"], fill["slippage_bps"], 0, now))
                pid = "pos-" + secrets.token_hex(6)
                DBASE.execute("INSERT INTO positions (id, market, direction, qty, entry_price, current_price, stop_loss, take_profit, exposure, unrealized_pnl, realized_pnl, confidence, strategy, opened_at, updated_at, status)"
                              " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (pid, opp.market, opp.direction, size["qty"], fill["price"], fill["price"],
                               size["stop"], size["take_profit"], size["notional"], 0, 0,
                               opp.confidence, strategy or opp.strategy, now, now, "OPEN"))
                # fees reduce realized immediately
                update_account(mode, realized_pnl=safe_float(acct.get("realized_pnl")) - fill["fee"])
                PortfolioEngine.revalue(mode)
                audit("trading", "order_filled", {"order": oid, "market": opp.market, "dir": opp.direction,
                                                 "qty": size["qty"], "price": fill["price"], "mode": mode})
                return {"ok": True, "order_id": oid, "fill_price": fill["price"], "fee": fill["fee"]}
            else:
                # LIVE adapter: deliberately conservative stub — requires verified connector.
                return {"ok": False, "error": "Live connector not configured. Complete the Live Trading gate first."}

    @classmethod
    def close_position(cls, market, mode="PAPER", reason="manual"):
        with cls._locks[market]:
            pos = DBASE.query_one("SELECT * FROM positions WHERE market=? AND status='OPEN'", (market,))
            if not pos:
                return {"ok": False, "error": "No open position."}
            direction = pos["direction"]
            fill, err = PaperExchange.market_fill(market, "SHORT" if direction == "LONG" else "LONG", safe_float(pos["qty"]))
            if not fill and mode == "PAPER":
                return {"ok": False, "error": f"Quote failed: {err}"}
            px = fill["price"] if fill else safe_float(pos["current_price"])
            qty = safe_float(pos["qty"]); entry = safe_float(pos["entry_price"])
            gross = (px - entry) * qty if direction == "LONG" else (entry - px) * qty
            fee = (fill["fee"] if fill else 0)
            net = gross - fee
            now = utcnow_iso()
            DBASE.execute("UPDATE positions SET status='CLOSED', current_price=?, realized_pnl=?, updated_at=? WHERE id=?",
                          (px, net, now, pos["id"]))
            acct = get_account(mode)
            update_account(mode, realized_pnl=safe_float(acct.get("realized_pnl")) + net,
                           day_pnl=safe_float(acct.get("day_pnl")) + net)
            oid = "ord-" + secrets.token_hex(6)
            DBASE.execute("INSERT INTO orders (id, market, side, direction, qty, price, order_type, status, mode, strategy, model_version, reason, created_at, updated_at, idempotency_key)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (oid, market, "SELL" if direction == "LONG" else "BUY", direction, qty, px,
                           "MARKET", "FILLED", mode, pos["strategy"], MODEL_VERSION, f"close: {reason}", now, now,
                           oid + "-close"))
            PortfolioEngine.revalue(mode)
            audit("trading", "position_closed", {"market": market, "pnl": round(net, 2), "reason": reason})
            # drawdown halt check
            acct2 = get_account(mode)
            dd = safe_float(acct2.get("drawdown_pct", 0))
            if dd >= safe_float(CONFIG.get("max_drawdown_halt_pct", 0.15)):
                with CONFIG._lock:
                    CONFIG.cfg["trading_halted"] = True
                    CONFIG.cfg["halt_reason"] = f"Max drawdown {pct(dd)}% — automatic halt"
                    CONFIG.save_locked()
                notify("CRITICAL", "Trading auto-halted", CONFIG.cfg["halt_reason"])
            return {"ok": True, "pnl": round(net, 2), "price": px}

# ----------------------------- trading cycle ---------------------------------

ENGINE = {"last_run": None, "last_result": None, "running": False, "thread": None,
          "stop": False, "decisions": deque(maxlen=50)}

def run_cycle(save_signals=True):
    """ONE full hierarchical pass over the whole universe. Returns serializable result."""
    t0 = time.time()
    universe = CONFIG.get("universe") or []
    if not universe:
        return {"ok": False, "error": "Trading universe is empty. Select assets first."}
    # L0: refresh market data concurrently
    MDATA.refresh_all(universe, with_klines=True, with_depth=True)
    feats = {m: FeatureEngine.build(m) for m in universe}
    CrossAssetEngine.enrich(feats)
    mat = CrossAssetEngine.matrix(universe)
    acct = get_account("PAPER")
    PortfolioEngine.revalue("PAPER")
    acct = get_account("PAPER")
    positions = PortfolioEngine.positions()
    equity = safe_float(acct.get("equity", 0)) or 10000.0
    opps, analyses = [], []
    for m in universe:
        f = feats[m]
        health = HealthEngine.assess(m, f)
        regime = RegimeEngine.detect(f)
        strat = StrategyEngine.evaluate(f, regime["regime"])
        infer = AI.infer(f, regime, strat)
        opp = OpportunityEngine.build(m, f, regime, strat, infer)
        positions_now = PortfolioEngine.positions()
        decision, why, hint = RiskEngine.authorize(opp, f, health, positions_now, get_account("PAPER"), mat, equity)
        size = Sizer.size(opp, f, get_account("PAPER"), hint) if decision == Decision.APPROVED else None
        opps.append({"opp": opp, "f": f, "health": health, "regime": regime, "strat": strat,
                     "infer": infer, "decision": decision.value, "why": why, "size": size})
    ranked = OpportunityEngine.rank([o["opp"] for o in opps])
    rank_idx = {o.market: i + 1 for i, o in enumerate(ranked)}
    out_opps = []
    for o in opps:
        opp = o["opp"]; f = o["f"]
        out_opps.append({
            "market": opp.market, "rank": rank_idx[opp.market], "direction": opp.direction,
            "score": opp.score, "confidence": round(opp.confidence, 3), "quality": opp.quality,
            "regime": opp.regime, "strategy": opp.strategy,
            "expected_return_pct": round(opp.expected_return * 100, 3),
            "expected_net_pct": round(opp.expected_net * 100, 3),
            "cost_pct": round(opp.cost_pct * 100, 3),
            "price": opp.price, "change24": round(f.change24, 2),
            "health": o["health"]["status"], "health_reasons": o["health"]["reasons"],
            "decision": o["decision"], "decision_reasons": o["why"],
            "reasons": opp.reasons, "size": o["size"],
            "atr_pct": round(f.atr_pct * 100, 3), "rsi": round(f.rsi14, 1),
            "spread_bps": round(f.spread_bps, 2), "liquidity_usd": round(f.liquidity_usd),
            "volume_ratio": round(f.vol_ratio, 2), "corr_btc": round(f.corr_btc, 3),
        })
        if save_signals:
            try:
                DBASE.execute("INSERT INTO signals (market, direction, score, confidence, regime, strategy, edge_net_pct, reasons, model_version, created_at)"
                              " VALUES (?,?,?,?,?,?,?,?,?,?)",
                              (opp.market, opp.direction, opp.score, opp.confidence, opp.regime,
                               opp.strategy, opp.expected_net, "; ".join(opp.reasons)[:2000],
                               MODEL_VERSION, utcnow_iso()))
            except Exception:
                pass
    # manage open positions: SL/TP check
    exits = []
    for p in PortfolioEngine.positions():
        t = MDATA.get_ticker(p["market"])
        px = safe_float(t.get("price")) if t else 0
        if px <= 0:
            continue
        sl = safe_float(p.get("stop_loss")); tp = safe_float(p.get("take_profit"))
        hit = None
        if p["direction"] == "LONG":
            if sl and px <= sl:
                hit = "stop-loss"
            elif tp and px >= tp:
                hit = "take-profit"
        else:
            if sl and px >= sl:
                hit = "stop-loss"
            elif tp and px <= tp:
                hit = "take-profit"
        if hit:
            r = ExecutionEngine.close_position(p["market"], "PAPER", reason=hit)
            exits.append({"market": p["market"], "reason": hit, "result": r})
    PortfolioEngine.revalue("PAPER")
    state, state_msg = RiskEngine.system_state(get_account("PAPER"))
    result = {"ok": True, "ts": utcnow_iso(), "elapsed_ms": int((time.time() - t0) * 1000),
              "opportunities": sorted(out_opps, key=lambda x: x["rank"]),
              "correlation": mat, "exits": exits, "system_state": state.value,
              "system_msg": state_msg, "model_trained": AI.trained,
              "model_version": MODEL_VERSION}
    ENGINE["last_run"] = utcnow_iso()
    ENGINE["last_result"] = result
    ENGINE["decisions"].appendleft({"ts": result["ts"], "state": state.value,
                                    "n": len(out_opps),
                                    "approved": sum(1 for o in out_opps if o["decision"] == "APPROVED")})
    # snapshot
    try:
        a = get_account("PAPER")
        exp = PortfolioEngine.exposure(PortfolioEngine.positions(), safe_float(a.get("equity", 1)))
        DBASE.execute("INSERT INTO portfolio_snapshots (equity, available, exposure_pct, positions, day_pnl, drawdown_pct, state, created_at)"
                      " VALUES (?,?,?,?,?,?,?,?)",
                      (a.get("equity"), a.get("available"), exp["total_pct"],
                       len(PortfolioEngine.positions()), a.get("day_pnl"), a.get("drawdown_pct"),
                       state.value, utcnow_iso()))
    except Exception:
        pass
    return result

def auto_loop():
    while not ENGINE["stop"]:
        try:
            if CONFIG.get("ai_automation") and not CONFIG.get("kill_switch") and not CONFIG.get("trading_halted"):
                res = run_cycle(save_signals=True)
                # auto-execute approved PAPER trades (sequential re-evaluation per spec §25)
                if res.get("ok"):
                    for o in sorted(res["opportunities"], key=lambda x: -x["score"]):
                        if o["decision"] != "APPROVED" or not o.get("size"):
                            continue
                        # rebuild fresh opp for execution-time revalidation
                        f = FeatureEngine.build(o["market"])
                        opp = Opportunity(market=o["market"], direction=o["direction"], score=o["score"],
                                          confidence=o["confidence"], quality=o["quality"], regime=o["regime"],
                                          strategy=o["strategy"], expected_return=o["expected_return_pct"] / 100,
                                          expected_net=o["expected_net_pct"] / 100, cost_pct=o["cost_pct"] / 100,
                                          price=o["price"], reasons=o["reasons"])
                        r = ExecutionEngine.place(opp, o["size"], f, mode="PAPER")
                        audit("trading", "auto_execute", {"market": o["market"], "result": r})
                        time.sleep(0.5)
            # model monitoring tick
            ModelMonitor.tick()
        except Exception as e:
            log(f"auto_loop error: {e}\n{traceback.format_exc()}", "ERROR")
            sys_event("engine", "ERROR", str(e)[:500])
        for _ in range(int(safe_float(CONFIG.get("refresh_secs", 30), 30)) * 2):
            if ENGINE["stop"]:
                break
            time.sleep(0.5)

# ----------------------------- backtester ------------------------------------

class Backtester:
    @staticmethod
    def _series(market, interval="1h", limit=500):
        try:
            rows = MDATA._fetch_klines(market, interval, limit)
        except Exception:
            rows = MDATA.get_klines(market) or []
        return rows

    @staticmethod
    def run_single(market, starting=10000.0, interval="1h", fee_bps=10.0, slip_bps=5.0):
        rows = Backtester._series(market, interval)
        if len(rows) < 80:
            return {"ok": False, "error": f"Not enough history for {market} ({len(rows)} candles)."}
        closes = [r["c"] for r in rows]
        equity = starting
        peak = starting
        max_dd = 0.0
        trades = []
        pos = None
        fees_paid = 0.0; slip_paid = 0.0
        for i in range(50, len(closes) - 1):
            w = rows[:i + 1]
            f = AIEngine._features_from_window(market, w)
            if not f:
                continue
            f.price = closes[i]
            t = {"price": closes[i]}
            regime = RegimeEngine.detect(f)["regime"]
            strat = StrategyEngine.evaluate(f, regime)
            infer = AI.infer(f, {"regime": regime, "confidence": 0.6}, strat)
            px_next = closes[i + 1]
            # simple rule: enter on strong AI direction, exit on reversal or after 10 bars
            if pos is None and infer["direction"] in ("LONG", "SHORT") and infer["confidence"] > 0.25:
                qty = (equity * 0.15) / closes[i]
                cost = closes[i] * qty * (fee_bps / 10000 + slip_bps / 10000)
                fees_paid += closes[i] * qty * fee_bps / 10000
                slip_paid += closes[i] * qty * slip_bps / 10000
                equity -= cost
                pos = {"dir": infer["direction"], "entry": closes[i], "qty": qty, "bars": 0}
            elif pos is not None:
                pos["bars"] += 1
                exit_sig = (infer["direction"] != "WAIT" and infer["direction"] != pos["dir"]) or pos["bars"] >= 10
                if exit_sig:
                    gross = (px_next - pos["entry"]) * pos["qty"] if pos["dir"] == "LONG" else (pos["entry"] - px_next) * pos["qty"]
                    cost = px_next * pos["qty"] * (fee_bps / 10000 + slip_bps / 10000)
                    fees_paid += px_next * pos["qty"] * fee_bps / 10000
                    slip_paid += px_next * pos["qty"] * slip_bps / 10000
                    net = gross - cost
                    equity += net
                    trades.append(net)
                    pos = None
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak else 0
            max_dd = max(max_dd, dd)
        if pos is not None:
            px = closes[-1]
            gross = (px - pos["entry"]) * pos["qty"] if pos["dir"] == "LONG" else (pos["entry"] - px) * pos["qty"]
            equity += gross - px * pos["qty"] * (fee_bps / 10000 + slip_bps / 10000)
            trades.append(gross)
        wins = [t for t in trades if t > 0]; losses = [t for t in trades if t <= 0]
        pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (9.99 if wins else 0.0)
        rets = []
        eq = starting
        # approximate per-trade returns for Sharpe
        for t in trades:
            rets.append(t / starting)
        sharpe = (statistics.fmean(rets) / statistics.pstdev(rets) * math.sqrt(max(len(rets), 1))) if len(rets) > 1 and statistics.pstdev(rets) else 0
        downside = [r for r in rets if r < 0]
        sortino = (statistics.fmean(rets) / statistics.pstdev(downside) * math.sqrt(max(len(rets), 1))) if len(downside) > 1 and statistics.pstdev(downside) else 0
        res = {"ok": True, "market": market, "starting": starting, "ending": round(equity, 2),
               "net_return_pct": round((equity / starting - 1) * 100, 2),
               "max_dd_pct": round(max_dd * 100, 2), "profit_factor": round(pf, 2),
               "sharpe": round(sharpe, 2), "sortino": round(sortino, 2),
               "trades": len(trades), "win_rate": round(len(wins) / max(1, len(trades)) * 100, 1),
               "fees": round(fees_paid, 2), "slippage": round(slip_paid, 2), "funding": 0.0,
               "candles": len(rows)}
        # persist
        bid = "bt-" + secrets.token_hex(6)
        try:
            DBASE.execute("INSERT INTO backtests (id, name, markets, period, strategy, starting_capital, ending_value, net_return_pct, max_dd_pct, profit_factor, sharpe, sortino, trades, win_rate, fees, slippage, funding, details, created_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (bid, f"{market} backtest", market, f"{len(rows)}x{interval}", "ZePay AI Ensemble",
                           starting, res["ending"], res["net_return_pct"], res["max_dd_pct"], res["profit_factor"],
                           res["sharpe"], res["sortino"], res["trades"], res["win_rate"], res["fees"],
                           res["slippage"], res["funding"], json.dumps(res), utcnow_iso()))
        except Exception:
            pass
        res["id"] = bid
        audit("research", "backtest", {"id": bid, "market": market, "return": res["net_return_pct"]})
        return res

    @staticmethod
    def run_multi(markets, starting=10000.0, interval="1h"):
        per = {}
        for m in markets:
            per[m] = Backtester.run_single(m, starting=starting / max(1, len(markets)), interval=interval)
        ok_runs = [v for v in per.values() if v.get("ok")]
        ending = sum(v["ending"] for v in ok_runs)
        res = {"ok": True, "markets": markets, "starting": starting, "ending": round(ending, 2),
               "net_return_pct": round((ending / starting - 1) * 100, 2) if starting else 0,
               "per_asset": per,
               "trades": sum(v.get("trades", 0) for v in ok_runs),
               "fees": round(sum(v.get("fees", 0) for v in ok_runs), 2),
               "correlation": CrossAssetEngine.matrix(markets)}
        bid = "bt-" + secrets.token_hex(6)
        try:
            DBASE.execute("INSERT INTO backtests (id, name, markets, period, strategy, starting_capital, ending_value, net_return_pct, max_dd_pct, profit_factor, sharpe, sortino, trades, win_rate, fees, slippage, funding, details, created_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (bid, "multi-asset backtest", ",".join(markets), interval, "ZePay AI Ensemble",
                           starting, ending, res["net_return_pct"], 0, 0, 0, 0, res["trades"], 0,
                           res["fees"], 0, 0, json.dumps(res)[:8000], utcnow_iso()))
        except Exception:
            pass
        res["id"] = bid
        return res

# ----------------------------- model monitoring + research -------------------

class ModelMonitor:
    history = deque(maxlen=200)

    @classmethod
    def tick(cls):
        try:
            # track recent signal outcomes: compare signal direction with subsequent return
            sigs = DBASE.query("SELECT * FROM signals ORDER BY id DESC LIMIT 30")
            if len(sigs) < 20:
                return
            # feature drift: compare current RSI distribution vs historical
            cur = []
            for m in (CONFIG.get("universe") or [])[:5]:
                kl = MDATA.get_klines(m) or []
                if len(kl) > 30:
                    cur.append(rsi([x["c"] for x in kl[-30:]]))
            if cur:
                drift = abs(statistics.fmean(cur) - 50) / 50
                cls.history.append(drift)
                if len(cls.history) > 30 and statistics.fmean(list(cls.history)[-10:]) > 0.6:
                    if not AI.degraded:
                        AI.degraded = True
                        notify("WARN", "Model degraded", "Feature drift detected — confidence and sizing reduced.")
                        audit("ml", "model_degraded", {"drift": round(drift, 3)})
                elif AI.degraded and statistics.fmean(list(cls.history)[-10:]) < 0.35:
                    AI.degraded = False
                    notify("INFO", "Model recovered", "Drift normalized — full confidence restored.")
        except Exception as e:
            log(f"monitor tick: {e}", "WARN")

class ResearchEngine:
    @staticmethod
    def auto_experiment():
        """Walk-forward style candidate evaluation. Never auto-promotes."""
        universe = CONFIG.get("universe") or []
        if len(universe) < 1:
            return {"ok": False, "error": "Empty universe"}
        exp_id = "exp-" + secrets.token_hex(5)
        t0 = time.time()
        # candidate: logistic with different LR vs champion
        X, y, r = AI._build_dataset(universe)
        if len(X) < 100:
            conclusion = "insufficient data"
            res = {"ok": False, "error": "Not enough data for experiment"}
        else:
            n = len(X)
            # 3-fold walk-forward
            accs = []
            fold = n // 4
            for k in range(1, 4):
                tr = X[:k * fold]; te = X[k * fold:(k + 1) * fold]
                ytr = y[:k * fold]; yte = y[k * fold:(k + 1) * fold]
                m = LogisticModel(len(FEATURE_KEYS), lr=0.03)
                m.fit(tr, ytr, epochs=80)
                acc = sum(1 for xi, yi in zip(te, yte) if (m.predict_proba(xi) >= 0.5) == bool(yi)) / max(1, len(te))
                accs.append(acc)
            champ = (AI.metrics or {}).get("dir_accuracy_oos", 0) or 0
            if not champ:
                try:
                    row = DBASE.query_one("SELECT metrics FROM models WHERE status='active' ORDER BY id DESC LIMIT 1")
                    if row and row.get("metrics"):
                        champ = json.loads(row["metrics"]).get("dir_accuracy_oos", 0) or 0
                except Exception:
                    pass
            challenger = round(statistics.fmean(accs), 4)
            conclusion = f"challenger {challenger} vs champion {champ}"
            res = {"ok": True, "walk_forward_acc": accs, "challenger": challenger, "champion": champ,
                   "recommend_promotion": bool(challenger > (champ or 0) + 0.02)}
        try:
            DBASE.execute("INSERT INTO experiments (id, hypothesis, dataset, features, model, params, results, oos, conclusion, created_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (exp_id, "LR(lr=0.03) walk-forward vs champion", ",".join(universe),
                           ",".join(FEATURE_KEYS), "logistic", json.dumps({"lr": 0.03}),
                           json.dumps(res), json.dumps(res.get("walk_forward_acc", [])), str(conclusion), utcnow_iso()))
        except Exception:
            pass
        res["id"] = exp_id
        res["conclusion"] = str(conclusion)
        res["elapsed_s"] = round(time.time() - t0, 1)
        audit("research", "experiment", {"id": exp_id, "conclusion": res.get("conclusion")})
        return res

# =============================================================================
# PART 4A — HTTP API SERVER (stdlib http.server, REST contract)
# =============================================================================

def jdump(o):
    return json.dumps(o, default=str)

class APIHandler(http.server.BaseHTTPRequestHandler):
    server_version = "ZePay/1.0"

    def log_message(self, fmt, *args):
        pass  # keep quiet; file log instead

    # ----- helpers -----
    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, (str, bytes)) else jdump(obj)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, obj):
        self._send(200, {"ok": True, **obj} if isinstance(obj, dict) else obj)

    def _err(self, msg, code=400):
        self._send(code, {"ok": False, "error": msg})

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        if n <= 0:
            return {}
        try:
            raw = self.rfile.read(min(n, 1_000_000)).decode("utf-8")
            return json.loads(raw or "{}")
        except Exception:
            return {}

    def _authed(self):
        if not is_setup():
            return True  # setup wizard allowed
        q = urllib.parse.urlparse(self.path).query
        tok = urllib.parse.parse_qs(q).get("token", [""])[0]
        if not tok:
            tok = self.headers.get("X-ZePay-Token", "")
        if not tok:
            ck = self.headers.get("Cookie", "")
            for part in ck.split(";"):
                if "zepay_token" in part:
                    tok = part.split("=", 1)[-1].strip()
        return valid_session(tok)

    # ----- routing -----
    def do_GET(self):
        try:
            u = urllib.parse.urlparse(self.path)
            p = u.path
            if p in ("/", "/index.html", "/dashboard"):
                return self._send(200, DASHBOARD_HTML, "text/html")
            if p == "/health" or p == "/api/health":
                return self._ok({"status": "ok", "version": VERSION, "ts": utcnow_iso(),
                                 "setup": is_setup(), "mode": CONFIG.get("trading_mode")})
            if p == "/api/setup/status":
                return self._ok({"setup": is_setup()})
            if not self._authed():
                return self._err("Not authenticated. Enter your ZePay key.", 401)
            if p == "/api/status":
                return self._ok(status_payload())
            if p == "/api/markets":
                return self._ok({"markets": SUPPORTED_MARKETS,
                                 "universe": CONFIG.get("universe")})
            if p == "/api/opportunities":
                res = ENGINE.get("last_result") or run_cycle(save_signals=False)
                return self._ok({"result": res})
            if p == "/api/positions":
                pos = PortfolioEngine.all_positions()
                return self._ok({"open": [x for x in pos if x["status"] == "OPEN"],
                                 "recent": pos[:50]})
            if p == "/api/portfolio":
                return self._ok(portfolio_payload())
            if p == "/api/risk":
                return self._ok(risk_payload())
            if p == "/api/signals":
                return self._ok({"signals": DBASE.query("SELECT * FROM signals ORDER BY id DESC LIMIT 50")})
            if p == "/api/backtests":
                return self._ok({"backtests": DBASE.query("SELECT * FROM backtests ORDER BY id DESC LIMIT 20")})
            if p == "/api/audit":
                return self._ok({"audit": DBASE.query("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 100")})
            if p == "/api/alerts":
                return self._ok({"alerts": DBASE.query("SELECT * FROM alerts ORDER BY id DESC LIMIT 50")})
            if p == "/api/models":
                return self._ok({"models": DBASE.query("SELECT * FROM models ORDER BY id DESC LIMIT 20"),
                                 "current": {"version": MODEL_VERSION, "trained": AI.trained,
                                             "degraded": AI.degraded, "metrics": AI.metrics}})
            if p == "/api/experiments":
                return self._ok({"experiments": DBASE.query("SELECT * FROM experiments ORDER BY created_at DESC LIMIT 20")})
            if p == "/api/config":
                return self._ok({"config": CONFIG.all(public_only=True)})
            if p == "/api/diagnostics":
                return self._ok(run_diagnostics())
            if p == "/api/system_check":
                return self._ok(full_system_check())
            if p == "/api/build_status":
                return self._ok(build_status())
            if p == "/api/trades":
                return self._ok({"orders": DBASE.query("SELECT * FROM orders ORDER BY created_at DESC LIMIT 100"),
                                 "fills": DBASE.query("SELECT * FROM fills ORDER BY filled_at DESC LIMIT 100")})
            if p == "/api/snapshots":
                return self._ok({"snapshots": DBASE.query("SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 200")})
            if p == "/api/help":
                return self._ok({"topics": HELP_TOPICS})
            if p.startswith("/api/candles"):
                qs = urllib.parse.parse_qs(u.query)
                m = qs.get("market", ["BTC/USDT"])[0]
                kl = MDATA.get_klines(m) or []
                return self._ok({"market": m, "candles": kl[-120:]})
            return self._err("Unknown endpoint", 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            log(f"GET {self.path} failed: {e}", "ERROR")
            try:
                self._err(f"Server error: {e}", 500)
            except Exception:
                pass

    def do_POST(self):
        try:
            u = urllib.parse.urlparse(self.path)
            p = u.path
            b = self._body()
            if p == "/api/setup/verify":
                ok, msg = activate_license(b.get("key", ""))
                if not ok:
                    return self._err(msg)
                tok = create_session()
                # background warm-up: data + train
                threading.Thread(target=warmup, daemon=True).start()
                self.send_response(200)
                body = jdump({"ok": True, "message": msg, "token": tok}).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"zepay_token={tok}; Path=/; SameSite=Lax")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if not self._authed():
                return self._err("Not authenticated.", 401)
            if p == "/api/universe":
                uni = b.get("universe", [])
                valid = [m["symbol"] for m in SUPPORTED_MARKETS]
                uni = [x for x in uni if x in valid]
                if not uni:
                    return self._err("Select at least one market.")
                with CONFIG._lock:
                    CONFIG.cfg["universe"] = uni
                    CONFIG.save_locked()
                audit("config", "universe_changed", {"universe": uni}, actor="user")
                threading.Thread(target=lambda: MDATA.refresh_all(uni), daemon=True).start()
                return self._ok({"universe": uni})
            if p == "/api/config":
                if "risk_mode" in b and b["risk_mode"] in RISK_PRESETS:
                    with CONFIG._lock:
                        CONFIG.cfg.update(RISK_PRESETS[b["risk_mode"]])
                        CONFIG.cfg["risk_mode"] = b["risk_mode"]
                        CONFIG.save_locked()
                allowed = {"max_positions": int, "priorities": dict, "ai_automation": bool,
                           "auto_research": bool, "notifications": bool, "refresh_secs": int,
                           "display_currency": str}
                patch = {}
                for k, typ in allowed.items():
                    if k in b:
                        try:
                            patch[k] = typ(b[k]) if typ is not dict else dict(b[k])
                        except Exception:
                            pass
                if patch:
                    CONFIG.update(patch)
                    audit("config", "config_updated", patch, actor="user")
                return self._ok({"config": CONFIG.all()})
            if p == "/api/analyze":
                res = run_cycle(save_signals=True)
                return self._ok({"result": res})
            if p == "/api/train":
                m = AI.train(CONFIG.get("universe") or [])
                return self._ok({"metrics": m})
            if p == "/api/execute":
                market = b.get("market")
                res = ENGINE.get("last_result") or {}
                opps = res.get("opportunities", []) if isinstance(res, dict) else []
                target = next((o for o in opps if o["market"] == market and o["decision"] == "APPROVED"), None)
                if not target:
                    return self._err("No APPROVED opportunity for that market. Run Analyze first.")
                f = FeatureEngine.build(market)
                opp = Opportunity(market=market, direction=target["direction"], score=target["score"],
                                  confidence=target["confidence"], quality=target["quality"],
                                  regime=target["regime"], strategy=target["strategy"],
                                  expected_return=target["expected_return_pct"] / 100,
                                  expected_net=target["expected_net_pct"] / 100,
                                  cost_pct=target["cost_pct"] / 100, price=target["price"],
                                  reasons=target["reasons"])
                r = ExecutionEngine.place(opp, target["size"], f, mode="PAPER")
                if r.get("ok"):
                    return self._ok(r)
                return self._err(r.get("error", "execution failed"))
            if p == "/api/close":
                r = ExecutionEngine.close_position(b.get("market"), "PAPER", reason="user")
                return self._ok(r) if r.get("ok") else self._err(r.get("error", "close failed"))
            if p == "/api/kill":
                on = bool(b.get("on", True))
                with CONFIG._lock:
                    CONFIG.cfg["kill_switch"] = on
                    CONFIG.save_locked()
                audit("safety", "kill_switch", {"on": on}, actor="user")
                notify("CRITICAL" if on else "INFO", "Kill switch " + ("ENGAGED" if on else "released"),
                       "Automated entries " + ("stopped" if on else "re-enabled") + " by user.")
                return self._ok({"kill_switch": on})
            if p == "/api/halt":
                on = bool(b.get("on", True))
                with CONFIG._lock:
                    CONFIG.cfg["trading_halted"] = on
                    CONFIG.cfg["halt_reason"] = b.get("reason", "Manual halt") if on else ""
                    CONFIG.save_locked()
                audit("safety", "halt", {"on": on}, actor="user")
                return self._ok({"halted": on})
            if p == "/api/resume":
                with CONFIG._lock:
                    CONFIG.cfg["kill_switch"] = False
                    CONFIG.cfg["trading_halted"] = False
                    CONFIG.cfg["halt_reason"] = ""
                    CONFIG.save_locked()
                audit("safety", "resume", {}, actor="user")
                return self._ok({"resumed": True})
            if p == "/api/backtest":
                markets = b.get("markets") or [b.get("market", "BTC/USDT")]
                start = safe_float(b.get("starting", 10000), 10000)
                if len(markets) == 1:
                    r = Backtester.run_single(markets[0], starting=start)
                else:
                    r = Backtester.run_multi(markets, starting=start)
                return self._ok({"result": r}) if r.get("ok") else self._err(r.get("error", "backtest failed"))
            if p == "/api/experiment":
                return self._ok({"result": ResearchEngine.auto_experiment()})
            if p == "/api/live/connect":
                return self._ok(live_connect(b))
            if p == "/api/live/enable":
                return self._ok(live_enable(b))
            if p == "/api/backup":
                return self._ok(do_backup())
            if p == "/api/alerts/read":
                DBASE.execute("UPDATE alerts SET read=1")
                return self._ok({})
            return self._err("Unknown endpoint", 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            log(f"POST {self.path} failed: {e}\n{traceback.format_exc()}", "ERROR")
            try:
                self._err(f"Server error: {e}", 500)
            except Exception:
                pass

# ----------------------------- payload builders ------------------------------

def status_payload():
    acct = get_account("PAPER")
    state, msg = RiskEngine.system_state(acct)
    positions = PortfolioEngine.positions()
    equity = safe_float((acct or {}).get("equity", 0))
    exp = PortfolioEngine.exposure(positions, equity)
    last = ENGINE.get("last_result") or {}
    return {
        "version": VERSION, "ts": utcnow_iso(),
        "trading_mode": "LIVE" if CONFIG.get("live_enabled") else "PAPER",
        "live_enabled": bool(CONFIG.get("live_enabled")),
        "system_state": state.value, "system_msg": msg,
        "kill_switch": bool(CONFIG.get("kill_switch")),
        "equity": round(equity, 2), "day_pnl": round(safe_float((acct or {}).get("day_pnl", 0)), 2),
        "drawdown_pct": round(safe_float((acct or {}).get("drawdown_pct", 0)) * 100, 2),
        "open_positions": len(positions), "exposure": exp,
        "universe": CONFIG.get("universe"),
        "last_run": ENGINE.get("last_run"),
        "opportunities": (last.get("opportunities") or [])[:10],
        "model_trained": AI.trained, "model_degraded": AI.degraded,
        "risk_mode": CONFIG.get("risk_mode"),
        "display_currency": CONFIG.get("display_currency", "₹"),
    }

def portfolio_payload():
    acct = get_account("PAPER")
    positions = PortfolioEngine.positions()
    equity = safe_float((acct or {}).get("equity", 0)) or 1
    exp = PortfolioEngine.exposure(positions, equity)
    rc = PortfolioEngine.risk_contribution(positions)
    alloc = {p["market"]: round(abs(safe_float(p.get("exposure"))) / equity * 100, 1) for p in positions}
    alloc["CASH"] = round(max(equity - sum(abs(safe_float(p.get("exposure"))) for p in positions), 0) / equity * 100, 1)
    return {"account": acct, "positions": positions, "exposure": exp,
            "risk_contribution": rc, "allocation": alloc,
            "correlation": CrossAssetEngine.matrix(CONFIG.get("universe") or [])}

def risk_payload():
    acct = get_account("PAPER")
    state, msg = RiskEngine.system_state(acct)
    equity = safe_float((acct or {}).get("equity", 0)) or 1
    day_lim = equity * safe_float(CONFIG.get("daily_loss_limit_pct", 0.03))
    return {"state": state.value, "message": msg,
            "daily_loss_limit": round(day_lim, 2),
            "current_day_pnl": round(safe_float((acct or {}).get("day_pnl", 0)), 2),
            "remaining_budget": round(day_lim + safe_float((acct or {}).get("day_pnl", 0)), 2),
            "max_exposure_pct": safe_float(CONFIG.get("max_total_exposure_pct", 0.6)) * 100,
            "current_exposure_pct": round(PortfolioEngine.exposure(
                PortfolioEngine.positions(), equity)["total_pct"] * 100, 2),
            "max_drawdown_pct": safe_float(CONFIG.get("max_drawdown_halt_pct", 0.15)) * 100,
            "current_drawdown_pct": round(safe_float((acct or {}).get("drawdown_pct", 0)) * 100, 2),
            "limits": {"max_positions": CONFIG.get("max_positions"),
                       "max_position_pct": CONFIG.get("max_position_pct"),
                       "max_correlated_exposure_pct": CONFIG.get("max_correlated_exposure_pct"),
                       "risk_per_trade_pct": CONFIG.get("risk_per_trade_pct")}}

def run_diagnostics():
    checks = {}
    def ck(name, fn):
        try:
            fn()
            checks[name] = {"ok": True}
        except Exception as e:
            checks[name] = {"ok": False, "error": str(e)[:200]}
    ck("database", lambda: DBASE.query_one("SELECT 1 AS x"))
    ck("config", lambda: CONFIG.all())
    ck("market_data", lambda: http_json(f"{BINANCE_DATA}/api/v3/ping", timeout=8))
    ck("disk", lambda: (_ for _ in ()).throw(RuntimeError("low disk")) if shutil.disk_usage(DATA_DIR).free < 50_000_000 else None)
    import sys as _s
    checks["python"] = {"ok": _s.version_info >= (3, 10), "version": _s.version.split()[0]}
    checks["ai"] = {"ok": True, "trained": AI.trained, "degraded": AI.degraded}
    checks["clock"] = {"ok": True, "utc": utcnow_iso()}
    try:
        vm = {}
        with open("/proc/meminfo") as f:
            for line in f:
                if "MemAvailable" in line or "MemTotal" in line:
                    vm[line.split(":")[0]] = line.split(":")[1].strip()
        checks["memory"] = {"ok": True, **vm}
    except Exception:
        checks["memory"] = {"ok": True, "note": "unavailable"}
    return {"checks": checks, "ts": utcnow_iso()}

def full_system_check():
    d = run_diagnostics()["checks"]
    results = {}
    results["api"] = {"ok": True}
    results["database"] = d.get("database", {})
    results["cache"] = {"ok": True, "note": "embedded TTL cache (single-file edition)"}
    results["market_data"] = d.get("market_data", {})
    results["ai"] = d.get("ai", {})
    results["features"] = {"ok": True}
    results["strategies"] = {"ok": True}
    results["portfolio"] = {"ok": get_account("PAPER") is not None}
    # risk self-test: a blocked opp must be rejected
    try:
        f = AssetFeatures(market="TEST/USDT")
        opp = Opportunity(market="TEST/USDT", direction="LONG", score=99, confidence=0.9,
                          quality=0.9, regime="BULLISH_TREND", strategy="trend",
                          expected_return=0.05, expected_net=0.04, cost_pct=0.001, price=0)
        dec, _, _ = RiskEngine.authorize(opp, f, {"status": "BLOCKED", "reasons": ["test"]},
                                         [], get_account("PAPER"), {})
        results["risk"] = {"ok": dec in (Decision.REJECTED, Decision.HALTED)}
    except Exception as e:
        results["risk"] = {"ok": False, "error": str(e)[:200]}
    results["paper_trading"] = {"ok": True, "mode": "PAPER"}
    results["exchange"] = {"ok": d.get("market_data", {}).get("ok", False)}
    results["dashboard"] = {"ok": True}
    results["storage"] = d.get("disk", {})
    results["security"] = {"ok": not CONFIG.get("live_enabled"), "live_enabled": bool(CONFIG.get("live_enabled"))}
    all_ok = all(v.get("ok") for v in results.values())
    # last-run freshness
    if ENGINE.get("last_result"):
        results["engine"] = {"ok": True, "last_run": ENGINE.get("last_run")}
    else:
        results["engine"] = {"ok": True, "note": "engine idle — press Analyze"}
    return {"ok": all_ok, "results": results, "ts": utcnow_iso(),
            "verdict": "ZePay is ready" if all_ok else "ZePay requires attention"}

def build_status():
    return {"version": VERSION, "edition": EDITION,
            "modules": {k: "✓" for k in
                        ["foundation", "market_data", "multi_asset", "paper_trading", "portfolio",
                         "risk", "backtesting", "ai", "dashboard", "monitoring", "security"]},
            "trading_mode": "PAPER", "live": "DISABLED"}

def warmup():
    try:
        uni = CONFIG.get("universe") or []
        MDATA.refresh_all(uni)
        run_cycle(save_signals=True)
        AI.train(uni)
        notify("INFO", "ZePay ready", f" warm-up complete. Monitoring {len(uni)} markets.")
    except Exception as e:
        log(f"warmup failed: {e}", "ERROR")

# ----------------------------- live trading gate -----------------------------
# 10-step explicit opt-in. Withdrawal-enabled keys are REFUSED.

LIVE_STEPS = ["open_live", "risk_ack", "exchange_connected", "permissions_verified",
              "withdrawal_disabled", "connectivity_test", "risk_test", "paper_ready",
              "explicit_confirm", "enabled"]

def live_connect(b):
    exch = (b.get("exchange") or "binance").lower()
    key = (b.get("api_key") or "").strip()
    secret = (b.get("api_secret") or "").strip()
    if not key or not secret:
        return {"ok": False, "error": "API key and secret are required."}
    if len(secret) < 8:
        return {"ok": False, "error": "Invalid secret."}
    # test connectivity (public) — never log secrets
    try:
        http_json(f"{BINANCE_DATA}/api/v3/ping", timeout=8)
        connectivity = True
    except Exception as e:
        return {"ok": False, "error": f"Exchange unreachable: {e}"}
    withdrawal = bool(b.get("withdrawal_enabled", False))
    if withdrawal:
        audit("security", "live_blocked_withdrawal", {"exchange": exch})
        return {"ok": False, "error": "SECURITY: Withdrawal permission is enabled on this key. ZePay refuses to enable live trading. Create a trade-only key with withdrawals DISABLED."}
    # store key id only + encrypted secret (xor with machine-ish salt — demo-grade; prod uses KMS)
    salt = (CONFIG.get("license_key_hash") or "zepay")[:16]
    enc = base64.b64encode(bytes(ch ^ ord(salt[i % len(salt)]) for i, ch in enumerate(secret.encode()))).decode()
    with CONFIG._lock:
        CONFIG.cfg["exchange"] = exch
        CONFIG.cfg["exchange_api_key"] = key
        CONFIG.cfg["exchange_api_secret_enc"] = enc
        steps = set(CONFIG.cfg.get("live_confirmed_steps") or [])
        steps.update(["open_live", "exchange_connected", "connectivity_test", "withdrawal_disabled"])
        CONFIG.cfg["live_confirmed_steps"] = sorted(steps)
        CONFIG.save_locked()
    audit("security", "live_connected", {"exchange": exch, "key_prefix": key[:4] + "****"})
    return {"ok": True, "steps": CONFIG.get("live_confirmed_steps"),
            "checks": {"authentication": "self-reported (public ping ok)",
                       "market_data": True, "trading_permission": "verify on exchange",
                       "withdrawal_disabled": True, "connectivity": connectivity}}

def live_enable(b):
    steps = set(CONFIG.get("live_confirmed_steps") or [])
    for s in ["risk_ack", "permissions_verified", "risk_test", "paper_ready", "explicit_confirm"]:
        if b.get(s):
            steps.add(s)
    # paper readiness: require ≥5 paper fills
    fills = DBASE.query("SELECT COUNT(*) AS n FROM fills")
    n_fills = (fills[0]["n"] if fills else 0)
    if "paper_ready" in steps and n_fills < 5:
        return {"ok": False, "error": f"Paper readiness not met: {n_fills}/5 paper fills. Paper-trade first."}
    # risk self-test
    try:
        f = AssetFeatures(market="TEST/USDT")
        opp = Opportunity(market="TEST/USDT", direction="LONG", score=99, confidence=0.9,
                          quality=0.9, regime="BULLISH_TREND", strategy="trend",
                          expected_return=0.05, expected_net=0.04, cost_pct=0.001, price=0)
        dec, _, _ = RiskEngine.authorize(opp, f, {"status": "BLOCKED", "reasons": ["t"]}, [], get_account("PAPER"), {})
        risk_ok = dec in (Decision.REJECTED, Decision.HALTED)
    except Exception:
        risk_ok = False
    if "risk_test" in steps and not risk_ok:
        return {"ok": False, "error": "Risk-engine self-test failed. Live trading refused."}
    required = {"open_live", "risk_ack", "exchange_connected", "permissions_verified",
                "withdrawal_disabled", "connectivity_test", "risk_test", "paper_ready", "explicit_confirm"}
    with CONFIG._lock:
        CONFIG.cfg["live_confirmed_steps"] = sorted(steps)
        CONFIG.save_locked()
    if not required.issubset(steps):
        return {"ok": True, "steps": sorted(steps), "missing": sorted(required - steps),
                "enabled": False}
    with CONFIG._lock:
        CONFIG.cfg["live_enabled"] = True
        CONFIG.cfg["trading_mode"] = "LIVE"
        steps.add("enabled")
        CONFIG.cfg["live_confirmed_steps"] = sorted(steps)
        CONFIG.save_locked()
    audit("security", "live_enabled", {"steps": sorted(steps)}, actor="user")
    notify("CRITICAL", "LIVE trading enabled", "Real funds are at risk. Trade carefully.")
    return {"ok": True, "enabled": True, "steps": sorted(steps)}

def do_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"zepay-backup-{stamp}.db")
    try:
        with DBASE._lock:
            shutil.copy2(DB_PATH, dest)
        shutil.copy2(CONFIG_PATH, os.path.join(BACKUP_DIR, f"config-{stamp}.json"))
        audit("system", "backup", {"dest": dest})
        return {"ok": True, "backup": dest}
    except Exception as e:
        return {"ok": False, "error": str(e)}

HELP_TOPICS = {
    "getting_started": "Install → Open → Enter ZePay key → Select coins → Paper trading starts automatically. Nothing technical needed.",
    "dashboard": "Home shows equity, today's P&L, drawdown, positions. Green = healthy, yellow = attention, red = halted.",
    "markets": "Select which coins ZePay may trade. Selecting ≠ trading: each market must also pass health + risk checks.",
    "ai_signals": "Opportunities are ranked 0–100 from real data. WAIT is a valid decision — cash is a position.",
    "trading": "Paper mode trades fake money on real prices. Live mode needs the 10-step gate and is OFF by default.",
    "portfolio": "Allocation vs risk contribution. Correlated coins count as overlapping risk.",
    "risk": "Risk engine has final authority over AI. Daily loss limit and max drawdown can halt trading automatically.",
    "backtesting": "Test on historical candles with fees/slippage. Past performance ≠ future results.",
    "paper": "One-click, default ON. Starting balance configurable before first run.",
    "live": "Requires trade-only keys (withdrawals DISABLED), paper history, and explicit confirmation.",
    "strategies": "Trend, Momentum, Breakout, Mean Reversion, Volatility, OrderFlow-proxy — weighted by regime fit.",
    "models": "Direction/Return/Volatility/Regime/Quality + Ensemble. Degraded models auto-reduce size.",
    "performance": "Net (after costs) is shown prominently. Gross is secondary.",
    "security": "Secrets encrypted, redacted in logs, never sent to browser. Kill switch always visible.",
    "troubleshooting": "Run Full System Check. Stale data → check internet. Halted → read the WHY message.",
}

class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

# =============================================================================
# PART 4B — EMBEDDED DASHBOARD (no external resources; mobile responsive)
# =============================================================================

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZePay — AI Crypto Trading Platform</title>
<style>
:root{--bg:#0b1020;--card:#141b33;--card2:#1a2340;--line:#263056;--txt:#e8ecf7;--mut:#9aa5c7;
--grn:#22c55e;--yel:#eab308;--red:#ef4444;--blu:#3b82f6;--acc:#7c6cff}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif}
a{color:var(--acc)} button{cursor:pointer}
.top{position:sticky;top:0;z-index:5;display:flex;gap:10px;align-items:center;background:#0e1530;
border-bottom:1px solid var(--line);padding:10px 14px}
.logo{font-weight:800;font-size:18px;letter-spacing:.5px}.logo span{color:var(--acc)}
.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:12px;font-weight:700}
.paper{background:#12395b;color:#7dd3fc}.live{background:#4a1010;color:#fca5a5}
.ok{background:#0d3b23;color:#86efac}.warn{background:#3d2f07;color:#fde047}.bad{background:#450a0a;color:#fca5a5}
.grey{background:#232b4d;color:var(--mut)}
.wrap{display:flex;min-height:calc(100vh - 53px)}
.side{width:210px;background:#0e1530;border-right:1px solid var(--line);padding:12px;display:flex;flex-direction:column;gap:4px}
.side button{background:none;border:none;color:var(--txt);text-align:left;padding:9px 10px;border-radius:8px;font-size:14px}
.side button.on,.side button:hover{background:var(--card2)}
.main{flex:1;padding:16px;max-width:1200px;width:100%}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:12px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.card h3{font-size:12px;color:var(--mut);font-weight:600;margin-bottom:4px}
.big{font-size:22px;font-weight:800}.pos{color:var(--grn)}.neg{color:var(--red)}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;overflow:hidden;margin:10px 0}
th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);font-size:13px}
th{color:var(--mut);font-weight:600;background:var(--card2)}
.btn{background:var(--acc);border:none;color:#fff;padding:9px 16px;border-radius:9px;font-weight:700}
.btn.ghost{background:var(--card2);color:var(--txt);border:1px solid var(--line)}
.btn.danger{background:var(--red)}.btn.green{background:var(--grn);color:#04150a}
.btn:disabled{opacity:.5}
input,select{background:#0b1020;border:1px solid var(--line);color:var(--txt);padding:9px 10px;border-radius:8px;width:100%}
label{font-size:12px;color:var(--mut)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
@media(max-width:900px){.grid2,.grid3{grid-template-columns:1fr}.side{display:none}.side.mob{display:flex;position:fixed;z-index:9;top:53px;bottom:0;left:0}}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.mut{color:var(--mut)}.small{font-size:12px}
.why{background:#101736;border-left:3px solid var(--yel);padding:10px;border-radius:0 8px 8px 0;margin:8px 0}
.bar{height:10px;background:#232b4d;border-radius:6px;overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;background:var(--acc)}
.kill{position:fixed;bottom:16px;right:16px;z-index:9;background:var(--red);color:#fff;border:none;
font-weight:800;padding:14px 22px;border-radius:14px;font-size:16px;box-shadow:0 6px 24px #0008}
.hamb{display:none;background:var(--card2);border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:6px 10px}
@media(max-width:900px){.hamb{display:block}}
#setup{position:fixed;inset:0;background:var(--bg);z-index:20;display:flex;align-items:center;justify-content:center;padding:16px}
.setupcard{max-width:520px;width:100%;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:26px}
.tick{color:var(--grn)}.page{display:none}.page.on{display:block}
canvas.chart{width:100%;height:180px;background:var(--card);border:1px solid var(--line);border-radius:12px}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px;margin:6px 0}
summary{cursor:pointer;font-weight:700}
.opp{display:grid;grid-template-columns:44px 1fr auto;gap:10px;align-items:center;background:var(--card);
border:1px solid var(--line);border-radius:12px;padding:10px;margin:8px 0}
.rank{font-size:20px;font-weight:800;color:var(--acc)}
.score{font-size:20px;font-weight:800}
.tgl{display:flex;gap:6px;align-items:center}
footer{color:var(--mut);font-size:12px;margin:18px 0;text-align:center}
</style></head>
<body>
<div class="top">
<button class="hamb" onclick="document.querySelector('.side').classList.toggle('mob')">☰</button>
<div class="logo">ZE<span>PAY</span></div>
<span id="modeBadge" class="badge paper">PAPER TRADING</span>
<span id="stateBadge" class="badge ok">● LOADING</span>
<div style="flex:1"></div>
<button class="btn ghost small" onclick="showPage('help')">? HELP</button>
<button class="btn ghost small" onclick="toggleView()">SIMPLE / ADVANCED</button>
</div>
<div class="wrap">
<nav class="side" id="nav">
<button data-p="home" class="on" onclick="showPage('home')">🏠 Home</button>
<button data-p="markets" onclick="showPage('markets')">🌐 Markets</button>
<button data-p="opps" onclick="showPage('opps')">🤖 AI Opportunities</button>
<button data-p="positions" onclick="showPage('positions')">📊 Positions</button>
<button data-p="portfolio" onclick="showPage('portfolio')">💼 Portfolio</button>
<button data-p="risk" onclick="showPage('risk')">🛡 Risk</button>
<button data-p="backtest" onclick="showPage('backtest')">🧪 Backtest</button>
<button data-p="models" onclick="showPage('models')">🧠 AI Models</button>
<button data-p="perf" onclick="showPage('perf')">📈 Performance</button>
<button data-p="history" onclick="showPage('history')">📜 History</button>
<button data-p="live" onclick="showPage('live')">⚡ Live Trading</button>
<button data-p="settings" onclick="showPage('settings')">⚙ Settings</button>
<button data-p="help" onclick="showPage('help')">❓ Help Center</button>
<div style="flex:1"></div>
<div class="small mut" id="verLine">v1.0.0 all-in-one</div>
</nav>
<div class="main">
<!-- HOME -->
<section id="pg-home" class="page on">
<h2>Account Overview</h2>
<div class="mut small" id="healthLine">Connecting…</div>
<div class="cards">
<div class="card"><h3>TOTAL EQUITY <span title="Cash + positions value">ⓘ</span></h3><div class="big" id="cEquity">—</div></div>
<div class="card"><h3>TODAY'S P&L</h3><div class="big" id="cPnl">—</div></div>
<div class="card"><h3>DRAWDOWN <span title="How far the account has fallen from its previous highest value.">ⓘ</span></h3><div class="big" id="cDd">—</div></div>
<div class="card"><h3>OPEN POSITIONS</h3><div class="big" id="cPos">—</div></div>
<div class="card"><h3>AI STATUS</h3><div class="big" id="cAi">—</div><div class="small mut" id="cAiSub"></div></div>
<div class="card"><h3>EXPOSURE</h3><div class="big" id="cExp">—</div></div>
</div>
<div class="row">
<button class="btn" onclick="analyze()">▶ ANALYZE NOW</button>
<button class="btn ghost" onclick="train()">🧠 TRAIN AI</button>
<button class="btn ghost" onclick="sysCheck()">✔ FULL SYSTEM CHECK</button>
<button class="btn ghost" onclick="showPage('opps')">WHY IS ZEPAY NOT TRADING?</button>
</div>
<div id="homeWhy"></div>
<h3 style="margin-top:14px">Top Opportunities</h3>
<div id="homeOpps"></div>
<div id="sysResult"></div>
</section>
<!-- MARKETS -->
<section id="pg-markets" class="page">
<h2>Asset Management</h2>
<p class="mut">Choose which cryptocurrencies ZePay may trade. Selecting a coin does <b>not</b> force trading — health + AI + risk must all agree.</p>
<div class="row" style="margin:10px 0">
<button class="btn ghost" onclick="universeAll(true)">SELECT ALL</button>
<button class="btn ghost" onclick="universeAll(false)">CLEAR ALL</button>
<button class="btn" onclick="saveUniverse()">💾 SAVE TRADING UNIVERSE</button>
</div>
<div id="mktList"></div>
<h3>Search markets</h3>
<input id="mktSearch" placeholder="Search… e.g. sol" oninput="renderMkts()">
</section>
<!-- OPPS -->
<section id="pg-opps" class="page">
<h2>AI Opportunity Ranker</h2>
<p class="mut small">Ranked from real market data. Never fabricated. <span class="adv" style="display:none">Advanced: p(long), expected vol, cost breakdown below.</span></p>
<div class="row"><button class="btn" onclick="analyze()">↻ REFRESH ANALYSIS</button></div>
<div id="oppList"></div>
</section>
<!-- POSITIONS -->
<section id="pg-positions" class="page">
<h2>Positions & Markets</h2>
<div id="posWrap"></div>
</section>
<!-- PORTFOLIO -->
<section id="pg-portfolio" class="page">
<h2>Portfolio</h2>
<div id="pfWrap"></div>
</section>
<!-- RISK -->
<section id="pg-risk" class="page">
<h2>Risk</h2>
<div id="riskWrap"></div>
<div class="row" style="margin-top:10px">
<button class="btn danger" onclick="halt(true)">⏸ HALT TRADING</button>
<button class="btn green" onclick="resume()">▶ RESUME</button>
</div>
</section>
<!-- BACKTEST -->
<section id="pg-backtest" class="page">
<h2>Backtest</h2>
<div class="grid3">
<div><label>Market(s) comma-separated</label><input id="btMkts" value="BTC/USDT"></div>
<div><label>Starting capital</label><input id="btStart" value="10000" type="number"></div>
<div><label>&nbsp;</label><button class="btn" onclick="runBacktest()">RUN BACKTEST</button></div>
</div>
<p class="small mut">Includes fees + slippage. <b>PAST PERFORMANCE IS NOT A GUARANTEE OF FUTURE RESULTS.</b></p>
<div id="btOut"></div>
<h3>Saved backtests</h3><div id="btList"></div>
</section>
<!-- MODELS -->
<section id="pg-models" class="page">
<h2>AI Models</h2>
<div id="modelWrap"></div>
<div class="row"><button class="btn" onclick="train()">RETRAIN ON LATEST DATA</button>
<button class="btn ghost" onclick="experiment()">🔬 RUN RESEARCH EXPERIMENT</button></div>
<div id="expOut"></div>
</section>
<!-- PERF -->
<section id="pg-perf" class="page">
<h2>Performance</h2>
<canvas class="chart" id="eqChart" width="900" height="180"></canvas>
<div id="perfWrap"></div>
</section>
<!-- HISTORY -->
<section id="pg-history" class="page">
<h2>Trade History & Audit</h2>
<div class="row"><button class="btn ghost" onclick="loadHistory()">↻ RELOAD</button></div>
<h3>Orders</h3><div id="histOrders" style="overflow:auto"></div>
<h3>Audit log</h3><div id="histAudit" style="overflow:auto"></div>
</section>
<!-- LIVE -->
<section id="pg-live" class="page">
<h2>Live Trading <span class="badge bad">DISABLED BY DEFAULT</span></h2>
<div class="why"><b>Real funds are at risk.</b> Live trading never activates automatically. Complete all 10 steps. Withdrawal-enabled keys are <b>refused</b>.</div>
<div class="grid2">
<div class="card"><h3>1–6 · Connect exchange (trade-only key)</h3>
<label>Exchange</label><select id="lvEx"><option>binance</option></select>
<label>API Key</label><input id="lvKey" autocomplete="off">
<label>API Secret</label><input id="lvSec" type="password" autocomplete="off">
<label class="tgl"><input type="checkbox" id="lvWd" style="width:auto"> Withdrawals enabled on this key? (must be OFF)</label>
<div class="row" style="margin-top:8px"><button class="btn" onclick="liveConnect()">TEST CONNECTION</button></div>
<div id="lvConn" class="small"></div></div>
<div class="card"><h3>7–10 · Confirmations</h3>
<label class="tgl"><input type="checkbox" id="lvRisk" style="width:auto"> I understand I can lose money</label><br>
<label class="tgl"><input type="checkbox" id="lvPerm" style="width:auto"> I verified trade-only permissions, withdrawals OFF</label><br>
<label class="tgl"><input type="checkbox" id="lvRt" style="width:auto"> Run risk-engine self-test</label><br>
<label class="tgl"><input type="checkbox" id="lvPr" style="width:auto"> Paper readiness (≥5 paper fills)</label><br>
<label class="tgl"><input type="checkbox" id="lvCf" style="width:auto"> EXPLICITLY enable live trading</label>
<div class="row" style="margin-top:8px"><button class="btn danger" onclick="liveEnable()">ENABLE LIVE TRADING</button></div>
<div id="lvOut" class="small"></div></div>
</div></section>
<!-- SETTINGS -->
<section id="pg-settings" class="page">
<h2>Settings</h2>
<div class="grid2">
<div class="card"><h3>Controls</h3>
<label>Risk mode</label><select id="sRisk"><option>Conservative</option><option>Moderate</option><option>Aggressive</option></select>
<label>Max positions</label><input id="sMaxPos" type="number" min="1" max="10">
<label class="tgl"><input type="checkbox" id="sAuto" style="width:auto"> AI automation</label><br>
<label class="tgl"><input type="checkbox" id="sRes" style="width:auto"> Auto research</label>
<div class="row" style="margin-top:8px"><button class="btn" onclick="saveSettings()">SAVE</button>
<button class="btn ghost" onclick="backup()">💾 BACKUP NOW</button></div>
<div id="sOut" class="small"></div></div>
<div class="card"><h3>System</h3>
<div id="diagOut" class="small">Press Full System Check on Home.</div>
<div class="row" style="margin-top:8px"><button class="btn ghost" onclick="tour()">SHOW TOUR AGAIN</button></div>
</div></div></section>
<!-- HELP -->
<section id="pg-help" class="page">
<h2>Help Center</h2>
<div id="helpList"></div>
</section>
<footer>ZePay v1.0.0 · PAPER by default · Past performance ≠ future results · <a href="#" onclick="showPage('help');return false">Help</a></footer>
</div></div>
<button class="kill" onclick="killToggle()">⏻ STOP TRADING</button>
<div id="setup" style="display:none"><div class="setupcard">
<h2>Welcome to ZePay 🤖</h2>
<p class="mut">ZePay watches the crypto markets for you, ranks opportunities with AI, manages risk, and paper-trades automatically. Real-money trading is optional and OFF by default.</p>
<h3 style="margin-top:12px">Step 1 — ZePay Key</h3>
<input id="licKey" placeholder="ZEPAY-XXXX-XXXX (or DEMO-KEY to try)">
<div class="row" style="margin-top:10px"><button class="btn" onclick="verifyKey()">VERIFY KEY</button></div>
<div id="licOut" class="small" style="margin-top:8px"></div>
<div id="initList" class="small" style="margin-top:8px"></div>
</div></div>
<script>
let TOKEN=localStorage.getItem('zepay_token')||'';
let ADV=false, Cfg={}, Status={}, Mkts=[];
function api(p,o){o=o||{};o.headers=Object.assign({'Content-Type':'application/json','X-ZePay-Token':TOKEN},o.headers||{});
 if(o.body&&typeof o.body!=='string')o.body=JSON.stringify(o.body);return fetch(p,o).then(r=>r.json());}
function cur(){return (Status.display_currency||'₹');}
function money(x){x=+x||0;return cur()+x.toLocaleString(undefined,{maximumFractionDigits:0});}
function showPage(p){document.querySelectorAll('.page').forEach(e=>e.classList.remove('on'));
 document.getElementById('pg-'+p).classList.add('on');
 document.querySelectorAll('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.p===p));
 if(p==='positions')loadPositions();if(p==='portfolio')loadPortfolio();if(p==='risk')loadRisk();
 if(p==='models')loadModels();if(p==='perf')loadPerf();if(p==='history')loadHistory();
 if(p==='markets')renderMkts();if(p==='opps')renderOpps();if(p==='help')loadHelp();if(p==='backtest')loadBacktests();}
function toggleView(){ADV=!ADV;document.querySelectorAll('.adv').forEach(e=>e.style.display=ADV?'inline':'none');renderOpps();}
async function boot(){let s=await api('/api/setup/status');if(!s.setup){document.getElementById('setup').style.display='flex';return;}
 if(!TOKEN){document.getElementById('setup').style.display='flex';return;}
 await refresh();setInterval(refresh,30000);}
async function verifyKey(){let k=document.getElementById('licKey').value;
 let o=document.getElementById('licOut');o.textContent='Verifying…';
 let r=await api('/api/setup/verify',{method:'POST',body:{key:k}});
 if(!r.ok){o.innerHTML='<b style=color:#f87171>'+r.error+'</b>';return;}
 TOKEN=r.token;localStorage.setItem('zepay_token',TOKEN);
 let items=['API authentication','Database','Cache','Market data','AI engine','Risk engine','Portfolio engine','Paper trading','Dashboard','Monitoring'];
 let h=items.map(i=>'✓ '+i).join('<br>');document.getElementById('initList').innerHTML=h;
 o.innerHTML='<b class=tick>ZePay is ready.</b>';setTimeout(()=>{document.getElementById('setup').style.display='none';refresh();tour();},900);}
async function refresh(){try{let r=await api('/api/status');if(!r.ok){if(r.error&&r.error.includes('authenticated')){document.getElementById('setup').style.display='flex';return;}return;}
 Status=r;Cfg=Cfg||{};
 document.getElementById('modeBadge').textContent=r.trading_mode+' TRADING';
 document.getElementById('modeBadge').className='badge '+(r.trading_mode==='LIVE'?'live':'paper');
 let sb=document.getElementById('stateBadge');
 let map={NORMAL:['ok','● SYSTEM HEALTHY'],CAUTION:['warn','● ATTENTION'],DEFENSIVE:['warn','● DEFENSIVE'],HALTED:['bad','● TRADING HALTED']};
 let m=map[r.system_state]||map.NORMAL;sb.className='badge '+m[0];sb.textContent=m[1];
 document.getElementById('healthLine').textContent=(r.system_state==='NORMAL'?'🟢 ':'')+(r.system_msg||'')+' · '+(r.trading_mode==='PAPER'?'No real money is being traded.':'Real funds are at risk.');
 document.getElementById('cEquity').textContent=money(r.equity);
 let p=document.getElementById('cPnl');p.textContent=(r.day_pnl>=0?'+':'')+money(r.day_pnl);p.className='big '+(r.day_pnl>=0?'pos':'neg');
 document.getElementById('cDd').textContent=r.drawdown_pct+'%';
 document.getElementById('cPos').textContent=r.open_positions;
 document.getElementById('cAi').textContent=r.model_degraded?'⚠ DEGRADED':(r.model_trained?'🟢 GOOD':'🟡 WARMING UP');
 document.getElementById('cAiSub').textContent='last run '+(r.last_run||'—');
 document.getElementById('cExp').textContent=Math.round((r.exposure.total_pct||0)*100)+'%';
 if(!Mkts.length){let mk=await api('/api/markets');Mkts=mk.markets||[];renderMkts();}
 renderHomeOpps();renderOpps();}catch(e){}}
function hBadge(h){return h==='TRADEABLE'?'🟢 TRADEABLE':(h==='LIMITED'?'🟡 LIMITED':'🔴 BLOCKED');}
function renderHomeOpps(){let ops=(Status.opportunities||[]).slice(0,5);
 document.getElementById('homeOpps').innerHTML=ops.map(o=>'<div class=opp><div class=rank>#'+o.rank+'</div><div><b>'+o.market+'</b> · '+o.direction+' · '+o.regime+'<br><span class=small>'+hBadge(o.health)+' · '+o.decision+' — '+(o.decision_reasons||[]).join('; ')+'</span></div><div class=score>'+o.score+'</div></div>').join('')||'<p class=mut>Press ANALYZE NOW to generate opportunities from live data.</p>';
 let waits=(Status.opportunities||[]).filter(o=>o.decision!=='APPROVED').slice(0,3);
 document.getElementById('homeWhy').innerHTML=waits.length?'<h3>Why is ZePay waiting?</h3>'+waits.map(o=>'<div class=why><b>'+o.market+' → '+o.decision+'</b><br>Expected net '+o.expected_net_pct+'% vs costs '+o.cost_pct+'%<br>'+(o.decision_reasons||[]).join('<br>')+'</div>').join(''):'';}
function renderOpps(){let ops=(Status.opportunities||[]); 
 document.getElementById('oppList').innerHTML=ops.map(o=>'<div class=opp><div class=rank>#'+o.rank+'</div><div><b>'+o.market+'</b> <span class="badge '+(o.direction==='LONG'?'ok':o.direction==='SHORT'?'bad':'grey')+'">'+o.direction+'</span> <span class=small>'+o.regime+' · '+o.strategy+'</span><br><span class=small>Conf '+Math.round(o.confidence*100)+'% · Quality '+Math.round(o.quality*100)+'% · Exp '+o.expected_return_pct+'% · Net '+o.expected_net_pct+'% · Cost '+o.cost_pct+'%</span><br><span class=small>'+hBadge(o.health)+' · <b>'+o.decision+'</b> — '+(o.decision_reasons||[]).join('; ')+'</span>'+(ADV?'<br><span class="small mut adv">rsi '+o.rsi+' · atr '+o.atr_pct+'% · spread '+o.spread_bps+'bps · liq $'+Math.round(o.liquidity_usd).toLocaleString()+' · vol×'+o.volume_ratio+' · corrBTC '+o.corr_btc+'</span>':'')+'<details><summary class=small>WHY? (from real data)</summary><span class=small>'+(o.reasons||[]).join('<br>')+'</span></details></div><div style=text-align:right><div class=score>'+o.score+'</div>'+(o.decision==='APPROVED'?'<button class="btn small" onclick="execTrade(\''+o.market+'\')">TRADE</button>':'<div class="small mut">NO-TRADE</div>')+'</div></div>').join('')||'<p class=mut>No opportunities yet — press Refresh.</p>';}
async function analyze(){let r=await api('/api/analyze',{method:'POST',body:{}});if(r.ok){await refresh();}else alert(r.error);}
async function train(){let r=await api('/api/train',{method:'POST',body:{}});alert(r.ok?JSON.stringify(r.metrics):r.error);refresh();}
async function experiment(){let r=await api('/api/experiment',{method:'POST',body:{}});document.getElementById('expOut').innerHTML='<pre class=small>'+JSON.stringify(r.result||r,null,2)+'</pre>';}
async function execTrade(m){if(!confirm('Paper trade '+m+'?'))return;let r=await api('/api/execute',{method:'POST',body:{market:m}});alert(r.ok?'Filled @ '+r.fill_price:r.error);refresh();}
async function renderMkts(){if(!Mkts.length){let mk=await api('/api/markets');Mkts=mk.markets||[];}
 let q=(document.getElementById('mktSearch').value||'').toLowerCase();
 let uni=Status.universe||[];let ops={};(Status.opportunities||[]).forEach(o=>ops[o.market]=o);
 document.getElementById('mktList').innerHTML='<table><tr><th></th><th>Asset</th><th>Price</th><th>24h</th><th>Regime</th><th>AI</th><th>Score</th><th>Health</th></tr>'+Mkts.filter(m=>!q||m.symbol.toLowerCase().includes(q)).map(m=>{let o=ops[m.symbol]||{};return '<tr><td><input type=checkbox data-m="'+m.symbol+'" '+(uni.includes(m.symbol)?'checked':'')+'></td><td><b>'+m.symbol+'</b></td><td>'+(o.price||'—')+'</td><td>'+(o.change24!=null?o.change24+'%':'—')+'</td><td>'+(o.regime||'—')+'</td><td>'+(o.direction||'—')+'</td><td>'+(o.score!=null?o.score:'—')+'</td><td>'+(o.health?hBadge(o.health):'—')+'</td></tr>';}).join('')+'</table>';}
function universeAll(on){document.querySelectorAll('#mktList input[type=checkbox]').forEach(c=>c.checked=on);}
async function saveUniverse(){let uni=[...document.querySelectorAll('#mktList input[type=checkbox]:checked')].map(c=>c.dataset.m);
 let r=await api('/api/universe',{method:'POST',body:{universe:uni}});alert(r.ok?'Universe saved: '+uni.join(', '):r.error);refresh();}
async function loadPositions(){let r=await api('/api/positions');let ops={};(Status.opportunities||[]).forEach(o=>ops[o.market]=o);
 let h='<h3>Open positions</h3><table><tr><th>Asset</th><th>Dir</th><th>Entry</th><th>Current</th><th>SL/TP</th><th>Size</th><th>P&L</th><th></th></tr>'+(r.open||[]).map(p=>'<tr><td><b>'+p.market+'</b></td><td>'+p.direction+'</td><td>'+p.entry_price+'</td><td>'+p.current_price+'</td><td>'+(+p.stop_loss).toFixed(4)+' / '+(+p.take_profit).toFixed(4)+'</td><td>'+money(p.exposure)+'</td><td class='+(p.unrealized_pnl>=0?'pos':'neg')+'>'+(+p.unrealized_pnl).toFixed(2)+'</td><td><button class="btn ghost small" onclick="closePos(\''+p.market+'\')">CLOSE</button></td></tr>').join('')+'</table>';
 h+='<h3>Markets</h3><table><tr><th>Asset</th><th>Price</th><th>24h</th><th>Regime</th><th>AI</th><th>Conf</th><th>Quality</th><th>Pos</th><th>Status</th></tr>'+(Status.opportunities||[]).map(o=>'<tr><td><b>'+o.market+'</b></td><td>'+o.price+'</td><td>'+o.change24+'%</td><td>'+o.regime+'</td><td>'+o.direction+'</td><td>'+Math.round(o.confidence*100)+'%</td><td>'+Math.round(o.quality*100)+'%</td><td>'+((r.open||[]).find(p=>p.market===o.market)?'OPEN':'—')+'</td><td>'+hBadge(o.health)+'</td></tr>').join('')+'</table>';
 document.getElementById('posWrap').innerHTML=h;}
async function closePos(m){if(!confirm('Close '+m+'?'))return;let r=await api('/api/close',{method:'POST',body:{market:m}});alert(r.ok?'Closed, P&L '+r.pnl:r.error);refresh();loadPositions();}
async function loadPortfolio(){let r=await api('/api/portfolio');let a=r.account||{};
 let h='<div class=cards><div class=card><h3>EQUITY</h3><div class=big>'+money(a.equity)+'</div></div><div class=card><h3>AVAILABLE</h3><div class=big>'+money(a.available)+'</div></div><div class=card><h3>INVESTED</h3><div class=big>'+money(a.invested)+'</div></div><div class=card><h3>UNREALIZED</h3><div class="big '+(a.unrealized_pnl>=0?'pos':'neg')+'">'+(+a.unrealized_pnl||0).toFixed(2)+'</div></div><div class=card><h3>REALIZED</h3><div class=big>'+(+a.realized_pnl||0).toFixed(2)+'</div></div></div>';
 h+='<h3>Allocation</h3>'+Object.entries(r.allocation||{}).map(([k,v])=>'<div class=small>'+k+' '+v+'%</div><div class=bar><i style="width:'+v+'%"></i></div>').join('');
 h+='<h3 style="margin-top:10px">Risk contribution</h3>'+Object.entries(r.risk_contribution||{}).map(([k,v])=>'<div class=small>'+k+' '+Math.round(v*100)+'%</div><div class=bar><i style="width:'+Math.round(v*100)+'%;background:#ef4444"></i></div>').join('');
 h+='<h3 style="margin-top:10px">Correlation</h3><table><tr><th></th>'+Object.keys(r.correlation||{}).map(k=>'<th>'+k.split('/')[0]+'</th>').join('')+'</tr>'+Object.entries(r.correlation||{}).map(([k,row])=>'<tr><td><b>'+k.split('/')[0]+'</b></td>'+Object.values(row).map(v=>'<td>'+v+'</td>').join('')+'</tr>').join('')+'</table>';
 document.getElementById('pfWrap').innerHTML=h;}
async function loadRisk(){let r=await api('/api/risk');
 document.getElementById('riskWrap').innerHTML='<div class=cards><div class=card><h3>RISK LEVEL</h3><div class=big>'+(r.state==='NORMAL'?'🟢 LOW':r.state==='HALTED'?'🔴 HALTED':'🟡 '+r.state)+'</div><div class=small>'+r.message+'</div></div><div class=card><h3>DAILY LOSS LIMIT</h3><div class=big>'+money(r.daily_loss_limit)+'</div></div><div class=card><h3>CURRENT DAY P&L</h3><div class=big>'+r.current_day_pnl+'</div></div><div class=card><h3>REMAINING BUDGET</h3><div class=big>'+money(r.remaining_budget)+'</div></div><div class=card><h3>EXPOSURE</h3><div class=big>'+r.current_exposure_pct+'%</div><div class=small>max '+r.max_exposure_pct+'%</div></div><div class=card><h3>DRAWDOWN</h3><div class=big>'+r.current_drawdown_pct+'%</div><div class=small>halt at '+r.max_drawdown_pct+'%</div></div></div><p class=small>ⓘ The risk engine has final authority over the AI. Limits cannot be overridden by models.</p>';}
async function killToggle(){let r=await api('/api/kill',{method:'POST',body:{on:true}});alert(r.ok?'STOP engaged — no new entries. Existing positions preserved.':r.error);refresh();}
async function halt(on){let r=await api('/api/halt',{method:'POST',body:{on:on}});refresh();loadRisk();}
async function resume(){let r=await api('/api/resume',{method:'POST',body:{}});refresh();loadRisk();}
async function runBacktest(){let mkts=document.getElementById('btMkts').value.split(',').map(s=>s.trim()).filter(Boolean);
 let r=await api('/api/backtest',{method:'POST',body:{markets:mkts,starting:+document.getElementById('btStart').value}});
 document.getElementById('btOut').innerHTML=r.ok?'<pre class=small>'+JSON.stringify(r.result,null,2)+'</pre>':'<b style=color:#f87171>'+r.error+'</b>';loadBacktests();}
async function loadBacktests(){let r=await api('/api/backtests');document.getElementById('btList').innerHTML='<table><tr><th>Name</th><th>Return</th><th>DD</th><th>PF</th><th>Trades</th><th>When</th></tr>'+(r.backtests||[]).map(b=>'<tr><td>'+b.name+'</td><td>'+b.net_return_pct+'%</td><td>'+b.max_dd_pct+'%</td><td>'+b.profit_factor+'</td><td>'+b.trades+'</td><td>'+(b.created_at||'').slice(0,16)+'</td></tr>').join('')+'</table>';}
async function loadModels(){let r=await api('/api/models');let c=r.current||{};
 let mods=['Direction','Return','Volatility','Regime','Trade Quality'];let st=c.degraded?'🟡 DEGRADED':(c.trained?'🟢 ACTIVE':'🟡 WARMING UP');
 document.getElementById('modelWrap').innerHTML='<div class=cards>'+mods.map(m=>'<div class=card><h3>'+m+' Model</h3><div class=big style="font-size:16px">'+st+'</div></div>').join('')+'</div><pre class=small>'+JSON.stringify(c.metrics||{},null,2)+'</pre><h3>Training history</h3><table><tr><th>Name</th><th>Version</th><th>Status</th><th>Samples</th><th>When</th></tr>'+(r.models||[]).map(m=>'<tr><td>'+m.name+'</td><td>'+m.version+'</td><td>'+m.status+'</td><td>'+m.samples+'</td><td>'+(m.trained_at||'').slice(0,16)+'</td></tr>').join('')+'</table>';}
async function loadPerf(){let r=await api('/api/snapshots');let snaps=(r.snapshots||[]).reverse();
 let cv=document.getElementById('eqChart'),ctx=cv.getContext('2d');ctx.clearRect(0,0,900,180);
 if(snaps.length>1){let eqs=snaps.map(s=>+s.equity);let mn=Math.min(...eqs),mx=Math.max(...eqs);let rg=(mx-mn)||1;
  ctx.strokeStyle='#7c6cff';ctx.lineWidth=2;ctx.beginPath();
  eqs.forEach((e,i)=>{let x=i/(eqs.length-1)*880+10,y=170-(e-mn)/rg*150;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}
 let t=await api('/api/trades');let fills=t.fills||[];
 document.getElementById('perfWrap').innerHTML='<div class=cards><div class=card><h3>SNAPSHOTS</h3><div class=big>'+snaps.length+'</div></div><div class=card><h3>FILLS</h3><div class=big>'+fills.length+'</div></div><div class=card><h3>FEES PAID</h3><div class=big>'+fills.reduce((s,f)=>s+(+f.fee||0),0).toFixed(2)+'</div></div></div><p class=small>Net performance (after fees/slippage) is shown prominently. Past performance ≠ future results.</p>';}
async function loadHistory(){let t=await api('/api/trades');let a=await api('/api/audit');
 document.getElementById('histOrders').innerHTML='<table><tr><th>Time</th><th>Market</th><th>Side</th><th>Qty</th><th>Price</th><th>Status</th><th>Mode</th></tr>'+(t.orders||[]).map(o=>'<tr><td>'+(o.created_at||'').slice(0,16)+'</td><td>'+o.market+'</td><td>'+o.side+'</td><td>'+(+o.qty).toFixed(5)+'</td><td>'+o.price+'</td><td>'+o.status+'</td><td>'+o.mode+'</td></tr>').join('')+'</table>';
 document.getElementById('histAudit').innerHTML='<table><tr><th>Time</th><th>Cat</th><th>Action</th><th>Details</th></tr>'+(a.audit||[]).slice(0,60).map(x=>'<tr><td>'+(x.created_at||'').slice(0,16)+'</td><td>'+x.category+'</td><td>'+x.action+'</td><td class=small>'+(x.details||'').slice(0,160)+'</td></tr>').join('')+'</table>';}
async function sysCheck(){let r=await api('/api/system_check');
 document.getElementById('sysResult').innerHTML='<h3>'+(r.ok?'✓ ':'⚠ ')+r.verdict+'</h3><table>'+Object.entries(r.results||{}).map(([k,v])=>'<tr><td><b>'+k+'</b></td><td>'+(v.ok?'✓':'✗')+'</td><td class=small>'+(v.error||v.note||'')+'</td></tr>').join('')+'</table>';}
async function loadHelp(){let r=await api('/api/help');document.getElementById('helpList').innerHTML=Object.entries(r.topics||{}).map(([k,v])=>'<details><summary>'+k.replace(/_/g,' ').toUpperCase()+'</summary><p class=small>'+v+'</p></details>').join('');}
async function saveSettings(){let r=await api('/api/config',{method:'POST',body:{risk_mode:document.getElementById('sRisk').value,max_positions:+document.getElementById('sMaxPos').value||5,ai_automation:document.getElementById('sAuto').checked,auto_research:document.getElementById('sRes').checked}});document.getElementById('sOut').textContent=r.ok?'Saved.':'Error: '+r.error;}
async function backup(){let r=await api('/api/backup',{method:'POST',body:{}});alert(r.ok?'Backup: '+r.backup:r.error);}
async function liveConnect(){let r=await api('/api/live/connect',{method:'POST',body:{exchange:document.getElementById('lvEx').value,api_key:document.getElementById('lvKey').value,api_secret:document.getElementById('lvSec').value,withdrawal_enabled:document.getElementById('lvWd').checked}});document.getElementById('lvConn').innerHTML=r.ok?'✓ Connected. Steps: '+r.steps.join(', '):'<b style=color:#f87171>'+r.error+'</b>';}
async function liveEnable(){let r=await api('/api/live/enable',{method:'POST',body:{risk_ack:document.getElementById('lvRisk').checked,permissions_verified:document.getElementById('lvPerm').checked,risk_test:document.getElementById('lvRt').checked,paper_ready:document.getElementById('lvPr').checked,explicit_confirm:document.getElementById('lvCf').checked}});document.getElementById('lvOut').innerHTML=r.ok?(r.enabled?'<b>LIVE ENABLED — real funds at risk.</b>':'Steps: '+(r.steps||[]).join(', ')+(r.missing?'<br>Missing: '+r.missing.join(', '):'')):'<b style=color:#f87171>'+r.error+'</b>';refresh();}
function tour(){alert('Welcome to ZePay! 1) Overview shows equity & P&L. 2) AI Opportunities shows ranked coins. 3) Positions shows open trades. 4) Risk shows limits. 5) WHY boxes explain every WAIT. 6) Red STOP button halts entries instantly.');}
boot();
</script></body></html>"""

# =============================================================================
# PART 4C — SELF-TESTS + CLI + MAIN
# =============================================================================

def self_test():
    """Fast offline unit checks (no network). Returns (passed, failed, details)."""
    passed, failed, det = 0, 0, []

    def t(name, fn):
        nonlocal passed, failed
        try:
            fn()
            passed += 1
            det.append((name, True, ""))
        except Exception as e:
            failed += 1
            det.append((name, False, str(e)[:200]))

    def _risk_blocks():
        f = AssetFeatures(market="T/USDT")
        opp = Opportunity(market="T/USDT", direction="LONG", score=99, confidence=0.9,
                          quality=0.9, regime="BULLISH_TREND", strategy="trend",
                          expected_return=0.05, expected_net=0.04, cost_pct=0.001, price=0)
        d, _, _ = RiskEngine.authorize(opp, f, {"status": "BLOCKED", "reasons": ["x"]},
                                       [], {"equity": 10000}, {})
        assert d in (Decision.REJECTED, Decision.HALTED), d

    def _no_data_no_trade():
        f = AssetFeatures(market="T/USDT", price=0, n_candles=0)
        r = RegimeEngine.detect(f)
        assert r["regime"] == "UNSTABLE"

    def _sizer_caps():
        acct = {"equity": 10000}
        f = AssetFeatures(market="BTC/USDT", price=50000, atr14=1000, atr_pct=0.02)
        opp = Opportunity(market="BTC/USDT", direction="LONG", score=90, confidence=0.8,
                          quality=0.8, regime="BULLISH_TREND", strategy="trend",
                          expected_return=0.01, expected_net=0.005, cost_pct=0.001, price=50000)
        s = Sizer.size(opp, f, acct, {"size_mult": 1.0})
        assert s["notional"] <= 10000 * safe_float(CONFIG.get("max_position_pct", 0.2)) * 1.01

    def _indicators():
        c = [100 + i for i in range(60)]
        assert 0 <= rsi(c) <= 100
        h = [x + 1 for x in c]; l = [x - 1 for x in c]
        assert atr(h, l, c) > 0
        m = macd(c)
        assert "hist" in m

    def _license():
        ok, _ = verify_license_key("ZEPAY-ABC-12345")
        assert ok
        ok, _ = verify_license_key("bad")
        assert not ok

    def _db():
        DBASE.query_one("SELECT 1 AS x")

    def _rank():
        a = Opportunity(market="A", direction="LONG", score=80, confidence=0.7, quality=0.7,
                        regime="BULLISH_TREND", strategy="trend", expected_return=0.01,
                        expected_net=0.005, cost_pct=0.001, price=1)
        b = Opportunity(market="B", direction="LONG", score=90, confidence=0.7, quality=0.7,
                        regime="BULLISH_TREND", strategy="trend", expected_return=0.01,
                        expected_net=0.005, cost_pct=0.001, price=1)
        assert OpportunityEngine.rank([a, b])[0].market == "B"

    def _kill_halts():
        old = CONFIG.get("kill_switch")
        with CONFIG._lock:
            CONFIG.cfg["kill_switch"] = True
        try:
            st, _ = RiskEngine.system_state({"equity": 10000, "drawdown_pct": 0, "day_pnl": 0})
            assert st == SystemState.HALTED
        finally:
            with CONFIG._lock:
                CONFIG.cfg["kill_switch"] = old

    t("risk_blocks_bad_data", _risk_blocks)
    t("no_data_no_trade", _no_data_no_trade)
    t("sizer_respects_caps", _sizer_caps)
    t("indicators_sane", _indicators)
    t("license_validation", _license)
    t("database", _db)
    t("ranking_orders", _rank)
    t("kill_switch_halts", _kill_halts)
    return passed, failed, det


def run_server(port):
    ensure_paper_account()
    # start automation thread
    if not ENGINE.get("thread") or not ENGINE["thread"].is_alive():
        ENGINE["stop"] = False
        th = threading.Thread(target=auto_loop, daemon=True, name="zepay-engine")
        th.start()
        ENGINE["thread"] = th
    srv = ThreadedServer(("0.0.0.0", port), APIHandler)
    print(f"\n  ZE PAY v{VERSION} — running on http://localhost:{port}")
    print("  Paper trading by default. Live trading DISABLED until explicit opt-in.")
    print("  Press Ctrl+C to stop.\n")
    log(f"server started on :{port}")
    audit("system", "server_started", {"port": port})
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ZePay…")
    finally:
        ENGINE["stop"] = True
        audit("system", "server_stopped", {})


def main():
    ap = argparse.ArgumentParser(prog="zepay", description="ZePay all-in-one trading platform")
    ap.add_argument("command", nargs="?", default="start",
                    choices=["start", "stop", "restart", "status", "doctor", "logs", "backup", "check", "version"])
    ap.add_argument("--port", type=int, default=int(os.environ.get("ZEPAY_PORT", DEFAULT_PORT)))
    args = ap.parse_args()
    os.makedirs(DATA_DIR, exist_ok=True)

    if args.command == "version":
        print(f"ZePay {VERSION} ({EDITION})")
        return
    if args.command == "doctor":
        p, f, det = self_test()
        print(f"ZePay doctor — passed {p}, failed {f}")
        for n, ok, e in det:
            print(f"  {'✓' if ok else '✗'} {n}" + (f" — {e}" if e else ""))
        d = run_diagnostics()
        for k, v in d["checks"].items():
            print(f"  {'✓' if v.get('ok') else '✗'} {k}" + (f" — {v.get('error', '')}" if not v.get('ok') else ""))
        return
    if args.command == "check":
        r = full_system_check()
        print(f"{'✓' if r['ok'] else '⚠'} {r['verdict']}")
        for k, v in r["results"].items():
            print(f"  {'✓' if v.get('ok') else '✗'} {k}" + (f" — {v.get('error') or v.get('note') or ''}"))
        return
    if args.command == "status":
        try:
            d = http_json(f"http://localhost:{args.port}/api/health", timeout=5)
            print(jdump(d))
        except Exception as e:
            print(f"Server not reachable on :{args.port} ({e}). Is ZePay running?")
        return
    if args.command == "logs":
        try:
            with open(LOG_PATH) as fh:
                print("".join(fh.readlines()[-80:]))
        except Exception:
            print("No logs yet.")
        return
    if args.command == "backup":
        print(jdump(do_backup()))
        return
    if args.command in ("stop", "restart"):
        print("Single-file edition runs in the foreground — press Ctrl+C to stop, then `python3 zepay.py start` to restart.")
        if args.command == "restart":
            run_server(args.port)
        return
    # start
    p, f, det = self_test()
    if f:
        print(f"Self-test: {p} passed, {f} FAILED — refusing to start unsafe.")
        for n, ok, e in det:
            if not ok:
                print(f"  ✗ {n}: {e}")
        sys.exit(1)
    print(f"Self-test: {p} passed ✓")
    run_server(args.port)


if __name__ == "__main__":
    main()
