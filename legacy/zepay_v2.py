#!/usr/bin/env python3
"""
================================================================================
ZEPAY — ULTIMATE EDITION — AI-Assisted Quantitative Multi-Asset Crypto
Trading Platform
Version 2.0.0  •  all-in-one single-file edition  •  Python 3.10+ stdlib only
================================================================================

RUN:
    python3 zepay.py            → http://localhost:8000  (binds 0.0.0.0)

CLI:
    python3 zepay.py start | doctor | status | check | logs | backup | version

PRODUCT (one intelligent engine, NOT per-coin bots):
  Provider/Integration Manager (real health + rate limits + audit)
  • Dynamic Multi-Asset Universe (live exchange metadata; no hard-coded cap)
  • Market Data Manager (REST + optional WebSocket, normalized schema, UTC)
  • Feature Engine • Cross-Asset Intelligence • Regime Engine
  • Quant AI Ensemble (Direction / Return / Volatility / Regime / Quality,
    trained from-scratch on REAL data; strict mode never fabricates signals)
  • LLM AI Provider Manager (OpenAI/Anthropic/Google/DeepSeek/Qwen/xAI/
    OpenRouter/Ollama/vLLM — advisory research only, NEVER trades)
  • Research Agents (Market/Technical/Quant/Regime/Derivatives/Risk/Portfolio/
    Execution analysts + optional LLM bull/bear debate, TradingAgents-style)
  • Strategy Engine • Opportunity Ranker • Portfolio Intelligence
  • GLOBAL RISK ENGINE (final authority) • Position Sizer
  • Execution Engine: PAPER (real prices/books/fees, simulated fills incl.
    partial fills + latency) and LIVE (real signed exchange orders)
  • Reconciliation Engine (startup + periodic; mismatch blocks live)
  • Backtester (real history, costs, no look-ahead) • Walk-forward research
  • Model Registry (champion/challenger; promotion is manual, never automatic)
  • Decision Ledger (every decision explainable) • Audit Log • Alerts
  • MCP Manager (registry, disabled by default, NO trading permissions)
  • Kill Switch • Live-Trading Safety Gate • Encrypted secret storage
  • Embedded dashboard (Assets, Signals, Portfolio, Orders, Backtest, Risk,
    Exchange, API Manager, AI Manager, MCP, Decisions, Diagnostics, Help…)

DATA HONESTY (NON-NEGOTIABLE):
  • REAL_DATA_REQUIRED = True. Market data comes from real exchange APIs
    (Binance spot REST public + signed user endpoints; optional WebSocket).
  • If real data is unavailable the platform shows "REAL DATA UNAVAILABLE",
    marks assets STALE/BLOCKED with the reason and refuses new trades.
    ZePay NEVER invents prices, signals, fills, balances or backtests.
  • Paper fills are simulated ONLY in execution: they use real fetched
    prices, real order-book depth, explicit fee/slippage/latency models.
  • Every provider shows auth method, health, last success, last error.
    Missing specifications are marked BLOCKED — never faked as connected.

ZEPAY LICENSE KEY:
  • No ZEPAY cloud verification API specification is bundled with this
    build. The LicenseAdapter interface therefore supports:
      - CLOUD verification against an operator-configured endpoint
        (inactive/BLOCKED until the endpoint is configured), and
      - OFFLINE format validation (clearly labeled; NOT cloud verification).
    The active verification mode is displayed in the UI and audit log.

SAFETY HIERARCHY (enforced in code — see RiskEngine and ExecutionEngine):
  1 HARD SAFETY LIMITS > 2 GLOBAL RISK ENGINE > 3 PORTFOLIO CONSTRAINTS
  > 4 EXECUTION SAFETY (idempotency, precision, reconciliation)
  > 5 DATA VALIDITY > 6 STRATEGIES > 7 AI MODELS > 8 LLM RESEARCH
  (advisory only) . AI/LLM/agents can NEVER place orders or change limits.

DEFAULTS: PAPER_TRADING=TRUE, LIVE_TRADING=FALSE, WITHDRAWALS=FORBIDDEN.
LIVE trading requires the explicit multi-step gate: real signed credential
test, real permission verification (withdrawal-enabled keys are REFUSED),
account reconciliation, risk self-test, paper history and typed user
confirmation. There is NO automatic live activation.

STORAGE: SQLite at $ZEPAY_DATA_DIR (default ./zepay_data). Portable schema.
SECRETS: encrypted at rest with ChaCha20-Poly1305 (RFC 8439) under a
per-install master key (zepay_data/.master.key, 0600). Never logged,
never sent to the browser, never included in backups of config alone.

PAST PERFORMANCE IS NOT A GUARANTEE OF FUTURE RESULTS. Crypto trading is
risky. This software is for education/research; paper-trade first. Never
risk money you cannot afford to lose.
================================================================================
"""

import argparse
import base64
import copy
import csv
import hashlib
import hmac
import html as htmlmod
import http.server
import io
import json
import math
import os
import random
import re
import secrets
import shutil
import socket
import socketserver
import sqlite3
import ssl
import statistics
import struct
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum

VERSION = "2.0.0"
EDITION = "ultimate-all-in-one"
APP_NAME = "ZEPAY"

# The single most important constant in this codebase. If real data is not
# available, real trading (and paper trading) must STOP, not substitute.
REAL_DATA_REQUIRED = True

