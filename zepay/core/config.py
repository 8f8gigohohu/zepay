"""ZEPAY V3 configuration.

Two layers:
  * Settings   — process-level wiring (data dir, DB backend, Redis URL, ports).
                 Read from environment once at boot. Never holds secrets.
  * ConfigStore — persistent, user-editable runtime configuration (risk limits,
                 universe, stage, feature flags) stored as JSON in the data dir.
                 Privileged keys (kill switch, live gating, credential refs) are
                 audit-logged on every change. Secrets themselves are NEVER
                 stored here — only ids pointing into the encrypted SecretsStore.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field

from zepay.core.errors import ConfigError
from zepay.core.util import utcnow_iso

VERSION = "3.0.0"
EDITION = "v3-modular"
APP_NAME = "ZEPAY"

# The single most important constant in this codebase (preserved from v2).
# If real data is not available, trading STOPS — nothing is ever substituted.
REAL_DATA_REQUIRED = True

DEFAULT_DATA_DIR = os.environ.get("ZEPAY_DATA_DIR") or os.path.join(os.getcwd(), "zepay_data")


@dataclass
class Settings:
    data_dir: str = field(
        default_factory=lambda: os.environ.get("ZEPAY_DATA_DIR") or DEFAULT_DATA_DIR
    )
    host: str = field(default_factory=lambda: os.environ.get("ZEPAY_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("ZEPAY_PORT", "8000")))
    db_backend: str = field(
        default_factory=lambda: os.environ.get("ZEPAY_DB_BACKEND", "sqlite").lower()
    )
    postgres_dsn: str = field(default_factory=lambda: os.environ.get("ZEPAY_POSTGRES_DSN", ""))
    redis_url: str = field(default_factory=lambda: os.environ.get("ZEPAY_REDIS_URL", ""))
    log_level: str = field(default_factory=lambda: os.environ.get("ZEPAY_LOG_LEVEL", "INFO"))
    log_json: bool = field(default_factory=lambda: os.environ.get("ZEPAY_LOG_JSON", "0") == "1")
    engine_autostart: bool = field(
        default_factory=lambda: os.environ.get("ZEPAY_AUTOSTART", "1") == "1"
    )

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "zepay.db")

    @property
    def config_path(self) -> str:
        return os.path.join(self.data_dir, "config.json")

    @property
    def backup_dir(self) -> str:
        return os.path.join(self.data_dir, "backups")

    @property
    def log_path(self) -> str:
        return os.path.join(self.data_dir, "zepay.log")

    @property
    def master_key_path(self) -> str:
        return os.path.join(self.data_dir, ".master.key")

    @property
    def model_dir(self) -> str:
        return os.path.join(self.data_dir, "models")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.backup_dir, self.model_dir):
            os.makedirs(d, exist_ok=True)


SETTINGS = Settings()


DEFAULT_CONFIG = {
    "version": VERSION,
    "setup_complete": False,
    "license_key_hash": None,
    "license_mode": None,
    "license_verify_endpoint": None,
    # --- §22 operational stage. Promotion is manual, never automatic. ---
    "operational_stage": "PAPER",  # PAPER | SHADOW | LIMITED_LIVE | FULL_LIVE
    "stage_approved_by": None,
    "live_enabled": False,  # legacy flag kept in sync with stage
    "live_confirmed_steps": [],
    "live_block_reason": "",
    "limited_live_max_notional_usd": 50.0,  # hard cap while LIMITED_LIVE
    "limited_live_max_positions": 1,
    "full_live_max_position_pct": 0.20,
    # --- account / universe ---
    "paper_starting_balance": 10000.0,
    "quote_currency": "USDT",
    "display_currency": "₹",
    "universe": ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    "priorities": {},
    # --- venues (§9) ---
    "venues_enabled": {
        "binance_spot": True,
        "binance_futures": False,  # requires signed creds + explicit enable
        "zepay": False,  # NOT_CONFIGURED until endpoint spec exists
        "solana": False,  # read-only market data unless wallet connected
    },
    "exchange": "binance_spot",  # fallback execution venue for live (per-instrument routing wins)
    "exchange_api_key_id": None,  # reference into encrypted SecretsStore
    "exchange_account_type": "SPOT",
    # --- multi-venue routing (v3.1: per-instrument venue selection) ---
    "default_trading_venue": "binance_spot",  # binance_spot | binance_futures | zepay
    "market_venue_overrides": {},  # symbol → venue (set via /markets/{m}/venue)
    "futures_universe_scan": True,  # scan fapi exchangeInfo for the catalogue (public)
    "futures_leverage": 1,  # requested leverage; capped by risk engine + venue brackets
    "max_futures_leverage": 3.0,  # Risk Engine futures cap (hard ceiling 5x, §21)
    "futures_margin_mode": "ISOLATED",  # ISOLATED | CROSSED
    # --- ZEPAY license (§6) ---
    "require_license_key": True,  # trading gated until a key is verified
    # --- risk (§21) — Risk Engine is ALWAYS more powerful than AI ---
    "risk_mode": "Conservative",
    "max_positions": 5,
    "max_position_pct": 0.15,
    "max_total_exposure_pct": 0.45,
    "max_asset_exposure_pct": 0.25,
    "max_correlated_exposure_pct": 0.40,
    "max_directional_exposure_pct": 0.50,
    "max_leverage": 1.0,  # spot default; futures capped by venue rules
    "daily_loss_limit_pct": 0.02,
    "max_drawdown_halt_pct": 0.15,
    "max_consecutive_losses": 5,
    "risk_per_trade_pct": 0.0075,
    "min_edge_pct": 0.002,
    "max_spread_bps": 25.0,
    "min_liquidity_usd": 50000.0,
    "stale_data_secs": 120,
    "kill_switch": False,
    "kill_switch_cancel_orders": True,
    "kill_switch_flatten_policy": "none",  # none | reduce | flatten (manual trigger only)
    "trading_halted": False,
    "halt_reason": "",
    # --- costs / execution ---
    "fee_bps": 10.0,
    "slippage_bps": 5.0,
    "funding_bps_8h": 1.0,
    "paper_participation_max": 0.25,
    "paper_latency_ms": 120,
    "min_order_notional_usd": 10.0,
    "execution_quality_min_score": 0.5,
    # --- data engine ---
    "candle_interval": "1h",
    "candle_limit": 200,
    "refresh_secs": 30,
    "ws_streaming": True,  # V3: real-time layer ON by default (REST remains source of truth)
    "ws_stale_secs": 25,
    "ws_reconnect_max_secs": 120,
    "ws_heartbeat_secs": 180,  # Binance combined streams: server ping every 3 min
    "derivs_enabled": True,  # funding/OI context when futures public API reachable
    # --- AI ---
    "ai_automation": True,
    "strict_ai_mode": True,  # untrained/degraded models → WAIT, never fabricated signals
    "ai_failure_policy": "reduce_risk",  # reduce_risk | stop_trading
    "ai_mode": "LOCAL_QUANT_ONLY",  # LOCAL_QUANT_ONLY | CLOUD_LLM | LOCAL_LLM | HYBRID
    "ai_providers": {},  # provider_id → {enabled, base_url, model, key_id}
    "research_llm_enabled": False,
    "research_llm_interval_cycles": 10,
    "ensemble_mode": "regime_weighted",  # equal | weighted | regime_weighted
    "auto_research": False,
    "auto_research_interval_hours": 24,
    # --- ops ---
    "notifications": True,
    "webhook_url": None,
    "reconcile_interval_cycles": 20,
    "mcp_servers": [],
}

RISK_PRESETS = {
    "Conservative": {
        "max_positions": 5,
        "max_position_pct": 0.15,
        "max_total_exposure_pct": 0.45,
        "risk_per_trade_pct": 0.0075,
        "daily_loss_limit_pct": 0.02,
        "min_edge_pct": 0.002,
    },
    "Moderate": {
        "max_positions": 6,
        "max_position_pct": 0.20,
        "max_total_exposure_pct": 0.60,
        "risk_per_trade_pct": 0.01,
        "daily_loss_limit_pct": 0.03,
        "min_edge_pct": 0.0015,
    },
    "Aggressive": {
        "max_positions": 8,
        "max_position_pct": 0.25,
        "max_total_exposure_pct": 0.80,
        "risk_per_trade_pct": 0.015,
        "daily_loss_limit_pct": 0.05,
        "min_edge_pct": 0.001,
    },
}

PRIVILEGED_KEYS = (
    "license_key_hash",
    "license_mode",
    "live_enabled",
    "operational_stage",
    "trading_mode",
    "kill_switch",
    "trading_halted",
    "halt_reason",
    "live_confirmed_steps",
    "exchange_api_key_id",
    "live_block_reason",
    "stage_approved_by",
    "kill_switch_flatten_policy",
    "venues_enabled",
)

# keys that may never be changed through the public settings API
IMMUTABLE_KEYS = ("version",)


class ConfigStore:
    """Thread-safe persistent runtime config."""

    def __init__(self, path: str, audit_fn=None):
        self.path = path
        self.cfg: dict = {}
        self._lock = threading.RLock()
        self._audit = audit_fn
        self.load()

    def load(self) -> None:
        with self._lock:
            self.cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
            if os.path.exists(self.path):
                try:
                    with open(self.path, encoding="utf-8") as f:
                        stored = json.load(f)
                    if isinstance(stored, dict):
                        self.cfg.update(stored)
                except Exception as e:
                    raise ConfigError(f"config file corrupt: {e}") from e

    def save_locked(self) -> None:
        tmp = self.path + ".tmp"
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2, default=str)
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except Exception:
            pass

    def save(self) -> None:
        with self._lock:
            self.save_locked()

    def get(self, k, d=None):
        with self._lock:
            v = self.cfg.get(k, d)
            return v

    def all(self, public_only: bool = True) -> dict:
        with self._lock:
            cfg = json.loads(json.dumps(self.cfg, default=str))
        if public_only:
            for k in PRIVILEGED_KEYS:
                if k in cfg and cfg[k] not in (None, False, "", [], {}):
                    cfg[k] = "***SET***" if k.endswith("_id") or "key" in k else cfg[k]
            cfg.pop("license_key_hash", None)
        return cfg

    def update(self, patch: dict, actor: str = "user") -> dict:
        changed = {}
        with self._lock:
            for k, v in (patch or {}).items():
                if k in IMMUTABLE_KEYS:
                    continue
                if k not in DEFAULT_CONFIG:
                    continue  # unknown keys ignored (schema is explicit)
                if self.cfg.get(k) != v:
                    changed[k] = {"old": self.cfg.get(k), "new": v}
                    self.cfg[k] = v
            if "risk_mode" in changed and changed["risk_mode"]["new"] in RISK_PRESETS:
                preset = RISK_PRESETS[changed["risk_mode"]["new"]]
                for pk, pv in preset.items():
                    self.cfg[pk] = pv
                    changed[pk] = {"old": None, "new": pv, "via_preset": True}
            self.cfg["updated_at"] = utcnow_iso()
            self.save_locked()
        if changed and self._audit:
            try:
                self._audit("config", "update", {"changed_keys": list(changed.keys())}, actor=actor)
            except Exception:
                pass
        return changed