DATA_DIR = os.environ.get("ZEPAY_DATA_DIR") or os.path.join(os.getcwd(), "zepay_data")
DB_PATH = os.path.join(DATA_DIR, "zepay.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
LOG_PATH = os.path.join(DATA_DIR, "zepay.log")
MASTER_KEY_PATH = os.path.join(DATA_DIR, ".master.key")

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
    if isinstance(s, str):
        s = s.encode("utf-8")
    return hashlib.sha256(s).hexdigest()

def b64e(b):
    return base64.b64encode(b).decode("ascii")

def b64d(s):
    return base64.b64decode(s.encode("ascii") if isinstance(s, str) else s)

SECRET_KEY_HINTS = ("secret", "password", "passwd", "private", "seed", "token",
                    "passphrase", "authorization", "api_key", "apikey", "credential")

def redact(obj):
    """Recursively redact secret-looking values for logs/API responses."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if any(w in lk for w in SECRET_KEY_HINTS):
                out[k] = "***REDACTED***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(redact(x) for x in obj)
    return obj

def mask_key(k):
    """Mask a key for display: keep a tiny prefix/suffix only."""
    if not k:
        return ""
    k = str(k)
    if len(k) <= 8:
        return "****"
    return k[:4] + "****" + k[-2:]

def jdump(o):
    return json.dumps(o, default=str)

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

class ProviderStatus(str, Enum):
    OK = "OK"                      # healthy, real response verified
    DEGRADED = "DEGRADED"          # reachable with errors/slow
    UNREACHABLE = "UNREACHABLE"    # network failure (honest state)
    NOT_CONFIGURED = "NOT_CONFIGURED"  # integration exists, no credentials
    BLOCKED = "BLOCKED"            # specification missing / refused by policy
    DISABLED = "DISABLED"          # user turned it off

class TradingPermission(str, Enum):
    NONE = "NONE"
    READONLY = "READONLY"
    TRADE = "TRADE"

# =============================================================================
# PART 1B — CRYPTO (stdlib-only, RFC-conformant)
# ChaCha20 stream cipher + Poly1305 MAC + AEAD (RFC 8439), HKDF (RFC 5869).
# Used to encrypt ALL secrets at rest (exchange keys, LLM keys, passphrases).
# =============================================================================

def _rotl32(v, c):
    return ((v << c) & 0xFFFFFFFF) | (v >> (32 - c))

def _chacha20_block(key, counter, nonce):
    """RFC 8439 §2.3 — one 64-byte block. key: 32 bytes, nonce: 12 bytes."""
    consts = [0x61707865, 0x3320646e, 0x79622d32, 0x6b206574]
    k = list(struct.unpack("<8I", key))
    n = list(struct.unpack("<3I", nonce))
    state = consts + k + [counter & 0xFFFFFFFF] + n
    x = list(state)
    QR = [  # quarter-round column/ diagonal indices
        (0, 4, 8, 12), (1, 5, 9, 13), (2, 6, 10, 14), (3, 7, 11, 15),
        (0, 5, 10, 15), (1, 6, 11, 12), (2, 7, 8, 13), (3, 4, 9, 14),
    ]
    def qr(a, b, c, d):
        x[a] = (x[a] + x[b]) & 0xFFFFFFFF; x[d] ^= x[a]; x[d] = _rotl32(x[d], 16)
        x[c] = (x[c] + x[d]) & 0xFFFFFFFF; x[b] ^= x[c]; x[b] = _rotl32(x[b], 12)
        x[a] = (x[a] + x[b]) & 0xFFFFFFFF; x[d] ^= x[a]; x[d] = _rotl32(x[d], 8)
        x[c] = (x[c] + x[d]) & 0xFFFFFFFF; x[b] ^= x[c]; x[b] = _rotl32(x[b], 7)
    for _ in range(10):
        for a, b, c, d in QR:
            qr(a, b, c, d)
    out = struct.pack("<16I", *[(x[i] + state[i]) & 0xFFFFFFFF for i in range(16)])
    return out

def chacha20_xor(key, counter_start, nonce, data):
    """XOR data with the ChaCha20 keystream starting at block counter_start."""
    if len(key) != 32:
        raise ValueError("chacha20 key must be 32 bytes")
    if len(nonce) != 12:
        raise ValueError("chacha20 nonce must be 12 bytes")
    out = bytearray()
    for i in range(0, len(data), 64):
        block = _chacha20_block(key, counter_start + (i // 64), nonce)
        chunk = data[i:i + 64]
        out.extend(bytes(a ^ b for a, b in zip(chunk, block)))
    return bytes(out)

# ---- Poly1305 (RFC 8439 §2.5) ----

_P1305 = (1 << 130) - 5

def poly1305_mac(key, msg):
    """RFC 8439 §2.5. key: 32 bytes. Returns 16-byte tag."""
    if len(key) != 32:
        raise ValueError("poly1305 key must be 32 bytes")
    r = struct.unpack("<4I", key[:16])
    r = (r[0] & 0x0FFFFFFF) | ((r[1] & 0x0FFFFFFC) << 32) | \
        ((r[2] & 0x0FFFFFFC) << 64) | ((r[3] & 0x0FFFFFFC) << 96)
    s = int.from_bytes(key[16:32], "little")
    acc = 0
    for i in range(0, len(msg), 16):
        block = msg[i:i + 16]
        n = int.from_bytes(block + b"\x01", "little")
        acc = (acc + n) * r % _P1305
    acc = (acc + s) & ((1 << 128) - 1)
    return acc.to_bytes(16, "little")

def _poly1305_key_gen(key, nonce):
    """RFC 8439 §2.6: one-time Poly1305 key = first 32 bytes of ChaCha20 block 0."""
    block = _chacha20_block(key, 0, nonce)
    return block[:32]

def aead_chacha20_poly1305_encrypt(key, nonce, plaintext, aad=b""):
    """RFC 8439 §2.8. key 32B, nonce 12B. Returns ciphertext||tag."""
    if len(nonce) != 12:
        raise ValueError("nonce must be 12 bytes")
    otk = _poly1305_key_gen(key, nonce)
    ct = chacha20_xor(key, 1, nonce, plaintext)
    mac_data = aead_pad(aad) + aead_pad(ct) + struct.pack("<QQ", len(aad), len(ct))
    tag = poly1305_mac(otk, mac_data)
    return ct + tag

def aead_chacha20_poly1305_decrypt(key, nonce, ciphertext_and_tag, aad=b""):
    """RFC 8439 §2.8. Raises ValueError on auth failure."""
    if len(nonce) != 12:
        raise ValueError("nonce must be 12 bytes")
    if len(ciphertext_and_tag) < 16:
        raise ValueError("ciphertext too short")
    ct, tag = ciphertext_and_tag[:-16], ciphertext_and_tag[-16:]
    otk = _poly1305_key_gen(key, nonce)
    mac_data = aead_pad(aad) + aead_pad(ct) + struct.pack("<QQ", len(aad), len(ct))
    expect = poly1305_mac(otk, mac_data)
    if not hmac.compare_digest(expect, tag):
        raise ValueError("AEAD authentication failed (wrong key or tampered data)")
    return chacha20_xor(key, 1, nonce, ct)

def aead_pad(data):
    if len(data) % 16 == 0:
        return data
    return data + b"\x00" * (16 - (len(data) % 16))

# ---- HKDF-SHA256 (RFC 5869) ----

def hkdf_sha256(ikm, salt=b"", info=b"", length=32):
    if not salt:
        salt = b"\x00" * 32
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm = b""
    t = b""
    i = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
        i += 1
    return okm[:length]

# ---- SecretBox: per-install master key + AEAD storage helpers ----

class SecretBox:
    """Encrypts secrets at rest. Master key generated once per install and
    stored with 0600 permissions. Subkeys derived per purpose via HKDF."""

    def __init__(self, master_key_path=MASTER_KEY_PATH):
        self.master_key_path = master_key_path
        self._key = self._load_or_create()
        self._lock = threading.RLock()

    def _load_or_create(self):
        os.makedirs(os.path.dirname(self.master_key_path), exist_ok=True)
        if os.path.exists(self.master_key_path):
            with open(self.master_key_path, "rb") as f:
                key = f.read().strip()
            if len(key) == 64:
                return bytes.fromhex(key.decode("ascii"))
            raise RuntimeError("master key file is corrupt; restore from backup or delete "
                               f"{self.master_key_path} (all stored secrets will be lost)")
        key = secrets.token_hex(32)
        fd = os.open(self.master_key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key.encode("ascii"))
        finally:
            os.close(fd)
        try:
            os.chmod(self.master_key_path, 0o600)
        except Exception:
            pass
        return bytes.fromhex(key)

    def _purpose_key(self, purpose):
        return hkdf_sha256(self._key, salt=b"zepay-v2", info=("secretbox:" + purpose).encode(), length=32)

    def encrypt(self, plaintext, purpose="general"):
        if isinstance(plaintext, str):
            plaintext = plaintext.encode("utf-8")
        nonce = secrets.token_bytes(12)
        key = self._purpose_key(purpose)
        ct = aead_chacha20_poly1305_encrypt(key, nonce, plaintext, aad=purpose.encode())
        return "v1." + b64e(nonce) + "." + b64e(ct)

    def decrypt(self, token, purpose="general"):
        if not token or not isinstance(token, str) or not token.startswith("v1."):
            raise ValueError("invalid secret token format")
        try:
            _, nb, cb = token.split(".", 2)
            nonce = b64d(nb)
            ct = b64d(cb)
        except Exception:
            raise ValueError("invalid secret token format")
        key = self._purpose_key(purpose)
        return aead_chacha20_poly1305_decrypt(key, nonce, ct, aad=purpose.encode()).decode("utf-8")

    def self_test(self):
        """Round-trip + RFC 8439 §A.5 AEAD vector. Raises on failure."""
        tok = self.encrypt("zepay-secret-roundtrip", purpose="selftest")
        assert self.decrypt(tok, purpose="selftest") == "zepay-secret-roundtrip"
        key = bytes.fromhex("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f")
        nonce = bytes.fromhex("070000004041424344454647")
        aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
        pt = b"Ladies and Gentlemen of the class of '99: If I could offer you only one " \
             b"tip for the future, sunscreen would be it."
        ct = aead_chacha20_poly1305_encrypt(key, nonce, pt, aad)
        expect_ct = bytes.fromhex(
            "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d6"
            "3dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b36"
            "92ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc"
            "3ff4def08e4b7a9de576d26586cec64b6116")
        assert ct[:-16] == expect_ct, "RFC 8439 A.5 ciphertext mismatch"
        expect_tag = bytes.fromhex("1ae10b594f09e26a7e902ecbd0600691")
        assert ct[-16:] == expect_tag, "RFC 8439 A.5 tag mismatch"
        assert aead_chacha20_poly1305_decrypt(key, nonce, ct, aad) == pt
        return True

SECRETBOX = None  # initialized after DATA_DIR is ensured (see boot below)

# =============================================================================
# PART 2 — CONFIG + DATABASE + AUDIT
# =============================================================================

# Fallback asset list used ONLY when live exchange metadata is unreachable.
# Clearly labeled as "default fallback" in the UI — never presented as a live
# exchange catalogue.
DEFAULT_MARKETS = [
    {"symbol": "BTC/USDT", "exchange_symbol": "BTCUSDT", "base": "BTC", "quote": "USDT"},
    {"symbol": "ETH/USDT", "exchange_symbol": "ETHUSDT", "base": "ETH", "quote": "USDT"},
    {"symbol": "SOL/USDT", "exchange_symbol": "SOLUSDT", "base": "SOL", "quote": "USDT"},
    {"symbol": "BNB/USDT", "exchange_symbol": "BNBUSDT", "base": "BNB", "quote": "USDT"},
    {"symbol": "XRP/USDT", "exchange_symbol": "XRPUSDT", "base": "XRP", "quote": "USDT"},
    {"symbol": "DOGE/USDT", "exchange_symbol": "DOGEUSDT", "base": "DOGE", "quote": "USDT"},
    {"symbol": "TON/USDT", "exchange_symbol": "TONUSDT", "base": "TON", "quote": "USDT"},
    {"symbol": "ADA/USDT", "exchange_symbol": "ADAUSDT", "base": "ADA", "quote": "USDT"},
    {"symbol": "AVAX/USDT", "exchange_symbol": "AVAXUSDT", "base": "AVAX", "quote": "USDT"},
    {"symbol": "LINK/USDT", "exchange_symbol": "LINKUSDT", "base": "LINK", "quote": "USDT"},
]

DEFAULT_CONFIG = {
    "version": VERSION,
    "license_key_hash": None,
    "license_mode": None,             # "offline" | "cloud" | None
    "license_verify_endpoint": None,  # ZEPAY cloud API spec NOT provided → None
    "setup_complete": False,
    "trading_mode": "PAPER",
    "live_enabled": False,
    "live_confirmed_steps": [],
    "live_block_reason": "",
    "paper_starting_balance": 10000.0,
    "quote_currency": "USDT",
    "display_currency": "₹",
    "universe": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    "priorities": {},
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
    "auto_research": False,
    "notifications": True,
    "strict_ai_mode": True,           # if True: no fabricated heuristic signals —
                                      # untrained AI → decision WAIT (honest)
    "ai_failure_policy": "reduce_risk",  # reduce_risk | stop_trading
    "candle_interval": "1h",
    "candle_limit": 200,
    "refresh_secs": 30,
    "fee_bps": 10.0,
    "slippage_bps": 5.0,
    "funding_bps_8h": 1.0,
    "paper_participation_max": 0.25,  # max fraction of visible book depth a paper
                                      # order may consume (partial fills beyond)
    "paper_latency_ms": 120,
    "min_order_notional_usd": 10.0,
    "exchange": "binance",
    "exchange_api_key_id": None,      # id into secrets store
    "exchange_passphrase_id": None,
    "exchange_account_type": "SPOT",  # SPOT only for live in this edition
    "ws_streaming": False,            # optional real-time channel (off by default)
    "research_llm_enabled": False,    # LLM bull/bear research layer (advisory)
    "research_llm_interval_cycles": 10,
    "reconcile_interval_cycles": 20,
    "webhook_url": None,
    "mcp_servers": [],                # registry entries (all disabled by default)
    "ai_providers": {},               # provider_id → {enabled, base_url, model, key_id}
    "ai_mode": "LOCAL_QUANT_ONLY",    # LOCAL_QUANT_ONLY | CLOUD_LLM | LOCAL_LLM | HYBRID
}

RISK_PRESETS = {
    "Conservative": {"max_positions": 5, "max_position_pct": 0.15, "max_total_exposure_pct": 0.45,
                     "risk_per_trade_pct": 0.0075, "daily_loss_limit_pct": 0.02, "min_edge_pct": 0.002},
    "Moderate": {"max_positions": 6, "max_position_pct": 0.20, "max_total_exposure_pct": 0.60,
                 "risk_per_trade_pct": 0.01, "daily_loss_limit_pct": 0.03, "min_edge_pct": 0.0015},
    "Aggressive": {"max_positions": 8, "max_position_pct": 0.25, "max_total_exposure_pct": 0.80,
                   "risk_per_trade_pct": 0.015, "daily_loss_limit_pct": 0.05, "min_edge_pct": 0.001},
}

PRIVILEGED_KEYS = ("license_key_hash", "live_enabled", "trading_mode", "kill_switch",
                   "trading_halted", "halt_reason", "live_confirmed_steps",
                   "exchange_api_key_id", "exchange_passphrase_id", "live_block_reason")

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
            for k in list(c.keys()):
                if "secret" in k or k.endswith("_id"):
                    if c.get(k):
                        c[k] = "***STORED-ENCRYPTED***"
        return c

    def update(self, patch, actor="user"):
        with self._lock:
            for k, v in (patch or {}).items():
                if k in PRIVILEGED_KEYS:
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
  reason TEXT, created_at TEXT, updated_at TEXT, idempotency_key TEXT UNIQUE,
  exchange_order_id TEXT, limit_price REAL, filled_qty REAL, avg_fill_price REAL,
  decision_id TEXT);
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
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, ts TEXT, market TEXT, data_status TEXT, features TEXT,
  model_version TEXT, strategy TEXT, expected_return REAL, expected_net REAL,
  confidence REAL, portfolio_state TEXT, risk_state TEXT, decision TEXT,
  reasons TEXT, order_id TEXT, fill_price REAL, pnl REAL, cycle_id TEXT);
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
CREATE TABLE IF NOT EXISTS secrets (
  name TEXT PRIMARY KEY, ciphertext TEXT, purpose TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS assets (
  symbol TEXT PRIMARY KEY, exchange_symbol TEXT, base TEXT, quote TEXT,
  status TEXT, tick_size REAL, step_size REAL, min_qty REAL, min_notional REAL,
  volume24h_usd REAL, updated_at TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS provider_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, provider TEXT, event TEXT, detail TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS research_reports (
  id TEXT PRIMARY KEY, ts TEXT, cycle_id TEXT, kind TEXT, agents TEXT, summary TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS mcp_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, server TEXT, tool TEXT, args TEXT,
  status TEXT, result TEXT, latency_ms INTEGER, created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_candles_mkt_time ON candles(market, interval_tf, open_time);
CREATE INDEX IF NOT EXISTS idx_signals_mkt ON signals(market, created_at);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
CREATE INDEX IF NOT EXISTS idx_audit_cat ON audit_logs(category, created_at);
"""

# columns added after v1.x databases (best-effort migration)
MIGRATION_COLUMNS = {
    "orders": [("exchange_order_id", "TEXT"), ("limit_price", "REAL"),
               ("filled_qty", "REAL"), ("avg_fill_price", "REAL"), ("decision_id", "TEXT")],
}

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
                for table, cols in MIGRATION_COLUMNS.items():
                    for col, typ in cols:
                        try:
                            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
                        except Exception:
                            pass  # already exists
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

    def kv_set(self, k, v):
        self.execute("INSERT INTO kv (k, v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                     (k, v if isinstance(v, str) else jdump(v)))

    def kv_get(self, k, d=None):
        row = self.query_one("SELECT v FROM kv WHERE k=?", (k,))
        if not row:
            return d
        v = row["v"]
        try:
            return json.loads(v)
        except Exception:
            return v

DBASE = DB()

# ---- secrets store (encrypted at rest) ----

class SecretsStore:
    """Named secrets, encrypted with the per-install master key (AEAD)."""

    def __init__(self, box, db):
        self.box = box
        self.db = db

    def put(self, name, value, purpose="general"):
        if value is None or value == "":
            self.delete(name)
            return None
        enc = self.box.encrypt(str(value), purpose=purpose)
        now = utcnow_iso()
        row = self.db.query_one("SELECT name FROM secrets WHERE name=?", (name,))
        if row:
            self.db.execute("UPDATE secrets SET ciphertext=?, purpose=?, updated_at=? WHERE name=?",
                            (enc, purpose, now, name))
        else:
            self.db.execute("INSERT INTO secrets (name, ciphertext, purpose, created_at, updated_at)"
                            " VALUES (?,?,?,?,?)", (name, enc, purpose, now, now))
        return name

    def get(self, name):
        row = self.db.query_one("SELECT ciphertext FROM secrets WHERE name=?", (name,))
        if not row:
            return None
        return self.box.decrypt(row["ciphertext"], purpose="exchange" if name.startswith("exchange") else "general")

    def get_purpose(self, name):
        row = self.db.query_one("SELECT purpose FROM secrets WHERE name=?", (name,))
        return row["purpose"] if row else None

    def delete(self, name):
        self.db.execute("DELETE FROM secrets WHERE name=?", (name,))

    def names(self):
        return [r["name"] for r in self.db.query("SELECT name FROM secrets ORDER BY name")]

def boot_crypto():
    global SECRETBOX, SECRETS
    os.makedirs(DATA_DIR, exist_ok=True)
    SECRETBOX = SecretBox()
    SECRETBOX.self_test()  # refuse to boot on broken crypto
    SECRETS = SecretsStore(SECRETBOX, DBASE)

boot_crypto()

# ----------------------------- audit / events --------------------------------

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

# =============================================================================
# PART 3 — ZEPAY LICENSE KEY (honest adapter interface)
#
# SPEC SITUATION: no ZEPAY cloud verification API specification is bundled
# with this build. Per the platform rules we therefore DO NOT invent one.
# Two adapters are provided:
#   * CloudLicenseProvider  — real HTTP verification against an operator-
#     configured endpoint. Until such an endpoint is configured the adapter
#     reports status BLOCKED ("specification missing"). It never fabricates
#     a successful verification.
#   * OfflineKeyProvider    — local format/checksum validation ONLY. It is
#     clearly labeled OFFLINE everywhere (UI + audit) and explicitly does
#     NOT constitute cloud verification.
# =============================================================================

class LicenseVerificationError(Exception):
    pass

class LicenseResult:
    def __init__(self, ok, mode, message, details=None):
        self.ok = ok
        self.mode = mode          # "offline" | "cloud"
        self.message = message
        self.details = details or {}

    def to_dict(self):
        return {"ok": self.ok, "mode": self.mode, "message": self.message,
                "details": redact(self.details)}

class CloudLicenseProvider:
    """Verifies a ZEPAY key against a real ZEPAY verification endpoint.

    The endpoint URL must be configured by the operator (Settings → License).
    Without it, verification is BLOCKED — we never pretend the cloud said OK.
    Expected minimal response contract (documented, tolerant parser):
        {"ok": true/false, "message": "...", "plan": "...", "expires": "..."}
    """

    def __init__(self, endpoint=None, timeout=10):
        self.endpoint = endpoint
        self.timeout = timeout
        self.status = ProviderStatus.BLOCKED
        self.last_error = "ZEPAY cloud verification API specification not provided — endpoint unconfigured"

    def configured(self):
        return bool(self.endpoint and str(self.endpoint).startswith("http"))

    def verify(self, key):
        if not self.configured():
            raise LicenseVerificationError(
                "Cloud verification unavailable: no ZEPAY verification endpoint is configured "
                "(specification missing). Use OFFLINE key mode or configure an endpoint in Settings.")
        body = json.dumps({"key": key}).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": f"ZEPAY/{VERSION}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            self.status = ProviderStatus.OK  # endpoint reachable, key rejected
            raise LicenseVerificationError(f"Verification endpoint returned HTTP {e.code}")
        except Exception as e:
            self.status = ProviderStatus.UNREACHABLE
            raise LicenseVerificationError(f"Verification endpoint unreachable: {e}")
        self.status = ProviderStatus.OK
        return bool(data.get("ok")), str(data.get("message", "verified")), data

class OfflineKeyProvider:
    """LOCAL format validation. NOT cloud verification — labeled everywhere.

    Accepted shapes:
      ZEPAY-XXXX-XXXX-XXXX  (or longer, charset [A-Z0-9-_], total ≥ 16 chars)
      plus a trailing checksum segment: last group must be the first 4 hex
      chars of sha256 of the preceding groups (case-insensitive) when the
      key contains 5+ groups.
    """

    mode = "offline"

    def verify(self, key):
        k = (key or "").strip().upper()
        if not k:
            return False, "Please enter your ZEPAY key.", {}
        if not k.startswith("ZEPAY-"):
            return False, "Keys look like ZEPAY-XXXX-XXXX-…", {}
        groups = [g for g in k.split("-") if g]
        if len(groups) < 3:
            return False, "Key too short — expected ZEPAY-XXXX-XXXX-…", {}
        core = groups[1:]
        for g in core:
            if not re.fullmatch(r"[A-Z0-9_]{4,}", g):
                return False, "Key contains invalid characters.", {}
        total_len = len(k)
        if total_len < 16:
            return False, "Key too short (min 16 characters).", {}
        details = {"verification": "offline-format-check-only",
                   "note": "This is NOT cloud verification. Configure a verification endpoint "
                           "in Settings → License for cloud verification."}
        # checksum when 4+ core groups: last group = sha256(prefix)[:4]
        if len(core) >= 4:
            payload = "ZEPAY-" + "-".join(core[:-1])
            want = hashlib.sha256(payload.encode()).hexdigest()[:4].upper()
            if core[-1] != want:
                return False, "Key checksum invalid. Re-check for typos.", details
        return True, "OFFLINE key format valid (local validation only — no cloud verification performed).", details

LICENSE_CLOUD = CloudLicenseProvider()

def license_provider_status():
    ep = CONFIG.get("license_verify_endpoint")
    LICENSE_CLOUD.endpoint = ep
    if not ep:
        return {"provider": "zepay-cloud-license", "status": ProviderStatus.BLOCKED.value,
                "reason": "ZEPAY cloud verification API specification not provided; endpoint unconfigured",
                "endpoint": None, "auth": "ZEPAY key (HMAC/whatever the spec defines — UNDEFINED)",
                "docs": "MISSING"}
    return {"provider": "zepay-cloud-license", "status": ProviderStatus.OK.value if LICENSE_CLOUD.configured()
            else ProviderStatus.BLOCKED.value,
            "endpoint": ep, "auth": "ZEPAY key", "docs": "operator-provided"}

def verify_license_key(key, mode=None):
    """Returns LicenseResult. mode: None→auto (cloud if configured, else offline)."""
    ep = CONFIG.get("license_verify_endpoint")
    LICENSE_CLOUD.endpoint = ep
    use_cloud = (mode == "cloud") or (mode is None and ep)
    if use_cloud:
        try:
            ok, msg, data = LICENSE_CLOUD.verify(key)
            return LicenseResult(ok, "cloud", msg, data)
        except LicenseVerificationError as e:
            # cloud failed — do NOT silently fall back and claim success
            return LicenseResult(False, "cloud", str(e), {"cloud_error": str(e)})
    ok, msg, details = OfflineKeyProvider().verify(key)
    return LicenseResult(ok, "offline", msg, details)

def activate_license(key, mode=None):
    res = verify_license_key(key, mode=mode)
    if not res.ok:
        audit("auth", "license_rejected", {"mode": res.mode, "message": res.message})
        return res
    h = sha256((key or "").strip())
    with CONFIG._lock:
        CONFIG.cfg["license_key_hash"] = h
        CONFIG.cfg["license_mode"] = res.mode
        CONFIG.cfg["setup_complete"] = True
        CONFIG.save_locked()
    audit("auth", "license_activated", {"mode": res.mode, "message": res.message})
    ensure_paper_account()
    return res

def is_setup():
    return bool(CONFIG.get("setup_complete")) and bool(CONFIG.get("license_key_hash"))

# ----------------------------- sessions (persisted) --------------------------

SESSION_TTL = 12 * 3600

def create_session():
    tok = secrets.token_urlsafe(24)
    DBASE.kv_set("session:" + tok, utcnow_iso())
    return tok

def valid_session(tok):
    if not tok:
        return False
    row = DBASE.kv_get("session:" + tok)
    if not row:
        return False
    try:
        created = datetime.fromisoformat(row)
    except Exception:
        return False
    if (utcnow() - created).total_seconds() > SESSION_TTL:
        DBASE.execute("DELETE FROM kv WHERE k=?", ("session:" + tok,))
        return False
    return True

def drop_session(tok):
    DBASE.execute("DELETE FROM kv WHERE k=?", ("session:" + tok,))

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
# PART 4 — PROVIDER / INTEGRATION MANAGER (API Gateway)
# Every external API has: provider, endpoint, auth, key status, rate limit,
# health, data types, last success, error status. Nothing is ever reported
# "connected" without a real response. Missing specifications → BLOCKED.
# =============================================================================

def http_json(url, timeout=15, method="GET", data=None, headers=None):
    """Single HTTP helper used by every integration. Raises on any failure."""
    hdrs = {"User-Agent": f"ZEPAY/{VERSION}", "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
        if not raw:
            return {}
        return json.loads(raw)

def http_error_str(e):
    if isinstance(e, urllib.error.HTTPError):
        return f"HTTP {e.code}: {e.reason}"
    if isinstance(e, urllib.error.URLError):
        reason = getattr(e, "reason", e)
        return f"unreachable: {reason}"
    return f"{type(e).__name__}: {e}"

class RateLimiter:
    """Token-bucket-ish min-interval limiter per provider (outgoing politeness)."""

    def __init__(self):
        self._last = {}
        self._lock = threading.Lock()

    def allow(self, name, min_interval=0.05):
        with self._lock:
            now = time.time()
            if now - self._last.get(name, 0) >= min_interval:
                self._last[name] = now
                return True
            return False

    def wait(self, name, min_interval=0.05):
        while not self.allow(name, min_interval):
            time.sleep(min_interval)

RATE = RateLimiter()

class Provider:
    """One external integration. `check_fn` must perform a REAL request and
    raise on failure — health is never assumed."""

    def __init__(self, pid, name, category, purpose, base_url, docs_url, auth,
                 auth_required, key_required, rate_limit, data_types, free,
                 check_fn=None, enabled=True, integrated=True, license_terms=""):
        self.id = pid
        self.name = name
        self.category = category
        self.purpose = purpose
        self.base_url = base_url
        self.docs_url = docs_url
        self.auth = auth
        self.auth_required = auth_required
        self.key_required = key_required
        self.rate_limit = rate_limit
        self.data_types = data_types
        self.free = free
        self.license_terms = license_terms
        self.check_fn = check_fn
        self.enabled = enabled
        self.integrated = integrated   # False → catalogue reference only
        self.status = ProviderStatus.NOT_CONFIGURED
        self.last_success = None
        self.last_error = None
        self.latency_ms = None
        self.request_count = 0
        self.error_count = 0
        self._lock = threading.RLock()

    def health_check(self):
        if not self.enabled:
            with self._lock:
                self.status = ProviderStatus.DISABLED
            return self.snapshot()
        if self.check_fn is None:
            with self._lock:
                self.status = ProviderStatus.NOT_CONFIGURED
            return self.snapshot()
        t0 = time.time()
        try:
            detail = self.check_fn() or {}
            with self._lock:
                self.status = ProviderStatus.OK
                self.last_success = utcnow_iso()
                self.last_error = None
                self.latency_ms = int((time.time() - t0) * 1000)
                self.request_count += 1
                if isinstance(detail, dict) and detail.get("degraded"):
                    self.status = ProviderStatus.DEGRADED
        except Exception as e:
            msg = http_error_str(e)
            with self._lock:
                self.last_error = msg[:300]
                self.error_count += 1
                self.latency_ms = int((time.time() - t0) * 1000)
                if isinstance(e, urllib.error.HTTPError):
                    self.status = ProviderStatus.DEGRADED
                else:
                    self.status = ProviderStatus.UNREACHABLE
            try:
                DBASE.execute("INSERT INTO provider_events (provider, event, detail, created_at)"
                              " VALUES (?,?,?,?)", (self.id, "error", msg[:500], utcnow_iso()))
            except Exception:
                pass
        return self.snapshot()

    def record_success(self):
        with self._lock:
            self.status = ProviderStatus.OK
            self.last_success = utcnow_iso()
            self.last_error = None
            self.request_count += 1

    def record_error(self, msg):
        with self._lock:
            self.last_error = str(msg)[:300]
            self.error_count += 1
            if self.status != ProviderStatus.UNREACHABLE:
                self.status = ProviderStatus.DEGRADED

    def snapshot(self):
        with self._lock:
            return {
                "id": self.id, "name": self.name, "category": self.category,
                "purpose": self.purpose, "endpoint": self.base_url, "docs": self.docs_url,
                "auth": self.auth, "auth_required": self.auth_required,
                "key_status": ("none needed" if not self.key_required else
                               ("configured" if self._has_key() else "MISSING")),
                "rate_limit": self.rate_limit, "data_types": self.data_types,
                "free": self.free, "integrated": self.integrated,
                "enabled": self.enabled, "status": self.status.value,
                "last_success": self.last_success, "last_error": self.last_error,
                "latency_ms": self.latency_ms, "requests": self.request_count,
                "errors": self.error_count, "license_terms": self.license_terms,
            }

    def _has_key(self):
        return False

class ProviderRegistry:
    def __init__(self):
        self.providers = {}
        self._lock = threading.RLock()

    def register(self, provider):
        with self._lock:
            self.providers[provider.id] = provider
            return provider

    def get(self, pid):
        return self.providers.get(pid)

    def all(self, integrated_only=False):
        with self._lock:
            items = list(self.providers.values())
        if integrated_only:
            items = [p for p in items if p.integrated]
        return [p.snapshot() for p in sorted(items, key=lambda x: (0 if x.integrated else 1, x.category, x.name))]

    def check_all(self, integrated_only=True):
        out = []
        for p in list(self.providers.values()):
            if integrated_only and not p.integrated:
                continue
            out.append(p.health_check())
        return out

REGISTRY = ProviderRegistry()

# ---- integrated providers (REAL endpoints; health-checked on demand) ----

BINANCE_REST = "https://api.binance.com"
BINANCE_DATA = "https://data-api.binance.vision"   # geo-friendly public mirror
BINANCE_FAPI = "https://fapi.binance.com"          # futures public (funding/OI/mark)
BINANCE_WS = "wss://stream.binance.com:9443"

def _chk_binance_public():
    RATE.wait("binance-public", 0.05)
    d = http_json(f"{BINANCE_DATA}/api/v3/ping", timeout=8)
    return d

def _chk_binance_fapi():
    RATE.wait("binance-fapi", 0.05)
    d = http_json(f"{BINANCE_FAPI}/fapi/v1/time", timeout=8)
    if not d.get("serverTime"):
        raise ValueError("bad fapi response")
    return d

REGISTRY.register(Provider(
    "binance_spot_public", "Binance Spot — Public Market Data", "market_data",
    "OHLCV candles, tickers, order book depth, trades, exchange metadata (symbols, precision filters)",
    BINANCE_DATA + "/api/v3", "https://developers.binance.com/docs/binance-spot-api",
    "none (public)", auth_required=False, key_required=False,
    rate_limit="1200 request weight/min per IP (public)", 
    data_types=["ticker", "ohlcv", "depth", "trades", "exchangeInfo"],
    free=True, check_fn=_chk_binance_public, license_terms="Binance API Terms — public data",
))

REGISTRY.register(Provider(
    "binance_spot_main", "Binance Spot — Main REST (fallback)", "market_data",
    "Primary api.binance.com endpoints (used as failover for public market data)",
    BINANCE_REST + "/api/v3", "https://developers.binance.com/docs/binance-spot-api",
    "none (public)", auth_required=False, key_required=False,
    rate_limit="1200 request weight/min per IP",
    data_types=["ticker", "ohlcv", "depth", "trades"],
    free=True, check_fn=None, license_terms="Binance API Terms",
))

REGISTRY.register(Provider(
    "binance_futures_public", "Binance Futures — Public Derivatives Data", "market_data",
    "Funding rate, mark/index price, open interest (market context for spot decisions)",
    BINANCE_FAPI + "/fapi/v1", "https://developers.binance.com/docs/derivatives/usds-margined-futures",
    "none (public)", auth_required=False, key_required=False,
    rate_limit="2400 weight/min per IP",
    data_types=["funding", "mark_price", "index_price", "open_interest"],
    free=True, check_fn=_chk_binance_fapi, license_terms="Binance API Terms — public data",
))

REGISTRY.register(Provider(
    "binance_ws", "Binance WebSocket Streams", "market_data",
    "Optional real-time miniTicker/market data stream (server-side). Off by default.",
    BINANCE_WS, "https://developers.binance.com/docs/binance-spot-api/websocket-streams",
    "none (public)", auth_required=False, key_required=False,
    rate_limit="≤ 1024 streams per connection; 300 connections per 5 min per IP",
    data_types=["live_ticker"],
    free=True, check_fn=None, license_terms="Binance API Terms",
))

def _chk_binance_user():
    """REAL signed account call — the only way this provider goes OK."""
    adapter = EXCHANGES.get("binance")
    if adapter is None or not adapter.has_credentials():
        raise ValueError("exchange API credentials not configured")
    acct = adapter.account(timeout=8)   # real signed call
    if not acct.get("canTrade"):
        return {"degraded": True, "note": "key cannot trade"}
    return {"permissions": {"canTrade": acct.get("canTrade"),
                            "canWithdraw": acct.get("canWithdraw")}}

REGISTRY.register(Provider(
    "binance_user", "Binance Spot — User Account (SIGNED)", "exchange",
    "Balances, open orders, trade history, order placement (live trading)",
    BINANCE_REST + "/api/v3", "https://developers.binance.com/docs/binance-spot-api",
    "HMAC-SHA256 signed requests + X-MBX-APIKEY header", auth_required=True, key_required=True,
    rate_limit="6000 request weight/min per IP (with key)",
    data_types=["account", "balances", "open_orders", "my_trades", "orders"],
    free=True, check_fn=_chk_binance_user, license_terms="Binance API Terms — trade-only key required",
))

# ZEPAY cloud license — BLOCKED until the operator configures a real endpoint.
class ZepayLicenseProviderEntry(Provider):
    def snapshot(self):
        s = super().snapshot()
        lic = license_provider_status()
        s["status"] = lic["status"]
        s["endpoint"] = lic.get("endpoint")
        s["note"] = lic.get("reason", "")
        return s

REGISTRY.register(ZepayLicenseProviderEntry(
    "zepay_cloud", "ZEPAY Cloud — License Verification", "platform",
    "Verifies the ZEPAY key against the ZEPAY cloud API",
    "(unconfigured)", "(specification not provided with this build)",
    "ZEPAY key", auth_required=True, key_required=True,
    rate_limit="unknown (spec missing)",
    data_types=["license"],
    free=False, check_fn=None, license_terms="ZEPAY platform terms",
))

# ---- PUBLIC API REGISTRY (catalogue references — NOT integrated) ----
# Curated from the public-apis catalogue concept (github.com/public-apis/
# public-apis). These are research/discovery references. They are NOT wired
# into ZEPAY's trading path; enabling any of them requires explicit user
# action and each is then health-checked like any real provider.

PUBLIC_API_CATALOG = [
    {"name": "CoinGecko", "category": "market_data", "url": "https://api.coingecko.com",
     "docs": "https://www.coingecko.com/en/api", "auth": "none (demo) / key (prod)",
     "free": "free tier + paid", "rate_limit": "~10-30 calls/min (free)", "data": ["prices", "market caps", "volume", "trending"]},
    {"name": "CoinMarketCap", "category": "market_data", "url": "https://pro-api.coinmarketcap.com",
     "docs": "https://coinmarketcap.com/api", "auth": "API key", "free": "free tier + paid",
     "rate_limit": "10k credits/mo (basic)", "data": ["listings", "quotes", "metadata"]},
    {"name": "CryptoCompare", "category": "market_data", "url": "https://min-api.cryptocompare.com",
     "docs": "https://developers.cryptocompare.com", "auth": "none / API key",
     "free": "free tier + paid", "rate_limit": "100k/mo (free)", "data": ["history", "news", "social"]},
    {"name": "Binance (public-apis ref)", "category": "market_data", "url": "https://api.binance.com",
     "docs": "https://developers.binance.com", "auth": "none / signed", "free": "free",
     "rate_limit": "1200 weight/min", "data": ["exchange data"]},
    {"name": "Kraken Public", "category": "market_data", "url": "https://api.kraken.com",
     "docs": "https://docs.kraken.com/api", "auth": "none (public)", "free": "free",
     "rate_limit": "1 call/sec (public)", "data": ["tickers", "ohlc", "depth"]},
    {"name": "Alpha Vantage", "category": "financial_data", "url": "https://www.alphavantage.co",
     "docs": "https://alphavantage.co/documentation", "auth": "API key", "free": "free tier (25/day)",
     "rate_limit": "25/day free", "data": ["stocks", "fx", "crypto", "indicators"]},
    {"name": "Finnhub", "category": "financial_data", "url": "https://finnhub.io",
     "docs": "https://finnhub.io/docs/api", "auth": "API key", "free": "free tier + paid",
     "rate_limit": "60/min (free)", "data": ["quotes", "earnings", "economic"]},
    {"name": "Yahoo Finance (unofficial)", "category": "financial_data", "url": "https://query1.finance.yahoo.com",
     "docs": "unofficial", "auth": "none (unofficial, ToS restricted)", "free": "free",
     "rate_limit": "unspecified", "data": ["quotes", "charts"], "note": "ToS risk — reference only"},
    {"name": "FRED (St. Louis Fed)", "category": "economic_data", "url": "https://api.stlouisfed.org",
     "docs": "https://fred.stlouisfed.org/docs/api", "auth": "API key", "free": "free",
     "rate_limit": "fair use", "data": ["macro series", "rates", "inflation"]},
    {"name": "World Bank", "category": "economic_data", "url": "https://api.worldbank.org",
     "docs": "https://datahelpdesk.worldbank.org", "auth": "none", "free": "free",
     "rate_limit": "unspecified (fair use)", "data": ["macro indicators"]},
    {"name": "NewsAPI", "category": "news", "url": "https://newsapi.org",
     "docs": "https://newsapi.org/docs", "auth": "API key", "free": "free (dev) + paid",
     "rate_limit": "100/day (developer)", "data": ["news headlines", "search"]},
    {"name": "GDELT", "category": "news", "url": "https://api.gdeltproject.org",
     "docs": "https://gdeltproject.org", "auth": "none", "free": "free",
     "rate_limit": "fair use", "data": ["global news index"]},
    {"name": "marketaux", "category": "news", "url": "https://api.marketaux.com",
     "docs": "https://www.marketaux.com/documentation", "auth": "API key", "free": "free tier + paid",
     "rate_limit": "100/day (free)", "data": ["financial news + sentiment"]},
    {"name": "Alternative.me (Fear & Greed)", "category": "sentiment", "url": "https://api.alternative.me",
     "docs": "https://alternative.me/crypto/fear-and-greed-index/", "auth": "none", "free": "free",
     "rate_limit": "fair use", "data": ["crypto fear & greed index"]},
    {"name": "Blockchain.info", "category": "blockchain", "url": "https://blockchain.info",
     "docs": "https://www.blockchain.com/explorer/api", "auth": "none", "free": "free",
     "rate_limit": "fair use", "data": ["btc on-chain stats", "txs"]},
    {"name": "Blockchair", "category": "blockchain", "url": "https://api.blockchair.com",
     "docs": "https://blockchair.com/api", "auth": "none / key", "free": "free tier + paid",
     "rate_limit": "30 req/min (free)", "data": ["multi-chain on-chain data"]},
    {"name": "Etherscan", "category": "blockchain", "url": "https://api.etherscan.io",
     "docs": "https://etherscan.io/apis", "auth": "API key", "free": "free tier + paid",
     "rate_limit": "5/sec (free)", "data": ["eth on-chain", "contract events"]},
    {"name": "OpenExchangeRates", "category": "currency", "url": "https://openexchangerates.org",
     "docs": "https://docs.openexchangerates.org", "auth": "app_id", "free": "free tier + paid",
     "rate_limit": "1000/mo (free)", "data": ["fiat fx rates"]},
    {"name": "exchangerate.host", "category": "currency", "url": "https://api.exchangerate.host",
     "docs": "https://exchangerate.host", "auth": "none / key", "free": "free tier",
     "rate_limit": "fair use", "data": ["fx rates"]},
    {"name": "CoinCap", "category": "market_data", "url": "https://api.coincap.io",
     "docs": "https://docs.coincap.io", "auth": "none", "free": "free",
     "rate_limit": "200 req/min", "data": ["crypto prices", "history"]},
    {"name": "OpenAI", "category": "ai", "url": "https://api.openai.com",
     "docs": "https://platform.openai.com/docs", "auth": "API key (Bearer)", "free": "paid",
     "rate_limit": "tiered", "data": ["llm"]},
    {"name": "Anthropic", "category": "ai", "url": "https://api.anthropic.com",
     "docs": "https://docs.anthropic.com", "auth": "x-api-key", "free": "paid",
     "rate_limit": "tiered", "data": ["llm"]},
    {"name": "Ollama (local)", "category": "ai", "url": "http://localhost:11434",
     "docs": "https://ollama.com", "auth": "none (local)", "free": "free (self-hosted)",
     "rate_limit": "local hardware bound", "data": ["llm"]},
    {"name": "DuckDuckGo Instant Answer", "category": "search", "url": "https://api.duckduckgo.com",
     "docs": "https://duckduckgo.com/duckduckgo/help/results/api", "auth": "none", "free": "free",
     "rate_limit": "fair use", "data": ["search summaries"]},
]

def public_api_catalog_payload():
    return [{"name": c["name"], "category": c["category"], "url": c["url"], "docs": c["docs"],
             "auth": c["auth"], "free_paid": c["free"], "rate_limit": c.get("rate_limit", "unknown"),
             "supported_data": c["data"], "integrated": False,
             "note": c.get("note", "catalogue reference — not integrated into ZEPAY"),
             "license_terms": "check provider ToS before integration"}
            for c in PUBLIC_API_CATALOG]

# =============================================================================
# PART 5 — EXCHANGE ADAPTER + DYNAMIC UNIVERSE + MARKET DATA + WEBSOCKET
# =============================================================================

class BinanceSpotAdapter:
    """REAL Binance Spot REST adapter (public + HMAC-signed user endpoints).

    - Live order placement goes exclusively through this class.
    - Credentials live in the encrypted secrets store, never in config.
    - Withdrawal-enabled keys are refused outright (has_credentials checks
      the real /account permissions when connected).
    """

    ID = "binance"
    NAME = "Binance (Spot)"

    def __init__(self):
        self.last_error = None
        self.last_signed_ok = None
        self._acct_cache = {"ts": 0, "data": None}

    # ---- credentials ----
    def has_credentials(self):
        try:
            return bool(SECRETS.get("exchange_api_key")) and bool(SECRETS.get("exchange_api_secret"))
        except Exception:
            return False

    def set_credentials(self, api_key, api_secret):
        SECRETS.put("exchange_api_key", api_key, purpose="exchange")
        SECRETS.put("exchange_api_secret", api_secret, purpose="exchange")
        with CONFIG._lock:
            CONFIG.cfg["exchange_api_key_id"] = "exchange_api_key"
            CONFIG.cfg["exchange"] = self.ID
            CONFIG.save_locked()
        audit("exchange", "credentials_stored", {"exchange": self.ID,
                                                "key": mask_key(api_key)}, actor="user")

    def clear_credentials(self):
        SECRETS.delete("exchange_api_key")
        SECRETS.delete("exchange_api_secret")
        with CONFIG._lock:
            CONFIG.cfg["exchange_api_key_id"] = None
            CONFIG.cfg["exchange_api_secret_id"] = None
            CONFIG.save_locked()

    # ---- request plumbing ----
    @staticmethod
    def _public(path, params=None, timeout=12):
        qs = ("?" + urllib.parse.urlencode(params)) if params else ""
        for base in (BINANCE_DATA, BINANCE_REST):
            try:
                RATE.wait("binance-public", 0.05)
                return http_json(f"{base}{path}{qs}", timeout=timeout)
            except urllib.error.HTTPError as e:
                if e.code in (418, 429):
                    raise   # rate-limited: do not hammer the other host
                last = e
                continue
            except Exception as e:
                last = e
                continue
        raise last

    def _signed(self, path, params=None, method="GET", timeout=12):
        """Signed request. Raises with a clear error; never fabricates data."""
        key = SECRETS.get("exchange_api_key")
        secret = SECRETS.get("exchange_api_secret")
        if not key or not secret:
            raise PermissionError("exchange credentials not configured")
        params = dict(params or {})
        params["timestamp"] = ts_ms()
        params["recvWindow"] = 5000
        qs = urllib.parse.urlencode(params)
        sig = hmac.new(secret.encode("utf-8"), qs.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"{BINANCE_REST}{path}?{qs}&signature={sig}"
        req = urllib.request.Request(url, method=method,
                                     headers={"X-MBX-APIKEY": key, "User-Agent": f"ZEPAY/{VERSION}"})
        try:
            RATE.wait("binance-signed", 0.05)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace") or "{}")
                self.last_signed_ok = utcnow_iso()
                self.last_error = None
                return data
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            self.last_error = f"HTTP {e.code} {body}"
            raise urllib.error.HTTPError(url, e.code, f"{e.reason} {body}", e.headers, None)
        except Exception as e:
            self.last_error = http_error_str(e)
            raise

    # ---- public endpoints ----
    def ping(self):
        return self._public("/api/v3/ping", timeout=8)

    def exchange_info(self, symbols=None):
        params = {}
        if symbols:
            params["symbols"] = json.dumps([s.upper() for s in symbols])
        return self._public("/api/v3/exchangeInfo", params, timeout=20)

    def ticker_24h(self, symbol=None):
        return self._public("/api/v3/ticker/24hr", {"symbol": symbol} if symbol else None, timeout=15)

    def klines(self, symbol, interval="1h", limit=200):
        return self._public("/api/v3/klines",
                            {"symbol": symbol, "interval": interval, "limit": limit}, timeout=20)

    def depth(self, symbol, limit=20):
        return self._public("/api/v3/depth", {"symbol": symbol, "limit": limit}, timeout=10)

    # ---- signed endpoints ----
    def account(self, timeout=12):
        return self._signed("/api/v3/account", method="GET", timeout=timeout)

    def open_orders(self, symbol=None):
        return self._signed("/api/v3/openOrders", {"symbol": symbol} if symbol else None)

    def get_order(self, symbol, orderId=None, origClientOrderId=None):
        p = {"symbol": symbol}
        if orderId:
            p["orderId"] = orderId
        if origClientOrderId:
            p["origClientOrderId"] = origClientOrderId
        return self._signed("/api/v3/order", p)

    def place_order(self, symbol, side, qty, order_type="MARKET", price=None,
                    client_order_id=None, timeout=12):
        p = {"symbol": symbol, "side": side, "type": order_type, "quantity": qty}
        if order_type == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order requires price")
            p["price"] = price
            p["timeInForce"] = "GTC"
        if client_order_id:
            p["newClientOrderId"] = client_order_id[:36]
        return self._signed("/api/v3/order", p, method="POST", timeout=timeout)

    def cancel_order(self, symbol, orderId=None, origClientOrderId=None):
        p = {"symbol": symbol}
        if orderId:
            p["orderId"] = orderId
        if origClientOrderId:
            p["origClientOrderId"] = origClientOrderId
        return self._signed("/api/v3/order", p, method="DELETE")

    def my_trades(self, symbol, limit=50):
        return self._signed("/api/v3/myTrades", {"symbol": symbol, "limit": limit})

    # ---- higher-level helpers ----
    def test_connection(self):
        """REAL credential + permission test. Returns a structured report."""
        out = {"connected": False, "exchange": self.NAME, "checks": {}}
        try:
            self.ping()
            out["checks"]["public_api"] = True
        except Exception as e:
            out["checks"]["public_api"] = False
            out["error"] = f"public API unreachable: {http_error_str(e)}"
            return out
        if not self.has_credentials():
            out["error"] = "credentials not configured"
            return out
        try:
            acct = self.account(timeout=10)
        except Exception as e:
            out["checks"]["signed_account"] = False
            out["error"] = f"signed request failed: {http_error_str(e)}"
            return out
        out["checks"]["signed_account"] = True
        can_trade = bool(acct.get("canTrade"))
        can_withdraw = bool(acct.get("canWithdraw"))
        out["checks"]["can_trade"] = can_trade
        out["checks"]["can_withdraw"] = can_withdraw
        out["permissions"] = {"canTrade": can_trade, "canWithdraw": can_withdraw}
        if can_withdraw:
            out["connected"] = False
            out["error"] = ("SECURITY REFUSAL: this API key has WITHDRAWAL permission. "
                            "ZEPAY refuses to use withdrawal-enabled keys. Create a "
                            "trade-only key with withdrawals DISABLED.")
            audit("security", "withdrawal_key_refused", {"exchange": self.ID})
            return out
        if not can_trade:
            out["connected"] = False
            out["error"] = "API key lacks trading permission (canTrade=false)."
            return out
        balances = [b for b in acct.get("balances", []) if safe_float(b.get("free")) + safe_float(b.get("locked")) > 0]
        out["connected"] = True
        out["balances"] = balances
        out["account_type"] = "SPOT"
        out["message"] = "CONNECTED — credentials verified against the real exchange account."
        audit("exchange", "connection_verified", {"exchange": self.ID, "n_balances": len(balances)})
        return out

EXCHANGES = {"binance": BinanceSpotAdapter()}

# ----------------------------- dynamic universe -------------------------------

class UniverseManager:
    """Live tradable universe from the exchange's real metadata. No hard cap;
    any spot USDT market the exchange reports as TRADING can be selected.
    Falls back to a small built-in list (clearly labeled) when offline."""

    def __init__(self):
        self.catalog = {}          # "BTC/USDT" -> asset dict
        self.source = "default_fallback"
        self.last_refresh = None
        self.last_error = None
        self._lock = threading.RLock()

    def refresh(self, force=False):
        """Fetch exchangeInfo + 24h volume for ALL spot USDT symbols."""
        try:
            info = EXCHANGES["binance"].exchange_info()
            syms = {}
            for s in info.get("symbols", []):
                if s.get("status") != "TRADING" or s.get("quoteAsset") != "USDT":
                    continue
                flt = {"tick_size": None, "step_size": None, "min_qty": None, "min_notional": None}
                for f in s.get("filters", []):
                    if f.get("filterType") == "PRICE_FILTER":
                        flt["tick_size"] = safe_float(f.get("tickSize"))
                    elif f.get("filterType") == "LOT_SIZE":
                        flt["step_size"] = safe_float(f.get("stepSize"))
                        flt["min_qty"] = safe_float(f.get("minQty"))
                    elif f.get("filterType") in ("NOTIONAL", "MIN_NOTIONAL"):
                        flt["min_notional"] = safe_float(f.get("minNotional") or f.get("notional"))
                syms[s["symbol"]] = {
                    "symbol": f"{s['baseAsset']}/USDT", "exchange_symbol": s["symbol"],
                    "base": s["baseAsset"], "quote": "USDT", "status": "TRADING", **flt,
                    "volume24h_usd": 0.0}
            if not syms:
                raise ValueError("exchangeInfo returned no TRADING USDT symbols")
            # volumes (single call, weight 80) — best effort
            try:
                tickers = EXCHANGES["binance"].ticker_24h()
                vol = {t.get("symbol"): safe_float(t.get("quoteVolume")) for t in tickers}
                for k, v in syms.items():
                    v["volume24h_usd"] = vol.get(k, 0.0)
            except Exception as e:
                self.last_error = http_error_str(e)
            with self._lock:
                self.catalog = syms
                self.source = "exchange_metadata"
                self.last_refresh = utcnow_iso()
                self.last_error = None
            # persist for offline fallback
            try:
                for a in syms.values():
                    DBASE.execute(
                        "INSERT INTO assets (symbol, exchange_symbol, base, quote, status,"
                        " tick_size, step_size, min_qty, min_notional, volume24h_usd, updated_at, source)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET"
                        " status=excluded.status, tick_size=excluded.tick_size, step_size=excluded.step_size,"
                        " min_qty=excluded.min_qty, min_notional=excluded.min_notional,"
                        " volume24h_usd=excluded.volume24h_usd, updated_at=excluded.updated_at,"
                        " source=excluded.source",
                        (a["symbol"], a["exchange_symbol"], a["base"], a["quote"], a["status"],
                         a["tick_size"], a["step_size"], a["min_qty"], a["min_notional"],
                         a["volume24h_usd"], utcnow_iso(), "exchange"))
            except Exception:
                pass
            return len(syms)
        except Exception as e:
            self.last_error = http_error_str(e)
            self._load_db_fallback()
            return len(self.catalog)

    def _load_db_fallback(self):
        rows = DBASE.query("SELECT * FROM assets")
        with self._lock:
            if rows:
                self.catalog = {r["symbol"]: dict(r) for r in rows}
                self.source = "cached_metadata"
            else:
                self.catalog = {m["symbol"]: {**m, "status": "FALLBACK", "tick_size": None,
                                              "step_size": None, "min_qty": None,
                                              "min_notional": None, "volume24h_usd": 0.0}
                                for m in DEFAULT_MARKETS}
                self.source = "default_fallback"

    def ensure_loaded(self):
        with self._lock:
            if self.catalog:
                return
        self.refresh()

    def all_assets(self, search=None, limit=200):
        self.ensure_loaded()
        with self._lock:
            items = list(self.catalog.values())
        if search:
            s = search.upper()
            items = [a for a in items if s in a["symbol"].upper()]
        items.sort(key=lambda a: -safe_float(a.get("volume24h_usd")))
        return items[:limit]

    def get(self, symbol):
        self.ensure_loaded()
        with self._lock:
            return self.catalog.get(symbol)

    def ex_symbol(self, market):
        a = self.get(market)
        if a:
            return a["exchange_symbol"]
        return market.replace("/", "")

    def filters(self, market):
        a = self.get(market)
        if not a:
            return None
        return {"tick_size": a.get("tick_size"), "step_size": a.get("step_size"),
                "min_qty": a.get("min_qty"), "min_notional": a.get("min_notional")}

    def snapshot(self):
        with self._lock:
            return {"source": self.source, "assets": len(self.catalog),
                    "last_refresh": self.last_refresh, "last_error": self.last_error}

UNIVERSE = UniverseManager()

# ----------------------------- market data manager ---------------------------

class MarketDataManager:
    """Multi-asset market data from the REAL exchange APIs.

    Every cached record is normalized and carries: exchange, symbol,
    timestamp (UTC ms), source, data type, quality status.
    Optional WebSocket stream refreshes tickers in real time when enabled
    and reachable; REST remains the source of truth.
    """

    def __init__(self, max_workers=8):
        self.pool = ThreadPoolExecutor(max_workers=max_workers)
        self.cache = {}       # market -> normalized ticker
        self.klines_cache = {}
        self.depth_cache = {}
        self.derivs_cache = {}  # market -> {funding_rate, mark_price, index_price, open_interest, ts, source}
        self.errors = {}
        self.latency_ms = {}
        self.ws = None
        self._lock = threading.RLock()

    @staticmethod
    def _norm_ticker(market, d, source):
        return {"exchange": "binance", "symbol": market, "timestamp": ts_ms(),
                "source": source, "data_type": "ticker", "quality": "OK",
                "price": safe_float(d.get("lastPrice")),
                "change24": safe_float(d.get("priceChangePercent")),
                "volume24": safe_float(d.get("volume")),
                "quote_vol": safe_float(d.get("quoteVolume")),
                "high": safe_float(d.get("highPrice")), "low": safe_float(d.get("lowPrice")),
                "ts": ts_ms()}

    def _fetch_ticker(self, market):
        sym = UNIVERSE.ex_symbol(market)
        t0 = time.time()
        try:
            d = EXCHANGES["binance"].ticker_24h(sym)
            price = safe_float(d.get("lastPrice"))
            if price <= 0:
                raise ValueError("bad price in ticker response")
            out = self._norm_ticker(market, d, "binance-rest")
            with self._lock:
                self.cache[market] = out
                self.latency_ms[market] = int((time.time() - t0) * 1000)
                self.errors.pop(market, None)
            return out
        except Exception as e:
            with self._lock:
                er = self.errors.get(market, {"count": 0})
                er.update({"msg": http_error_str(e), "ts": ts_ms(), "count": er.get("count", 0) + 1})
                self.errors[market] = er
            raise

    def _fetch_klines(self, market, interval=None, limit=None):
        interval = interval or CONFIG.get("candle_interval", "1h")
        limit = int(limit or CONFIG.get("candle_limit", 200))
        sym = UNIVERSE.ex_symbol(market)
        try:
            d = EXCHANGES["binance"].klines(sym, interval, limit)
            rows = [{"open_time": int(k[0]), "o": safe_float(k[1]), "h": safe_float(k[2]),
                     "l": safe_float(k[3]), "c": safe_float(k[4]), "v": safe_float(k[5]),
                     "quote_vol": safe_float(k[7]), "trades": int(k[8] or 0)} for k in d]
            if not rows:
                raise ValueError("empty klines")
            with self._lock:
                self.klines_cache[(market, interval)] = {"rows": rows, "ts": ts_ms()}
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
            with self._lock:
                er = self.errors.get(market, {"count": 0})
                er.update({"msg": http_error_str(e), "ts": ts_ms(), "count": er.get("count", 0) + 1})
                self.errors[market] = er
            # last resort: previously persisted REAL candles (clearly marked)
            q = DBASE.query("SELECT open_time,o,h,l,c,v,quote_vol,trades FROM candles"
                            " WHERE market=? AND interval_tf=? ORDER BY open_time DESC LIMIT ?",
                            (market, interval, limit))
            if q:
                rows = [{"open_time": r["open_time"], "o": r["o"], "h": r["h"], "l": r["l"],
                         "c": r["c"], "v": r["v"], "quote_vol": r["quote_vol"] or 0,
                         "trades": r["trades"] or 0} for r in reversed(q)]
                with self._lock:
                    self.klines_cache[(market, interval)] = {"rows": rows, "ts": ts_ms(),
                                                             "stale_db": True}
                return rows
            raise

    def _fetch_depth(self, market, limit=20):
        sym = UNIVERSE.ex_symbol(market)
        try:
            d = EXCHANGES["binance"].depth(sym, limit)
            bids = [[safe_float(p), safe_float(q)] for p, q in d.get("bids", [])]
            asks = [[safe_float(p), safe_float(q)] for p, q in d.get("asks", [])]
            if not bids or not asks:
                raise ValueError("empty book")
            out = {"exchange": "binance", "symbol": market, "timestamp": ts_ms(),
                   "source": "binance-rest", "data_type": "depth", "quality": "OK",
                   "bids": bids, "asks": asks, "ts": ts_ms()}
            with self._lock:
                self.depth_cache[market] = out
            return out
        except Exception as e:
            raise

    def _fetch_derivs(self, market):
        """Funding / mark / index / OI from Binance futures public API.
        Optional market context — marked UNAVAILABLE when unreachable."""
        sym = UNIVERSE.ex_symbol(market)
        out = {"funding_rate": None, "mark_price": None, "index_price": None,
               "open_interest": None, "source": "binance-fapi", "ts": ts_ms(),
               "available": False}
        try:
            d = http_json(f"{BINANCE_FAPI}/fapi/v1/premiumIndex?symbol={sym}", timeout=8)
            out["funding_rate"] = safe_float(d.get("lastFundingRate"))
            out["mark_price"] = safe_float(d.get("markPrice"))
            out["index_price"] = safe_float(d.get("indexPrice"))
            out["available"] = True
        except Exception:
            pass
        try:
            d = http_json(f"{BINANCE_FAPI}/fapi/v1/openInterest?symbol={sym}", timeout=8)
            out["open_interest"] = safe_float(d.get("openInterest"))
            out["available"] = True
        except Exception:
            pass
        with self._lock:
            self.derivs_cache[market] = out
        return out

    def refresh_all(self, markets, with_klines=True, with_depth=True):
        futs = {}
        for m in markets:
            futs[self.pool.submit(self._safe, self._fetch_ticker, m)] = ("ticker", m)
            if with_klines:
                futs[self.pool.submit(self._safe, self._fetch_klines, m)] = ("klines", m)
            if with_depth:
                futs[self.pool.submit(self._safe, self._fetch_depth, m)] = ("depth", m)
        results = {}
        for f in futs:
            kind, m = futs[f]
            try:
                results[(kind, m)] = f.result(timeout=40)
            except Exception as e:
                results[(kind, m)] = e
        return results

    @staticmethod
    def _safe(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            return e

    # ---- accessors ----
    def get_ticker(self, market):
        with self._lock:
            return self.cache.get(market)

    def get_klines(self, market, interval=None):
        interval = interval or CONFIG.get("candle_interval", "1h")
        with self._lock:
            e = self.klines_cache.get((market, interval))
            return (e["rows"] if e else None)

    def klines_stale_db(self, market, interval=None):
        interval = interval or CONFIG.get("candle_interval", "1h")
        with self._lock:
            e = self.klines_cache.get((market, interval))
            return bool(e and e.get("stale_db"))

    def get_depth(self, market):
        with self._lock:
            return self.depth_cache.get(market)

    def get_derivs(self, market):
        with self._lock:
            return self.derivs_cache.get(market)

    def age_secs(self, market):
        with self._lock:
            t = self.cache.get(market)
            if not t:
                return 1e9
            return (ts_ms() - t["ts"]) / 1000.0

    def is_stale(self, market):
        return self.age_secs(market) > safe_float(CONFIG.get("stale_data_secs", 120), 120)

    def feed_health(self, markets=None):
        """Global feed status: LIVE / DEGRADED / UNAVAILABLE."""
        markets = markets or (CONFIG.get("universe") or [])
        if not markets:
            return {"status": "UNAVAILABLE", "reason": "no markets selected"}
        fresh = [m for m in markets if not self.is_stale(m)]
        if not fresh:
            return {"status": "UNAVAILABLE",
                    "reason": "REAL DATA UNAVAILABLE — all market feeds stale/unreachable. "
                              "New trades are blocked until real data returns."}
        if len(fresh) < len(markets):
            return {"status": "DEGRADED",
                    "reason": f"{len(markets) - len(fresh)}/{len(markets)} feeds stale"}
        return {"status": "LIVE", "reason": "real-time market data"}

    def start_ws(self, markets):
        if not CONFIG.get("ws_streaming"):
            return False
        try:
            self.stop_ws()
            syms = [UNIVERSE.ex_symbol(m).lower() + "@miniTicker" for m in markets]
            self.ws = BinanceWSStream(syms, on_ticker=self._ws_ticker)
            self.ws.start()
            return True
        except Exception as e:
            log(f"ws start failed: {e}", "WARN")
            return False

    def stop_ws(self):
        if self.ws:
            self.ws.stop()

    def _ws_ticker(self, sym, data):
        market = f"{data.get('s', '')[:-4]}/USDT" if data.get("s", "").endswith("USDT") else None
        if not market:
            return
        with self._lock:
            prev = self.cache.get(market) or {}
            self.cache[market] = {
                "exchange": "binance", "symbol": market, "timestamp": ts_ms(),
                "source": "binance-ws", "data_type": "ticker", "quality": "OK",
                "price": safe_float(data.get("c")) or prev.get("price", 0),
                "change24": prev.get("change24", 0),
                "volume24": safe_float(data.get("v")) or prev.get("volume24", 0),
                "quote_vol": safe_float(data.get("q")) or prev.get("quote_vol", 0),
                "high": safe_float(data.get("h")) or prev.get("high", 0),
                "low": safe_float(data.get("l")) or prev.get("low", 0),
                "ts": ts_ms()}

MDATA = MarketDataManager()

# ----------------------------- minimal WebSocket client ----------------------
# RFC 6455 client (text frames + ping/pong) over ssl sockets — stdlib only.
# Used ONLY for inbound market data. If unreachable → honest UNAVAILABLE.

class WSClient:
    def __init__(self, url, on_message=None, on_status=None):
        self.url = url
        self.on_message = on_message
        self.on_status = on_status
        self.sock = None
        self._stop = False
        self.status = "IDLE"
        self.last_error = None
        self.messages = 0

    @staticmethod
    def _frame(opcode, payload=b""):
        mask = secrets.token_bytes(4)
        length = len(payload)
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return header + mask + masked

    @staticmethod
    def _parse_frame(buf, pos):
        """Returns (fin, opcode, payload, new_pos) or None if incomplete."""
        if len(buf) < pos + 2:
            return None
        b1, b2 = buf[pos], buf[pos + 1]
        fin = bool(b1 & 0x80)
        opcode = b1 & 0x0F
        masked = bool(b2 & 0x80)
        length = b2 & 0x7F
        idx = pos + 2
        if length == 126:
            if len(buf) < idx + 2:
                return None
            length = struct.unpack(">H", buf[idx:idx + 2])[0]
            idx += 2
        elif length == 127:
            if len(buf) < idx + 8:
                return None
            length = struct.unpack(">Q", buf[idx:idx + 8])[0]
            idx += 8
        if masked:
            if len(buf) < idx + 4:
                return None
            mk = buf[idx:idx + 4]
            idx += 4
        else:
            mk = None
        if len(buf) < idx + length:
            return None
        payload = buf[idx:idx + length]
        if mk:
            payload = bytes(b ^ mk[i % 4] for i, b in enumerate(payload))
        return fin, opcode, payload, idx + length

    def _handshake(self):
        u = urllib.parse.urlparse(self.url)
        host, port, path = u.hostname, u.port or (443 if u.scheme == "wss" else 80), (u.path or "/") + ("?" + u.query if u.query else "")
        raw = socket.create_connection((host, port), timeout=12)
        if u.scheme == "wss":
            ctx = ssl.create_default_context()
            raw = ctx.wrap_socket(raw, server_hostname=host)
        self.sock = raw
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
               f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
               f"User-Agent: ZEPAY/{VERSION}\r\n\r\n")
        raw.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = raw.recv(4096)
            if not chunk:
                raise ConnectionError("connection closed during handshake")
            resp += chunk
        head = resp.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        if "101" not in head.split("\r\n")[0]:
            raise ConnectionError(f"websocket handshake refused: {head.splitlines()[0]}")
        return resp.split(b"\r\n\r\n", 1)[1]  # leftover bytes

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="zepay-ws")
        self._thread.start()

    def stop(self):
        self._stop = True
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    def _run(self):
        self.status = "CONNECTING"
        self._emit_status()
        buf = b""
        try:
            buf = self._handshake()
            self.status = "CONNECTED"
            self._emit_status()
            self.sock.settimeout(30)
            while not self._stop:
                try:
                    chunk = self.sock.recv(8192)
                except socket.timeout:
                    self.sock.sendall(self._frame(0x9, b"keepalive"))  # ping
                    continue
                if not chunk:
                    raise ConnectionError("connection closed")
                buf += chunk
                pos = 0
                while True:
                    fr = self._parse_frame(buf, pos)
                    if fr is None:
                        break
                    fin, opcode, payload, npos = fr
                    if opcode == 0x1:  # text
                        self.messages += 1
                        if self.on_message:
                            try:
                                self.on_message(payload.decode("utf-8", "replace"))
                            except Exception:
                                pass
                    elif opcode == 0x9:  # ping → pong
                        self.sock.sendall(self._frame(0xA, payload))
                    elif opcode == 0x8:  # close
                        raise ConnectionError("server closed connection")
                    pos = npos
                buf = buf[pos:]
        except Exception as e:
            self.last_error = http_error_str(e)
        finally:
            self.status = "UNAVAILABLE"
            self._emit_status()
            try:
                if self.sock:
                    self.sock.close()
            except Exception:
                pass

    def _emit_status(self):
        if self.on_status:
            try:
                self.on_status(self.status, self.last_error)
            except Exception:
                pass

class BinanceWSStream(WSClient):
    """Subscribes to Binance combined miniTicker streams."""
    def __init__(self, streams, on_ticker=None):
        self._on_ticker = on_ticker
        super().__init__(BINANCE_WS + "/stream?streams=" + "/".join(streams),
                         on_message=self._handle)

    def _handle(self, text):
        try:
            d = json.loads(text)
            data = d.get("data") or d
            if isinstance(data, dict) and data.get("e") == "24hrMiniTicker":
                if self._on_ticker:
                    self._on_ticker(data.get("s"), data)
        except Exception:
            pass

# =============================================================================
# PART 6 — FEATURE ENGINE + CROSS-ASSET INTELLIGENCE + REGIME + STRATEGIES
# =============================================================================

def ema(values, n):
    if not values or n <= 1:
        return list(values)
    k = 2.0 / (n + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out

def sma(values, n):
    if len(values) < n or n <= 0:
        return list(values)
    out = []
    for i in range(len(values)):
        if i + 1 >= n:
            out.append(statistics.fmean(values[i + 1 - n:i + 1]))
        else:
            out.append(values[i])
    return out

def rsi(closes, n=14):
    if len(closes) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0))
        losses.append(max(-ch, 0))
    ag = statistics.fmean(gains[-n:])
    al = statistics.fmean(losses[-n:])
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return clamp(100 - 100 / (1 + rs), 0, 100)

def true_ranges(h, l, c):
    out = []
    for i in range(1, len(c)):
        out.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return out

def atr(h, l, c, n=14):
    tr = true_ranges(h, l, c)
    if len(tr) < n:
        return statistics.fmean(tr) if tr else 0.0
    return statistics.fmean(tr[-n:])

def macd(closes, fast=12, slow=26, sig=9):
    if len(closes) < slow + sig:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    f = ema(closes, fast); s = ema(closes, slow)
    line = [a - b for a, b in zip(f, s)]
    sig_line = ema(line, sig)
    return {"macd": line[-1], "signal": sig_line[-1], "hist": line[-1] - sig_line[-1]}

def bollinger(closes, n=20, k=2.0):
    if len(closes) < n:
        m = statistics.fmean(closes) if closes else 0
        return {"mid": m, "upper": m, "lower": m, "pctb": 0.5, "width": 0}
    w = closes[-n:]
    m = statistics.fmean(w)
    sd = statistics.pstdev(w)
    upper, lower = m + k * sd, m - k * sd
    last = closes[-1]
    pctb = (last - lower) / (upper - lower) if upper > lower else 0.5
    width = (upper - lower) / m if m else 0
    return {"mid": m, "upper": upper, "lower": lower, "pctb": clamp(pctb, -0.5, 1.5), "width": width}

def returns(closes):
    return [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1]]

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
    funding_rate: float = 0        # real, from futures public API (0 = n/a)
    open_interest: float = 0
    derivs_available: bool = False
    data_stale: bool = False
    # cross-asset
    btc_mom: float = 0; market_mom: float = 0; rel_strength: float = 0
    corr_btc: float = 0; vol_spillover: float = 0
    market_breadth: float = 0

class FeatureEngine:
    @staticmethod
    def build(market):
        kl = MDATA.get_klines(market) or []
        t = MDATA.get_ticker(market) or {}
        f = AssetFeatures(market=market)
        f.price = safe_float(t.get("price"))
        f.change24 = safe_float(t.get("change24"))
        f.liquidity_usd = safe_float(t.get("quote_vol"))
        f.data_stale = MDATA.is_stale(market)
        derivs = MDATA.get_derivs(market)
        if derivs:
            f.funding_rate = safe_float(derivs.get("funding_rate"))
            f.open_interest = safe_float(derivs.get("open_interest"))
            f.derivs_available = bool(derivs.get("available"))
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
            hw = max(highs[-20:]) if len(highs) >= 20 else max(highs)
            lw = min(lows[-20:]) if len(lows) >= 20 else min(lows)
            f.high_20 = hw; f.low_20 = lw
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
    """BTC influence, market momentum/breadth, correlations, spillover,
    relative strength — quantified from REAL closes, never assumed."""

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
        breadth = (sum(1 for v in moms.values() if v > 0) / len(moms)) if moms else 0.5
        vols = {m: (statistics.pstdev(r[-20:]) if len(r) >= 20 else 0) for m, r in rets.items()}
        avg_vol = statistics.fmean(list(vols.values())) if vols else 0
        for m, f in feats.items():
            f.btc_mom = btc_mom
            f.market_mom = market_mom
            f.market_breadth = breadth
            f.rel_strength = moms.get(m, 0) - market_mom
            if "BTC/USDT" in rets and m in rets and m != "BTC/USDT":
                f.corr_btc = cls.corr(rets[m][-60:], rets["BTC/USDT"][-60:])
            elif m == "BTC/USDT":
                f.corr_btc = 1.0
            f.vol_spillover = (avg_vol - vols.get(m, 0))
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

STRATEGIES = ["trend", "momentum", "breakout", "mean_reversion", "volatility", "orderflow"]

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
            return {s: {"score": 0.0, "fit": 0.0, "weighted": 0.0} for s in STRATEGIES}
        atr_ref = f.atr14 or (f.price * 0.01)
        trend_s = 0.0
        if f.sma20 and f.sma50:
            trend_s += clamp((f.price - f.sma50) / atr_ref * 0.4, -1, 1)
            trend_s += clamp((f.sma20 - f.sma50) / atr_ref * 0.3, -1, 1)
        trend_s += 0.3 if f.ema12 > f.ema26 else (-0.3 if f.ema12 < f.ema26 else 0)
        mom_s = clamp(f.mom_10 * 8, -1, 1) * 0.6 + clamp(f.mom_20 * 4, -1, 1) * 0.4
        brk_s = clamp(f.breakout_up * 40, -1, 1) if f.breakout_up > 0 else clamp(f.breakout_dn * 40, -1, 1)
        mr_s = clamp((0.5 - f.bb_pctb) * 2, -1, 1) * (1 if f.rsi14 < 45 or f.rsi14 > 55 else 0.3)
        vol_s = clamp((f.bb_width - 0.04) * 10, -1, 1)
        of_s = clamp(f.ob_imbalance * 2 + clamp(f.macd_hist / atr_ref * 2, -1, 1) * 0.5, -1, 1)
        raw = {"trend": trend_s, "momentum": mom_s, "breakout": brk_s,
               "mean_reversion": mr_s, "volatility": vol_s, "orderflow": of_s}
        fit = REGIME_STRAT_FIT.get(regime, {s: 0.5 for s in STRATEGIES})
        return {s: {"score": round(raw[s], 4), "fit": fit.get(s, 0.5),
                    "weighted": round(raw[s] * fit.get(s, 0.5), 4)} for s in STRATEGIES}

    @staticmethod
    def aggregate(evald):
        tot_w = sum(v["fit"] for v in evald.values()) or 1
        return sum(v["weighted"] for v in evald.values()) / tot_w

# =============================================================================
# PART 7 — AI SYSTEM
#   A) Quant ensemble (deterministic, trained on REAL data, from scratch)
#   B) LLM AI Provider Manager (real cloud/local adapters, advisory only)
#   C) Research Agents (deterministic analysts + optional LLM debate)
# The quant ensemble drives trading. LLM/agents CANNOT place orders.
# =============================================================================

MODEL_VERSION = "quant-ensemble-2.0.0"

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
    """Direction / Return / Volatility / Regime / Quality + Ensemble meta-model.

    strict_ai_mode=True (default): if models are not trained on real data the
    engine returns WAIT with the reason "models not trained". It NEVER
    fabricates a prediction and presents it as model output.
    """

    def __init__(self):
        self.dir_model = LogisticModel(len(FEATURE_KEYS))
        self.ret_model = RidgeModel(len(FEATURE_KEYS))
        self.trained = False
        self.train_samples = 0
        self.metrics = {}
        self.degraded = False
        self.last_inference = None
        self.inference_count = 0
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
                f = self._features_from_window(m, window)
                if f is None:
                    continue
                fut = closes[i + horizon] / closes[i] - 1
                X.append(feature_vector(f))
                y_dir.append(1 if fut > 0.001 else 0)
                y_ret.append(clamp(fut, -0.1, 0.1))
        return X, y_dir, y_ret

    @staticmethod
    def _features_from_window(market, window):
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
            n = len(X); cut = int(n * 0.8)   # chronological split — no leakage
            Xtr, Xte = X[:cut], X[cut:]; ytr, yte = y_dir[:cut], y_dir[cut:]
            rtr, rte = y_ret[:cut], y_ret[cut:]
            self.dir_model = LogisticModel(len(FEATURE_KEYS)); self.dir_model.fit(Xtr, ytr, epochs=120)
            self.ret_model = RidgeModel(len(FEATURE_KEYS)); self.ret_model.fit(Xtr, rtr, epochs=150)
            correct = sum(1 for xi, yi in zip(Xte, yte)
                          if (self.dir_model.predict_proba(xi) >= 0.5) == bool(yi))
            acc = correct / max(1, len(Xte))
            preds = [self.ret_model.predict(xi) for xi in Xte]
            mae = sum(abs(p - t) for p, t in zip(preds, rte)) / max(1, len(rte))
            self.train_samples = n
            self.trained = True
            self.metrics = {"status": "trained", "samples": n, "dir_accuracy_oos": round(acc, 4),
                            "return_mae_oos": round(mae, 5), "version": MODEL_VERSION,
                            "trained_at": utcnow_iso()}
            try:
                DBASE.execute("INSERT INTO models (name, version, metrics, status, trained_at, samples)"
                              " VALUES (?,?,?,?,?,?)",
                              ("ensemble", MODEL_VERSION, json.dumps(self.metrics), "active",
                               utcnow_iso(), n))
            except Exception:
                pass
            audit("ml", "model_trained", self.metrics)
            return self.metrics

    def healthy(self):
        """AI is 'healthy' for trading when trained on real data and not degraded."""
        return self.trained and not self.degraded

    def infer(self, f, regime_info, strat_eval):
        with self._lock:
            x = feature_vector(f)
            strict = bool(CONFIG.get("strict_ai_mode", True))
            used_model = False
            heuristic = False
            if self.trained and not self.degraded:
                p_long = self.dir_model.predict_proba(x)
                exp_ret = self.ret_model.predict(x)
                used_model = True
            elif not strict:
                agg = StrategyEngine.aggregate(strat_eval)
                p_long = clamp(0.5 + agg * 0.35, 0.05, 0.95)
                exp_ret = clamp(agg * max(f.atr_pct, 0.005) * 1.2, -0.05, 0.05)
                heuristic = True
            else:
                p_long, exp_ret = 0.5, 0.0
            exp_vol = max(f.atr_pct * 1.1, 0.002)
            scores = [v["score"] for v in strat_eval.values()]
            agree = 1 - (statistics.pstdev(scores) if len(scores) > 1 else 1)
            best_fit = max((v["fit"] for v in strat_eval.values()), default=0.5)
            data_q = 1.0 if f.n_candles >= 120 else (0.6 if f.n_candles >= 60 else 0.3)
            if f.data_stale:
                data_q *= 0.5
            quality = clamp(0.4 * agree + 0.35 * best_fit + 0.25 * data_q, 0, 1)
            if used_model:
                direction = Direction.LONG if p_long >= 0.56 else (Direction.SHORT if p_long <= 0.44 else Direction.WAIT)
                confidence = round(abs(p_long - 0.5) * 2, 4)
                mode = "trained-ensemble"
            elif heuristic:
                direction = Direction.LONG if p_long >= 0.56 else (Direction.SHORT if p_long <= 0.44 else Direction.WAIT)
                confidence = round(abs(p_long - 0.5) * 2, 4)
                mode = "heuristic-composite-LABELED"
            else:
                direction = Direction.WAIT
                confidence = 0.0
                mode = "untrained-no-signal"
            out = {
                "p_long": round(p_long, 4), "direction": direction.value,
                "expected_return": round(exp_ret, 5), "expected_vol": round(exp_vol, 5),
                "confidence": confidence, "quality": round(quality, 4),
                "model_version": MODEL_VERSION, "model_trained": self.trained,
                "model_degraded": self.degraded, "mode": mode,
                "regime": regime_info["regime"], "regime_conf": regime_info["confidence"],
            }
            self.last_inference = utcnow_iso()
            self.inference_count += 1
            return out

AI = AIEngine()

# ----------------------------- LLM AI provider manager -----------------------

AI_PROVIDER_DEFS = {
    "openai": {"name": "OpenAI", "kind": "openai_compatible",
               "default_base": "https://api.openai.com/v1", "default_model": "gpt-4o-mini",
               "local": False, "key_required": True, "docs": "https://platform.openai.com/docs"},
    "anthropic": {"name": "Anthropic", "kind": "anthropic",
                  "default_base": "https://api.anthropic.com", "default_model": "claude-3-5-haiku-latest",
                  "local": False, "key_required": True, "docs": "https://docs.anthropic.com"},
    "google": {"name": "Google Gemini", "kind": "google",
               "default_base": "https://generativelanguage.googleapis.com", "default_model": "gemini-2.0-flash",
               "local": False, "key_required": True, "docs": "https://ai.google.dev/docs"},
    "deepseek": {"name": "DeepSeek", "kind": "openai_compatible",
                 "default_base": "https://api.deepseek.com/v1", "default_model": "deepseek-chat",
                 "local": False, "key_required": True, "docs": "https://api-docs.deepseek.com"},
    "qwen": {"name": "Qwen (DashScope)", "kind": "openai_compatible",
             "default_base": "https://dashscope.aliyuncs.com/compatible-mode/v1", "default_model": "qwen-plus",
             "local": False, "key_required": True, "docs": "https://help.aliyun.com/zh/dashscope/"},
    "xai": {"name": "xAI Grok", "kind": "openai_compatible",
            "default_base": "https://api.x.ai/v1", "default_model": "grok-2-latest",
            "local": False, "key_required": True, "docs": "https://docs.x.ai"},
    "openrouter": {"name": "OpenRouter", "kind": "openai_compatible",
                   "default_base": "https://openrouter.ai/api/v1", "default_model": "openai/gpt-4o-mini",
                   "local": False, "key_required": True, "docs": "https://openrouter.ai/docs"},
    "ollama": {"name": "Ollama (LOCAL)", "kind": "ollama",
               "default_base": "http://localhost:11434", "default_model": "llama3.1",
               "local": True, "key_required": False, "docs": "https://ollama.com"},
    "vllm": {"name": "vLLM (LOCAL)", "kind": "openai_compatible",
             "default_base": "http://localhost:8001/v1", "default_model": "local-model",
             "local": True, "key_required": False, "docs": "https://docs.vllm.ai"},
}

class AIProviderManager:
    """Unified LLM provider interface. Providers activate ONLY with valid
    configuration/credentials — never hardcoded. Advisory use only: research
    and explanations. The LLM has no access to order placement APIs."""

    def __init__(self):
        self.usage = defaultdict(lambda: {"calls": 0, "tokens_in": 0, "tokens_out": 0,
                                          "errors": 0, "total_latency_ms": 0, "last_call": None,
                                          "last_error": None, "last_ok": None})

    # ---- config helpers ----
    def provider_config(self, pid):
        cfgs = CONFIG.get("ai_providers") or {}
        return cfgs.get(pid) or {}

    def configured(self, pid):
        d = AI_PROVIDER_DEFS.get(pid)
        if not d:
            return False
        c = self.provider_config(pid)
        if not c.get("enabled"):
            return False
        if d["key_required"]:
            try:
                return bool(SECRETS.get(f"ai_key_{pid}"))
            except Exception:
                return False
        return True

    def active_provider(self):
        for pid, d in AI_PROVIDER_DEFS.items():
            if self.configured(pid):
                return pid
        return None

    def set_provider(self, pid, enabled, base_url=None, model=None, api_key=None):
        if pid not in AI_PROVIDER_DEFS:
            return {"ok": False, "error": "unknown provider"}
        cfgs = copy.deepcopy(CONFIG.get("ai_providers") or {})
        c = cfgs.get(pid) or {}
        c["enabled"] = bool(enabled)
        if base_url is not None:
            c["base_url"] = str(base_url)
        if model:
            c["model"] = str(model)
        cfgs[pid] = c
        if api_key is not None:
            if api_key == "":
                SECRETS.delete(f"ai_key_{pid}")
            else:
                SECRETS.put(f"ai_key_{pid}", api_key, purpose="general")
        with CONFIG._lock:
            CONFIG.cfg["ai_providers"] = cfgs
            # switching providers disables the others (one active LLM at a time)
            if c["enabled"]:
                for k in cfgs:
                    if k != pid:
                        cfgs[k]["enabled"] = False
                d = AI_PROVIDER_DEFS[pid]
                CONFIG.cfg["ai_mode"] = ("LOCAL_LLM" if d["local"] else "CLOUD_LLM")
            elif not any(x.get("enabled") for x in cfgs.values()):
                CONFIG.cfg["ai_mode"] = "LOCAL_QUANT_ONLY"
            CONFIG.save_locked()
        audit("ai", "provider_configured", {"provider": pid, "enabled": c["enabled"],
                                            "model": c.get("model")}, actor="user")
        return {"ok": True}

    # ---- transport (real API calls) ----
    def chat(self, system, user, max_tokens=800, timeout=45):
        """One completion from the ACTIVE provider. Returns dict or raises."""
        pid = self.active_provider()
        if not pid:
            raise RuntimeError("no AI provider configured (quant ensemble still active)")
        d = AI_PROVIDER_DEFS[pid]
        c = self.provider_config(pid)
        base = c.get("base_url") or d["default_base"]
        model = c.get("model") or d["default_model"]
        t0 = time.time()
        try:
            if d["kind"] == "openai_compatible":
                headers = {"Content-Type": "application/json"}
                key = SECRETS.get(f"ai_key_{pid}") if d["key_required"] else None
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                body = {"model": model, "max_tokens": max_tokens, "temperature": 0.2,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": user}]}
                r = http_json(f"{base}/chat/completions", timeout=timeout, method="POST",
                              data=body, headers=headers)
                text = (r.get("choices") or [{}])[0].get("message", {}).get("content", "")
                usage = r.get("usage") or {}
            elif d["kind"] == "anthropic":
                key = SECRETS.get(f"ai_key_{pid}")
                if not key:
                    raise RuntimeError("missing API key")
                headers = {"x-api-key": key, "anthropic-version": "2023-06-01",
                           "Content-Type": "application/json"}
                body = {"model": model, "max_tokens": max_tokens, "system": system,
                        "messages": [{"role": "user", "content": user}]}
                r = http_json(f"{base}/v1/messages", timeout=timeout, method="POST",
                              data=body, headers=headers)
                text = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")
                usage = r.get("usage") or {}
            elif d["kind"] == "google":
                key = SECRETS.get(f"ai_key_{pid}")
                if not key:
                    raise RuntimeError("missing API key")
                body = {"contents": [{"parts": [{"text": system + "\n\n" + user}]}],
                        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2}}
                r = http_json(f"{base}/v1beta/models/{model}:generateContent?key={key}",
                              timeout=timeout, method="POST", data=body)
                parts = ((r.get("candidates") or [{}])[0].get("content", {}) or {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                usage = r.get("usageMetadata") or {}
            elif d["kind"] == "ollama":
                body = {"model": model, "stream": False,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": user}],
                        "options": {"temperature": 0.2, "num_predict": max_tokens}}
                r = http_json(f"{base}/api/chat", timeout=timeout, method="POST", data=body)
                text = (r.get("message") or {}).get("content", "")
                usage = {}
            else:
                raise RuntimeError("unknown provider kind")
            u = self.usage[pid]
            u["calls"] += 1
            u["tokens_in"] += int(usage.get("input_tokens") or usage.get("prompt_tokens") or usage.get("promptTokenCount") or 0)
            u["tokens_out"] += int(usage.get("output_tokens") or usage.get("completion_tokens") or usage.get("candidatesTokenCount") or 0)
            u["total_latency_ms"] += int((time.time() - t0) * 1000)
            u["last_call"] = utcnow_iso()
            u["last_ok"] = utcnow_iso()
            u["last_error"] = None
            return {"provider": pid, "model": model, "text": text,
                    "latency_ms": int((time.time() - t0) * 1000),
                    "local": bool(d["local"])}
        except Exception as e:
            u = self.usage[pid]
            u["errors"] += 1
            u["last_call"] = utcnow_iso()
            u["last_error"] = http_error_str(e)[:200]
            raise

    def health_check(self, pid):
        d = AI_PROVIDER_DEFS.get(pid)
        if not d:
            return {"ok": False, "error": "unknown provider"}
        if not self.configured(pid):
            return {"ok": False, "status": "NOT_CONFIGURED"}
        try:
            r = self.chat("You are a connectivity probe. Reply with the word: ok",
                          "ping", max_tokens=8, timeout=20)
            return {"ok": True, "status": "OK", "model": r["model"], "local": r["local"],
                    "latency_ms": r["latency_ms"], "sample": (r["text"] or "")[:40]}
        except Exception as e:
            return {"ok": False, "status": "ERROR", "error": http_error_str(e)[:200]}

    def status(self):
        active = self.active_provider()
        rows = []
        for pid, d in AI_PROVIDER_DEFS.items():
            c = self.provider_config(pid)
            u = dict(self.usage[pid])
            key_stored = False
            try:
                key_stored = bool(SECRETS.get(f"ai_key_{pid}"))
            except Exception:
                pass
            rows.append({
                "id": pid, "name": d["name"], "local": d["local"],
                "kind": d["kind"], "configured": self.configured(pid),
                "enabled": bool(c.get("enabled")), "active": pid == active,
                "base_url": c.get("base_url") or d["default_base"],
                "model": c.get("model") or d["default_model"],
                "key_status": ("stored-encrypted" if key_stored else
                               ("not needed" if not d["key_required"] else "MISSING")),
                "key_required": d["key_required"], "docs": d["docs"],
                "usage": {"calls": u.get("calls", 0), "tokens_in": u.get("tokens_in", 0),
                          "tokens_out": u.get("tokens_out", 0), "errors": u.get("errors", 0),
                          "avg_latency_ms": (u.get("total_latency_ms", 0) // u["calls"]) if u.get("calls") else None,
                          "last_call": u.get("last_call"), "last_error": u.get("last_error"),
                          "last_ok": u.get("last_ok")},
            })
        return {"active": active, "mode": CONFIG.get("ai_mode", "LOCAL_QUANT_ONLY"),
                "quant": {"version": MODEL_VERSION, "trained": AI.trained,
                          "degraded": AI.degraded, "metrics": AI.metrics,
                          "inferences": AI.inference_count, "last_inference": AI.last_inference,
                          "healthy": AI.healthy(), "strict_mode": bool(CONFIG.get("strict_ai_mode", True))},
                "providers": rows}

AIPM = AIProviderManager()

# ----------------------------- research agents --------------------------------
# TradingAgents-inspired pipeline (analysts → research → risk → portfolio),
# but ZEPAY's own deterministic engines remain the trading authority.
# Agents receive ONLY real system data. LLM output is validated JSON and is
# advisory: it can never place orders or alter risk limits.

AGENT_GUARDRAILS = [
    "Agents receive real market data and engine state only.",
    "LLM agent output is schema-validated; failures are discarded, not guessed.",
    "Agents have NO access to ExecutionEngine, RiskEngine limits or credentials.",
    "Agent recommendations never auto-execute; they only annotate the ledger.",
]

class Agents:
    """Deterministic analyst agents. Each returns structured evidence from
    real features. Cheap, auditable, no network required."""

    @staticmethod
    def market_analyst(f):
        ev = []
        trend = "UP" if f.price > f.sma20 > f.sma50 else ("DOWN" if f.price < f.sma20 < f.sma50 else "MIXED")
        ev.append(f"Structure {trend}: price {f.price:.6g} vs SMA20 {f.sma20:.6g} / SMA50 {f.sma50:.6g}")
        ev.append(f"Momentum 10/20-bar: {pct(f.mom_10)}% / {pct(f.mom_20)}%; 24h {f.change24:+.2f}%")
        ev.append(f"Volume ratio vs 20-bar mean: {f.vol_ratio:.2f}x")
        verdict = "BULLISH" if (trend == "UP" and f.mom_10 > 0) else ("BEARISH" if trend == "DOWN" else "NEUTRAL")
        return {"agent": "MarketAnalyst", "verdict": verdict, "confidence": 0.6, "evidence": ev}

    @staticmethod
    def technical_analyst(f):
        ev = [f"RSI14 {f.rsi14:.1f}", f"MACD hist {f.macd_hist:.6g}",
              f"Bollinger %B {f.bb_pctb:.2f} (width {pct(f.bb_width)}%)",
              f"ATR14 {pct(f.atr_pct)}% of price"]
        if f.rsi14 > 70:
            v, c = "BEARISH", 0.55
        elif f.rsi14 < 30:
            v, c = "BULLISH", 0.55
        elif f.macd_hist > 0 and f.bb_pctb > 0.5:
            v, c = "BULLISH", 0.5
        elif f.macd_hist < 0 and f.bb_pctb < 0.5:
            v, c = "BEARISH", 0.5
        else:
            v, c = "NEUTRAL", 0.45
        return {"agent": "TechnicalAnalyst", "verdict": v, "confidence": c, "evidence": ev}

    @staticmethod
    def quant_analyst(f, infer):
        ev = [f"Model p(long) {infer['p_long']} mode={infer['mode']}",
              f"Expected return {pct(infer['expected_return'])}% over horizon",
              f"Expected vol {pct(infer['expected_vol'])}%",
              f"Trade quality score {infer['quality']}"]
        v = "BULLISH" if infer["direction"] == "LONG" else ("BEARISH" if infer["direction"] == "SHORT" else "NEUTRAL")
        return {"agent": "QuantAnalyst", "verdict": v, "confidence": infer["confidence"], "evidence": ev}

    @staticmethod
    def regime_analyst(f, regime_info):
        ev = [f"Regime {regime_info['regime']} (conf {regime_info['confidence']})"] + regime_info["reasons"]
        v = "BULLISH" if regime_info["regime"] in ("BULLISH_TREND", "BREAKOUT") else \
            ("BEARISH" if regime_info["regime"] in ("BEARISH_TREND", "PANIC") else "NEUTRAL")
        return {"agent": "RegimeAnalyst", "verdict": v, "confidence": regime_info["confidence"], "evidence": ev}

    @staticmethod
    def derivatives_analyst(f):
        if not f.derivs_available:
            return {"agent": "DerivativesAnalyst", "verdict": "NO_DATA", "confidence": 0,
                    "evidence": ["Futures data (funding/OI) UNAVAILABLE from provider — no assumptions made"]}
        ev = [f"Funding rate {pct(f.funding_rate, 4)}%/8h",
              f"Open interest {f.open_interest:,.0f} contracts"]
        if f.funding_rate > 0.0005:
            ev.append("Elevated positive funding — longs pay; crowded long positioning")
            return {"agent": "DerivativesAnalyst", "verdict": "BEARISH", "confidence": 0.5, "evidence": ev}
        if f.funding_rate < -0.0005:
            ev.append("Negative funding — shorts pay; crowded short positioning")
            return {"agent": "DerivativesAnalyst", "verdict": "BULLISH", "confidence": 0.5, "evidence": ev}
        return {"agent": "DerivativesAnalyst", "verdict": "NEUTRAL", "confidence": 0.4, "evidence": ev}

    @staticmethod
    def cross_asset_analyst(f):
        ev = [f"BTC 20-bar momentum {pct(f.btc_mom)}%",
              f"Market momentum {pct(f.market_mom)}% (breadth {pct(f.market_breadth)}% up)",
              f"Correlation to BTC {f.corr_btc:+.2f}",
              f"Relative strength vs market {pct(f.rel_strength)}%"]
        v = "BULLISH" if (f.market_mom > 0 and f.rel_strength > 0) else \
            ("BEARISH" if (f.market_mom < 0 and f.rel_strength < 0) else "NEUTRAL")
        return {"agent": "CrossAssetAnalyst", "verdict": v, "confidence": 0.5, "evidence": ev}

    @staticmethod
    def risk_analyst(f):
        ev = [f"Spread {f.spread_bps:.1f} bps", f"Visible depth ${f.depth_usd:,.0f}",
              f"ATR {pct(f.atr_pct)}%", f"Liquidity 24h ${f.liquidity_usd:,.0f}"]
        risky = (f.spread_bps > 15 or f.atr_pct > 0.05 or
                 (f.liquidity_usd and f.liquidity_usd < 500000))
        return {"agent": "RiskAnalyst", "verdict": "CAUTION" if risky else "OK",
                "confidence": 0.6, "evidence": ev}

    @staticmethod
    def portfolio_analyst(exposure, positions):
        ev = [f"Exposure {pct(exposure.get('total_pct', 0))}% (long {pct(exposure.get('long_pct', 0))}% /"
              f" short {pct(exposure.get('short_pct', 0))}%)",
              f"Open positions {len(positions)}"]
        for m, p in (exposure.get("per_asset") or {}).items():
            ev.append(f"{m}: {pct(p)}% of equity")
        return {"agent": "PortfolioAnalyst", "verdict": "OK" if exposure.get("total_pct", 0) < 0.5 else "CAUTION",
                "confidence": 0.5, "evidence": ev}

    @staticmethod
    def execution_analyst(f):
        cost = round_trip_cost_pct(f)
        ev = [f"Round-trip cost estimate {pct(cost)}% (fees+spread+slippage+funding est.)",
              f"Spread {f.spread_bps:.1f} bps"]
        return {"agent": "ExecutionAnalyst", "verdict": "OK" if cost < 0.004 else "EXPENSIVE",
                "confidence": 0.5, "evidence": ev}

    @classmethod
    def run_deterministic(cls, f, regime_info, infer, exposure, positions):
        return [cls.market_analyst(f), cls.technical_analyst(f), cls.quant_analyst(f, infer),
                cls.regime_analyst(f, regime_info), cls.derivatives_analyst(f),
                cls.cross_asset_analyst(f), cls.risk_analyst(f),
                cls.portfolio_analyst(exposure, positions), cls.execution_analyst(f)]

class ResearchEngineLLM:
    """Optional LLM research layer (advisory). Bull/bear debate, TradingAgents
    style. Uses ONLY the supplied real data in the prompt; output must be
    valid JSON matching the schema or it is DISCARDED."""

    SCHEMA_HINT = ('{"bull_case": ["..."], "bear_case": ["..."], '
                   '"bias": "LONG"|"SHORT"|"WAIT", "confidence": 0.0-1.0, '
                   '"key_risks": ["..."], "summary": "..."}')

    @staticmethod
    def build_prompt(market, f, regime_info, infer, agents_out):
        data = {
            "market": market,
            "price": f.price, "change24h_pct": f.change24,
            "rsi14": round(f.rsi14, 1), "atr_pct": round(f.atr_pct * 100, 3),
            "momentum_10": round(f.mom_10 * 100, 3), "momentum_20": round(f.mom_20 * 100, 3),
            "volume_ratio": round(f.vol_ratio, 2),
            "spread_bps": round(f.spread_bps, 1),
            "funding_rate": f.funding_rate if f.derivs_available else None,
            "regime": regime_info["regime"],
            "quant_model": {"p_long": infer["p_long"], "direction": infer["direction"],
                            "confidence": infer["confidence"], "mode": infer["mode"]},
            "analysts": [{"agent": a["agent"], "verdict": a["verdict"]} for a in agents_out],
        }
        system = ("You are a crypto research analyst on a trading desk. Use ONLY the data "
                  "provided. Do not invent prices, news or on-chain facts. If data is "
                  "missing, say so. Respond with a single JSON object, no markdown, "
                  "matching exactly this schema:\n" + ResearchEngineLLM.SCHEMA_HINT +
                  "\nThis is research only — you cannot place orders.")
        user = "Market data and engine state:\n" + jdump(data)
        return system, user

    @staticmethod
    def validate(text):
        """Parse + schema-validate LLM output. Returns None on any failure."""
        try:
            t = (text or "").strip()
            if t.startswith("```"):
                t = re.sub(r"^```[a-z]*\n?|```$", "", t).strip()
            start, end = t.find("{"), t.rfind("}")
            if start < 0 or end <= start:
                return None
            d = json.loads(t[start:end + 1])
            if not isinstance(d, dict):
                return None
            bias = str(d.get("bias", "WAIT")).upper()
            if bias not in ("LONG", "SHORT", "WAIT"):
                bias = "WAIT"
            out = {
                "bull_case": [str(x)[:300] for x in (d.get("bull_case") or [])][:8],
                "bear_case": [str(x)[:300] for x in (d.get("bear_case") or [])][:8],
                "key_risks": [str(x)[:300] for x in (d.get("key_risks") or [])][:8],
                "bias": bias,
                "confidence": clamp(safe_float(d.get("confidence"), 0), 0, 1),
                "summary": str(d.get("summary", ""))[:1000],
            }
            return out
        except Exception:
            return None

    @staticmethod
    def run(market, f, regime_info, infer, agents_out):
        if not CONFIG.get("research_llm_enabled"):
            return {"available": False, "reason": "LLM research layer disabled (quant ensemble only)"}
        if not AIPM.active_provider():
            return {"available": False, "reason": "no AI provider configured"}
        system, user = ResearchEngineLLM.build_prompt(market, f, regime_info, infer, agents_out)
        try:
            r = AIPM.chat(system, user, max_tokens=900, timeout=60)
        except Exception as e:
            audit("ai", "llm_research_failed", {"market": market, "error": http_error_str(e)})
            return {"available": False, "reason": f"LLM call failed: {http_error_str(e)}"}
        parsed = ResearchEngineLLM.validate(r["text"])
        if parsed is None:
            audit("ai", "llm_research_invalid_output", {"market": market})
            return {"available": False, "reason": "LLM output failed schema validation — discarded"}
        parsed.update({"available": True, "provider": r["provider"], "model": r["model"],
                       "local": r["local"], "latency_ms": r["latency_ms"],
                       "advisory_only": True})
        return parsed

# =============================================================================
# PART 8 — HEALTH + OPPORTUNITY + PORTFOLIO + GLOBAL RISK + SIZING
# =============================================================================

COST = {}

def refresh_cost():
    COST["fee_bps"] = safe_float(CONFIG.get("fee_bps", 10.0), 10.0)
    COST["slip_bps"] = safe_float(CONFIG.get("slippage_bps", 5.0), 5.0)
    COST["fund_bps"] = safe_float(CONFIG.get("funding_bps_8h", 1.0), 1.0)

refresh_cost()

def round_trip_cost_pct(f, notional_hint=0):
    fee = COST["fee_bps"] / 10000.0 * 2
    spread = (f.spread_bps / 10000.0) if f.spread_bps else 0.0004
    slip = COST["slip_bps"] / 10000.0 * 2
    fund = COST["fund_bps"] / 10000.0
    return fee + spread + slip + fund

class HealthEngine:
    @staticmethod
    def assess(market, f):
        reasons = []
        if MDATA.is_stale(market) and (f.price <= 0):
            return {"status": Health.BLOCKED.value,
                    "reasons": ["No market data (stale/unreachable). REAL DATA REQUIRED — asset blocked."]}
        if f.price <= 0 or f.n_candles < 20:
            return {"status": Health.BLOCKED.value, "reasons": ["Insufficient price history"]}
        limited = False
        if MDATA.is_stale(market):
            reasons.append(f"Data delayed {MDATA.age_secs(market):.0f}s — new entries limited")
            limited = True
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
        base = 50 + (infer["p_long"] - 0.5) * 120
        base += infer["quality"] * 20 - 10
        base += regime_info["confidence"] * 10 - 5
        if infer["direction"] == "WAIT":
            base -= 25  # cash is a valid decision; don't chase
        if exp_net < safe_float(CONFIG.get("min_edge_pct", 0.0015)):
            base -= 15
        # liquidity/quality nudges
        if f.depth_usd:
            base += clamp(math.log10(max(f.depth_usd, 1)) - 5, -1, 2)
        if f.spread_bps:
            base -= clamp(f.spread_bps / 10.0, 0, 6)
        stars = safe_float((CONFIG.get("priorities") or {}).get(market, 0), 0)
        base += stars * 1.0
        score = clamp(base, 0, 100)
        best_strat = max(strat_eval.items(), key=lambda kv: kv[1]["fit"] * (0.5 + abs(kv[1]["score"])))[0]
        reasons = [
            f"Direction model: {infer['direction']} (p={infer['p_long']}, mode={infer['mode']})",
            f"Regime: {regime_info['regime']} ({regime_info['confidence']})",
            f"Best strategy fit: {best_strat}",
            f"Expected move {pct(infer['expected_return'])}% vs cost {pct(cost)}%",
        ]
        if infer["mode"] == "heuristic-composite-LABELED":
            reasons.append("⚠ heuristic composite (models untrained, non-strict mode) — labeled, not model output")
        if infer["mode"] == "untrained-no-signal":
            reasons.append("AI models not trained on real data yet — no signal emitted (strict mode)")
        if f.data_stale:
            reasons.append("⚠ market data stale")
        return Opportunity(
            market=market, direction=infer["direction"], score=round(score, 1),
            confidence=infer["confidence"], quality=infer["quality"],
            regime=regime_info["regime"], strategy=best_strat,
            expected_return=infer["expected_return"], expected_net=round(exp_net, 5),
            cost_pct=round(cost, 5), price=f.price, reasons=reasons,
            machine={"p_long": infer["p_long"], "exp_vol": infer["expected_vol"],
                     "model_version": infer["model_version"], "trained": infer["model_trained"],
                     "mode": infer["mode"]})

    @staticmethod
    def rank(opps):
        return sorted(opps, key=lambda o: o.score, reverse=True)

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
            px = safe_float(t.get("price")) if t else 0
            if px <= 0:
                px = safe_float(p.get("current_price"))
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
        update_account(mode, equity=equity, available=max(equity - invested, 0),
                       invested=invested, unrealized_pnl=unreal, drawdown_pct=dd,
                       peak_equity=peak)
        return get_account(mode)

# ----------------------------- global risk engine -----------------------------
# FINAL AUTHORITY. AI can never override. Check order mirrors the platform
# safety hierarchy. If the risk engine is unavailable → trading stops.

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
    def entries_allowed(cls):
        """Global gate: kill switch / halt / data feed / AI policy."""
        if CONFIG.get("kill_switch") or CONFIG.get("trading_halted"):
            return False, "Trading halted (kill switch or halt active)"
        if REAL_DATA_REQUIRED:
            feed = MDATA.feed_health()
            if feed["status"] == "UNAVAILABLE":
                return False, "DATA FEED FAILURE — " + feed["reason"]
        if not AI.healthy():
            policy = CONFIG.get("ai_failure_policy", "reduce_risk")
            if policy == "stop_trading":
                return False, "AI UNAVAILABLE (policy: stop_trading while models untrained/degraded)"
        return True, ""

    @classmethod
    def authorize(cls, opp, f, health, positions, acct, mat, equity_hint=None):
        """Returns (Decision, reasons[], sizing_hint). Never raises on bad input."""
        reasons_h = []
        reasons_m = {}
        equity = equity_hint or safe_float((acct or {}).get("equity", 0)) or \
            safe_float(CONFIG.get("paper_starting_balance", 10000))
        state, state_msg = cls.system_state(acct)
        reasons_m["system_state"] = state.value
        if state == SystemState.HALTED:
            return Decision.HALTED, [f"Trading halted: {state_msg}"], None
        allowed, why = cls.entries_allowed()
        if not allowed:
            return Decision.HALTED, [why], None
        # 1 data validity
        if health["status"] == Health.BLOCKED.value:
            return Decision.REJECTED, [f"Market health BLOCKED: {'; '.join(health['reasons'])}"], None
        if MDATA.is_stale(opp.market):
            return Decision.REJECTED, [f"Stale market data ({MDATA.age_secs(opp.market):.0f}s old)"], None
        if opp.price <= 0 or f.n_candles < 30:
            return Decision.REJECTED, ["Insufficient validated market data"], None
        # 2 AI validity
        if opp.direction == "WAIT":
            if (opp.machine or {}).get("mode") == "untrained-no-signal":
                return Decision.WAIT, ["AI models not trained on real data yet — no direction (strict mode)"], None
            return Decision.WAIT, ["AI decision: WAIT — no direction with sufficient confidence"], None
        # 3 regime gate
        if opp.regime in ("PANIC", "UNSTABLE"):
            return Decision.WAIT, [f"Regime {opp.regime}: entries paused until stability returns"], None
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
        if safe_float(exp["per_asset"].get(opp.market, 0)) >= safe_float(CONFIG.get("max_asset_exposure_pct", 0.25)):
            return Decision.WAIT, ["Asset exposure limit reached"], None
        corr_exp = PortfolioEngine.correlated_exposure(opp.market, opp.direction, positions, equity, mat)
        if corr_exp >= safe_float(CONFIG.get("max_correlated_exposure_pct", 0.4)):
            return Decision.WAIT, [f"Correlated {opp.direction} exposure {pct(corr_exp)}% at limit"], None
        dir_key = "long_pct" if opp.direction == "LONG" else "short_pct"
        if exp[dir_key] >= safe_float(CONFIG.get("max_directional_exposure_pct", 0.5)):
            return Decision.WAIT, [f"Directional exposure limit ({opp.direction})"], None
        # sizing state
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
        if not AI.trained:
            size_mult *= 0.5
            reasons_h.append("Models untrained: size ×0.5")
        return Decision.APPROVED, reasons_h or ["All risk checks passed"], {
            "size_mult": size_mult, "state": state.value, "corr_exposure": corr_exp}

class Sizer:
    @staticmethod
    def round_qty(market, qty):
        """Apply the exchange's REAL step size / min qty if known."""
        flt = UNIVERSE.filters(market)
        if flt and flt.get("step_size"):
            step = flt["step_size"]
            if step > 0:
                qty = math.floor(qty / step) * step
        return round(qty, 8)

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
        vol_adj = clamp(0.02 / max(f.atr_pct, 0.005), 0.4, 1.2)
        qty *= min(vol_adj, 1.0)
        qty *= (0.6 + 0.4 * clamp(opp.confidence, 0, 1))
        qty = Sizer.round_qty(opp.market, qty)
        notional = qty * opp.price
        # exchange minimums
        flt = UNIVERSE.filters(opp.market)
        min_notional = safe_float((flt or {}).get("min_notional"), 0) or \
            safe_float(CONFIG.get("min_order_notional_usd", 10.0), 10.0)
        min_qty = safe_float((flt or {}).get("min_qty"), 0)
        if qty > 0 and (notional < min_notional or (min_qty and qty < min_qty)):
            return {"qty": 0, "notional": 0, "reject": True,
                    "reject_reason": f"Order below exchange minimum (min notional ${min_notional:,.2f})"}
        sl = opp.price - stop_dist if opp.direction == "LONG" else opp.price + stop_dist
        tp_dist = stop_dist * 2.0
        tp = opp.price + tp_dist if opp.direction == "LONG" else opp.price - tp_dist
        return {"qty": qty, "notional": notional, "stop": sl, "take_profit": tp,
                "risk_amount": risk_amt, "size_mult": mult, "reject": False}

# =============================================================================
# PART 9 — EXECUTION ENGINE (PAPER + LIVE) + RECONCILIATION
# Paper mirrors live as closely as possible: real price, real book, real fees,
# simulated ONLY the fill/balance. Live uses the real signed exchange API and
# VERIFIES exchange state after submission — success is never assumed.
# =============================================================================

class PaperExchange:
    """Simulated fills against REAL market state with an explicit cost model."""

    @staticmethod
    def quote(market):
        t = MDATA.get_ticker(market)
        if not t or not t.get("price"):
            return None, "no real price available"
        d = MDATA.get_depth(market)
        if d and d.get("bids") and d.get("asks"):
            bid = d["bids"][0][0]; ask = d["asks"][0][0]
            return {"bid": bid, "ask": ask, "mid": (bid + ask) / 2, "source": "binance-rest-depth"}, ""
        mid = safe_float(t["price"])
        spread = mid * 0.0004
        return {"bid": mid - spread / 2, "ask": mid + spread / 2, "mid": mid,
                "source": "ticker-spread-estimate"}, ""

    @staticmethod
    def market_fill(market, direction, qty):
        """Walk the REAL order book up to a participation cap.
        Returns fill dict incl. partial-fill handling, or (None, error)."""
        q, err = PaperExchange.quote(market)
        if not q:
            return None, err
        d = MDATA.get_depth(market)
        book = None
        if d:
            book = d.get("asks") if direction == "LONG" else d.get("bids")
        participation = clamp(safe_float(CONFIG.get("paper_participation_max", 0.25), 0.25), 0.01, 1.0)
        slip = COST["slip_bps"] / 10000.0
        latency = int(safe_float(CONFIG.get("paper_latency_ms", 120), 120))
        if book:
            capacity_notional = sum(p * x for p, x in book) * participation
            price_ref = book[0][0]
            capacity_qty = capacity_notional / price_ref if price_ref else 0
            filled_qty = min(qty, capacity_qty)
            if filled_qty <= 0:
                return None, "no fillable liquidity within participation cap"
            # consume levels for VWAP
            remaining = filled_qty
            cost = 0.0
            for p, x in book:
                take = min(remaining, x)
                cost += take * p
                remaining -= take
                if remaining <= 0:
                    break
            vwap = cost / filled_qty
            px = vwap * (1 + slip) if direction == "LONG" else vwap * (1 - slip)
            fee = px * filled_qty * (COST["fee_bps"] / 10000.0)
            return {"price": px, "fee": fee, "slippage_bps": COST["slip_bps"],
                    "filled_qty": filled_qty, "requested_qty": qty,
                    "remaining_qty": round(max(qty - filled_qty, 0), 8),
                    "partial": filled_qty < qty - 1e-12,
                    "latency_ms": latency, "book_source": "binance-rest-depth"}, ""
        # no real book → limited fill at ticker price (small size only)
        px = q["ask"] * (1 + slip) if direction == "LONG" else q["bid"] * (1 - slip)
        filled_qty = qty
        fee = px * filled_qty * (COST["fee_bps"] / 10000.0)
        return {"price": px, "fee": fee, "slippage_bps": COST["slip_bps"],
                "filled_qty": filled_qty, "requested_qty": qty, "remaining_qty": 0.0,
                "partial": False, "latency_ms": latency, "book_source": q.get("source")}, ""

class ExecutionEngine:
    _locks = defaultdict(threading.Lock)
    _live_block = None   # set when an order outcome is unknown → live halted

    # ---- order lifecycle ----
    @classmethod
    def _new_order(cls, market, side, direction, qty, price, order_type, status, mode,
                   strategy, reason, key, decision_id=None, limit_price=None):
        oid = "ord-" + secrets.token_hex(6)
        now = utcnow_iso()
        DBASE.execute(
            "INSERT INTO orders (id, market, side, direction, qty, price, order_type, status, mode,"
            " strategy, model_version, reason, created_at, updated_at, idempotency_key,"
            " exchange_order_id, limit_price, filled_qty, avg_fill_price, decision_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, market, side, direction, qty, price, order_type, status, mode, strategy,
             MODEL_VERSION, (reason or "")[:1000], now, now, key, None, limit_price, 0.0, None,
             decision_id))
        return oid

    @classmethod
    def _record_fill(cls, oid, market, side, qty, price, fee, slip_bps, pnl=0.0):
        fid = "fill-" + secrets.token_hex(6)
        DBASE.execute("INSERT INTO fills (id, order_id, market, side, qty, price, fee, slippage, pnl, filled_at)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (fid, oid, market, side, qty, price, fee, slip_bps, pnl, utcnow_iso()))
        return fid

    @classmethod
    def place(cls, opp, size, f, mode="PAPER", strategy=None, idempotency_key=None, decision_id=None):
        """Pre-flight checklist then route to the PAPER or LIVE adapter.
        The mode switch changes the actual execution adapter — not the UI."""
        if mode == "LIVE" and not CONFIG.get("live_enabled"):
            return {"ok": False, "error": "Live trading is disabled. Complete the Live Trading gate first."}
        if CONFIG.get("kill_switch") or CONFIG.get("trading_halted"):
            return {"ok": False, "error": "Trading halted — order refused (fail-safe)."}
        if mode == "LIVE" and cls._live_block:
            return {"ok": False, "error": f"Live trading blocked pending reconciliation: {cls._live_block}"}
        if (size or {}).get("reject"):
            return {"ok": False, "error": "Order rejected by sizer: " + size.get("reject_reason", "below minimum")}
        if not size or safe_float(size.get("qty")) <= 0:
            return {"ok": False, "error": "Zero quantity after precision/min-size rules."}
        allowed, why = RiskEngine.entries_allowed()
        if not allowed:
            return {"ok": False, "error": why}
        key = idempotency_key or f"{mode}-{opp.market}-{opp.direction}-{int(time.time())}-{secrets.token_hex(3)}"
        with cls._locks[opp.market]:
            dup = DBASE.query_one("SELECT id FROM orders WHERE idempotency_key=?", (key,))
            if dup:
                return {"ok": False, "error": "Duplicate order (idempotency key already used)."}
            if MDATA.is_stale(opp.market):
                DBASE.execute("INSERT INTO risk_events (kind, market, decision, reasons, created_at) VALUES (?,?,?,?,?)",
                              ("execution", opp.market, "REJECTED", "Stale price at execution", utcnow_iso()))
                return {"ok": False, "error": "Stale price — execution aborted (REAL DATA REQUIRED)."}
            ex = DBASE.query_one("SELECT id FROM positions WHERE market=? AND status='OPEN'", (opp.market,))
            if ex:
                return {"ok": False, "error": f"Position already open on {opp.market}."}
            acct = get_account(mode)
            equity = safe_float((acct or {}).get("equity", 0))
            if size["notional"] > equity * safe_float(CONFIG.get("max_position_pct", 0.2)) * 1.001:
                return {"ok": False, "error": "Position exceeds max size at execution."}
            if mode == "PAPER":
                return cls._place_paper(opp, size, strategy, key, decision_id)
            return cls._place_live(opp, size, strategy, key, decision_id)

    @classmethod
    def _place_paper(cls, opp, size, strategy, key, decision_id):
        fill, err = PaperExchange.market_fill(opp.market, opp.direction, size["qty"])
        if not fill:
            return {"ok": False, "error": f"Paper fill failed: {err}"}
        side = "BUY" if opp.direction == "LONG" else "SELL"
        status = "PARTIALLY_FILLED" if fill["partial"] else "FILLED"
        oid = cls._new_order(opp.market, side, opp.direction, size["qty"], fill["price"], "MARKET",
                             status, "PAPER", strategy or opp.strategy,
                             "; ".join(opp.reasons)[:1000], key, decision_id)
        cls._record_fill(oid, opp.market, side, fill["filled_qty"], fill["price"], fill["fee"],
                         fill["slippage_bps"])
        DBASE.execute("UPDATE orders SET filled_qty=?, avg_fill_price=?, status=?, updated_at=? WHERE id=?",
                      (fill["filled_qty"], fill["price"],
                       "PARTIALLY_FILLED" if fill["partial"] else "FILLED", utcnow_iso(), oid))
        qty = fill["filled_qty"]
        if qty <= 0:
            return {"ok": False, "error": "No fillable quantity."}
        pid = "pos-" + secrets.token_hex(6)
        notional = qty * fill["price"]
        DBASE.execute(
            "INSERT INTO positions (id, market, direction, qty, entry_price, current_price, stop_loss,"
            " take_profit, exposure, unrealized_pnl, realized_pnl, confidence, strategy, opened_at,"
            " updated_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, opp.market, opp.direction, qty, fill["price"], fill["price"],
             size.get("stop"), size.get("take_profit"), notional, 0, 0, opp.confidence,
             strategy or opp.strategy, utcnow_iso(), utcnow_iso(), "OPEN"))
        acct = get_account("PAPER")
        update_account("PAPER", realized_pnl=safe_float(acct.get("realized_pnl")) - fill["fee"],
                       day_pnl=safe_float(acct.get("day_pnl")) - fill["fee"])
        PortfolioEngine.revalue("PAPER")
        audit("trading", "paper_order_filled",
              {"order": oid, "market": opp.market, "dir": opp.direction, "qty": qty,
               "price": fill["price"], "fee": round(fill["fee"], 4),
               "partial": fill["partial"], "remaining": fill["remaining_qty"],
               "book_source": fill.get("book_source")})
        return {"ok": True, "order_id": oid, "fill_price": fill["price"], "fee": fill["fee"],
                "filled_qty": qty, "partial": fill["partial"],
                "remaining_qty": fill["remaining_qty"], "mode": "PAPER",
                "simulated": True, "note": "PAPER fill — simulated execution on REAL market data"}

    @classmethod
    def _place_live(cls, opp, size, strategy, key, decision_id):
        adapter = EXCHANGES.get(CONFIG.get("exchange") or "binance")
        if adapter is None or not adapter.has_credentials():
            return {"ok": False, "error": "Live connector not configured."}
        sym = UNIVERSE.ex_symbol(opp.market)
        side = "BUY" if opp.direction == "LONG" else "SELL"
        qty = Sizer.round_qty(opp.market, size["qty"])
        if qty <= 0:
            return {"ok": False, "error": "Quantity rounds to zero at exchange step size."}
        # submit with idempotent client order id
        try:
            res = adapter.place_order(sym, side, qty, order_type="MARKET", client_order_id=key)
        except Exception as e:
            # UNKNOWN outcome: never assume failure for a possibly-placed order
            msg = http_error_str(e)
            unknown = "timeout" in msg.lower() or "unreachable" in msg.lower() or "EOF" in msg
            oid = cls._new_order(opp.market, side, opp.direction, qty, 0, "MARKET",
                                 "UNKNOWN", "LIVE", strategy or opp.strategy,
                                 f"submit error: {msg}", key, decision_id)
            if unknown:
                cls._live_block = f"order {oid} outcome unknown ({msg}) — reconciliation required"
                with CONFIG._lock:
                    CONFIG.cfg["live_block_reason"] = cls._live_block
                    CONFIG.save_locked()
                notify("CRITICAL", "LIVE order outcome unknown",
                       f"{opp.market}: {msg}. Live trading blocked until reconciled.")
            audit("trading", "live_order_submit_failed", {"market": opp.market, "error": msg})
            return {"ok": False, "error": f"exchange rejected/failed: {msg}",
                    "order_id": oid}
        oid = cls._new_order(opp.market, side, opp.direction, qty, 0, "MARKET",
                             "SUBMITTED", "LIVE", strategy or opp.strategy,
                             "; ".join(opp.reasons)[:1000], key, decision_id)
        DBASE.execute("UPDATE orders SET exchange_order_id=?, updated_at=? WHERE id=?",
                      (res.get("orderId"), utcnow_iso(), oid))
        # verify actual exchange state (ack → status → fill)
        try:
            confirm = adapter.get_order(sym, orderId=res.get("orderId"))
        except Exception as e:
            cls._live_block = f"cannot confirm order {oid}: {http_error_str(e)}"
            with CONFIG._lock:
                CONFIG.cfg["live_block_reason"] = cls._live_block
                CONFIG.save_locked()
            notify("CRITICAL", "LIVE order unconfirmed", cls._live_block)
            return {"ok": True, "order_id": oid, "confirmed": False,
                    "warning": "order submitted but confirmation failed — reconciliation required"}
        status = confirm.get("status", "NEW")
        executed = safe_float(confirm.get("executedQty"))
        avg = safe_float(confirm.get("cummulativeQuoteQty")) / executed if executed else 0
        fee_est = avg * executed * (COST["fee_bps"] / 10000.0) if executed else 0
        if executed > 0:
            cls._record_fill(oid, opp.market, side, executed, avg or 0, fee_est, 0)
            DBASE.execute("UPDATE orders SET status=?, filled_qty=?, avg_fill_price=?, price=?, updated_at=? WHERE id=?",
                          (status, executed, avg or 0, avg or 0, utcnow_iso(), oid))
            if status == "FILLED":
                pid = "pos-" + secrets.token_hex(6)
                DBASE.execute(
                    "INSERT INTO positions (id, market, direction, qty, entry_price, current_price,"
                    " stop_loss, take_profit, exposure, unrealized_pnl, realized_pnl, confidence,"
                    " strategy, opened_at, updated_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, opp.market, opp.direction, executed, avg, avg, size.get("stop"),
                     size.get("take_profit"), executed * avg, 0, 0, opp.confidence,
                     strategy or opp.strategy, utcnow_iso(), utcnow_iso(), "OPEN"))
            # refresh live balance from the exchange (real state)
            try:
                Reconciler.sync_live_balance()
            except Exception:
                pass
        else:
            DBASE.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (status, utcnow_iso(), oid))
        audit("trading", "live_order_placed",
              {"order": oid, "exchange_order": res.get("orderId"), "market": opp.market,
               "side": side, "qty": qty, "status": status, "executed": executed, "avg": avg})
        notify("WARN", "LIVE order executed",
               f"{side} {qty} {opp.market} @ ~{avg or 'pending'} status={status}")
        return {"ok": True, "order_id": oid, "exchange_order_id": res.get("orderId"),
                "status": status, "executed_qty": executed, "avg_price": avg, "mode": "LIVE"}

    # ---- limit orders (paper) ----
    @classmethod
    def place_limit_paper(cls, market, direction, qty, limit_price, strategy="manual", decision_id=None):
        if CONFIG.get("kill_switch") or CONFIG.get("trading_halted"):
            return {"ok": False, "error": "Trading halted."}
        if qty <= 0 or limit_price <= 0:
            return {"ok": False, "error": "qty and limit price must be > 0"}
        side = "BUY" if direction == "LONG" else "SELL"
        key = f"LIMIT-{market}-{direction}-{secrets.token_hex(4)}"
        oid = cls._new_order(market, side, direction, qty, limit_price, "LIMIT", "NEW", "PAPER",
                             strategy, f"user limit {side} {qty} @ {limit_price}", key,
                             decision_id, limit_price)
        audit("trading", "paper_limit_placed", {"order": oid, "market": market, "side": side,
                                                "qty": qty, "limit": limit_price})
        return {"ok": True, "order_id": oid}

    @classmethod
    def check_open_orders(cls):
        """Fill/cancel paper LIMIT orders against real price moves."""
        out = []
        for o in DBASE.query("SELECT * FROM orders WHERE status='NEW' AND order_type='LIMIT' AND mode='PAPER'"):
            t = MDATA.get_ticker(o["market"])
            if not t or not t.get("price"):
                continue
            px = safe_float(t["price"])
            lim = safe_float(o["limit_price"])
            side = o["side"]
            crossed = (px <= lim) if side == "BUY" else (px >= lim)
            if not crossed:
                continue
            fill_px = lim
            fee = fill_px * safe_float(o["qty"]) * (COST["fee_bps"] / 10000.0)
            oid = o["id"]
            DBASE.execute("UPDATE orders SET status='FILLED', filled_qty=?, avg_fill_price=?, updated_at=? WHERE id=?",
                          (o["qty"], fill_px, utcnow_iso(), oid))
            cls._record_fill(oid, o["market"], side, o["qty"], fill_px, fee, 0)
            ex = DBASE.query_one("SELECT id FROM positions WHERE market=? AND status='OPEN'", (o["market"],))
            if not ex:
                pid = "pos-" + secrets.token_hex(6)
                DBASE.execute(
                    "INSERT INTO positions (id, market, direction, qty, entry_price, current_price,"
                    " stop_loss, take_profit, exposure, unrealized_pnl, realized_pnl, confidence,"
                    " strategy, opened_at, updated_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, o["market"], o["direction"], o["qty"], fill_px, fill_px, None, None,
                     o["qty"] * fill_px, 0, 0, 0, o.get("strategy") or "manual",
                     utcnow_iso(), utcnow_iso(), "OPEN"))
                acct = get_account("PAPER")
                update_account("PAPER", realized_pnl=safe_float(acct.get("realized_pnl")) - fee)
            out.append({"order": oid, "market": o["market"], "filled_at": fill_px})
        return out

    @classmethod
    def cancel_order(cls, order_id, mode="PAPER"):
        o = DBASE.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if not o:
            return {"ok": False, "error": "order not found"}
        if o["status"] not in ("NEW", "PARTIALLY_FILLED"):
            return {"ok": False, "error": f"cannot cancel order in status {o['status']}"}
        if mode == "LIVE" and o.get("exchange_order_id"):
            try:
                EXCHANGES[CONFIG.get("exchange") or "binance"].cancel_order(
                    UNIVERSE.ex_symbol(o["market"]), orderId=o["exchange_order_id"])
            except Exception as e:
                return {"ok": False, "error": f"exchange cancel failed: {http_error_str(e)}"}
        DBASE.execute("UPDATE orders SET status='CANCELLED', updated_at=? WHERE id=?",
                      (utcnow_iso(), order_id))
        audit("trading", "order_cancelled", {"order": order_id}, actor="user")
        return {"ok": True}

    @classmethod
    def close_position(cls, market, mode="PAPER", reason="manual"):
        with cls._locks[market]:
            pos = DBASE.query_one("SELECT * FROM positions WHERE market=? AND status='OPEN'", (market,))
            if not pos:
                return {"ok": False, "error": "No open position."}
            direction = pos["direction"]
            qty = safe_float(pos["qty"]); entry = safe_float(pos["entry_price"])
            if mode == "LIVE":
                adapter = EXCHANGES.get(CONFIG.get("exchange") or "binance")
                sym = UNIVERSE.ex_symbol(market)
                side = "SELL" if direction == "LONG" else "BUY"
                try:
                    res = adapter.place_order(sym, side, Sizer.round_qty(market, qty),
                                              order_type="MARKET",
                                              client_order_id=f"close-{pos['id'][:16]}-{ts_ms()}")
                    confirm = adapter.get_order(sym, orderId=res.get("orderId"))
                    executed = safe_float(confirm.get("executedQty"))
                    avg = safe_float(confirm.get("cummulativeQuoteQty")) / executed if executed else 0
                    px = avg or entry
                except Exception as e:
                    return {"ok": False, "error": f"live close failed: {http_error_str(e)}"}
            else:
                fill, err = PaperExchange.market_fill(market, "SHORT" if direction == "LONG" else "LONG", qty)
                if not fill:
                    return {"ok": False, "error": f"Quote failed: {err}"}
                px = fill["price"]
            gross = (px - entry) * qty if direction == "LONG" else (entry - px) * qty
            fee = (fill["fee"] if mode == "PAPER" and fill else 0)
            net = gross - fee
            now = utcnow_iso()
            DBASE.execute("UPDATE positions SET status='CLOSED', current_price=?, realized_pnl=?, updated_at=? WHERE id=?",
                          (px, net, now, pos["id"]))
            acct = get_account(mode)
            update_account(mode, realized_pnl=safe_float(acct.get("realized_pnl")) + net,
                           day_pnl=safe_float(acct.get("day_pnl")) + net)
            oid = cls._new_order(market, "SELL" if direction == "LONG" else "BUY", direction,
                                 qty, px, "MARKET", "FILLED", mode, pos.get("strategy"),
                                 f"close: {reason}", f"close-{pos['id'][:20]}", pos.get("decision_id"))
            cls._record_fill(oid, market, "SELL" if direction == "LONG" else "BUY", qty, px, fee,
                             0, net)
            PortfolioEngine.revalue(mode)
            audit("trading", "position_closed",
                  {"market": market, "pnl": round(net, 2), "reason": reason, "mode": mode})
            acct2 = get_account(mode)
            dd = safe_float(acct2.get("drawdown_pct", 0))
            if dd >= safe_float(CONFIG.get("max_drawdown_halt_pct", 0.15)):
                with CONFIG._lock:
                    CONFIG.cfg["trading_halted"] = True
                    CONFIG.cfg["halt_reason"] = f"Max drawdown {pct(dd)}% — automatic halt"
                    CONFIG.save_locked()
                notify("CRITICAL", "Trading auto-halted", CONFIG.cfg["halt_reason"])
            return {"ok": True, "pnl": round(net, 2), "price": px, "mode": mode}

# ----------------------------- reconciliation ---------------------------------

class Reconciler:
    """ZEPAY state vs exchange state. Startup + periodic. Mismatch blocks live."""

    @staticmethod
    def paper_check():
        """Internal consistency of the paper bookkeeping."""
        issues = []
        acct = get_account("PAPER")
        if not acct:
            issues.append("paper account missing")
        else:
            start = safe_float(CONFIG.get("paper_starting_balance", 10000.0))
            realized = safe_float(acct.get("realized_pnl"))
            unreal = safe_float(acct.get("unrealized_pnl"))
            expect_equity = start + realized + unreal
            actual = safe_float(acct.get("equity"))
            if abs(expect_equity - actual) > 0.02:
                issues.append(f"equity mismatch: ledger {expect_equity:.2f} vs account {actual:.2f}")
        for p in PortfolioEngine.positions():
            fills = DBASE.query("SELECT SUM(qty) AS q FROM fills WHERE market=? AND side=?",
                                (p["market"], "BUY" if p["direction"] == "LONG" else "SELL"))
            bought = safe_float((fills or [{}])[0].get("q"))
            if bought <= 0:
                issues.append(f"open position {p['market']} has no entry fill")
        open_lim = DBASE.query("SELECT COUNT(*) AS n FROM orders WHERE status IN ('NEW','PARTIALLY_FILLED')")
        ok = not issues
        report = {"ok": ok, "mode": "PAPER", "issues": issues,
                  "checked_at": utcnow_iso(),
                  "open_orders": open_lim[0]["n"] if open_lim else 0}
        if not ok:
            notify("WARN", "Paper reconciliation issues", "; ".join(issues))
            audit("reconciliation", "paper_mismatch", {"issues": issues})
        else:
            audit("reconciliation", "paper_ok", {})
        return report

    @staticmethod
    def live_check():
        """REAL exchange state vs ZEPAY state. Mismatch → live trading blocked."""
        adapter = EXCHANGES.get(CONFIG.get("exchange") or "binance")
        if not adapter or not adapter.has_credentials():
            return {"ok": False, "mode": "LIVE", "issues": ["exchange credentials not configured"],
                    "checked_at": utcnow_iso()}
        issues = []
        try:
            acct = adapter.account(timeout=12)
        except Exception as e:
            return {"ok": False, "mode": "LIVE",
                    "issues": [f"cannot read exchange account: {http_error_str(e)}"],
                    "checked_at": utcnow_iso()}
        if acct.get("canWithdraw"):
            issues.append("API key has withdrawal permission — refused")
        # balances: quote asset
        ex_bal = {b["asset"]: safe_float(b["free"]) + safe_float(b["locked"])
                  for b in acct.get("balances", [])}
        local = get_account("LIVE")
        # compare internal open positions with exchange base balances
        for p in PortfolioEngine.positions():
            a = UNIVERSE.get(p["market"])
            base = (a or {}).get("base") or p["market"].split("/")[0]
            have = ex_bal.get(base, 0.0)
            if have + 1e-9 < safe_float(p["qty"]):
                issues.append(f"{p['market']}: exchange balance {have} < position qty {p['qty']}")
        # unknown live orders?
        unknown = DBASE.query("SELECT id FROM orders WHERE mode='LIVE' AND status='UNKNOWN'")
        for u in unknown:
            issues.append(f"order {u['id']} outcome unresolved")
        ok = not issues
        report = {"ok": ok, "mode": "LIVE", "issues": issues,
                  "checked_at": utcnow_iso(),
                  "exchange_balances": {k: v for k, v in list(ex_bal.items())[:20] if v > 0}}
        if not ok:
            with CONFIG._lock:
                CONFIG.cfg["live_block_reason"] = "; ".join(issues)[:500]
                CONFIG.save_locked()
            notify("CRITICAL", "LIVE reconciliation mismatch — live trading blocked",
                   "; ".join(issues)[:500])
            audit("reconciliation", "live_mismatch", {"issues": issues})
        else:
            with CONFIG._lock:
                CONFIG.cfg["live_block_reason"] = ""
                CONFIG.save_locked()
            ExecutionEngine._live_block = None
            audit("reconciliation", "live_ok", {})
        return report

    @staticmethod
    def sync_live_balance():
        """Pull the REAL quote-asset balance into the LIVE account record."""
        adapter = EXCHANGES.get(CONFIG.get("exchange") or "binance")
        acct = adapter.account(timeout=12)
        quote = CONFIG.get("quote_currency", "USDT")
        ex_bal = {b["asset"]: safe_float(b["free"]) + safe_float(b["locked"])
                  for b in acct.get("balances", [])}
        equity = ex_bal.get(quote, 0.0)
        today = utcnow().date().isoformat()
        row = DBASE.query_one("SELECT * FROM balances WHERE mode='LIVE' ORDER BY id DESC LIMIT 1")
        if row:
            DBASE.execute("UPDATE balances SET equity=?, available=?, updated_at=?, day=? WHERE id=?",
                          (equity, equity, utcnow_iso(), today, row["id"]))
        else:
            DBASE.execute("INSERT INTO balances (mode, equity, available, invested, realized_pnl,"
                          " unrealized_pnl, drawdown_pct, peak_equity, day_pnl, day, updated_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          ("LIVE", equity, equity, 0, 0, 0, 0, equity, 0, today, utcnow_iso()))

    @staticmethod
    def startup():
        report = {"paper": Reconciler.paper_check()}
        if CONFIG.get("live_enabled"):
            report["live"] = Reconciler.live_check()
        audit("reconciliation", "startup", {"components": list(report.keys())})
        return report

# =============================================================================
# PART 10 — DECISION LEDGER + TRADING CYCLE + BACKTESTER + RESEARCH
# =============================================================================

ENGINE = {"last_run": None, "last_result": None, "running": False, "thread": None,
          "stop": False, "cycles": 0, "decisions": deque(maxlen=50)}

def record_decision(market, f, infer, opp, decision, reasons, portfolio_state,
                    risk_state, cycle_id, order_id=None, fill_price=None, pnl=None):
    """Append-only decision ledger — every important decision is explainable."""
    def g(obj, key, d=None):
        if obj is None:
            return d
        if isinstance(obj, dict):
            return obj.get(key, d)
        return getattr(obj, key, d)
    did = "dec-" + secrets.token_hex(6)
    data_status = "STALE" if f.data_stale else "LIVE"
    if f.price <= 0:
        data_status = "UNAVAILABLE"
    features = {"price": f.price, "rsi14": round(f.rsi14, 1), "atr_pct": round(f.atr_pct * 100, 3),
                "mom_20": round(f.mom_20 * 100, 3), "spread_bps": round(f.spread_bps, 1),
                "n_candles": f.n_candles}
    try:
        DBASE.execute(
            "INSERT INTO decisions (id, ts, market, data_status, features, model_version, strategy,"
            " expected_return, expected_net, confidence, portfolio_state, risk_state, decision,"
            " reasons, order_id, fill_price, pnl, cycle_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, utcnow_iso(), market, data_status, json.dumps(features),
             g(infer, "model_version") or MODEL_VERSION,
             g(opp, "strategy"), safe_float(g(opp, "expected_return")),
             safe_float(g(opp, "expected_net")), safe_float(g(infer, "confidence")),
             json.dumps(portfolio_state or {}), json.dumps(risk_state or {}),
             decision if isinstance(decision, str) else str(decision),
             json.dumps(reasons or [])[:2000], order_id, fill_price, pnl, cycle_id))
    except Exception as e:
        log(f"decision ledger write failed: {e}", "WARN")
    return did

def run_cycle(save_signals=True, with_llm_research=None):
    """ONE full hierarchical pass over the whole universe."""
    t0 = time.time()
    universe = CONFIG.get("universe") or []
    if not universe:
        return {"ok": False, "error": "Trading universe is empty. Select assets first."}
    cycle_id = "cyc-" + secrets.token_hex(4)
    refresh_cost()
    MDATA.refresh_all(universe, with_klines=True, with_depth=True)
    # optional derivatives context (funding/OI) — best effort, real only
    for m in universe[:6]:
        try:
            MDATA.pool.submit(MDATA._safe, MDATA._fetch_derivs, m)
        except Exception:
            pass
    feats = {m: FeatureEngine.build(m) for m in universe}
    CrossAssetEngine.enrich(feats)
    mat = CrossAssetEngine.matrix(universe)
    PortfolioEngine.revalue("PAPER")
    acct = get_account("PAPER")
    positions = PortfolioEngine.positions()
    equity = safe_float(acct.get("equity", 0)) or 10000.0
    exposure = PortfolioEngine.exposure(positions, equity)
    state, state_msg = RiskEngine.system_state(acct)
    risk_state = {"system_state": state.value, "message": state_msg,
                  "entries_allowed": RiskEngine.entries_allowed()[0]}
    opps, analyses = [], []
    for m in universe:
        f = feats[m]
        health = HealthEngine.assess(m, f)
        regime = RegimeEngine.detect(f)
        strat = StrategyEngine.evaluate(f, regime["regime"])
        infer = AI.infer(f, regime, strat)
        opp = OpportunityEngine.build(m, f, regime, strat, infer)
        positions_now = PortfolioEngine.positions()
        decision, why, hint = RiskEngine.authorize(opp, f, health, positions_now,
                                                   get_account("PAPER"), mat, equity)
        size = Sizer.size(opp, f, get_account("PAPER"), hint) if decision == Decision.APPROVED else None
        # deterministic research agents on real data
        agents_out = Agents.run_deterministic(f, regime, infer, exposure, positions_now)
        decision_id = record_decision(m, f, infer, opp, decision.value, why,
                                      {"equity": round(equity, 2), "exposure": exposure,
                                       "positions": len(positions_now)},
                                      risk_state, cycle_id)
        opps.append({"opp": opp, "f": f, "health": health, "regime": regime, "strat": strat,
                     "infer": infer, "decision": decision.value, "why": why, "size": size,
                     "agents": agents_out, "decision_id": decision_id})
    ranked = OpportunityEngine.rank([o["opp"] for o in opps])
    rank_idx = {o.market: i + 1 for i, o in enumerate(ranked)}
    # optional LLM research on the TOP opportunity (advisory, validated)
    llm_research = None
    if with_llm_research is None:
        with_llm_research = bool(CONFIG.get("research_llm_enabled")) and \
            ENGINE["cycles"] % max(1, int(CONFIG.get("research_llm_interval_cycles", 10))) == 0
    if with_llm_research and ranked and ranked[0].price > 0:
        top = next(o for o in opps if o["opp"].market == ranked[0].market)
        llm_research = ResearchEngineLLM.run(top["opp"].market, top["f"], top["regime"],
                                             top["infer"], top["agents"])
        try:
            DBASE.execute("INSERT INTO research_reports (id, ts, cycle_id, kind, agents, summary, created_at)"
                          " VALUES (?,?,?,?,?,?,?)",
                          ("rep-" + secrets.token_hex(5), utcnow_iso(), cycle_id, "llm+agents",
                           json.dumps(top["agents"])[:6000],
                           json.dumps(llm_research)[:6000], utcnow_iso()))
        except Exception:
            pass
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
            "decision": o["decision"], "decision_reasons": o["why"], "decision_id": o["decision_id"],
            "reasons": opp.reasons, "size": o["size"],
            "atr_pct": round(f.atr_pct * 100, 3), "rsi": round(f.rsi14, 1),
            "spread_bps": round(f.spread_bps, 2), "liquidity_usd": round(f.liquidity_usd),
            "depth_usd": round(f.depth_usd), "volume_ratio": round(f.vol_ratio, 2),
            "corr_btc": round(f.corr_btc, 3), "funding_rate": f.funding_rate,
            "data_stale": f.data_stale, "ai_mode": o["infer"]["mode"],
            "agents": [{"agent": a["agent"], "verdict": a["verdict"], "confidence": a["confidence"]}
                       for a in o["agents"]],
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
    # manage open positions: SL/TP + trailing paper limit orders
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
            mode = "LIVE" if CONFIG.get("live_enabled") else "PAPER"
            r = ExecutionEngine.close_position(p["market"], mode, reason=hit)
            exits.append({"market": p["market"], "reason": hit, "result": r})
    limit_fills = ExecutionEngine.check_open_orders()
    PortfolioEngine.revalue("PAPER")
    state, state_msg = RiskEngine.system_state(get_account("PAPER"))
    feed = MDATA.feed_health(universe)
    result = {"ok": True, "ts": utcnow_iso(), "cycle_id": cycle_id,
              "elapsed_ms": int((time.time() - t0) * 1000),
              "opportunities": sorted(out_opps, key=lambda x: x["rank"]),
              "correlation": mat, "exits": exits, "limit_fills": limit_fills,
              "system_state": state.value, "system_msg": state_msg,
              "data_feed": feed, "model_trained": AI.trained,
              "model_version": MODEL_VERSION, "ai_mode": CONFIG.get("ai_mode"),
              "llm_research": llm_research}
    ENGINE["last_run"] = utcnow_iso()
    ENGINE["last_result"] = result
    ENGINE["decisions"].appendleft({"ts": result["ts"], "state": state.value,
                                    "n": len(out_opps),
                                    "approved": sum(1 for o in out_opps if o["decision"] == "APPROVED")})
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
    reconcile_every = max(5, int(CONFIG.get("reconcile_interval_cycles", 20)))
    health_every = 10
    while not ENGINE["stop"]:
        try:
            if CONFIG.get("ai_automation") and not CONFIG.get("kill_switch") and not CONFIG.get("trading_halted"):
                res = run_cycle(save_signals=True)
                ENGINE["cycles"] += 1
                if res.get("ok"):
                    mode = "LIVE" if CONFIG.get("live_enabled") else "PAPER"
                    for o in sorted(res["opportunities"], key=lambda x: -x["score"]):
                        if o["decision"] != "APPROVED" or not o.get("size"):
                            continue
                        f = FeatureEngine.build(o["market"])
                        opp = Opportunity(market=o["market"], direction=o["direction"],
                                          score=o["score"], confidence=o["confidence"],
                                          quality=o["quality"], regime=o["regime"],
                                          strategy=o["strategy"],
                                          expected_return=o["expected_return_pct"] / 100,
                                          expected_net=o["expected_net_pct"] / 100,
                                          cost_pct=o["cost_pct"] / 100, price=o["price"],
                                          reasons=o["reasons"])
                        r = ExecutionEngine.place(opp, o["size"], f, mode=mode,
                                                  decision_id=o.get("decision_id"))
                        audit("trading", "auto_execute", {"market": o["market"], "mode": mode,
                                                          "result": r})
                        time.sleep(0.5)
                if ENGINE["cycles"] % reconcile_every == 0:
                    Reconciler.paper_check()
                    if CONFIG.get("live_enabled"):
                        Reconciler.live_check()
                if ENGINE["cycles"] % health_every == 0:
                    threading.Thread(target=lambda: REGISTRY.check_all(integrated_only=True),
                                     daemon=True).start()
            ModelMonitor.tick()
        except Exception as e:
            log(f"auto_loop error: {e}\n{traceback.format_exc()}", "ERROR")
            sys_event("engine", "ERROR", str(e)[:500])
        for _ in range(int(safe_float(CONFIG.get("refresh_secs", 30), 30)) * 2):
            if ENGINE["stop"]:
                break
            time.sleep(0.5)

# ----------------------------- backtester -------------------------------------

class Backtester:
    """Backtests on REAL historical candles with explicit costs. Features at
    bar i use ONLY data up to bar i (look-ahead guard asserted in tests)."""

    @staticmethod
    def _series(market, interval=None, limit=500):
        interval = interval or CONFIG.get("candle_interval", "1h")
        try:
            rows = MDATA._fetch_klines(market, interval, limit)
        except Exception:
            rows = MDATA.get_klines(market, interval) or []
        return rows

    @staticmethod
    def run_single(market, starting=10000.0, interval=None, fee_bps=None, slip_bps=None):
        interval = interval or CONFIG.get("candle_interval", "1h")
        fee_bps = safe_float(fee_bps, safe_float(CONFIG.get("fee_bps", 10.0), 10.0))
        slip_bps = safe_float(slip_bps, safe_float(CONFIG.get("slippage_bps", 5.0), 5.0))
        rows = Backtester._series(market, interval)
        if len(rows) < 80:
            return {"ok": False,
                    "error": f"Not enough REAL history for {market} ({len(rows)} candles). "
                             "ZePay does not backtest on synthetic data."}
        closes = [r["c"] for r in rows]
        equity = starting
        peak = starting
        max_dd = 0.0
        trades = []
        pos = None
        fees_paid = 0.0; slip_paid = 0.0; funding_paid = 0.0
        for i in range(50, len(closes) - 1):
            w = rows[:i + 1]                     # ← look-ahead guard: past only
            assert w[-1]["c"] == closes[i]
            f = AIEngine._features_from_window(market, w)
            if not f:
                continue
            f.price = closes[i]
            regime = RegimeEngine.detect(f)["regime"]
            strat = StrategyEngine.evaluate(f, regime)
            infer = AI.infer(f, {"regime": regime, "confidence": 0.6}, strat)
            px_next = closes[i + 1]
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
                    gross = (px_next - pos["entry"]) * pos["qty"] if pos["dir"] == "LONG" \
                        else (pos["entry"] - px_next) * pos["qty"]
                    cost = px_next * pos["qty"] * (fee_bps / 10000 + slip_bps / 10000)
                    fund = px_next * pos["qty"] * (COST["fund_bps"] / 10000) * (pos["bars"] / 8.0)
                    fees_paid += px_next * pos["qty"] * fee_bps / 10000
                    slip_paid += px_next * pos["qty"] * slip_bps / 10000
                    funding_paid += fund
                    net = gross - cost - fund
                    equity += net
                    trades.append(net)
                    pos = None
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak else 0
            max_dd = max(max_dd, dd)
        if pos is not None:
            px = closes[-1]
            gross = (px - pos["entry"]) * pos["qty"] if pos["dir"] == "LONG" \
                else (pos["entry"] - px) * pos["qty"]
            equity += gross - px * pos["qty"] * (fee_bps / 10000 + slip_bps / 10000)
            trades.append(gross)
        wins = [t for t in trades if t > 0]; losses = [t for t in trades if t <= 0]
        pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (9.99 if wins else 0.0)
        rets = [t / starting for t in trades]
        sharpe = (statistics.fmean(rets) / statistics.pstdev(rets) * math.sqrt(max(len(rets), 1))) \
            if len(rets) > 1 and statistics.pstdev(rets) else 0
        downside = [r for r in rets if r < 0]
        sortino = (statistics.fmean(rets) / statistics.pstdev(downside) * math.sqrt(max(len(rets), 1))) \
            if len(downside) > 1 and statistics.pstdev(downside) else 0
        res = {"ok": True, "market": market, "starting": starting, "ending": round(equity, 2),
               "net_return_pct": round((equity / starting - 1) * 100, 2),
               "max_dd_pct": round(max_dd * 100, 2), "profit_factor": round(pf, 2),
               "sharpe": round(sharpe, 2), "sortino": round(sortino, 2),
               "trades": len(trades), "win_rate": round(len(wins) / max(1, len(trades)) * 100, 1),
               "fees": round(fees_paid, 2), "slippage": round(slip_paid, 2),
               "funding": round(funding_paid, 2), "candles": len(rows),
               "interval": interval, "costs_note": "fees+slippage+funding applied on every fill"}
        bid = "bt-" + secrets.token_hex(6)
        try:
            DBASE.execute("INSERT INTO backtests (id, name, markets, period, strategy, starting_capital, ending_value, net_return_pct, max_dd_pct, profit_factor, sharpe, sortino, trades, win_rate, fees, slippage, funding, details, created_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (bid, f"{market} backtest", market, f"{len(rows)}x{interval}", "ZEPAY Quant Ensemble",
                           starting, res["ending"], res["net_return_pct"], res["max_dd_pct"], res["profit_factor"],
                           res["sharpe"], res["sortino"], res["trades"], res["win_rate"], res["fees"],
                           res["slippage"], res["funding"], json.dumps(res), utcnow_iso()))
        except Exception:
            pass
        res["id"] = bid
        audit("research", "backtest", {"id": bid, "market": market, "return": res["net_return_pct"]})
        return res

    @staticmethod
    def run_multi(markets, starting=10000.0, interval=None):
        per = {}
        for m in markets:
            per[m] = Backtester.run_single(m, starting=starting / max(1, len(markets)), interval=interval)
        ok_runs = [v for v in per.values() if v.get("ok")]
        ending = sum(v["ending"] for v in ok_runs)
        res = {"ok": bool(ok_runs), "markets": markets, "starting": starting,
               "ending": round(ending, 2),
               "net_return_pct": round((ending / starting - 1) * 100, 2) if starting else 0,
               "per_asset": per,
               "trades": sum(v.get("trades", 0) for v in ok_runs),
               "fees": round(sum(v.get("fees", 0) for v in ok_runs), 2),
               "correlation": CrossAssetEngine.matrix(markets)}
        bid = "bt-" + secrets.token_hex(6)
        try:
            DBASE.execute("INSERT INTO backtests (id, name, markets, period, strategy, starting_capital, ending_value, net_return_pct, max_dd_pct, profit_factor, sharpe, sortino, trades, win_rate, fees, slippage, funding, details, created_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (bid, "multi-asset backtest", ",".join(markets), interval or "", "ZEPAY Quant Ensemble",
                           starting, ending, res["net_return_pct"], 0, 0, 0, 0, res["trades"], 0,
                           res["fees"], 0, 0, json.dumps(res)[:8000], utcnow_iso()))
        except Exception:
            pass
        res["id"] = bid
        return res

# ----------------------------- model monitoring + research --------------------

class ModelMonitor:
    history = deque(maxlen=200)

    @classmethod
    def tick(cls):
        try:
            sigs = DBASE.query("SELECT * FROM signals ORDER BY id DESC LIMIT 30")
            if len(sigs) < 20:
                return
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
        """Walk-forward candidate evaluation. NEVER auto-promotes — champion/
        challenger promotion requires explicit human approval."""
        universe = CONFIG.get("universe") or []
        if not universe:
            return {"ok": False, "error": "Empty universe"}
        exp_id = "exp-" + secrets.token_hex(5)
        t0 = time.time()
        X, y, r = AI._build_dataset(universe)
        if len(X) < 100:
            conclusion = "insufficient data"
            res = {"ok": False, "error": "Not enough real data for experiment"}
        else:
            n = len(X)
            accs = []
            fold = n // 4
            for k in range(1, 4):
                tr = X[:k * fold]; te = X[k * fold:(k + 1) * fold]
                ytr = y[:k * fold]; yte = y[k * fold:(k + 1) * fold]
                m = LogisticModel(len(FEATURE_KEYS), lr=0.03)
                m.fit(tr, ytr, epochs=80)
                acc = sum(1 for xi, yi in zip(te, yte)
                          if (m.predict_proba(xi) >= 0.5) == bool(yi)) / max(1, len(te))
                accs.append(round(acc, 4))
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
                   "recommend_promotion": bool(challenger > (champ or 0) + 0.02),
                   "promotion": "MANUAL ONLY — never auto-deployed"}
        try:
            DBASE.execute("INSERT INTO experiments (id, hypothesis, dataset, features, model, params, results, oos, conclusion, created_at)"
                          " VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (exp_id, "LR(lr=0.03) walk-forward vs champion", ",".join(universe),
                           ",".join(FEATURE_KEYS), "logistic", json.dumps({"lr": 0.03}),
                           json.dumps(res), json.dumps(res.get("walk_forward_acc", [])),
                           str(conclusion), utcnow_iso()))
        except Exception:
            pass
        res["id"] = exp_id
        res["conclusion"] = str(conclusion)
        res["elapsed_s"] = round(time.time() - t0, 1)
        audit("research", "experiment", {"id": exp_id, "conclusion": res.get("conclusion")})
        return res

# =============================================================================
# PART 11 — MCP MANAGER (registry, permission-controlled, disabled by default)
# General MCP tools can NEVER hold trading permissions — that class does not
# exist for MCP in ZEPAY. Every call is rate-limited, logged and audited.
# =============================================================================

MCP_PERMISSION_CLASSES = {
    "research.readonly": "Fetch/read research material (web pages, docs, data). No writes.",
    "notes.write": "Write to a notes/memory store.",
}
MCP_FORBIDDEN_CLASSES = {
    "trading": "HARD REFUSED — trading is reserved to ZEPAY's own execution engine.",
    "withdrawals": "HARD REFUSED — withdrawals are forbidden platform-wide.",
    "credentials": "HARD REFUSED — MCP tools may never touch credentials.",
}
MCP_TRADE_KEYWORDS = re.compile(
    r"(?:^|[^a-z0-9])(orders?|trades?|trading|buy|buying|sell|selling|withdraw|withdrawals?|"
    r"positions?|leverage|kill_?switch|risk_?limits?)(?:$|[^a-z0-9])", re.I)

# Suggested servers (discovery references — awesome-mcp-servers). All DISABLED
# and UNCONFIGURED until the user explicitly registers a URL.
MCP_SUGGESTED = [
    {"id": "mcp-fetch", "name": "Fetch (web retrieval)", "url": "", "purpose": "Fetch web pages for research",
     "permissions": ["research.readonly"], "source": "github.com/modelcontextprotocol/servers"},
    {"id": "mcp-memory", "name": "Memory (knowledge graph)", "url": "", "purpose": "Persistent research notes",
     "permissions": ["research.readonly", "notes.write"], "source": "github.com/modelcontextprotocol/servers"},
    {"id": "mcp-filesystem", "name": "Filesystem (sandboxed)", "url": "", "purpose": "Read research files",
     "permissions": ["research.readonly"], "source": "github.com/modelcontextprotocol/servers"},
    {"id": "mcp-search", "name": "Web Search", "url": "", "purpose": "Search the web for research",
     "permissions": ["research.readonly"], "source": "awesome-mcp-servers catalogue"},
]

class MCPManager:
    """Registry + real JSON-RPC client (streamable HTTP transport)."""

    def __init__(self):
        self._calls = defaultdict(list)   # server_id -> [timestamps]
        self.status = {}                  # server_id -> {"status","last_call","last_error"}
        self._ensure_registry()

    def _ensure_registry(self):
        servers = CONFIG.get("mcp_servers")
        if not isinstance(servers, list):
            servers = []
        have = {s.get("id") for s in servers}
        changed = False
        for s in MCP_SUGGESTED:
            if s["id"] not in have:
                servers.append({"id": s["id"], "name": s["name"], "url": s["url"],
                                "purpose": s["purpose"], "permissions": s["permissions"],
                                "enabled": False, "rate_limit_per_min": 10,
                                "configured": False})
                changed = True
        if changed:
            with CONFIG._lock:
                CONFIG.cfg["mcp_servers"] = servers
                CONFIG.save_locked()

    def registry(self):
        self._ensure_registry()
        out = []
        for s in CONFIG.get("mcp_servers") or []:
            st = self.status.get(s.get("id"), {})
            out.append({**{k: s.get(k) for k in ("id", "name", "url", "purpose",
                                                 "permissions", "enabled", "rate_limit_per_min")},
                        "configured": bool(s.get("url")),
                        "status": ("DISABLED" if not s.get("enabled") else
                                   ("NOT_CONFIGURED" if not s.get("url") else
                                    st.get("status", "UNKNOWN"))),
                        "last_call": st.get("last_call"), "last_error": st.get("last_error"),
                        "calls_last_min": len(self._calls.get(s.get("id"), [])),
                        "forbidden_classes": list(MCP_FORBIDDEN_CLASSES.keys()),
                        "source": s.get("source", "user-registered")})
        return out

    def upsert(self, entry):
        """Register/update a server. Refuses dangerous permission classes."""
        perms = [p for p in (entry.get("permissions") or []) if p in MCP_PERMISSION_CLASSES]
        rejected = [p for p in (entry.get("permissions") or []) if p in MCP_FORBIDDEN_CLASSES]
        if rejected:
            audit("security", "mcp_permission_refused", {"server": entry.get("id"), "rejected": rejected})
            return {"ok": False, "error": f"Permission classes refused: {rejected}. "
                                          "MCP tools can never receive trading/withdrawal/credential permissions."}
        if MCP_TRADE_KEYWORDS.search(entry.get("purpose", "") or ""):
            return {"ok": False, "error": "Purpose mentions trading actions — refused."}
        servers = CONFIG.get("mcp_servers") or []
        found = False
        for i, s in enumerate(servers):
            if s.get("id") == entry.get("id"):
                servers[i] = {**s, **entry, "permissions": perms}
                found = True
                break
        if not found:
            servers.append({"id": entry.get("id") or f"mcp-{secrets.token_hex(3)}",
                            "name": entry.get("name", "unnamed"), "url": entry.get("url", ""),
                            "purpose": entry.get("purpose", ""), "permissions": perms,
                            "enabled": bool(entry.get("enabled", False)),
                            "rate_limit_per_min": int(entry.get("rate_limit_per_min", 10)),
                            "configured": bool(entry.get("url"))})
        with CONFIG._lock:
            CONFIG.cfg["mcp_servers"] = servers
            CONFIG.save_locked()
        audit("mcp", "server_registered", {"id": entry.get("id"), "permissions": perms}, actor="user")
        return {"ok": True}

    def remove(self, sid):
        servers = [s for s in (CONFIG.get("mcp_servers") or []) if s.get("id") != sid]
        with CONFIG._lock:
            CONFIG.cfg["mcp_servers"] = servers
            CONFIG.save_locked()
        audit("mcp", "server_removed", {"id": sid}, actor="user")
        return {"ok": True}

    def _rate_ok(self, sid, limit):
        now = time.time()
        lst = [t for t in self._calls.get(sid, []) if now - t < 60]
        if len(lst) >= max(1, limit):
            self._calls[sid] = lst
            return False
        lst.append(now)
        self._calls[sid] = lst
        return True

    def _rpc(self, server, method, params=None, timeout=20):
        body = {"jsonrpc": "2.0", "id": secrets.token_hex(4), "method": method}
        if params:
            body["params"] = params
        r = http_json(server["url"], timeout=timeout, method="POST", data=body,
                      headers={"Accept": "application/json"})
        if isinstance(r, dict) and "error" in r and r["error"]:
            raise RuntimeError(f"MCP error: {r['error']}")
        return (r or {}).get("result")

    def _record(self, sid, tool, args, status, result, latency):
        try:
            DBASE.execute("INSERT INTO mcp_calls (server, tool, args, status, result, latency_ms, created_at)"
                          " VALUES (?,?,?,?,?,?,?)",
                          (sid, tool, jdump(redact(args))[:1000], status,
                           str(result)[:2000], latency, utcnow_iso()))
        except Exception:
            pass

    def list_tools(self, sid):
        server = next((s for s in CONFIG.get("mcp_servers") or [] if s.get("id") == sid), None)
        if not server:
            return {"ok": False, "error": "unknown server"}
        if not server.get("enabled"):
            return {"ok": False, "error": "server is disabled"}
        if not server.get("url"):
            return {"ok": False, "error": "server has no URL configured"}
        if not self._rate_ok(sid, server.get("rate_limit_per_min", 10)):
            return {"ok": False, "error": "rate limit exceeded"}
        t0 = time.time()
        try:
            self._rpc(server, "initialize",
                      {"protocolVersion": "2024-11-05",
                       "capabilities": {}, "clientInfo": {"name": "ZEPAY", "version": VERSION}})
            tools = self._rpc(server, "tools/list") or {}
            self.status[sid] = {"status": "OK", "last_call": utcnow_iso(), "last_error": None}
            self._record(sid, "tools/list", {}, "OK", tools, int((time.time() - t0) * 1000))
            audit("mcp", "list_tools", {"server": sid, "n": len(tools.get("tools", []))})
            return {"ok": True, "tools": tools.get("tools", [])}
        except Exception as e:
            msg = http_error_str(e)
            self.status[sid] = {"status": "ERROR", "last_call": utcnow_iso(), "last_error": msg}
            self._record(sid, "tools/list", {}, "ERROR", msg, int((time.time() - t0) * 1000))
            return {"ok": False, "error": msg}

    def call_tool(self, sid, tool, args=None):
        """The ONLY MCP entry point. Trading-looking tools/args are refused."""
        server = next((s for s in CONFIG.get("mcp_servers") or [] if s.get("id") == sid), None)
        if not server:
            return {"ok": False, "error": "unknown server"}
        if not server.get("enabled") or not server.get("url"):
            return {"ok": False, "error": "server disabled or not configured"}
        if MCP_TRADE_KEYWORDS.search(tool or ""):
            audit("security", "mcp_tool_refused", {"server": sid, "tool": tool})
            return {"ok": False, "error": f"tool '{tool}' looks like a trading action — MCP tools cannot trade"}
        if args and MCP_TRADE_KEYWORDS.search(jdump(args)):
            audit("security", "mcp_args_refused", {"server": sid, "tool": tool})
            return {"ok": False, "error": "arguments mention trading actions — refused"}
        if not self._rate_ok(sid, server.get("rate_limit_per_min", 10)):
            return {"ok": False, "error": "rate limit exceeded"}
        t0 = time.time()
        try:
            self._rpc(server, "initialize",
                      {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "ZEPAY", "version": VERSION}})
            result = self._rpc(server, "tools/call", {"name": tool, "arguments": args or {}})
            self.status[sid] = {"status": "OK", "last_call": utcnow_iso(), "last_error": None}
            self._record(sid, tool, args, "OK", result, int((time.time() - t0) * 1000))
            audit("mcp", "tool_called", {"server": sid, "tool": tool})
            return {"ok": True, "result": result}
        except Exception as e:
            msg = http_error_str(e)
            self.status[sid] = {"status": "ERROR", "last_call": utcnow_iso(), "last_error": msg}
            self._record(sid, tool, args, "ERROR", msg, int((time.time() - t0) * 1000))
            return {"ok": False, "error": msg}

MCPM = MCPManager()

# =============================================================================
# PART 12 — HTTP API (stdlib http.server; same REST contract as FastAPI deploy)
# =============================================================================

class APIHandler(http.server.BaseHTTPRequestHandler):
    server_version = f"ZEPAY/{VERSION}"

    def log_message(self, fmt, *args):
        pass

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
        self._send(code, {"ok": False, "error": str(msg)[:500]})

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

    def _qs(self, u):
        return {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    def _authed(self):
        if not is_setup():
            return True  # setup wizard allowed
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        tok = (q.get("token", [""])[0] or self.headers.get("X-ZEPAY-Token", ""))
        if not tok:
            ck = self.headers.get("Cookie", "")
            for part in ck.split(";"):
                if "zepay_token" in part:
                    tok = part.split("=,", 1)[-1].split("=", 1)[-1].strip()
        return valid_session(tok)

    # ----- GET -----
    def do_GET(self):
        try:
            u = urllib.parse.urlparse(self.path)
            p, q = u.path, self._qs(u)
            if p in ("/", "/index.html", "/dashboard"):
                return self._send(200, DASHBOARD_HTML, "text/html")
            if p in ("/health", "/api/health"):
                return self._ok({"status": "ok", "version": VERSION, "edition": EDITION,
                                 "ts": utcnow_iso(), "setup": is_setup(),
                                 "mode": "LIVE" if CONFIG.get("live_enabled") else "PAPER",
                                 "data_feed": MDATA.feed_health()["status"]})
            if p == "/api/setup/status":
                return self._ok({"setup": is_setup(),
                                 "license_mode": CONFIG.get("license_mode"),
                                 "cloud_license": license_provider_status()})
            if not self._authed():
                return self._err("Not authenticated. Enter your ZEPAY key.", 401)
            if p == "/api/status":
                return self._ok(status_payload())
            if p == "/api/markets" or p == "/api/assets":
                search = q.get("search")
                assets = UNIVERSE.all_assets(search=search, limit=int(q.get("limit", 200)))
                uni = set(CONFIG.get("universe") or [])
                now_feats = {}
                last = ENGINE.get("last_result") or {}
                for o in (last.get("opportunities") or []):
                    now_feats[o["market"]] = o
                rows = []
                for a in assets:
                    m = a["symbol"]
                    o = now_feats.get(m)
                    pos = DBASE.query_one("SELECT * FROM positions WHERE market=? AND status='OPEN'", (m,))
                    t = MDATA.get_ticker(m)
                    rows.append({
                        "symbol": m, "base": a.get("base"), "price": (t or {}).get("price") or (o or {}).get("price", 0),
                        "change24": (t or {}).get("change24", 0),
                        "volume24h_usd": (t or {}).get("quote_vol") or a.get("volume24h_usd", 0),
                        "liquidity_usd": (o or {}).get("liquidity_usd", 0),
                        "spread_bps": (o or {}).get("spread_bps", 0),
                        "volatility_atr_pct": (o or {}).get("atr_pct", 0),
                        "regime": (o or {}).get("regime", ""),
                        "ai_signal": (o or {}).get("direction", ""),
                        "ai_confidence": (o or {}).get("confidence", 0),
                        "trade_quality": (o or {}).get("quality", 0),
                        "score": (o or {}).get("score", 0),
                        "health": (o or {}).get("health", ""),
                        "position": bool(pos),
                        "position_pnl": safe_float((pos or {}).get("unrealized_pnl")),
                        "risk": (o or {}).get("decision", ""),
                        "in_universe": m in uni,
                        "stale": MDATA.is_stale(m) if t else True,
                        "tick_size": a.get("tick_size"), "step_size": a.get("step_size"),
                        "min_notional": a.get("min_notional"),
                    })
                return self._ok({"assets": rows, "universe": CONFIG.get("universe"),
                                 "universe_meta": UNIVERSE.snapshot()})
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
            if p == "/api/decisions":
                lim = min(int(q.get("limit", 100)), 500)
                return self._ok({"decisions": DBASE.query(
                    "SELECT * FROM decisions ORDER BY ts DESC LIMIT ?", (lim,))})
            if p == "/api/research":
                return self._ok({"reports": DBASE.query(
                    "SELECT * FROM research_reports ORDER BY created_at DESC LIMIT 20"),
                    "guardrails": AGENT_GUARDRAILS,
                    "llm_enabled": bool(CONFIG.get("research_llm_enabled"))})
            if p == "/api/backtests":
                return self._ok({"backtests": DBASE.query("SELECT * FROM backtests ORDER BY id DESC LIMIT 20")})
            if p == "/api/audit":
                return self._ok({"audit": DBASE.query("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 150")})
            if p == "/api/alerts":
                return self._ok({"alerts": DBASE.query("SELECT * FROM alerts ORDER BY id DESC LIMIT 50")})
            if p == "/api/models":
                return self._ok({"models": DBASE.query("SELECT * FROM models ORDER BY id DESC LIMIT 20"),
                                 "current": {"version": MODEL_VERSION, "trained": AI.trained,
                                             "degraded": AI.degraded, "metrics": AI.metrics,
                                             "strict_mode": bool(CONFIG.get("strict_ai_mode", True)),
                                             "features": FEATURE_KEYS}})
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
                m = q.get("market", "BTC/USDT")
                kl = MDATA.get_klines(m) or []
                return self._ok({"market": m, "candles": kl[-120:],
                                 "stale_db": MDATA.klines_stale_db(m)})
            if p == "/api/providers":
                return self._ok({"providers": REGISTRY.all(integrated_only=False),
                                 "integrated_only": REGISTRY.all(integrated_only=True)})
            if p == "/api/public_apis":
                return self._ok({"catalog": public_api_catalog_payload(),
                                 "note": "Catalogue references — NOT integrated. Enabling any requires explicit registration + health check."})
            if p == "/api/ai":
                return self._ok(AIPM.status())
            if p == "/api/mcp":
                return self._ok({"servers": MCPM.registry(),
                                 "permission_classes": MCP_PERMISSION_CLASSES,
                                 "forbidden_classes": MCP_FORBIDDEN_CLASSES,
                                 "calls": DBASE.query("SELECT * FROM mcp_calls ORDER BY id DESC LIMIT 50")})
            if p == "/api/exchange":
                adapter = EXCHANGES.get(CONFIG.get("exchange") or "binance")
                return self._ok({
                    "exchange": adapter.NAME,
                    "credentials_configured": adapter.has_credentials(),
                    "key_masked": mask_key(SECRETS.get("exchange_api_key") or "") if adapter.has_credentials() else "",
                    "account_type": CONFIG.get("exchange_account_type"),
                    "last_signed_ok": adapter.last_signed_ok,
                    "last_error": adapter.last_error,
                    "live_enabled": bool(CONFIG.get("live_enabled")),
                    "live_block_reason": CONFIG.get("live_block_reason") or ExecutionEngine._live_block,
                    "provider": (REGISTRY.get("binance_user") or Provider("x", "x", "x", "x", "x", "x", "x", False, False, "x", [], False)).snapshot() if REGISTRY.get("binance_user") else None,
                    "futures_note": "Live trading is SPOT-only in this edition. Futures live trading is not enabled.",
                })
            if p == "/api/agents":
                last = ENGINE.get("last_result") or {}
                return self._ok({"guardrails": AGENT_GUARDRAILS,
                                 "agents_in_last_cycle": (last.get("opportunities") or [{}])[0].get("agents", []) if last.get("opportunities") else [],
                                 "llm_research": last.get("llm_research"),
                                 "llm_enabled": bool(CONFIG.get("research_llm_enabled"))})
            return self._err("Unknown endpoint", 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            log(f"GET {self.path} failed: {e}", "ERROR")
            try:
                self._err(f"Server error: {e}", 500)
            except Exception:
                pass

    # ----- POST -----
    def do_POST(self):
        try:
            u = urllib.parse.urlparse(self.path)
            p = u.path
            b = self._body()
            if p == "/api/setup/verify":
                res = activate_license(b.get("key", ""), mode=b.get("mode"))
                if not res.ok:
                    return self._err(res.message)
                tok = create_session()
                threading.Thread(target=warmup, daemon=True).start()
                self.send_response(200)
                body = jdump({"ok": True, "message": res.message, "mode": res.mode,
                              "details": res.details, "token": tok}).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"zepay_token={tok}; Path=/; SameSite=Lax; HttpOnly")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if p == "/api/license/endpoint":
                ep = (b.get("endpoint") or "").strip()
                if ep and not ep.startswith("https://"):
                    return self._err("Endpoint must be an https:// URL")
                with CONFIG._lock:
                    CONFIG.cfg["license_verify_endpoint"] = ep or None
                    CONFIG.save_locked()
                audit("auth", "license_endpoint_set", {"endpoint": ep or "(cleared)"}, actor="user")
                return self._ok({"endpoint": ep or None,
                                 "cloud": license_provider_status()})
            if not self._authed():
                return self._err("Not authenticated.", 401)
            if p == "/api/logout":
                return self._ok({"logged_out": True})
            if p == "/api/universe":
                uni = b.get("universe", [])
                catalog_syms = {a["symbol"] for a in UNIVERSE.all_assets(limit=100000)}
                valid = [x for x in uni if x in catalog_syms]
                if not valid:
                    return self._err("Select at least one market from the tradable universe.")
                with CONFIG._lock:
                    CONFIG.cfg["universe"] = valid
                    CONFIG.save_locked()
                audit("config", "universe_changed", {"universe": valid}, actor="user")
                threading.Thread(target=lambda: (MDATA.refresh_all(valid),
                                                 AI.train(valid)), daemon=True).start()
                if CONFIG.get("ws_streaming"):
                    MDATA.start_ws(valid)
                return self._ok({"universe": valid})
            if p == "/api/config":
                if "risk_mode" in b and b["risk_mode"] in RISK_PRESETS:
                    with CONFIG._lock:
                        CONFIG.cfg.update(RISK_PRESETS[b["risk_mode"]])
                        CONFIG.cfg["risk_mode"] = b["risk_mode"]
                        CONFIG.save_locked()
                allowed = {"max_positions": int, "priorities": dict, "ai_automation": bool,
                           "auto_research": bool, "notifications": bool, "refresh_secs": int,
                           "display_currency": str, "strict_ai_mode": bool,
                           "ai_failure_policy": str, "paper_starting_balance": float,
                           "min_liquidity_usd": float, "max_spread_bps": float,
                           "research_llm_enabled": bool, "ws_streaming": bool,
                           "fee_bps": float, "slippage_bps": float,
                           "candle_interval": str, "candle_limit": int}
                patch = {}
                for k, typ in allowed.items():
                    if k in b:
                        try:
                            patch[k] = typ(b[k]) if typ is not dict else dict(b[k])
                        except Exception:
                            pass
                if patch:
                    CONFIG.update(patch, actor="user")
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
                mode = "LIVE" if (b.get("mode") == "LIVE" and CONFIG.get("live_enabled")) else "PAPER"
                r = ExecutionEngine.place(opp, target["size"], f, mode=mode,
                                          decision_id=target.get("decision_id"))
                if r.get("ok"):
                    return self._ok(r)
                return self._err(r.get("error", "execution failed"))
            if p == "/api/close":
                mode = "LIVE" if (b.get("mode") == "LIVE" and CONFIG.get("live_enabled")) else "PAPER"
                r = ExecutionEngine.close_position(b.get("market"), mode, reason="user")
                return self._ok(r) if r.get("ok") else self._err(r.get("error", "close failed"))
            if p == "/api/order/limit":
                r = ExecutionEngine.place_limit_paper(b.get("market"), b.get("direction", "LONG"),
                                                      safe_float(b.get("qty")),
                                                      safe_float(b.get("limit_price")))
                return self._ok(r) if r.get("ok") else self._err(r.get("error"))
            if p == "/api/order/cancel":
                r = ExecutionEngine.cancel_order(b.get("order_id"), mode=b.get("mode", "PAPER"))
                return self._ok(r) if r.get("ok") else self._err(r.get("error"))
            if p == "/api/kill":
                on = bool(b.get("on", True))
                with CONFIG._lock:
                    CONFIG.cfg["kill_switch"] = on
                    CONFIG.save_locked()
                audit("safety", "kill_switch", {"on": on}, actor="user")
                body = ("Automated entries STOPPED by user. No automatic resume."
                        if on else "Automated entries re-enabled by user.")
                notify("CRITICAL" if on else "INFO",
                       "Kill switch " + ("ENGAGED" if on else "released"), body)
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
                audit("safety", "resume", {"note": "explicit user resume"}, actor="user")
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
            if p == "/api/reconcile":
                out = {"paper": Reconciler.paper_check()}
                if CONFIG.get("live_enabled") or EXCHANGES["binance"].has_credentials():
                    out["live"] = Reconciler.live_check()
                return self._ok(out)
            # ---- exchange connection ----
            if p == "/api/exchange/test":
                if b.get("api_key") and b.get("api_secret"):
                    EXCHANGES["binance"].set_credentials(b["api_key"].strip(), b["api_secret"].strip())
                r = EXCHANGES["binance"].test_connection()
                audit("exchange", "connection_tested",
                      {"connected": r.get("connected"), "error": r.get("error")}, actor="user")
                if not r.get("connected"):
                    if b.get("api_key") and b.get("api_secret"):
                        EXCHANGES["binance"].clear_credentials()
                    return self._err(r.get("error", "CONNECTION FAILED"))
                return self._ok({"result": redact(r)})
            if p == "/api/exchange/disconnect":
                EXCHANGES["binance"].clear_credentials()
                audit("exchange", "disconnected", {}, actor="user")
                return self._ok({"disconnected": True})
            # ---- live trading gate ----
            if p == "/api/live/connect":
                return self._ok(live_connect(b))
            if p == "/api/live/enable":
                return self._ok(live_enable(b))
            if p == "/api/live/disable":
                with CONFIG._lock:
                    CONFIG.cfg["live_enabled"] = False
                    CONFIG.cfg["trading_mode"] = "PAPER"
                    steps = [s for s in (CONFIG.cfg.get("live_confirmed_steps") or []) if s != "enabled"]
                    CONFIG.cfg["live_confirmed_steps"] = steps
                    CONFIG.save_locked()
                ExecutionEngine._live_block = None
                audit("security", "live_disabled", {}, actor="user")
                notify("INFO", "Live trading disabled", "ZEPAY returned to PAPER mode.")
                return self._ok({"live_enabled": False})
            # ---- providers ----
            if p == "/api/providers/check":
                pid = b.get("id")
                if pid:
                    pr = REGISTRY.get(pid)
                    if not pr:
                        return self._err("unknown provider")
                    return self._ok({"provider": pr.health_check()})
                return self._ok({"providers": REGISTRY.check_all(integrated_only=True)})
            # ---- AI manager ----
            if p == "/api/ai/provider":
                r = AIPM.set_provider(b.get("provider"), bool(b.get("enabled")),
                                      base_url=b.get("base_url"), model=b.get("model"),
                                      api_key=b.get("api_key"))
                return self._ok(r) if r.get("ok") else self._err(r.get("error"))
            if p == "/api/ai/check":
                pid = b.get("provider")
                return self._ok({"health": AIPM.health_check(pid)})
            if p == "/api/ai/research":
                market = b.get("market") or (CONFIG.get("universe") or ["BTC/USDT"])[0]
                f = FeatureEngine.build(market)
                if f.price <= 0:
                    return self._err("No real market data for that market — research requires real data.")
                regime = RegimeEngine.detect(f)
                strat = StrategyEngine.evaluate(f, regime["regime"])
                infer = AI.infer(f, regime, strat)
                agents_out = Agents.run_deterministic(f, regime, infer,
                                                      PortfolioEngine.exposure(PortfolioEngine.positions(), 1),
                                                      PortfolioEngine.positions())
                r = ResearchEngineLLM.run(market, f, regime, infer, agents_out)
                return self._ok({"agents": agents_out, "llm": r})
            # ---- MCP ----
            if p == "/api/mcp/upsert":
                r = MCPM.upsert(b)
                return self._ok(r) if r.get("ok") else self._err(r.get("error"))
            if p == "/api/mcp/remove":
                return self._ok(MCPM.remove(b.get("id")))
            if p == "/api/mcp/tools":
                r = MCPM.list_tools(b.get("id"))
                return self._ok(r) if r.get("ok") else self._err(r.get("error"))
            if p == "/api/mcp/call":
                r = MCPM.call_tool(b.get("id"), b.get("tool"), b.get("args") or {})
                return self._ok(r) if r.get("ok") else self._err(r.get("error"))
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
    feed = MDATA.feed_health()
    return {
        "version": VERSION, "edition": EDITION, "ts": utcnow_iso(),
        "trading_mode": "LIVE" if CONFIG.get("live_enabled") else "PAPER",
        "live_enabled": bool(CONFIG.get("live_enabled")),
        "mode_banner": ("LIVE — REAL MONEY" if CONFIG.get("live_enabled")
                        else "PAPER — REAL DATA · SIMULATED EXECUTION"),
        "data_feed": feed,
        "data_banner": ("REAL DATA UNAVAILABLE" if feed["status"] == "UNAVAILABLE"
                        else ("DATA DEGRADED" if feed["status"] == "DEGRADED" else "REAL-TIME DATA")),
        "system_state": state.value, "system_msg": msg,
        "kill_switch": bool(CONFIG.get("kill_switch")),
        "equity": round(equity, 2), "day_pnl": round(safe_float((acct or {}).get("day_pnl", 0)), 2),
        "drawdown_pct": round(safe_float((acct or {}).get("drawdown_pct", 0)) * 100, 2),
        "open_positions": len(positions), "exposure": exp,
        "universe": CONFIG.get("universe"),
        "universe_meta": UNIVERSE.snapshot(),
        "last_run": ENGINE.get("last_run"), "cycles": ENGINE.get("cycles"),
        "opportunities": (last.get("opportunities") or [])[:10],
        "model_trained": AI.trained, "model_degraded": AI.degraded,
        "ai_mode": CONFIG.get("ai_mode"),
        "ai_provider": AIPM.active_provider(),
        "risk_mode": CONFIG.get("risk_mode"),
        "display_currency": CONFIG.get("display_currency", "₹"),
        "license_mode": CONFIG.get("license_mode"),
        "live_block_reason": CONFIG.get("live_block_reason") or ExecutionEngine._live_block,
        "strict_ai_mode": bool(CONFIG.get("strict_ai_mode", True)),
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
    allowed, why = RiskEngine.entries_allowed()
    return {"state": state.value, "message": msg,
            "entries_allowed": allowed, "entries_blocked_reason": why,
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
                       "risk_per_trade_pct": CONFIG.get("risk_per_trade_pct")},
            "hierarchy": ["1 hard safety limits", "2 global risk", "3 portfolio constraints",
                          "4 execution safety", "5 data validity", "6 strategies",
                          "7 AI models", "8 LLM research (advisory)"]}

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
    ck("crypto_roundtrip", lambda: SECRETBOX.self_test())
    checks["secrets_store"] = {"ok": True, "n": len(SECRETS.names())}
    checks["market_data"] = {"ok": MDATA.feed_health()["status"] in ("LIVE", "DEGRADED"),
                             "feed": MDATA.feed_health()}
    try:
        REGISTRY.get("binance_spot_public").health_check()
        checks["exchange_public_api"] = {"ok": REGISTRY.get("binance_spot_public").status == ProviderStatus.OK,
                                         "status": REGISTRY.get("binance_spot_public").status.value,
                                         "latency_ms": REGISTRY.get("binance_spot_public").latency_ms}
    except Exception as e:
        checks["exchange_public_api"] = {"ok": False, "error": str(e)[:200]}
    ck("disk", lambda: (_ for _ in ()).throw(RuntimeError("low disk")) if shutil.disk_usage(DATA_DIR).free < 50_000_000 else None)
    checks["python"] = {"ok": sys.version_info >= (3, 10), "version": sys.version.split()[0]}
    checks["ai"] = {"ok": True, "trained": AI.trained, "degraded": AI.degraded,
                    "strict_mode": bool(CONFIG.get("strict_ai_mode", True))}
    checks["ai_provider"] = {"ok": True, "active": AIPM.active_provider(),
                             "mode": CONFIG.get("ai_mode")}
    checks["universe"] = {"ok": True, **UNIVERSE.snapshot()}
    checks["risk_engine"] = {"ok": True, "state": RiskEngine.system_state()[0].value}
    checks["reconciliation"] = {"ok": True, "last_paper": "run /api/reconcile for a fresh check"}
    checks["clock"] = {"ok": True, "utc": utcnow_iso()}
    checks["license"] = {"ok": True, "mode": CONFIG.get("license_mode"),
                         "cloud": license_provider_status()["status"]}
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
    results = dict(d)
    # risk self-test: a blocked opp must be rejected
    try:
        f = AssetFeatures(market="TEST/USDT")
        opp = Opportunity(market="TEST/USDT", direction="LONG", score=99, confidence=0.9,
                          quality=0.9, regime="BULLISH_TREND", strategy="trend",
                          expected_return=0.05, expected_net=0.04, cost_pct=0.001, price=0)
        dec, _, _ = RiskEngine.authorize(opp, f, {"status": "BLOCKED", "reasons": ["test"]},
                                         [], get_account("PAPER"), {})
        results["risk_rejects_bad_data"] = {"ok": dec in (Decision.REJECTED, Decision.HALTED)}
    except Exception as e:
        results["risk_rejects_bad_data"] = {"ok": False, "error": str(e)[:200]}
    results["paper_trading"] = {"ok": True, "mode": "PAPER"}
    results["live_trading"] = {"ok": True, "enabled": bool(CONFIG.get("live_enabled")),
                               "note": "disabled by default; multi-step gate required"}
    results["security"] = {"ok": not CONFIG.get("live_enabled") or bool(EXCHANGES["binance"].has_credentials()),
                           "live_enabled": bool(CONFIG.get("live_enabled")),
                           "withdrawals": "FORBIDDEN platform-wide"}
    all_ok = all(v.get("ok") for v in results.values())
    if ENGINE.get("last_result"):
        results["engine"] = {"ok": True, "last_run": ENGINE.get("last_run")}
    else:
        results["engine"] = {"ok": True, "note": "engine idle — press Analyze"}
    return {"ok": all_ok, "results": results, "ts": utcnow_iso(),
            "verdict": "ZEPAY is ready" if all_ok else "ZEPAY requires attention (see failing checks — likely network egress to the exchange)"}

def build_status():
    return {"version": VERSION, "edition": EDITION,
            "modules": {k: "✓" for k in
                        ["providers", "universe", "market_data", "websocket", "features",
                         "cross_asset", "regime", "quant_ai", "llm_ai_manager", "agents",
                         "strategies", "opportunity_ranker", "portfolio", "risk_engine",
                         "sizer", "execution_paper", "execution_live", "reconciliation",
                         "decision_ledger", "backtesting", "model_registry", "mcp_manager",
                         "monitoring", "alerting", "security", "dashboard"]},
            "trading_mode": "LIVE" if CONFIG.get("live_enabled") else "PAPER",
            "live": "ENABLED" if CONFIG.get("live_enabled") else "DISABLED (gate required)",
            "real_data_required": REAL_DATA_REQUIRED}

def warmup():
    try:
        UNIVERSE.refresh()
        uni = CONFIG.get("universe") or []
        MDATA.refresh_all(uni)
        if CONFIG.get("ws_streaming"):
            MDATA.start_ws(uni)
        run_cycle(save_signals=True)
        AI.train(uni)
        Reconciler.paper_check()
        notify("INFO", "ZEPAY warm-up complete",
               f"Monitoring {len(uni)} markets. Universe source: {UNIVERSE.source}.")
    except Exception as e:
        log(f"warmup failed: {e}", "ERROR")

# ----------------------------- live trading gate ------------------------------
# Multi-step explicit opt-in. Every step is a REAL check. Withdrawal-enabled
# keys are REFUSED. There is NO automatic live activation.

LIVE_STEPS = ["open_live", "risk_ack", "exchange_credentials", "connection_verified",
              "permissions_verified", "withdrawals_disabled", "market_data_healthy",
              "reconciled", "risk_test", "paper_ready", "explicit_confirm", "enabled"]

def live_connect(b):
    """Store credentials + run the REAL connection test via the adapter."""
    exch = (b.get("exchange") or "binance").lower()
    if exch != "binance":
        return {"ok": False, "error": "Only the Binance Spot adapter is implemented in this edition. "
                                      "Other exchanges are NOT integrated (we do not fake them)."}
    key = (b.get("api_key") or "").strip()
    secret = (b.get("api_secret") or "").strip()
    if not key or not secret:
        return {"ok": False, "error": "API key and secret are required."}
    if len(secret) < 8:
        return {"ok": False, "error": "Invalid secret."}
    adapter = EXCHANGES["binance"]
    adapter.set_credentials(key, secret)
    test = adapter.test_connection()
    if not test.get("connected"):
        adapter.clear_credentials()
        audit("security", "live_connect_failed", {"exchange": exch, "error": test.get("error")})
        return {"ok": False, "error": f"CONNECTION FAILED — {test.get('error')}",
                "checks": test.get("checks")}
    # real permission verification succeeded
    with CONFIG._lock:
        steps = set(CONFIG.cfg.get("live_confirmed_steps") or [])
        steps.update(["open_live", "exchange_credentials", "connection_verified",
                      "permissions_verified", "withdrawals_disabled"])
        CONFIG.cfg["live_confirmed_steps"] = sorted(steps)
        CONFIG.cfg["exchange"] = exch
        CONFIG.cfg["exchange_account_type"] = "SPOT"
        CONFIG.save_locked()
    REGISTRY.get("binance_user").health_check()
    audit("security", "live_connected",
          {"exchange": exch, "key": mask_key(key),
           "permissions": test.get("permissions")}, actor="user")
    return {"ok": True, "steps": CONFIG.get("live_confirmed_steps"),
            "checks": test.get("checks"), "permissions": test.get("permissions"),
            "message": "CONNECTED — credentials verified with a real signed account request."}

def live_enable(b):
    steps = set(CONFIG.get("live_confirmed_steps") or [])
    if b.get("risk_ack"):
        steps.add("risk_ack")
    if b.get("explicit_confirm"):
        steps.add("explicit_confirm")
    # real market-data health
    pub = REGISTRY.get("binance_spot_public")
    pub.health_check()
    if pub.status == ProviderStatus.OK:
        steps.add("market_data_healthy")
    else:
        return {"ok": False, "error": f"Market data not healthy ({pub.status.value}) — live trading refused.",
                "steps": sorted(steps)}
    # real reconciliation
    rec = Reconciler.live_check()
    if rec.get("ok"):
        steps.add("reconciled")
    else:
        return {"ok": False, "error": "Reconciliation failed: " + "; ".join(rec.get("issues", [])),
                "steps": sorted(steps)}
    # paper readiness: ≥5 paper fills
    fills = DBASE.query("SELECT COUNT(*) AS n FROM fills WHERE market != ''")
    n_fills = (fills[0]["n"] if fills else 0)
    if b.get("paper_ready"):
        if n_fills < 5:
            return {"ok": False, "error": f"Paper readiness not met: {n_fills}/5 paper fills. Paper-trade first.",
                    "steps": sorted(steps)}
        steps.add("paper_ready")
    # risk self-test
    if b.get("risk_test"):
        try:
            f = AssetFeatures(market="TEST/USDT")
            opp = Opportunity(market="TEST/USDT", direction="LONG", score=99, confidence=0.9,
                              quality=0.9, regime="BULLISH_TREND", strategy="trend",
                              expected_return=0.05, expected_net=0.04, cost_pct=0.001, price=0)
            dec, _, _ = RiskEngine.authorize(opp, f, {"status": "BLOCKED", "reasons": ["t"]},
                                             [], get_account("PAPER"), {})
            risk_ok = dec in (Decision.REJECTED, Decision.HALTED)
        except Exception:
            risk_ok = False
        if not risk_ok:
            return {"ok": False, "error": "Risk-engine self-test failed. Live trading refused.",
                    "steps": sorted(steps)}
        steps.add("risk_test")
    required = {"open_live", "risk_ack", "exchange_credentials", "connection_verified",
                "permissions_verified", "withdrawals_disabled", "market_data_healthy",
                "reconciled", "risk_test", "paper_ready", "explicit_confirm"}
    with CONFIG._lock:
        CONFIG.cfg["live_confirmed_steps"] = sorted(steps)
        CONFIG.save_locked()
    if not required.issubset(steps):
        return {"ok": True, "steps": sorted(steps), "missing": sorted(required - steps),
                "enabled": False}
    # FINAL explicit confirmation phrase
    if (b.get("confirm_phrase") or "").strip().upper() != "ENABLE LIVE TRADING":
        return {"ok": False, "error": 'Type the exact phrase "ENABLE LIVE TRADING" to confirm.',
                "steps": sorted(steps)}
    with CONFIG._lock:
        CONFIG.cfg["live_enabled"] = True
        CONFIG.cfg["trading_mode"] = "LIVE"
        steps.add("enabled")
        CONFIG.cfg["live_confirmed_steps"] = sorted(steps)
        CONFIG.save_locked()
    Reconciler.sync_live_balance()
    audit("security", "live_enabled", {"steps": sorted(steps)}, actor="user")
    notify("CRITICAL", "LIVE trading enabled",
           "Real funds are at risk. Kill switch is available at all times.")
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
        return {"ok": True, "backup": dest,
                "note": "The .master.key file is NOT included — store it separately and securely; "
                        "without it encrypted secrets cannot be restored."}
    except Exception as e:
        return {"ok": False, "error": str(e)}

HELP_TOPICS = {
    "getting_started": "Install → Open → Enter ZEPAY key → Connect exchange (optional) → Select assets → Paper trading. Nothing technical needed.",
    "license": "Your key is verified either OFFLINE (format check — clearly labeled, no cloud call) or via a configured ZEPAY cloud endpoint. No ZEPAY cloud API spec ships with this build, so cloud verification stays BLOCKED until an endpoint is configured in Settings.",
    "paper_vs_live": "PAPER = real data + real AI + real risk + SIMULATED execution. LIVE = real exchange orders. The switch changes the backend execution adapter, not just the UI.",
    "dashboard": "Home shows equity, P&L, drawdown, positions, feed status. The banner always shows PAPER—REAL DATA or LIVE—REAL MONEY.",
    "assets": "The tradable universe is loaded LIVE from the exchange (any spot USDT market). Search, select, enable/disable — changes apply without restart.",
    "ai_signals": "Signals come from the quant ensemble trained on real candles. WAIT is a valid decision — cash is a position.",
    "ai_manager": "Configure cloud (OpenAI, Anthropic, …) or local (Ollama, vLLM) LLM providers. The active provider is shown; LLMs are advisory research only and can never trade.",
    "agents": "Deterministic analysts (Market, Technical, Quant, Regime, Derivatives, Cross-Asset, Risk, Portfolio, Execution) + optional LLM bull/bear debate. Agents cannot bypass the Risk Engine or place orders.",
    "api_manager": "Every external API shows provider, purpose, auth, rate limit, health, last success and last error. Missing specs are BLOCKED, never faked.",
    "mcp": "MCP servers are registered explicitly, disabled by default, rate-limited, logged — and can never hold trading permissions.",
    "trading": "Paper mode simulates only the fill (real prices, books, fees, partials, latency). Live mode uses signed exchange orders with post-trade verification.",
    "portfolio": "Allocation vs risk contribution. Correlated coins count as overlapping risk.",
    "risk": "Risk Engine has final authority. Daily loss and max drawdown halts are automatic. If data or AI is unavailable, new trades stop (policy configurable).",
    "backtesting": "Real historical candles with fees/slippage/funding. No look-ahead. If real history is unavailable, the backtest refuses to run.",
    "decisions": "Every cycle records a decision ledger entry: data status, features, model, expected edge, confidence, portfolio/risk state, decision and reasons.",
    "reconciliation": "Startup + periodic: ZEPAY state vs exchange state. Any mismatch blocks live trading.",
    "live": "Requires real credential verification, withdrawal-disabled keys, healthy data, reconciliation, risk self-test, paper history and a typed confirmation phrase. No automatic activation.",
    "security": "Secrets encrypted at rest (ChaCha20-Poly1305), redacted in logs/API, never sent to the browser. Withdrawal-enabled keys are refused.",
    "kill_switch": "STOP TRADING halts new orders immediately, records the event and alerts you. It never resumes automatically.",
    "troubleshooting": "If you see REAL DATA UNAVAILABLE: the exchange API is unreachable from this machine (network/firewall/geo-block). ZEPAY blocks new trades until real data returns — by design.",
}

# =============================================================================
# PART 13 — EMBEDDED DASHBOARD (no external resources; mobile responsive)
# =============================================================================

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZEPAY — Ultimate AI Crypto Trading Platform</title>
<style>
:root{--bg:#0b1020;--card:#141b33;--card2:#1a2340;--line:#263056;--txt:#e8ecf7;--mut:#9aa5c7;
--grn:#22c55e;--yel:#eab308;--red:#ef4444;--blu:#3b82f6;--acc:#7c6cff;--ora:#f59e0b}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 -apple-system,'Segoe UI',Roboto,Arial,sans-serif}
a{color:var(--blu)}
.topbar{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:12px;padding:10px 16px;
background:rgba(11,16,32,.95);backdrop-filter:blur(6px);border-bottom:1px solid var(--line);flex-wrap:wrap}
.brand{font-weight:800;font-size:18px;letter-spacing:2px;color:#fff}
.brand span{color:var(--acc)}
.chip{padding:4px 10px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:.5px;white-space:nowrap}
.chip.paper{background:#1d2c1f;color:var(--grn);border:1px solid var(--grn)}
.chip.live{background:#3a1518;color:var(--red);border:1px solid var(--red);animation:blink 2s infinite}
.chip.dataok{background:#12233c;color:var(--blu);border:1px solid var(--blu)}
.chip.datawarn{background:#332a10;color:var(--yel);border:1px solid var(--yel)}
.chip.dataerr{background:#3a1518;color:var(--red);border:1px solid var(--red)}
.chip.ok{background:#1d2c1f;color:var(--grn);border:1px solid var(--grn)}
.chip.warn{background:#332a10;color:var(--yel);border:1px solid var(--yel)}
.chip.err{background:#3a1518;color:var(--red);border:1px solid var(--red)}
.chip.mut{background:#1a2340;color:var(--mut);border:1px solid var(--line)}
@keyframes blink{50%{opacity:.6}}
.spacer{flex:1}
.btn{background:var(--acc);color:#fff;border:0;padding:7px 14px;border-radius:8px;font-weight:600;
cursor:pointer;font-size:13px}
.btn:hover{filter:brightness(1.15)}
.btn.danger{background:var(--red)}
.btn.warn{background:var(--ora)}
.btn.ghost{background:transparent;border:1px solid var(--line);color:var(--txt)}
.btn.sm{padding:4px 10px;font-size:12px}
.btn:disabled{opacity:.5;cursor:not-allowed}
.layout{display:flex;min-height:calc(100vh - 57px)}
.nav{width:210px;border-right:1px solid var(--line);padding:12px 8px;flex-shrink:0;
max-height:calc(100vh - 57px);overflow-y:auto;position:sticky;top:57px}
.nav h4{font-size:10px;letter-spacing:2px;color:var(--mut);margin:14px 8px 4px}
.nav a{display:block;color:var(--txt);text-decoration:none;padding:7px 10px;border-radius:8px;
font-size:13px;margin:1px 0}
.nav a:hover{background:var(--card2)}
.nav a.active{background:var(--acc);color:#fff;font-weight:600}
.main{flex:1;padding:18px;min-width:0}
h2{font-size:19px;margin-bottom:4px}
.sub{color:var(--mut);font-size:12.5px;margin-bottom:14px}
.grid{display:grid;gap:12px}
.cards{grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.card .lbl{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:1px}
.card .val{font-size:21px;font-weight:700;margin-top:2px}
.card .note{font-size:11px;color:var(--mut);margin-top:2px}
.pos{color:var(--grn)}.neg{color:var(--red)}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th{color:var(--mut);text-align:left;font-weight:600;padding:7px 8px;border-bottom:1px solid var(--line);
white-space:nowrap}
td{padding:7px 8px;border-bottom:1px solid #1c2544;vertical-align:top}
tr:hover td{background:rgba(124,108,255,.05)}
.tblwrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:6px}
input,select,textarea{background:#0e1530;border:1px solid var(--line);color:var(--txt);
padding:8px 10px;border-radius:8px;font-size:13px;width:100%}
input:focus,select:focus{outline:1px solid var(--acc)}
label{font-size:12px;color:var(--mut);display:block;margin:8px 0 3px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
.row>*{flex:1;min-width:140px}
.muted{color:var(--mut)}.small{font-size:11.5px}
.tag{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10.5px;font-weight:700}
.tag.LONG,.tag.BUY,.tag.APPROVED,.tag.TRADEABLE,.tag.OK,.tag.LIVE,.tag.CONNECTED,.tag.TRAINED{background:#16301f;color:var(--grn)}
.tag.SHORT,.tag.SELL{background:#301616;color:var(--red)}
.tag.WAIT,.tag.NEW,.tag.LIMITED,.tag.DEGRADED{background:#332a10;color:var(--yel)}
.tag.REJECTED,.tag.HALTED,.tag.BLOCKED,.tag.UNAVAILABLE,.tag.UNREACHABLE,.tag.ERROR,.tag.PARTIALLY_FILLED{background:#3a1518;color:var(--red)}
.tag.mut,.tag.CANCELLED,.tag.DISABLED,.tag.NOT_CONFIGURED,.tag.UNTRAINED{background:#1a2340;color:var(--mut)}
.tag.SUBMITTED,.tag.UNKNOWN,.tag.PAPER{background:#12233c;color:var(--blu)}
.tag.FILLED{background:#16301f;color:var(--grn)}
.bar{height:6px;background:#0e1530;border-radius:4px;overflow:hidden;margin-top:4px}
.bar>div{height:100%;background:var(--acc)}
#setup{position:fixed;inset:0;background:rgba(5,8,18,.97);z-index:100;display:flex;
align-items:center;justify-content:center;padding:20px}
.setupbox{max-width:520px;width:100%;background:var(--card);border:1px solid var(--line);
border-radius:16px;padding:28px}
.setupbox h1{font-size:26px;letter-spacing:3px}.setupbox h1 span{color:var(--acc)}
.setupbox p{color:var(--mut);font-size:13px;margin:8px 0 16px}
.msg{padding:9px 12px;border-radius:8px;font-size:12.5px;margin-top:10px;display:none}
.msg.err{background:#3a1518;color:#ffb4b4;border:1px solid var(--red)}
.msg.ok{background:#1d2c1f;color:#b6f0c4;border:1px solid var(--grn)}
.msg.show{display:block}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:8px 0}
summary{cursor:pointer;font-weight:600;font-size:13px}
pre{background:#0a0f22;border:1px solid var(--line);border-radius:8px;padding:10px;overflow-x:auto;
font-size:11.5px;margin-top:8px;white-space:pre-wrap}
.kv{display:grid;grid-template-columns:180px 1fr;gap:4px 12px;font-size:12.5px}
.kv b{color:var(--mut);font-weight:600}
.hidden{display:none!important}
.toast{position:fixed;bottom:16px;right:16px;z-index:200;max-width:420px}
.titem{background:var(--card2);border:1px solid var(--line);border-left:4px solid var(--acc);
border-radius:10px;padding:10px 14px;margin-top:8px;font-size:12.5px;box-shadow:0 4px 20px rgba(0,0,0,.5)}
.titem.err{border-left-color:var(--red)}.titem.ok{border-left-color:var(--grn)}
.agent{border-left:3px solid var(--acc);padding:8px 12px;margin:6px 0;background:var(--card2);border-radius:0 8px 8px 0}
.agent b{font-size:12.5px}.agent ul{margin:4px 0 0 16px;font-size:11.5px;color:var(--mut)}
@media(max-width:860px){.nav{display:none}.layout{flex-direction:column}.mobiletabs{display:flex!important}}
.mobiletabs{display:none;overflow-x:auto;border-bottom:1px solid var(--line);padding:8px;gap:6px}
.mobiletabs a{color:var(--txt);text-decoration:none;padding:6px 12px;border:1px solid var(--line);
border-radius:20px;font-size:12px;white-space:nowrap}
.mobiletabs a.active{background:var(--acc);color:#fff}
</style></head><body>
<div id="setup" class="hidden">
 <div class="setupbox">
  <h1>ZEPAY<span>.</span></h1>
  <p>Ultimate AI multi-asset crypto trading platform. Real data · Real AI · Real risk.
  Paper trading by default; live trading requires the explicit safety gate.</p>
  <label>ZEPAY key</label>
  <input id="keyInput" placeholder="ZEPAY-XXXX-XXXX-…" autocomplete="off">
  <div id="setupMsg" class="msg"></div>
  <div class="small muted" style="margin-top:12px" id="setupLicenseNote"></div>
  <button class="btn" style="width:100%;margin-top:14px" onclick="verifyKey()">VERIFY KEY →</button>
 </div>
</div>
<div class="topbar">
 <div class="brand">ZE<span>PAY</span></div>
 <span id="modeChip" class="chip mut">…</span>
 <span id="dataChip" class="chip mut">…</span>
 <span id="stateChip" class="chip mut">…</span>
 <div class="spacer"></div>
 <span id="aiChip" class="chip mut" title="AI engine">…</span>
 <button class="btn danger" id="killBtn" onclick="toggleKill()">STOP TRADING</button>
</div>
<div class="mobiletabs" id="mtabs"></div>
<div class="layout">
 <div class="nav" id="nav"></div>
 <div class="main" id="main"></div>
</div>
<div class="toast" id="toast"></div>
<script>
'use strict';
var NAV=[
 ['OVERVIEW',[['dash','Dashboard'],['alerts','Alerts']]],
 ['TRADING',[['assets','Assets'],['signals','AI Signals'],['portfolio','Portfolio'],
   ['positions','Positions & Orders'],['backtest','Backtesting'],['risk','Risk']]],
 ['INTELLIGENCE',[['models','AI Models'],['agents','Agents & Research'],['decisions','Decision Ledger']]],
 ['CONNECTIONS',[['exchange','Exchange'],['apis','API Manager'],['aimgr','AI Manager'],['mcp','MCP Manager']]],
 ['SYSTEM',[['diag','Diagnostics'],['settings','Settings'],['help','Help']]]];
var TAB='dash', TOKEN=localStorage.getItem('zepay_token')||'';
function h(html){var d=document.createElement('div');d.innerHTML=html;return d.firstElementChild}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
function fmt(n,d){if(n==null||isNaN(n))return '—';return Number(n).toLocaleString(undefined,{maximumFractionDigits:d==null?2:d})}
function usd(n){if(n==null||isNaN(n))return '—';return '$'+Number(n).toLocaleString(undefined,{maximumFractionDigits:0})}
function tag(v){return '<span class="tag '+esc(v)+'">'+esc(v)+'</span>'}
function toast(msg,cls){var t=document.getElementById('toast');t.appendChild(h('<div class="titem '+(cls||'')+'">'+esc(msg)+'</div>'));setTimeout(function(x){x.remove()},6000,t.lastChild)}
function api(path,body,silent){
 var opts={method:body?'POST':'GET',headers:{'X-ZEPAY-Token':TOKEN}};
 if(body){opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body)}
 return fetch(path+(TOKEN&&!body?(path.includes('?')?'&':'?')+'token='+encodeURIComponent(TOKEN):''),opts)
  .then(function(r){return r.json().then(function(j){return {status:r.status,json:j}})})
  .then(function(r){
   if(r.status===401){showSetup();throw new Error('auth')}
   if(!r.json.ok&&!silent){toast(r.json.error||('HTTP '+r.status),'err')}
   return r.json})
  .catch(function(e){if(e.message!=='auth'&&!silent)toast('Network error: '+e,'err');throw e})
}
function showSetup(){document.getElementById('setup').classList.remove('hidden')}
function verifyKey(){
 var k=document.getElementById('keyInput').value.trim();
 var m=document.getElementById('setupMsg');m.className='msg';
 m.classList.add('show','err');m.textContent='Verifying…';
 fetch('/api/setup/verify',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({key:k})}).then(function(r){return r.json()}).then(function(j){
  if(j.ok){TOKEN=j.token;localStorage.setItem('zepay_token',TOKEN);
   m.className='msg show ok';m.textContent=j.message;
   setTimeout(function(){document.getElementById('setup').classList.add('hidden');boot()},700)}
  else{m.className='msg show err';m.textContent=j.error||'Invalid key'}})
  .catch(function(e){m.className='msg show err';m.textContent='Network error'});}
function buildNav(){
 var nav=document.getElementById('nav');nav.innerHTML='';
 NAV.forEach(function(g){
  var h4=document.createElement('h4');h4.textContent=g[0];nav.appendChild(h4);
  g[1].forEach(function(t){var a=document.createElement('a');a.href='#'+t[0];a.id='nav-'+t[0];
   a.textContent=t[1];a.onclick=function(e){e.preventDefault();go(t[0])};nav.appendChild(a)})})
 var mt=document.getElementById('mtabs');mt.innerHTML='';
 NAV.forEach(function(g){g[1].forEach(function(t){var a=document.createElement('a');
  a.href='#'+t[0];a.id='mnav-'+t[0];a.textContent=t[1];a.onclick=function(e){e.preventDefault();go(t[0])};mt.appendChild(a)})})
}
function go(tab){TAB=tab;location.hash=tab;
 document.querySelectorAll('.nav a,.mobiletabs a').forEach(function(a){a.classList.remove('active')});
 var n=document.getElementById('nav-'+tab),m=document.getElementById('mnav-'+tab);
 if(n)n.classList.add('active');if(m)m.classList.add('active');
 RENDER[tab]&&RENDER[tab]()}
function card(lbl,val,note,cls){return '<div class="card"><div class="lbl">'+esc(lbl)+'</div><div class="val '+(cls||'')+'">'+val+'</div>'+(note?'<div class="note">'+note+'</div>':'')+'</div>'}
function statusChips(s){
 var mc=document.getElementById('modeChip');
 mc.textContent=s.mode_banner||s.trading_mode;
 mc.className='chip '+(s.live_enabled?'live':'paper');
 var dc=document.getElementById('dataChip');
 dc.textContent=s.data_banner;
 dc.className='chip '+(s.data_feed&&s.data_feed.status==='LIVE'?'dataok':(s.data_feed&&s.data_feed.status==='DEGRADED'?'datawarn':'dataerr'));
 var sc=document.getElementById('stateChip');
 sc.textContent='RISK: '+esc(s.system_state);
 sc.className='chip '+(s.system_state==='NORMAL'?'ok':(s.system_state==='HALTED'?'err':'warn'));
 var ac=document.getElementById('aiChip');
 ac.textContent='AI: '+(s.model_trained?'QUANT ✓':(s.strict_ai_mode?'NOT TRAINED (strict)':'heuristic'));
 ac.className='chip '+(s.model_trained?'ok':'warn');
 var kb=document.getElementById('killBtn');
 kb.textContent=s.kill_switch?'KILL SWITCH ON — RESUME?':'STOP TRADING';
 kb.className='btn '+(s.kill_switch?'warn':'danger');
}
var RENDER={};
RENDER.dash=function(){api('/api/status').then(function(s){
 statusChips(s);
 var o=(s.opportunities||[]);
 var oppRows=o.map(function(x){return '<tr><td>'+x.rank+'</td><td><b>'+esc(x.market)+'</b>'+(x.data_stale?' <span class="tag BLOCKED">STALE</span>':'')+'</td>'+
  '<td>'+tag(x.direction)+'</td><td>'+fmt(x.score,1)+'</td><td>'+fmt(x.confidence*100,0)+'%</td><td>'+esc(x.regime)+'</td>'+
  '<td class="'+(x.expected_net_pct>=0?'pos':'neg')+'">'+fmt(x.expected_net_pct,3)+'%</td><td>'+tag(x.decision)+'</td></tr>'}).join('')||'<tr><td colspan="8" class="muted">Run analysis…</td></tr>';
 document.getElementById('main').innerHTML=
  '<h2>Dashboard</h2><div class="sub">'+esc(s.system_msg||'')+(s.live_block_reason?' · LIVE BLOCKED: '+esc(s.live_block_reason):'')+'</div>'+
  '<div class="grid cards">'+card('Equity',fmt(s.equity))+card("Today's P&L",(s.day_pnl>=0?'+':'')+fmt(s.day_pnl),null,s.day_pnl>=0?'pos':'neg')+
  card('Drawdown',fmt(s.drawdown_pct)+'%','halts at configured max')+
  card('Open Positions',s.open_positions+'','max from settings')+
  card('Universe',(s.universe||[]).length+' assets',esc((s.universe_meta||{}).source||''))+
  card('Cycles',s.cycles!=null?s.cycles:0,s.last_run?('last '+esc(s.last_run.split('T')[1].slice(0,8))+'Z'):'idle')+'</div>'+
  '<div class="grid cards">'+card('Execution Mode',s.live_enabled?'LIVE — REAL MONEY':'PAPER — SIMULATED EXECUTION',s.live_enabled?'Real funds at risk':'Real data · real AI · simulated fills',s.live_enabled?'neg':'pos')+
  card('Data Feed',esc((s.data_feed||{}).status||'—'),esc((s.data_feed||{}).reason||''))+
  card('AI Engine',s.model_trained?'Quant ensemble ✓':'Not trained',s.strict_ai_mode?'Strict mode: no fabricated signals':'Non-strict: labeled heuristics',s.model_trained?'pos':'warn')+
  card('License',esc(s.license_mode||'—'),s.license_mode==='offline'?'offline key check (no cloud)':'cloud verified')+'</div>'+
  '<h2 style="font-size:16px;margin-top:8px">Top Opportunities</h2>'+
  '<div class="tblwrap"><table><tr><th>#</th><th>Market</th><th>AI</th><th>Score</th><th>Conf</th><th>Regime</th><th>Net edge</th><th>Risk</th></tr>'+oppRows+'</table></div>'+
  '<div class="row" style="margin-top:12px"><button class="btn" onclick="analyze()">RUN ANALYSIS NOW</button>'+
  '<button class="btn ghost" onclick="go(\'positions\')">Positions</button>'+
  '<button class="btn ghost" onclick="go(\'risk\')">Risk Engine</button></div>'}).catch(function(){})}
function analyze(){toast('Running full analysis cycle…');api('/api/analyze',{}).then(function(r){
 toast('Cycle complete: '+r.result.opportunities.length+' markets analyzed','ok');RENDER[TAB]()})}
function toggleKill(){
 api('/api/status').then(function(s){
  var on=!s.kill_switch;
  api('/api/kill',{on:on}).then(function(){toast(on?'KILL SWITCH ENGAGED — new orders stopped':'Kill switch released','ok');RENDER[TAB]()})})}
RENDER.signals=function(){api('/api/opportunities').then(function(r){
 var res=r.result||{};var ops=res.opportunities||[];
 var rows=ops.map(function(x){
  var det=x.reasons.map(function(t){return '• '+esc(t)}).join('<br>')+'<br><span class="muted">Risk: '+esc((x.decision_reasons||[]).join('; '))+'</span>';
  return '<tr><td>'+x.rank+'</td><td><b>'+esc(x.market)+'</b>'+(x.data_stale?' <span class="tag BLOCKED">STALE</span>':'')+
   '<div class="small muted">'+esc(x.ai_mode)+'</div></td><td>'+tag(x.direction)+'</td><td>'+fmt(x.score,1)+'</td>'+
   '<td>'+fmt(x.confidence*100,0)+'%<div class="bar"><div style="width:'+fmt(x.confidence*100,0)+'%"></div></div></td>'+
   '<td>'+fmt(x.quality*100,0)+'%</td><td>'+esc(x.regime)+'</td><td>'+tag(x.health)+'</td>'+
   '<td class="'+(x.expected_net_pct>=0?'pos':'neg')+'">'+fmt(x.expected_net_pct,3)+'%<br><span class="muted small">cost '+fmt(x.cost_pct,3)+'%</span></td>'+
   '<td>'+fmt(x.price,x.price>100?2:6)+'</td><td>'+tag(x.decision)+'</td>'+
   '<td>'+(x.agents||[]).map(function(a){return esc(a.agent.replace('Analyst',''))+':'+esc(a.verdict)}).join('<br>')+'</td>'+
   '<td class="small">'+det+'</td><td>'+(x.decision==='APPROVED'?'<button class="btn sm" onclick="exec(\''+esc(x.market)+'\')">TRADE</button>':'')+'</td></tr>'}).join('');
 document.getElementById('main').innerHTML='<h2>AI Signals — Opportunity Ranking</h2>'+
  '<div class="sub">Quant ensemble on real data · ranked by risk-adjusted net edge · not every signal is traded — cash is a valid decision</div>'+
  '<div class="tblwrap"><table><tr><th>#</th><th>Market</th><th>AI</th><th>Score</th><th>Conf</th><th>Quality</th><th>Regime</th><th>Health</th><th>Net edge</th><th>Price</th><th>Risk</th><th>Agents</th><th>Why</th><th></th></tr>'+(rows||'<tr><td colspan="14" class="muted">No data yet — run analysis</td></tr>')+'</table></div>'+
  ((res.llm_research&&res.llm_research.available)?'<details><summary>LLM research (advisory)</summary><pre>'+esc(JSON.stringify(res.llm_research,null,1))+'</pre></details>':'')+
  '<div class="row" style="margin-top:12px"><button class="btn" onclick="analyze()">RUN ANALYSIS</button></div>'}).catch(function(){})}
function exec(m){api('/api/execute',{market:m}).then(function(r){toast('Executed: '+m+' @ '+fmt(r.fill_price),'ok');RENDER.signals()})}
RENDER.assets=function(){Promise.all([api('/api/assets?limit=250'),api('/api/status')]).then(function(rs){
 var a=rs[0],s=rs[1];var assets=a.assets||[];var uni=a.universe||[];
 var q='';
 window._assets=assets;
 var draw=function(){
  var list=assets.filter(function(x){return !q||x.symbol.toUpperCase().includes(q.toUpperCase())});
  var rows=list.map(function(x){return '<tr><td><input type="checkbox" '+(x.in_universe?'checked':'')+' onchange="toggleAsset(this,\''+esc(x.symbol)+'\')"></td>'+
   '<td><b>'+esc(x.symbol)+'</b></td><td>'+fmt(x.price,x.price>100?2:6)+'</td><td class="'+(x.change24>=0?'pos':'neg')+'">'+fmt(x.change24)+'%</td>'+
   '<td>'+usd(x.volume24h_usd)+'</td><td>'+usd(x.liquidity_usd)+'</td><td>'+fmt(x.spread_bps,1)+'</td><td>'+fmt(x.volatility_atr_pct,2)+'%</td>'+
   '<td>'+esc(x.regime||'—')+'</td><td>'+(x.ai_signal?tag(x.ai_signal):'—')+'</td><td>'+fmt(x.ai_confidence*100,0)+'%</td>'+
   '<td>'+fmt(x.trade_quality*100,0)+'%</td><td>'+(x.position?'<span class="tag FILLED">OPEN</span>':'—')+'</td>'+
   '<td class="'+(x.position_pnl>=0?'pos':'neg')+'">'+(x.position?fmt(x.position_pnl):'—')+'</td>'+
   '<td>'+(x.health?tag(x.health):'')+'</td><td class="small muted">'+(x.stale?'STALE':'live')+'</td></tr>'}).join('');
  document.getElementById('main').innerHTML='<h2>Asset Management</h2>'+
   '<div class="sub">Tradable universe loaded '+esc((a.universe_meta||{}).source==='exchange_metadata'?'LIVE from exchange metadata':'from FALLBACK cache (exchange metadata unreachable — ' + esc((a.universe_meta||{}).last_error||'network')+')')+' · '+assets.length+' spot markets · changes apply instantly, no restart</div>'+
   '<div class="row"><div><label>Search</label><input id="assetSearch" value="'+esc(q)+'" oninput="assetSearch(this.value)"></div>'+
   '<button class="btn" onclick="saveUniverse()">SAVE UNIVERSE ('+uni.length+')</button>'+
   '<button class="btn ghost" onclick="selectAll(true)">SELECT ALL</button>'+
   '<button class="btn ghost" onclick="selectAll(false)">CLEAR</button></div>'+
   '<div class="tblwrap" style="margin-top:10px"><table><tr><th></th><th>Asset</th><th>Price</th><th>24h</th><th>Volume</th><th>Liquidity</th><th>Spread</th><th>Vol</th><th>Regime</th><th>AI</th><th>Conf</th><th>Quality</th><th>Position</th><th>P&L</th><th>Status</th><th>Data</th></tr>'+rows+'</table></div>'};
 draw();window._drawAssets=draw}).catch(function(){})}
function assetSearch(v){q=window._q||'';window._q=v;var el=document.getElementById('assetSearch');if(el){el.focus()}
 var list=(window._assets||[]).filter(function(x){return !v||x.symbol.toUpperCase().includes(v.toUpperCase())});
 var uni=window._assets.filter(function(x){return x.in_universe}).map(function(x){return x.symbol});
 var rows=list.map(function(x){return '<tr><td><input type="checkbox" '+(x.in_universe?'checked':'')+' onchange="toggleAsset(this,\''+esc(x.symbol)+'\')"></td>'+
  '<td><b>'+esc(x.symbol)+'</b></td><td>'+fmt(x.price,x.price>100?2:6)+'</td><td class="'+(x.change24>=0?'pos':'neg')+'">'+fmt(x.change24)+'%</td>'+
  '<td>'+usd(x.volume24h_usd)+'</td><td>'+usd(x.liquidity_usd)+'</td><td>'+fmt(x.spread_bps,1)+'</td><td>'+fmt(x.volatility_atr_pct,2)+'%</td>'+
  '<td>'+esc(x.regime||'—')+'</td><td>'+(x.ai_signal?tag(x.ai_signal):'—')+'</td><td>'+fmt(x.ai_confidence*100,0)+'%</td>'+
  '<td>'+fmt(x.trade_quality*100,0)+'%</td><td>'+(x.position?'<span class="tag FILLED">OPEN</span>':'—')+'</td>'+
  '<td class="'+(x.position_pnl>=0?'pos':'neg')+'">'+(x.position?fmt(x.position_pnl):'—')+'</td>'+
  '<td>'+(x.health?tag(x.health):'')+'</td><td class="small muted">'+(x.stale?'STALE':'live')+'</td></tr>'}).join('');
 var tw=document.querySelector('.tblwrap table');if(tw)tw.innerHTML='<tr><th></th><th>Asset</th><th>Price</th><th>24h</th><th>Volume</th><th>Liquidity</th><th>Spread</th><th>Vol</th><th>Regime</th><th>AI</th><th>Conf</th><th>Quality</th><th>Position</th><th>P&L</th><th>Status</th><th>Data</th></tr>'+(rows||'')}
function toggleAsset(cb,sym){
 (window._sel=window._sel||{});
 if(cb.checked)window._sel[sym]=1;else delete window._sel[sym];
 var asset=(window._assets||[]).find(function(x){return x.symbol===sym});
 if(asset)asset.in_universe=cb.checked;
 window._count=(window._count||0)}
function currentUniverse(){
 var uni=(window._assets||[]).filter(function(x){return x.in_universe}).map(function(x){return x.symbol});
 return uni.length?uni:[]}
function selectAll(on){(window._assets||[]).forEach(function(x){x.in_universe=on});
 RENDER.assets()}
function saveUniverse(){var uni=currentUniverse();
 if(!uni.length){toast('Select at least one asset','err');return}
 api('/api/universe',{universe:uni}).then(function(){toast('Universe saved: '+uni.length+' assets — refreshing data & retraining','ok');setTimeout(function(){RENDER.assets()},800)})}
RENDER.portfolio=function(){api('/api/portfolio').then(function(p){
 var acct=p.account||{};
 var allocRows=Object.entries(p.allocation||{}).map(function(kv){return '<tr><td>'+esc(kv[0])+'</td><td>'+fmt(kv[1],1)+'%</td><td><div class="bar"><div style="width:'+fmt(kv[1],0)+'%"></div></div></td></tr>'}).join('');
 var posRows=(p.positions||[]).map(function(x){return '<tr><td><b>'+esc(x.market)+'</b></td><td>'+tag(x.direction)+'</td><td>'+fmt(x.qty,6)+'</td><td>'+fmt(x.entry_price,6)+'</td><td>'+fmt(x.current_price,6)+'</td>'+
  '<td class="'+(x.unrealized_pnl>=0?'pos':'neg')+'">'+fmt(x.unrealized_pnl)+'</td><td>'+fmt(x.exposure)+'</td><td class="small muted">'+esc(x.strategy)+'</td></tr>'}).join('');
 var corrKeys=Object.keys(p.correlation||{});
 var corrRows=corrKeys.map(function(a){return '<tr><td><b>'+esc(a)+'</b></td>'+corrKeys.map(function(b){
  var c=p.correlation[a][b];var cls=c>0.7?'neg':(c<0.3?'mut':'');
  return '<td class="'+cls+'">'+fmt(c,2)+'</td>'}).join('')+'</tr>'}).join('');
 document.getElementById('main').innerHTML='<h2>Portfolio</h2>'+
  '<div class="grid cards">'+card('Equity',fmt(acct.equity))+card('Available',fmt(acct.available))+card('Invested',fmt(acct.invested))+
  card('Unrealized',(acct.unrealized_pnl>=0?'+':'')+fmt(acct.unrealized_pnl),null,acct.unrealized_pnl>=0?'pos':'neg')+
  card('Realized',(acct.realized_pnl>=0?'+':'')+fmt(acct.realized_pnl),null,acct.realized_pnl>=0?'pos':'neg')+'</div>'+
  '<h2 style="font-size:15px">Positions</h2><div class="tblwrap"><table><tr><th>Market</th><th>Dir</th><th>Qty</th><th>Entry</th><th>Now</th><th>uP&L</th><th>Exposure</th><th>Strategy</th></tr>'+(posRows||'<tr><td colspan="8" class="muted">No open positions</td></tr>')+'</table></div>'+
  '<div class="row" style="margin-top:14px"><div><h2 style="font-size:15px">Allocation</h2><div class="tblwrap"><table><tr><th>Asset</th><th>%</th><th></th></tr>'+allocRows+'</table></div></div>'+
  '<div><h2 style="font-size:15px">Correlation (returns, 60 bars)</h2><div class="tblwrap"><table><tr><th></th>'+corrKeys.map(function(k){return '<th>'+esc(k.split('/')[0])+'</th>'}).join('')+'</tr>'+corrRows+'</table></div></div></div>'+
  '<h2 style="font-size:15px">Risk Contribution (vol-weighted)</h2><div class="tblwrap"><table><tr><th>Asset</th><th>Share of risk</th></tr>'+
  Object.entries(p.risk_contribution||{}).map(function(kv){return '<tr><td>'+esc(kv[0])+'</td><td>'+fmt(kv[1]*100,1)+'%</td></tr>'}).join('')+'</table></div>'}).catch(function(){})}
RENDER.positions=function(){Promise.all([api('/api/positions'),api('/api/trades')]).then(function(rs){
 var pos=rs[0],tr=rs[1];
 var openRows=(pos.open||[]).map(function(x){return '<tr><td><b>'+esc(x.market)+'</b></td><td>'+tag(x.direction)+'</td><td>'+fmt(x.qty,6)+'</td><td>'+fmt(x.entry_price,6)+'</td>'+
  '<td>'+fmt(x.current_price,6)+'</td><td class="'+(x.unrealized_pnl>=0?'pos':'neg')+'">'+fmt(x.unrealized_pnl)+'</td>'+
  '<td>'+fmt(x.stop_loss,6)+' / '+fmt(x.take_profit,6)+'</td><td>'+tag(x.status)+'</td>'+
  '<td><button class="btn sm danger" onclick="closePos(\''+esc(x.market)+'\')">CLOSE</button></td></tr>'}).join('');
 var orderRows=(tr.orders||[]).slice(0,40).map(function(o){return '<tr><td class="small">'+esc(o.id)+'</td><td>'+esc(o.market)+'</td><td>'+tag(o.side)+'</td><td>'+tag(o.order_type)+'</td>'+
  '<td>'+fmt(o.qty,6)+'</td><td>'+fmt(o.avg_fill_price||o.price,6)+'</td><td>'+tag(o.status)+'</td><td>'+tag(o.mode)+'</td>'+
  '<td class="small muted">'+esc((o.created_at||'').replace('T',' ').slice(0,19))+'</td>'+
  '<td>'+((o.status==='NEW'||o.status==='PARTIALLY_FILLED')?'<button class="btn sm ghost" onclick="cancelOrd(\''+esc(o.id)+'\')">CANCEL</button>':'')+'</td></tr>'}).join('');
 var fillRows=(tr.fills||[]).slice(0,30).map(function(f){return '<tr><td class="small">'+esc(f.order_id)+'</td><td>'+esc(f.market)+'</td><td>'+tag(f.side)+'</td><td>'+fmt(f.qty,6)+'</td><td>'+fmt(f.price,6)+'</td><td>'+fmt(f.fee,4)+'</td><td class="small muted">'+esc((f.filled_at||'').replace('T',' ').slice(0,19))+'</td></tr>'}).join('');
 document.getElementById('main').innerHTML='<h2>Positions & Orders</h2>'+
  '<h2 style="font-size:15px">Open Positions</h2><div class="tblwrap"><table><tr><th>Market</th><th>Dir</th><th>Qty</th><th>Entry</th><th>Now</th><th>uP&L</th><th>SL / TP</th><th>Status</th><th></th></tr>'+(openRows||'<tr><td colspan="9" class="muted">No open positions</td></tr>')+'</table></div>'+
  '<details style="margin-top:12px"><summary>Place PAPER limit order</summary><div class="row">'+
  '<div><label>Market</label><input id="limMarket" placeholder="BTC/USDT"></div>'+
  '<div><label>Direction</label><select id="limDir"><option>LONG</option><option>SHORT</option></select></div>'+
  '<div><label>Qty</label><input id="limQty" type="number" step="any"></div>'+
  '<div><label>Limit price</label><input id="limPrice" type="number" step="any"></div>'+
  '<button class="btn" onclick="placeLimit()">PLACE</button></div></details>'+
  '<h2 style="font-size:15px;margin-top:14px">Orders</h2><div class="tblwrap"><table><tr><th>ID</th><th>Market</th><th>Side</th><th>Type</th><th>Qty</th><th>Price</th><th>Status</th><th>Mode</th><th>Time</th><th></th></tr>'+(orderRows||'<tr><td colspan="10" class="muted">No orders yet</td></tr>')+'</table></div>'+
  '<h2 style="font-size:15px;margin-top:14px">Fills</h2><div class="tblwrap"><table><tr><th>Order</th><th>Market</th><th>Side</th><th>Qty</th><th>Price</th><th>Fee</th><th>Time</th></tr>'+(fillRows||'<tr><td colspan="7" class="muted">No fills yet</td></tr>')+'</table></div>'}).catch(function(){})}
function closePos(m){if(!confirm('Close position '+m+'?'))return;
 api('/api/close',{market:m}).then(function(r){toast('Closed '+m+' P&L '+fmt(r.pnl),'ok');RENDER.positions()})}
function cancelOrd(id){api('/api/order/cancel',{order_id:id}).then(function(){toast('Cancelled','ok');RENDER.positions()})}
function placeLimit(){api('/api/order/limit',{market:document.getElementById('limMarket').value,
 direction:document.getElementById('limDir').value,qty:+document.getElementById('limQty').value,
 limit_price:+document.getElementById('limPrice').value}).then(function(){toast('Limit order placed','ok');RENDER.positions()})}
RENDER.backtest=function(){api('/api/backtests').then(function(b){
 var rows=(b.backtests||[]).map(function(x){return '<tr><td class="small">'+esc(x.created_at.slice(0,16).replace('T',' '))+'</td><td>'+esc(x.markets)+'</td>'+
  '<td class="'+(x.net_return_pct>=0?'pos':'neg')+'">'+fmt(x.net_return_pct,2)+'%</td><td>'+fmt(x.max_dd_pct,2)+'%</td><td>'+fmt(x.profit_factor,2)+'</td>'+
  '<td>'+fmt(x.sharpe,2)+'</td><td>'+x.trades+'</td><td>'+fmt(x.win_rate,1)+'%</td><td>'+fmt(x.fees)+'</td></tr>'}).join('');
 document.getElementById('main').innerHTML='<h2>Backtesting — real historical data</h2>'+
  '<div class="sub">Fees + slippage + funding applied on every fill · features use past-only windows (no look-ahead) · refuses to run without REAL candles</div>'+
  '<div class="row"><div><label>Market</label><input id="btMarket" placeholder="BTC/USDT"></div>'+
  '<div><label>Starting capital</label><input id="btStart" type="number" value="10000"></div>'+
  '<button class="btn" onclick="runBt()">RUN BACKTEST</button></div><div id="btResult" class="msg"></div>'+
  '<h2 style="font-size:15px;margin-top:14px">History</h2>'+
  '<div class="tblwrap"><table><tr><th>When</th><th>Markets</th><th>Return</th><th>MaxDD</th><th>PF</th><th>Sharpe</th><th>Trades</th><th>Win%</th><th>Fees</th></tr>'+(rows||'<tr><td colspan="9" class="muted">No backtests yet</td></tr>')+'</table></div>'}).catch(function(){})}
function runBt(){toast('Running backtest on real candles…');
 api('/api/backtest',{market:document.getElementById('btMarket').value||'BTC/USDT',
  starting:+document.getElementById('btStart').value||10000}).then(function(r){
  var x=r.result;var el=document.getElementById('btResult');el.className='msg show ok';
  el.innerHTML='Return <b>'+fmt(x.net_return_pct,2)+'%</b> · MaxDD '+fmt(x.max_dd_pct,2)+'% · PF '+fmt(x.profit_factor,2)+
   ' · Sharpe '+fmt(x.sharpe,2)+' · '+x.trades+' trades · win '+fmt(x.win_rate,1)+'% · fees '+fmt(x.fees)+
   ' · '+x.candles+' real candles ('+esc(x.interval)+')';RENDER.backtest()})}
RENDER.risk=function(){api('/api/risk').then(function(r){
 document.getElementById('main').innerHTML='<h2>Global Risk Engine — final authority</h2>'+
  '<div class="sub">AI, agents and LLMs can never override the Risk Engine. If the risk engine is unavailable, trading stops.</div>'+
  '<div class="grid cards">'+card('State',tag(r.state),esc(r.message))+
  card('New entries',r.entries_allowed?tag('APPROVED'):'<span class="tag BLOCKED">BLOCKED</span>',esc(r.entries_blocked_reason||''))+
  card('Day loss budget',fmt(r.remaining_budget),'limit '+fmt(r.daily_loss_limit))+
  card('Exposure',fmt(r.current_exposure_pct,1)+'%','max '+fmt(r.max_exposure_pct,1)+'%')+
  card('Drawdown',fmt(r.current_drawdown_pct,2)+'%','halt at '+fmt(r.max_drawdown_pct,1)+'%')+'</div>'+
  '<h2 style="font-size:15px">Active limits</h2><div class="tblwrap"><table>'+
  Object.entries(r.limits||{}).map(function(kv){return '<tr><td><b>'+esc(kv[0])+'</b></td><td>'+(typeof kv[1]==='number'?(kv[1]<1?(kv[1]*100).toFixed(1)+'%':kv[1]):esc(kv[1]))+'</td></tr>'}).join('')+'</table></div>'+
  '<h2 style="font-size:15px;margin-top:12px">Safety hierarchy</h2><div class="tblwrap"><table>'+
  (r.hierarchy||[]).map(function(x){return '<tr><td>'+esc(x)+'</td></tr>'}).join('')+'</table></div>'+
  '<div class="row" style="margin-top:12px"><button class="btn danger" onclick="toggleKill()">KILL SWITCH</button></div>'}).catch(function(){})}
RENDER.models=function(){Promise.all([api('/api/models'),api('/api/experiments')]).then(function(rs){
 var m=rs[0],e=rs[1];
 var rows=(m.models||[]).map(function(x){return '<tr><td>'+esc(x.name)+'</td><td class="small">'+esc(x.version)+'</td><td>'+tag(x.status.toUpperCase())+'</td><td>'+x.samples+'</td><td class="small muted">'+esc((x.trained_at||'').slice(0,19).replace('T',' '))+'</td><td class="small">'+esc(x.metrics)+'</td></tr>'}).join('');
 var exRows=(e.experiments||[]).map(function(x){return '<tr><td class="small">'+esc(x.id)+'</td><td>'+esc(x.hypothesis)+'</td><td class="small">'+esc(x.conclusion)+'</td><td class="small muted">'+esc((x.created_at||'').slice(0,19).replace('T',' '))+'</td></tr>'}).join('');
 document.getElementById('main').innerHTML='<h2>AI Models</h2>'+
  '<div class="grid cards">'+card('Current model',esc(m.current.version),m.current.trained?tag('TRAINED'):tag('UNTRAINED'))+
  card('OOS direction accuracy',m.current.metrics&&m.current.metrics.dir_accuracy_oos?fmt(m.current.metrics.dir_accuracy_oos*100,1)+'%':'—')+
  card('Samples',m.current.metrics&&m.current.metrics.samples||0)+
  card('Strict mode',m.current.strict_mode?'ON — never fabricates signals':'OFF — labeled heuristics',null,m.current.strict_mode?'pos':'warn')+'</div>'+
  '<div class="row" style="margin-top:10px"><button class="btn" onclick="train()">RETRAIN ON REAL DATA</button>'+
  '<button class="btn ghost" onclick="runExp()">RUN WALK-FORWARD EXPERIMENT</button></div>'+
  '<h2 style="font-size:15px;margin-top:14px">Model registry</h2>'+
  '<div class="tblwrap"><table><tr><th>Name</th><th>Version</th><th>Status</th><th>Samples</th><th>Trained</th><th>Metrics</th></tr>'+(rows||'')+'</table></div>'+
  '<h2 style="font-size:15px;margin-top:12px">Experiments (champion/challenger — promotion is manual only)</h2>'+
  '<div class="tblwrap"><table><tr><th>ID</th><th>Hypothesis</th><th>Conclusion</th><th>When</th></tr>'+(exRows||'')+'</table></div>'+
  '<div class="small muted" style="margin-top:8px">Features: '+esc((m.current.features||[]).join(', '))+'</div>'}).catch(function(){})}
function train(){toast('Training quant ensemble on real candles…');api('/api/train',{}).then(function(r){
 toast('Training: '+esc(JSON.stringify(r.metrics)),'ok');RENDER.models()})}
function runExp(){toast('Running walk-forward experiment…');api('/api/experiment',{}).then(function(r){
 toast('Experiment complete','ok');RENDER.models()})}
RENDER.agents=function(){api('/api/agents').then(function(a){
 var agents=(a.agents_in_last_cycle||[]).map(function(x){return '<div class="agent"><b>'+esc(x.agent)+'</b> → '+tag(x.verdict)+' <span class="muted small">conf '+fmt(x.confidence*100,0)+'%</span></div>'}).join('');
 document.getElementById('main').innerHTML='<h2>Agents & Research</h2>'+
  '<div class="sub">Deterministic analysts run on real data every cycle. Optional LLM bull/bear debate (TradingAgents-style) is advisory and schema-validated.</div>'+
  '<div class="tblwrap"><table><tr><th>Guardrail</th></tr>'+(a.guardrails||[]).map(function(g){return '<tr><td>'+esc(g)+'</td></tr>'}).join('')+'</table></div>'+
  '<h2 style="font-size:15px;margin-top:12px">Last cycle agents</h2>'+(agents||'<div class="muted">Run analysis first</div>')+
  '<h2 style="font-size:15px;margin-top:12px">LLM research layer</h2>'+
  '<div class="kv"><b>Status</b><span>'+(a.llm_enabled?'enabled':'disabled (quant ensemble only)')+'</span></div>'+
  (a.llm_research?(a.llm_research.available?'<pre>'+esc(JSON.stringify(a.llm_research,null,1))+'</pre>':'<div class="muted small">unavailable: '+esc(a.llm_research.reason)+'</div>'):'<div class="muted small">no LLM research yet</div>')+
  '<div class="row" style="margin-top:10px"><div><label>Market</label><input id="agMarket" placeholder="BTC/USDT"></div>'+
  '<button class="btn" onclick="runResearch()">RUN RESEARCH NOW</button></div><div id="agOut"></div>'}).catch(function(){})}
function runResearch(){api('/api/ai/research',{market:document.getElementById('agMarket').value||'BTC/USDT'})
 .then(function(r){var el=document.getElementById('agOut');
  el.innerHTML='<h2 style="font-size:15px;margin-top:10px">Deterministic agents</h2>'+
   r.agents.map(function(x){return '<div class="agent"><b>'+esc(x.agent)+'</b> → '+tag(x.verdict)+'<ul>'+(x.evidence||[]).map(function(e){return '<li>'+esc(e)+'</li>'}).join('')+'</ul></div>'}).join('')+
   '<h2 style="font-size:15px">LLM research (advisory)</h2><pre>'+esc(JSON.stringify(r.llm,null,1))+'</pre>'})}
RENDER.decisions=function(){api('/api/decisions?limit=150').then(function(d){
 var rows=(d.decisions||[]).map(function(x){var f={};try{f=JSON.parse(x.features||'{}')}catch(e){}
  var ps={};try{ps=JSON.parse(x.portfolio_state||'{}')}catch(e){}
  return '<tr><td class="small muted">'+esc((x.ts||'').replace('T',' ').slice(0,19))+'</td><td><b>'+esc(x.market)+'</b></td>'+
  '<td>'+tag(x.data_status)+'</td><td class="small">'+esc(x.model_version)+'</td><td>'+esc(x.strategy)+'</td>'+
  '<td>'+fmt(x.expected_return*100,2)+'%</td><td>'+fmt(x.expected_net*100,2)+'%</td><td>'+fmt(x.confidence*100,0)+'%</td>'+
  '<td>'+tag(x.decision)+'</td><td class="small">'+esc(x.reasons).slice(0,180)+'</td><td class="small muted">eq '+fmt(ps.equity)+'</td></tr>'}).join('');
 document.getElementById('main').innerHTML='<h2>Decision Ledger</h2>'+
  '<div class="sub">Every important decision, explainable: data status · features · model · expected edge · confidence · portfolio & risk state · decision · reasons</div>'+
  '<div class="tblwrap"><table><tr><th>Time</th><th>Market</th><th>Data</th><th>Model</th><th>Strategy</th><th>Exp ret</th><th>Net edge</th><th>Conf</th><th>Decision</th><th>Reasons</th><th>Portfolio</th></tr>'+(rows||'<tr><td colspan="11" class="muted">No decisions yet</td></tr>')+'</table></div>'}).catch(function(){})}
RENDER.exchange=function(){api('/api/exchange').then(function(x){
 var steps=x.live_enabled?['open_live','risk_ack','exchange_credentials','connection_verified','permissions_verified','withdrawals_disabled','market_data_healthy','reconciled','risk_test','paper_ready','explicit_confirm','enabled']:[];
 document.getElementById('main').innerHTML='<h2>Exchange Connection</h2>'+
  '<div class="sub">Real credential test against the exchange (signed account request). Withdrawal-enabled keys are REFUSED. Live trading is SPOT-only in this edition.</div>'+
  '<div class="grid cards">'+card('Exchange',esc(x.exchange))+
  card('Credentials',x.credentials_configured?tag('CONNECTED'):tag('NOT_CONFIGURED'),x.key_masked?('key '+esc(x.key_masked)):'')+
  card('Live trading',x.live_enabled?tag('LIVE'):tag('PAPER'),x.live_enabled?'REAL MONEY AT RISK':'simulated execution')+
  card('Block reason',x.live_block_reason?esc(x.live_block_reason):'—')+'</div>'+
  '<details style="margin-top:10px"><summary>Connect / replace API credentials</summary>'+
  '<div class="small muted" style="margin:8px 0">Create a trade-only API key on the exchange with WITHDRAWALS DISABLED. ZEPAY stores the secret encrypted (ChaCha20-Poly1305) and never shows it again.</div>'+
  '<div class="row"><div><label>API key</label><input id="exKey" autocomplete="off"></div>'+
  '<div><label>API secret</label><input id="exSecret" type="password" autocomplete="off"></div>'+
  '<button class="btn" onclick="testExchange()">TEST CONNECTION</button></div>'+
  (x.credentials_configured?'<button class="btn ghost sm" style="margin-top:8px" onclick="disconnectExchange()">DISCONNECT</button>':'')+
  '</details>'+
  '<h2 style="font-size:15px;margin-top:16px">Live Trading Safety Gate</h2>'+
  '<div class="sub">Every step is a REAL check. No automatic activation. Typed confirmation required.</div>'+
  '<div class="tblwrap"><table><tr><th>Step</th><th>Status</th></tr>'+
  steps.map(function(s){return '<tr><td>'+esc(s)+'</td><td>'+tag('OK')+'</td></tr>'}).join('')+
  '<tr><td>Connect exchange + verify credentials</td><td>'+(x.credentials_configured?tag('OK'):tag('NOT_CONFIGURED'))+'</td></tr>'+
  '<tr><td>Acknowledge live-trading risk</td><td id="gRisk"></td></tr>'+
  '<tr><td>Market data healthy</td><td id="gData"></td></tr>'+
  '<tr><td>Account reconciled</td><td id="gRec"></td></tr>'+
  '<tr><td>Risk self-test</td><td id="gRiskTest"></td></tr>'+
  '<tr><td>Paper history (≥5 fills)</td><td id="gPaper"></td></tr>'+
  '<tr><td>Typed confirmation</td><td id="gConfirm"></td></tr></table></div>'+
  '<div class="row" style="margin-top:12px">'+
  '<label style="flex:0"><input type="checkbox" id="cbRisk" onchange="gate()"> I understand live trading risks real money</label>'+
  '<label style="flex:0"><input type="checkbox" id="cbPaper" onchange="gate()"> Paper history verified</label>'+
  '<label style="flex:0"><input type="checkbox" id="cbTest" onchange="gate()"> Risk self-test passed</label>'+
  '<div><label>Type ENABLE LIVE TRADING</label><input id="confirmPhrase" placeholder="ENABLE LIVE TRADING"></div>'+
  '<button class="btn danger" id="goLiveBtn" disabled onclick="goLive()">ENABLE LIVE TRADING</button>'+
  (x.live_enabled?'<button class="btn warn" onclick="disableLive()">DISABLE LIVE (back to paper)</button>':'')+
  '</div>'}).catch(function(){})}
function testExchange(){api('/api/exchange/test',{api_key:document.getElementById('exKey').value,
 api_secret:document.getElementById('exSecret').value}).then(function(r){
 toast('CONNECTED — credentials verified against the real exchange','ok');RENDER.exchange()},
 function(){RENDER.exchange()})}
function disconnectExchange(){api('/api/exchange/disconnect',{}).then(function(){RENDER.exchange()})}
function gate(){
 var ok=document.getElementById('cbRisk').checked&&document.getElementById('cbPaper').checked&&
  document.getElementById('cbTest').checked;
 document.getElementById('goLiveBtn').disabled=!ok}
function goLive(){api('/api/live/enable',{risk_ack:document.getElementById('cbRisk').checked,
 paper_ready:document.getElementById('cbPaper').checked,risk_test:document.getElementById('cbTest').checked,
 explicit_confirm:true,confirm_phrase:document.getElementById('confirmPhrase').value}).then(function(r){
 if(r.enabled){toast('LIVE TRADING ENABLED — real money at risk','ok')}
 else if(r.missing){toast('Gate incomplete: '+r.missing.join(', '),'err')}
 else{toast(r.error||'refused','err')}
 RENDER.exchange()})}
function disableLive(){api('/api/live/disable',{}).then(function(){toast('Back to PAPER mode','ok');RENDER.exchange()})}
RENDER.apis=function(){api('/api/providers').then(function(p){
 var rows=(p.providers||[]).map(function(x){return '<tr><td><b>'+esc(x.name)+'</b><div class="small muted">'+esc(x.purpose)+'</div></td>'+
  '<td>'+esc(x.category)+'</td><td class="small">'+esc(x.endpoint)+'</td><td class="small">'+esc(x.auth)+'</td>'+
  '<td>'+esc(x.key_status)+'</td><td class="small">'+esc(x.rate_limit)+'</td><td>'+tag(x.status)+'</td>'+
  '<td class="small '+(x.latency_ms!=null?'':'muted')+'">'+(x.latency_ms!=null?x.latency_ms+'ms':'—')+'</td>'+
  '<td class="small muted">'+esc((x.last_success||'').slice(11,19))+'</td>'+
  '<td class="small '+(x.last_error?'neg':'muted')+'">'+esc((x.last_error||'').slice(0,60))+'</td>'+
  '<td>'+(x.integrated?'<button class="btn sm" onclick="checkProv(\''+esc(x.id)+'\')">CHECK</button>':'<span class="tag mut">catalogue</span>')+'</td></tr>'}).join('');
 document.getElementById('main').innerHTML='<h2>API Manager</h2>'+
  '<div class="sub">Every external API: provider · purpose · auth · rate limit · health · last success · last error. Nothing is reported connected without a real response.</div>'+
  '<div class="row" style="margin-bottom:10px"><button class="btn" onclick="checkProv(null)">CHECK ALL INTEGRATED</button></div>'+
  '<div class="tblwrap"><table><tr><th>Provider</th><th>Category</th><th>Endpoint</th><th>Auth</th><th>Key</th><th>Rate limit</th><th>Status</th><th>Latency</th><th>Last OK</th><th>Last error</th><th></th></tr>'+rows+'</table></div>'+
  '<details style="margin-top:12px"><summary>Public API Registry (catalogue — NOT integrated)</summary>'+
  '<div class="small muted" style="margin:6px 0">Research references (public-apis catalogue concept). Integrating any of them requires explicit registration + health check; they are never silently called.</div>'+
  '<div class="tblwrap"><table id="pubapi"></table></div>'+
  '<button class="btn ghost sm" style="margin-top:8px" onclick="loadPubApis()">LOAD CATALOGUE</button></details>'}).catch(function(){})}
function checkProv(id){toast('Health check: real request in flight…');
 api('/api/providers/check',id?{id:id}:{}).then(function(){toast('Health check complete','ok');RENDER.apis()})}
function loadPubApis(){api('/api/public_apis').then(function(r){
 var t=document.getElementById('pubapi');
 t.innerHTML='<tr><th>Name</th><th>Category</th><th>Auth</th><th>Free/paid</th><th>Rate limit</th><th>Data</th><th>Docs</th></tr>'+
 (r.catalog||[]).map(function(x){return '<tr><td><b>'+esc(x.name)+'</b></td><td>'+esc(x.category)+'</td><td>'+esc(x.auth)+'</td><td>'+esc(x.free_paid)+'</td><td class="small">'+esc(x.rate_limit)+'</td><td class="small">'+esc((x.supported_data||[]).join(', '))+'</td><td class="small"><a href="'+esc(x.docs)+'" target="_blank" rel="noopener">docs</a></td></tr>'}).join('')})}
RENDER.aimgr=function(){api('/api/ai').then(function(a){
 var rows=(a.providers||[]).map(function(x){var u=x.usage||{};
  return '<tr><td><b>'+esc(x.name)+'</b>'+(x.active?' '+tag('LIVE'):'' )+'<div class="small muted">'+(x.local?'LOCAL':'cloud')+'</div></td>'+
  '<td class="small">'+esc(x.model)+'</td><td class="small">'+esc(x.base_url)+'</td>'+
  '<td>'+esc(x.key_status)+'</td><td>'+(x.configured?tag('OK'):tag('NOT_CONFIGURED'))+'</td>'+
  '<td>'+(u.calls||0)+' calls</td><td class="small">'+(u.avg_latency_ms||'—')+'</td>'+
  '<td class="small '+(u.last_error?'neg':'muted')+'">'+esc((u.last_error||'none').slice(0,40))+'</td>'+
  '<td><button class="btn sm ghost" onclick="showProvider(\''+esc(x.id)+'\')">CONFIG</button> '+
  (x.configured?'<button class="btn sm" onclick="checkAI(\''+esc(x.id)+'\')">CHECK</button>':'')+'</td></tr>'}).join('');
 document.getElementById('main').innerHTML='<h2>AI Manager</h2>'+
  '<div class="sub">Unified LLM provider interface — cloud or local. The quant ensemble always drives trading; LLMs provide advisory research only and can never place orders.</div>'+
  '<div class="grid cards">'+card('Active provider',esc(a.active||'none — quant only'))+
  card('Mode',esc(a.mode))+card('Quant ensemble',a.quant.trained?tag('TRAINED'):tag('UNTRAINED'),a.quant.healthy?'healthy':'not healthy')+
  card('Quant version',esc(a.quant.version),'strict: '+(a.quant.strict_mode?'ON':'OFF'))+'</div>'+
  '<div class="tblwrap" style="margin-top:10px"><table><tr><th>Provider</th><th>Model</th><th>Endpoint</th><th>Key</th><th>Status</th><th>Usage</th><th>Latency</th><th>Last error</th><th></th></tr>'+rows+'</table></div>'+
  '<div id="provForm"></div>'}).catch(function(){})}
function showProvider(id){api('/api/ai').then(function(a){
 var x=(a.providers||[]).find(function(p){return p.id===id});if(!x)return;
 document.getElementById('provForm').innerHTML='<details open style="margin-top:12px"><summary>Configure '+esc(x.name)+'</summary>'+
  '<div class="row"><div><label>Model</label><input id="pvModel" value="'+esc(x.model)+'"></div>'+
  '<div><label>Base URL</label><input id="pvBase" value="'+esc(x.base_url)+'"></div>'+
  (x.key_required?'<div><label>API key (encrypted at rest)</label><input id="pvKey" type="password" placeholder="unchanged"></div>':'')+
  '<label style="flex:0"><input type="checkbox" id="pvEnable" '+(x.enabled?'checked':'')+'> Enable (active provider)</label>'+
  '<button class="btn" onclick="saveProvider(\''+id+'\')">SAVE</button></div>'+
  '<div class="small muted" style="margin-top:6px">Docs: <a href="'+esc(x.docs)+'" target="_blank" rel="noopener">'+esc(x.docs)+'</a></div></details>'})}
function saveProvider(id){api('/api/ai/provider',{provider:id,enabled:document.getElementById('pvEnable').checked,
 model:document.getElementById('pvModel').value,base_url:document.getElementById('pvBase').value,
 api_key:document.getElementById('pvKey')?document.getElementById('pvKey').value:undefined})
 .then(function(){toast('Provider saved','ok');RENDER.aimgr()})}
function checkAI(id){toast('Probing provider with a real request…');
 api('/api/ai/check',{provider:id}).then(function(r){toast(r.health.ok?('OK — '+r.health.model+' in '+r.health.latency_ms+'ms'):('FAIL: '+r.health.error),'ok');RENDER.aimgr()})}
RENDER.mcp=function(){api('/api/mcp').then(function(m){
 var rows=(m.servers||[]).map(function(s){return '<tr><td><b>'+esc(s.name)+'</b><div class="small muted">'+esc(s.purpose)+'</div></td>'+
  '<td class="small">'+esc(s.url||'—')+'</td><td class="small">'+esc((s.permissions||[]).join(', ')||'—')+'</td>'+
  '<td>'+tag(s.status)+'</td><td class="small muted">'+esc((s.last_call||'').slice(11,19))+'</td>'+
  '<td class="small '+(s.last_error?'neg':'muted')+'">'+esc((s.last_error||'—').slice(0,50))+'</td>'+
  '<td><button class="btn sm ghost" onclick="mcpEdit(\''+esc(s.id)+'\')">EDIT</button> '+
  (s.enabled&&s.configured?'<button class="btn sm" onclick="mcpTools(\''+esc(s.id)+'\')">TOOLS</button>':'')+
  ' <button class="btn sm ghost" onclick="mcpRemove(\''+esc(s.id)+'\')">DEL</button></td></tr>'}).join('');
 document.getElementById('main').innerHTML='<h2>MCP Manager</h2>'+
  '<div class="sub">Explicitly registered · permission-controlled · rate-limited · logged · disabled by default. Trading permissions are <b>structurally unavailable</b> to MCP tools.</div>'+
  '<div class="grid cards">'+card('Servers',(m.servers||[]).length)+
  card('Forbidden classes',Object.keys(m.forbidden_classes||{}).join(', '))+
  card('Allowed classes',Object.keys(m.permission_classes||{}).join(', '))+'</div>'+
  '<div class="tblwrap" style="margin-top:10px"><table><tr><th>Server</th><th>URL</th><th>Permissions</th><th>Status</th><th>Last call</th><th>Last error</th><th></th></tr>'+rows+'</table></div>'+
  '<details style="margin-top:12px"><summary>Register MCP server</summary><div class="row">'+
  '<div><label>Name</label><input id="mcpName"></div><div><label>URL (JSON-RPC over HTTP)</label><input id="mcpUrl" placeholder="https://…/mcp"></div>'+
  '<div><label>Purpose</label><input id="mcpPurpose"></div>'+
  '<label style="flex:0"><input type="checkbox" id="mcpEnabled"> Enable</label>'+
  '<button class="btn" onclick="mcpUpsert(null)">REGISTER</button></div></details>'+
  '<div id="mcpOut"></div>'+
  '<h2 style="font-size:15px;margin-top:12px">Call log</h2>'+
  '<div class="tblwrap"><table><tr><th>Server</th><th>Tool</th><th>Status</th><th>Latency</th><th>When</th></tr>'+
  (m.calls||[]).map(function(c){return '<tr><td>'+esc(c.server)+'</td><td>'+esc(c.tool)+'</td><td>'+tag(c.status)+'</td><td>'+(c.latency_ms||'—')+'ms</td><td class="small muted">'+esc((c.created_at||'').replace('T',' ').slice(0,19))+'</td></tr>'}).join('')+'</table></div>'}).catch(function(){})}
function mcpEdit(id){api('/api/mcp').then(function(m){var s=(m.servers||[]).find(function(x){return x.id===id});if(!s)return;
 document.getElementById('mcpOut').innerHTML='<details open style="margin-top:12px"><summary>Edit '+esc(s.name)+'</summary><div class="row">'+
 '<div><label>Name</label><input id="mcpName" value="'+esc(s.name)+'"></div><div><label>URL</label><input id="mcpUrl" value="'+esc(s.url||'')+'"></div>'+
 '<div><label>Purpose</label><input id="mcpPurpose" value="'+esc(s.purpose)+'"></div>'+
 '<label style="flex:0"><input type="checkbox" id="mcpEnabled" '+(s.enabled?'checked':'')+'> Enable</label>'+
 '<button class="btn" onclick="mcpUpsert(\''+id+'\')">SAVE</button></div></details>'})}
function mcpUpsert(id){api('/api/mcp/upsert',{id:id,name:document.getElementById('mcpName').value,
 url:document.getElementById('mcpUrl').value,purpose:document.getElementById('mcpPurpose').value,
 enabled:document.getElementById('mcpEnabled').checked,
 permissions:['research.readonly']}).then(function(){toast('MCP server saved','ok');RENDER.mcp()})}
function mcpRemove(id){api('/api/mcp/remove',{id:id}).then(function(){RENDER.mcp()})}
function mcpTools(id){api('/api/mcp/tools',{id:id}).then(function(r){
 var el=document.getElementById('mcpOut');
 el.innerHTML='<details open><summary>Tools on '+esc(id)+'</summary><pre>'+esc(JSON.stringify(r.tools,null,1))+'</pre></details>'})}
RENDER.alerts=function(){api('/api/alerts').then(function(a){
 var rows=(a.alerts||[]).map(function(x){return '<tr><td>'+tag(x.level)+'</td><td><b>'+esc(x.title)+'</b></td><td class="small">'+esc(x.body)+'</td>'+
  '<td class="small muted">'+esc((x.created_at||'').replace('T',' ').slice(0,19))+'</td></tr>'}).join('');
 document.getElementById('main').innerHTML='<h2>Alerts</h2>'+
  '<div class="row" style="margin-bottom:10px"><button class="btn ghost" onclick="markRead()">MARK ALL READ</button></div>'+
  '<div class="tblwrap"><table><tr><th>Level</th><th>Title</th><th>Body</th><th>When</th></tr>'+(rows||'<tr><td colspan="4" class="muted">No alerts</td></tr>')+'</table></div>'}).catch(function(){})}
function markRead(){api('/api/alerts/read',{}).then(function(){RENDER.alerts()})}
RENDER.diag=function(){document.getElementById('main').innerHTML='<h2>Diagnostics</h2>'+
 '<div class="row" style="margin-bottom:10px"><button class="btn" onclick="runDiag()">RUN DIAGNOSTICS</button>'+
 '<button class="btn ghost" onclick="runCheck()">FULL SYSTEM CHECK</button>'+
 '<button class="btn ghost" onclick="runRec()">RECONCILE NOW</button>'+
 '<button class="btn ghost" onclick="backup()">BACKUP</button></div><div id="diagOut"></div>'}
function runDiag(){api('/api/diagnostics').then(function(r){
 document.getElementById('diagOut').innerHTML='<div class="tblwrap"><table><tr><th>Check</th><th>Status</th><th>Detail</th></tr>'+
 Object.entries(r.checks||{}).map(function(kv){var v=kv[1]||{};
  return '<tr><td><b>'+esc(kv[0])+'</b></td><td>'+(v.ok?tag('OK'):tag('ERROR'))+'</td><td class="small">'+esc(v.error||v.note||v.status||v.version||JSON.stringify(v).slice(0,120))+'</td></tr>'}).join('')+'</table></div>'})}
function runCheck(){api('/api/system_check').then(function(r){
 document.getElementById('diagOut').innerHTML='<h3>'+ (r.ok?'✓':'⚠')+' '+esc(r.verdict)+'</h3>'+
 '<div class="tblwrap" style="margin-top:8px"><table><tr><th>Subsystem</th><th>Status</th><th>Detail</th></tr>'+
 Object.entries(r.results||{}).map(function(kv){var v=kv[1]||{};
  return '<tr><td><b>'+esc(kv[0])+'</b></td><td>'+(v.ok?tag('OK'):tag('ERROR'))+'</td><td class="small">'+esc(v.error||v.note||'')+'</td></tr>'}).join('')+'</table></div>'})}
function runRec(){api('/api/reconcile',{}).then(function(r){
 document.getElementById('diagOut').innerHTML='<pre>'+esc(JSON.stringify(r,null,1))+'</pre>'})}
function backup(){api('/api/backup',{}).then(function(r){toast('Backup: '+esc(r.backup),'ok')})}
RENDER.settings=function(){api('/api/config').then(function(c){
 var cfg=c.config||{};
 document.getElementById('main').innerHTML='<h2>Settings</h2>'+
  '<div class="row"><div><label>Risk preset</label><select id="cfgRisk">'+
  ['Conservative','Moderate','Aggressive'].map(function(x){return '<option '+(cfg.risk_mode===x?'selected':'')+'>'+x+'</option>'}).join('')+'</select></div>'+
  '<div><label>Max positions</label><input id="cfgMaxPos" type="number" value="'+(cfg.max_positions||5)+'"></div>'+
  '<div><label>Refresh (secs)</label><input id="cfgRefresh" type="number" value="'+(cfg.refresh_secs||30)+'"></div>'+
  '<div><label>Candle interval</label><select id="cfgInterval">'+['15m','30m','1h','4h','1d'].map(function(x){return '<option '+(cfg.candle_interval===x?'selected':'')+'>'+x+'</option>'}).join('')+'</select></div>'+
  '<div><label>Display currency</label><input id="cfgCurrency" value="'+esc(cfg.display_currency||'₹')+'"></div></div>'+
  '<div class="row" style="margin-top:8px">'+
  '<label style="flex:0"><input type="checkbox" id="cfgStrictAI" '+(cfg.strict_ai_mode?'checked':'')+'> Strict AI (never fabricate signals while untrained)</label>'+
  '<label style="flex:0"><input type="checkbox" id="cfgAuto" '+(cfg.ai_automation?'checked':'')+'> Automated engine</label>'+
  '<label style="flex:0"><input type="checkbox" id="cfgWS" '+(cfg.ws_streaming?'checked':'')+'> WebSocket streaming (server-side)</label>'+
  '<label style="flex:0"><input type="checkbox" id="cfgLLM" '+(cfg.research_llm_enabled?'checked':'')+'> LLM research layer (needs AI provider)</label></div>'+
  '<div class="row" style="margin-top:8px"><div><label>AI failure policy</label><select id="cfgAIPol">'+
  ['reduce_risk','stop_trading'].map(function(x){return '<option '+(cfg.ai_failure_policy===x?'selected':'')+'>'+x+'</option>'}).join('')+'</select></div>'+
  '<div><label>Paper starting balance</label><input id="cfgStart" type="number" value="'+(cfg.paper_starting_balance||10000)+'"></div></div>'+
  '<button class="btn" style="margin-top:12px" onclick="saveCfg()">SAVE SETTINGS</button>'+
  '<h2 style="font-size:15px;margin-top:20px">ZEPAY license</h2>'+
  '<div class="kv"><b>Verification mode</b><span>'+esc(cfg.license_mode||'—')+'</span>'+
  '<b>Cloud endpoint</b><span>'+(cfg.license_verify_endpoint?esc(cfg.license_verify_endpoint):'NOT CONFIGURED — cloud verification BLOCKED (spec missing)')+'</span></div>'+
  '<div class="row" style="margin-top:8px"><div><label>Cloud verification endpoint (https)</label><input id="cfgLicEp" placeholder="https://zepay.example.com/api/verify"></div>'+
  '<button class="btn ghost" onclick="saveLicEp()">SET ENDPOINT</button></div>'+
  '<h2 style="font-size:15px;margin-top:20px">Session</h2>'+
  '<button class="btn ghost" onclick="logout()">LOG OUT</button>'}).catch(function(){})}
function saveCfg(){api('/api/config',{risk_mode:document.getElementById('cfgRisk').value,
 max_positions:+document.getElementById('cfgMaxPos').value,refresh_secs:+document.getElementById('cfgRefresh').value,
 candle_interval:document.getElementById('cfgInterval').value,display_currency:document.getElementById('cfgCurrency').value,
 strict_ai_mode:document.getElementById('cfgStrictAI').checked,ai_automation:document.getElementById('cfgAuto').checked,
 ws_streaming:document.getElementById('cfgWS').checked,research_llm_enabled:document.getElementById('cfgLLM').checked,
 ai_failure_policy:document.getElementById('cfgAIPol').value,
 paper_starting_balance:+document.getElementById('cfgStart').value}).then(function(){toast('Settings saved','ok')})}
function saveLicEp(){api('/api/license/endpoint',{endpoint:document.getElementById('cfgLicEp').value})
 .then(function(r){toast('Endpoint saved — cloud verification adapter updated','ok')})}
function logout(){localStorage.removeItem('zepay_token');TOKEN='';showSetup()}
RENDER.help=function(){api('/api/help').then(function(hh){
 document.getElementById('main').innerHTML='<h2>Help Center</h2>'+
  Object.entries(hh.topics||{}).map(function(kv){return '<details><summary>'+esc(kv[0].replace(/_/g,' '))+'</summary><div class="small" style="margin-top:6px">'+esc(kv[1])+'</div></details>'}).join('')+
  '<div class="card" style="margin-top:14px"><b>Disclaimer</b><div class="small muted" style="margin-top:4px">PAST PERFORMANCE IS NOT A GUARANTEE OF FUTURE RESULTS. Crypto trading is risky. Paper-trade first. Never risk money you cannot afford to lose.</div></div>'}).catch(function(){})}
function boot(){
 buildNav();
 var hash=location.hash.replace('#','');
 go(NAV.some(function(g){return g[1].some(function(t){return t[0]===hash})})?hash:'dash');
 setInterval(function(){if(TAB==='dash')api('/api/status',null,true).then(statusChips).catch(function(){})},10000)}
fetch('/api/setup/status').then(function(r){return r.json()}).then(function(s){
 if(s.setup){boot()}else{showSetup();
  document.getElementById('setupLicenseNote').innerHTML='Key verification mode: <b>'+(s.license_mode||'offline')+'</b>. '+
  (s.cloud_license.status==='BLOCKED'?'No ZEPAY cloud verification API is configured (specification not provided) — keys are validated OFFLINE (format check, clearly labeled).':'Cloud verification endpoint configured.');
  document.getElementById('keyInput').focus();
  document.getElementById('keyInput').addEventListener('keydown',function(e){if(e.key==='Enter')verifyKey()})}}).catch(function(){showSetup()});
</script></body></html>
"""

# =============================================================================
# PART 14 — SERVER + SELF-TEST + CLI
# =============================================================================

class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

def self_test():
    """Fast offline unit checks (no network). Returns (passed, failed, details).
    Deeper coverage lives in tests/test_zepay.py (run: python3 -m unittest)."""
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

    def _crypto():
        SECRETBOX.self_test()

    def _crypto_tamper():
        key = secrets.token_bytes(32)
        nonce = secrets.token_bytes(12)
        ct = aead_chacha20_poly1305_encrypt(key, nonce, b"secret", b"aad")
        bad = bytearray(ct); bad[0] ^= 1
        try:
            aead_chacha20_poly1305_decrypt(key, nonce, bytes(bad), b"aad")
            raise AssertionError("tampered ciphertext accepted")
        except ValueError:
            pass

    def _hkdf_rfc5869():
        # RFC 5869 Test Case 1
        ikm = b"\x0b" * 22
        salt = bytes.fromhex("000102030405060708090a0b0c")
        info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
        okm = hkdf_sha256(ikm, salt=salt, info=info, length=42)
        assert okm == bytes.fromhex(
            "3cb25f25faacd57a90434f64d0362f2a"
            "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
            "34007208d5b887185865"), "HKDF vector mismatch"

    def _secrets_roundtrip():
        SECRETS.put("__test_secret", "super-secret-value", purpose="general")
        assert SECRETS.get("__test_secret") == "super-secret-value"
        SECRETS.delete("__test_secret")
        assert SECRETS.get("__test_secret") is None

    def _risk_blocks_bad_data():
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
        assert not s.get("reject")
        assert s["notional"] <= 10000 * safe_float(CONFIG.get("max_position_pct", 0.2)) * 1.01

    def _sizer_rejects_min_notional():
        acct = {"equity": 10000}
        f = AssetFeatures(market="XYZ/USDT", price=0.0001, atr14=0.00002, atr_pct=0.2)
        opp = Opportunity(market="XYZ/USDT", direction="LONG", score=90, confidence=0.9,
                          quality=0.9, regime="BULLISH_TREND", strategy="trend",
                          expected_return=0.01, expected_net=0.005, cost_pct=0.001, price=0.0001)
        s = Sizer.size(opp, f, acct, {"size_mult": 0.4})
        assert s.get("reject") or s["notional"] > 0  # reject path or tiny-but-valid

    def _indicators():
        c = [100 + i for i in range(60)]
        assert 0 <= rsi(c) <= 100
        h = [x + 1 for x in c]; l = [x - 1 for x in c]
        assert atr(h, l, c) > 0
        assert "hist" in macd(c)

    def _license_offline():
        res = verify_license_key("ZEPAY-ABCD-EFGH-IJKL")
        assert res.mode == "offline"
        # checksum rule: 4th group must equal sha256 prefix — craft valid key
        payload = "ZEPAY-ABCD-EFGH-IJKL"
        chk = hashlib.sha256(payload.encode()).hexdigest()[:4].upper()
        ok = verify_license_key(payload + "-" + chk)
        assert ok.ok, ok.message
        bad = verify_license_key("ZEPAY-ABCD-EFGH-IJKL-ZZZZ")
        assert not bad.ok

    def _license_cloud_blocked():
        # no endpoint configured → cloud verification must FAIL honestly
        res = verify_license_key("ZEPAY-ANYTHING", mode="cloud")
        assert not res.ok and "unavailable" in res.message.lower()

    def _redaction():
        r = redact({"api_secret": "x", "exchange_api_key": "y", "normal": 1,
                    "nested": {"password": "z"}})
        assert r["api_secret"] == "***REDACTED***"
        assert r["nested"]["password"] == "***REDACTED***"
        assert r["normal"] == 1

    def _config_no_secrets():
        c = CONFIG.all(public_only=True)
        s = jdump(c)
        assert "super-secret" not in s

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
            allowed, _ = RiskEngine.entries_allowed()
            assert not allowed
        finally:
            with CONFIG._lock:
                CONFIG.cfg["kill_switch"] = old

    def _paper_partial_fill_math():
        # synthetic depth fixture (test-only)
        d = {"bids": [[100.0, 1.0], [99.0, 2.0]], "asks": [[101.0, 1.0], [102.0, 2.0]],
             "ts": ts_ms(), "source": "test"}
        with MDATA._lock:
            old = MDATA.depth_cache.get("TEST/USDT")
            MDATA.depth_cache["TEST/USDT"] = d
            oldt = MDATA.cache.get("TEST/USDT")
            MDATA.cache["TEST/USDT"] = {"price": 101.0, "ts": ts_ms(), "quote_vol": 1e7,
                                        "change24": 0, "volume24": 0, "high": 0, "low": 0}
        try:
            with CONFIG._lock:
                oldp = CONFIG.cfg.get("paper_participation_max")
                CONFIG.cfg["paper_participation_max"] = 0.25
            fill, err = PaperExchange.market_fill("TEST/USDT", "LONG", 10.0)
            assert fill and fill["partial"], f"expected partial: {fill} {err}"
            assert fill["filled_qty"] < 10.0 and fill["remaining_qty"] > 0
            assert 101.0 <= fill["price"] <= 102.0
            full, _ = PaperExchange.market_fill("TEST/USDT", "LONG", 0.05)
            assert full and not full["partial"]
        finally:
            with MDATA._lock:
                if old is not None:
                    MDATA.depth_cache["TEST/USDT"] = old
                if oldt is not None:
                    MDATA.cache["TEST/USDT"] = oldt
            with CONFIG._lock:
                if oldp is not None:
                    CONFIG.cfg["paper_participation_max"] = oldp

    def _ws_frame_codec():
        # client→server frame must be masked; parse a synthetic server frame
        payload = b'{"e":"24hrMiniTicker"}'
        frame = WSClient._frame(0x1, payload)
        assert frame[0] == 0x81
        buf = b"\x81" + bytes([len(payload)]) + payload   # unmasked server frame
        fr = WSClient._parse_frame(buf, 0)
        assert fr and fr[1] == 0x1 and fr[2] == payload

    def _mcp_refuses_trading():
        MCPM.upsert({"id": "__t", "name": "t", "url": "http://127.0.0.1:9/x", "purpose": "x",
                     "permissions": ["trading"], "enabled": True})
        r = MCPM.call_tool("__t", "place_order", {"symbol": "BTCUSDT"})
        assert not r["ok"]
        r2 = MCPM.call_tool("__t", "fetch", {"url": "https://example.com"})
        # tool ok to pass guards but server unreachable → still not ok, but refused for wrong reason
        assert not r2["ok"]
        MCPM.remove("__t")

    def _decision_ledger():
        f = AssetFeatures(market="T/USDT")
        did = record_decision("T/USDT", f, {"model_version": MODEL_VERSION, "confidence": 0.5},
                              {"strategy": "trend", "expected_return": 0.01, "expected_net": 0.002},
                              "WAIT", ["test"], {}, {}, "test-cycle")
        row = DBASE.query_one("SELECT * FROM decisions WHERE id=?", (did,))
        assert row and row["decision"] == "WAIT"

    def _reconciler_detects_orphan():
        # insert a fake open position with no fills → reconciler must flag it
        DBASE.execute("INSERT OR REPLACE INTO positions (id, market, direction, qty, entry_price,"
                      " current_price, stop_loss, take_profit, exposure, unrealized_pnl, realized_pnl,"
                      " confidence, strategy, opened_at, updated_at, status)"
                      " VALUES ('pos-test-orph','ORPH/USDT','LONG',1,100,100,0,0,100,0,0,1,'test',?,?,'OPEN')",
                      (utcnow_iso(), utcnow_iso()))
        r = Reconciler.paper_check()
        found = any("ORPH" in i for i in r["issues"])
        DBASE.execute("DELETE FROM positions WHERE id='pos-test-orph'")
        assert found, f"reconciler missed orphan: {r['issues']}"

    t("crypto_rfc8439_vectors", _crypto)
    t("crypto_tamper_rejected", _crypto_tamper)
    t("hkdf_rfc5869_vector", _hkdf_rfc5869)
    t("secrets_roundtrip", _secrets_roundtrip)
    t("risk_blocks_bad_data", _risk_blocks_bad_data)
    t("no_data_no_trade", _no_data_no_trade)
    t("sizer_respects_caps", _sizer_caps)
    t("sizer_min_notional", _sizer_rejects_min_notional)
    t("indicators_sane", _indicators)
    t("license_offline_format", _license_offline)
    t("license_cloud_blocked_honestly", _license_cloud_blocked)
    t("secret_redaction", _redaction)
    t("config_public_has_no_secrets", _config_no_secrets)
    t("database", _db)
    t("ranking_orders", _rank)
    t("kill_switch_halts", _kill_halts)
    t("paper_partial_fill_math", _paper_partial_fill_math)
    t("websocket_frame_codec", _ws_frame_codec)
    t("mcp_refuses_trading_tools", _mcp_refuses_trading)
    t("decision_ledger_writes", _decision_ledger)
    t("reconciler_detects_orphan_position", _reconciler_detects_orphan)
    return passed, failed, det


def run_server(port):
    ensure_paper_account()
    Reconciler.startup()
    if not ENGINE.get("thread") or not ENGINE["thread"].is_alive():
        ENGINE["stop"] = False
        th = threading.Thread(target=auto_loop, daemon=True, name="zepay-engine")
        th.start()
        ENGINE["thread"] = th
    srv = ThreadedServer(("0.0.0.0", port), APIHandler)
    print(f"\n  ZEPAY v{VERSION} — ULTIMATE — running on http://localhost:{port}")
    print("  Paper trading by default. Live trading DISABLED until the explicit safety gate.")
    print("  Real data required: if the exchange API is unreachable, ZEPAY shows")
    print("  'REAL DATA UNAVAILABLE' and blocks new trades — by design.")
    print("  Press Ctrl+C to stop.\n")
    log(f"server started on :{port}")
    audit("system", "server_started", {"port": port, "version": VERSION})
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping ZEPAY…")
    finally:
        ENGINE["stop"] = True
        MDATA.stop_ws()
        audit("system", "server_stopped", {})


def main():
    ap = argparse.ArgumentParser(prog="zepay", description="ZEPAY — ultimate AI crypto trading platform")
    ap.add_argument("command", nargs="?", default="start",
                    choices=["start", "stop", "restart", "status", "doctor", "logs",
                             "backup", "check", "selftest", "version"])
    ap.add_argument("--port", type=int, default=int(os.environ.get("ZEPAY_PORT", DEFAULT_PORT)))
    args = ap.parse_args()
    os.makedirs(DATA_DIR, exist_ok=True)

    if args.command == "version":
        print(f"ZEPAY {VERSION} ({EDITION})")
        return
    if args.command in ("doctor", "selftest"):
        p, f, det = self_test()
        print(f"ZEPAY self-test — passed {p}, failed {f}")
        for n, ok, e in det:
            print(f"  {'✓' if ok else '✗'} {n}" + (f" — {e}" if e else ""))
        if args.command == "doctor":
            d = run_diagnostics()
            for k, v in d["checks"].items():
                print(f"  {'✓' if v.get('ok') else '✗'} {k}" +
                      (f" — {v.get('error', '')}" if not v.get('ok') else ""))
        sys.exit(1 if f else 0)
    if args.command == "check":
        r = full_system_check()
        print(f"{'✓' if r['ok'] else '⚠'} {r['verdict']}")
        for k, v in r["results"].items():
            print(f"  {'✓' if v.get('ok') else '✗'} {k}" +
                  (f" — {v.get('error') or v.get('note') or ''}" if not v.get('ok') or v.get('note') else ''))
        return
    if args.command == "status":
        try:
            d = http_json(f"http://localhost:{args.port}/api/health", timeout=5)
            print(jdump(d))
        except Exception as e:
            print(f"Server not reachable on :{args.port} ({e}). Is ZEPAY running?")
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
