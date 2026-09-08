"""ZEPAY V3 database schema (§44).

Portable DDL for SQLite (default) and PostgreSQL (production option).
Persistent trading records live here — Redis is a cache/bus ONLY and can be
lost at any time without corrupting state.
"""

from __future__ import annotations

SCHEMA_VERSION = 3

# --------------------------------------------------------------------------
# SQLite DDL (default backend; also used by the test suite)
# --------------------------------------------------------------------------
SQLITE_DDL = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT NOT NULL,               -- PAPER | SHADOW | LIMITED_LIVE | FULL_LIVE
  equity REAL DEFAULT 0, available REAL DEFAULT 0, invested REAL DEFAULT 0,
  realized_pnl REAL DEFAULT 0, unrealized_pnl REAL DEFAULT 0,
  drawdown_pct REAL DEFAULT 0, peak_equity REAL DEFAULT 0,
  day_pnl REAL DEFAULT 0, day TEXT, fees_paid REAL DEFAULT 0, funding_paid REAL DEFAULT 0,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS balances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  venue TEXT NOT NULL, mode TEXT NOT NULL, asset TEXT NOT NULL,
  free REAL DEFAULT 0, locked REAL DEFAULT 0, usd_value REAL DEFAULT 0,
  source TEXT, ts TEXT,
  UNIQUE(venue, mode, asset)
);

CREATE TABLE IF NOT EXISTS instruments (
  instrument_id TEXT PRIMARY KEY,
  venue TEXT NOT NULL, symbol TEXT NOT NULL, exchange_symbol TEXT,
  base_asset TEXT, quote_asset TEXT, trading_mode TEXT, contract_type TEXT,
  status TEXT, tick_size REAL, step_size REAL, min_qty REAL, min_notional REAL,
  price_precision INTEGER, qty_precision INTEGER,
  maker_fee_bps REAL, taker_fee_bps REAL, max_leverage REAL, margin_mode TEXT,
  metadata TEXT, discovered_at TEXT, source TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS candles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  venue TEXT DEFAULT 'binance_spot', market TEXT NOT NULL, interval_tf TEXT NOT NULL,
  open_time INTEGER NOT NULL, o REAL, h REAL, l REAL, c REAL, v REAL,
  quote_vol REAL, trades INTEGER, fetched_at TEXT,
  UNIQUE(venue, market, interval_tf, open_time)
);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  venue TEXT, market TEXT, ts INTEGER, bids TEXT, asks TEXT, spread_bps REAL,
  imbalance REAL, depth_usd REAL
);

CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY,
  client_order_id TEXT, exchange_order_id TEXT,
  venue TEXT, market TEXT NOT NULL, side TEXT, direction TEXT,
  qty REAL, filled_qty REAL DEFAULT 0, avg_fill_price REAL,
  price REAL, limit_price REAL, stop_price REAL,
  order_type TEXT, time_in_force TEXT, status TEXT, mode TEXT,
  reduce_only INTEGER DEFAULT 0, post_only INTEGER DEFAULT 0,
  strategy TEXT, model_version TEXT, model_id TEXT,
  decision_id TEXT, risk_decision_id TEXT, idempotency_key TEXT UNIQUE,
  reason TEXT, reject_reason TEXT,
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS order_transitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id TEXT NOT NULL, from_status TEXT, to_status TEXT,
  reason TEXT, venue TEXT, ts TEXT
);

CREATE TABLE IF NOT EXISTS fills (
  id TEXT PRIMARY KEY,
  order_id TEXT, venue TEXT, market TEXT, side TEXT,
  qty REAL, price REAL, fee REAL, fee_asset TEXT, slippage REAL,
  maker INTEGER DEFAULT 0, pnl REAL, latency_ms INTEGER, filled_at TEXT
);

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  venue TEXT, mode TEXT, market TEXT, direction TEXT,
  entry_price REAL, exit_price REAL, qty REAL,
  gross_pnl REAL, fees REAL, funding REAL, slippage REAL, net_pnl REAL,
  mae REAL, mfe REAL, bars_held INTEGER,
  strategy TEXT, model_version TEXT, opened_at TEXT, closed_at TEXT
);

CREATE TABLE IF NOT EXISTS positions (
  id TEXT PRIMARY KEY,
  venue TEXT, mode TEXT, market TEXT NOT NULL, direction TEXT,
  qty REAL, entry_price REAL, current_price REAL,
  stop_loss REAL, take_profit REAL,
  exposure REAL, unrealized_pnl REAL, realized_pnl REAL,
  leverage REAL DEFAULT 1, margin_used REAL DEFAULT 0, liquidation_price REAL,
  confidence REAL, strategy TEXT, model_version TEXT, decision_id TEXT,
  opened_at TEXT, updated_at TEXT, status TEXT
);

CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT, venue TEXT, direction TEXT, score REAL, confidence REAL,
  regime TEXT, strategy TEXT, edge_net_pct REAL, reasons TEXT,
  model_version TEXT, cycle_id TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS features (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT, venue TEXT, ts TEXT, price REAL, feature_json TEXT,
  feature_version TEXT, cycle_id TEXT
);

CREATE TABLE IF NOT EXISTS strategies (
  id TEXT PRIMARY KEY, name TEXT, kind TEXT, enabled INTEGER,
  params TEXT, regime_fit TEXT, version TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS models (
  id TEXT PRIMARY KEY, name TEXT, version TEXT, kind TEXT,
  lifecycle TEXT, status TEXT, champion INTEGER DEFAULT 0,
  features TEXT, dataset TEXT, train_period TEXT, valid_period TEXT, oos_period TEXT,
  metrics TEXT, calibration TEXT, feature_importance TEXT, drift_status TEXT,
  params_blob TEXT, samples INTEGER, trained_at TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS model_predictions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  model_id TEXT, market TEXT, ts TEXT, direction TEXT, p_long REAL,
  expected_return REAL, expected_vol REAL, confidence REAL, quality REAL,
  regime TEXT, outcome_known INTEGER DEFAULT 0, outcome_return REAL,
  outcome_direction TEXT, cycle_id TEXT
);

CREATE TABLE IF NOT EXISTS model_promotions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  model_id TEXT, from_lifecycle TEXT, to_lifecycle TEXT,
  champion INTEGER, approved_by TEXT, evidence TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS alpha_factors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, definition TEXT, ic_mean REAL, ic_ir REAL, ts TEXT
);

CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY, kind TEXT, name TEXT, hypothesis TEXT,
  dataset TEXT, feature_version TEXT, model_version TEXT, strategy TEXT,
  params TEXT, train_period TEXT, valid_period TEXT, oos_period TEXT,
  costs TEXT, results TEXT, drawdown REAL, sharpe REAL, sortino REAL, calmar REAL,
  profit_factor REAL, expectancy REAL, trade_count INTEGER, conclusion TEXT,
  status TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS walk_forward_runs (
  id TEXT PRIMARY KEY, experiment_id TEXT, model_id TEXT, strategy TEXT,
  windows TEXT, oos_results TEXT, aggregate TEXT, stability REAL,
  status TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS backtests (
  id TEXT PRIMARY KEY, name TEXT, markets TEXT, period TEXT, strategy TEXT,
  starting_capital REAL, ending_value REAL, net_return_pct REAL, max_dd_pct REAL,
  profit_factor REAL, sharpe REAL, sortino REAL, calmar REAL, expectancy REAL,
  trades INTEGER, win_rate REAL, fees REAL, slippage REAL, funding REAL,
  mae_avg REAL, mfe_avg REAL, details TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS risk_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT, market TEXT, decision TEXT, reasons TEXT,
  system_state TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT DEFAULT 'PAPER', equity REAL, available REAL, exposure_pct REAL,
  long_pct REAL, short_pct REAL, positions INTEGER, day_pnl REAL,
  drawdown_pct REAL, var_95 REAL, cvar_95 REAL, state TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_exposures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mode TEXT, market TEXT, direction TEXT, exposure REAL, weight REAL,
  vol_contribution REAL, beta REAL, ts TEXT
);

CREATE TABLE IF NOT EXISTS correlations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  a TEXT, b TEXT, corr REAL, window_n INTEGER, created_at TEXT
);

CREATE TABLE IF NOT EXISTS execution_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id TEXT, venue TEXT, market TEXT, mode TEXT,
  expected_price REAL, actual_price REAL, slippage_bps REAL, spread_bps REAL,
  latency_ms INTEGER, fill_ratio REAL, partial INTEGER, rejected INTEGER,
  maker INTEGER, market_impact_bps REAL, quality_score REAL, ts TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  level TEXT, title TEXT, body TEXT, delivered INTEGER DEFAULT 0, created_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  category TEXT, action TEXT, details TEXT, actor TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS system_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  component TEXT, level TEXT, message TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  venue TEXT, market TEXT, kind TEXT,   -- STALE | GAP | MALFORMED | OUTAGE | SEQ_RESET | DUPLICATE
  detail TEXT, stream TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY, ts TEXT, market TEXT, venue TEXT, data_status TEXT,
  features TEXT, model_version TEXT, strategy TEXT,
  expected_return REAL, expected_net REAL, confidence REAL,
  portfolio_state TEXT, risk_state TEXT, decision TEXT, reasons TEXT,
  order_id TEXT, fill_price REAL, pnl REAL, cycle_id TEXT,
  stage TEXT
);

CREATE TABLE IF NOT EXISTS secrets (
  name TEXT PRIMARY KEY, purpose TEXT, token TEXT, created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS provider_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT, kind TEXT, status TEXT, detail TEXT, latency_ms INTEGER, created_at TEXT
);

CREATE TABLE IF NOT EXISTS research_reports (
  id TEXT PRIMARY KEY, ts TEXT, cycle_id TEXT, kind TEXT, agents TEXT, summary TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS mcp_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  server TEXT, tool TEXT, allowed INTEGER, reason TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS ws_stream_state (
  stream TEXT PRIMARY KEY, venue TEXT, state TEXT, messages INTEGER,
  last_message_ts INTEGER, reconnects INTEGER, last_error TEXT, updated_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_candles_market ON candles(market, interval_tf, open_time);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status, mode);
CREATE INDEX IF NOT EXISTS ix_positions_status ON positions(status, market);
CREATE INDEX IF NOT EXISTS ix_fills_order ON fills(order_id);
CREATE INDEX IF NOT EXISTS ix_decisions_ts ON decisions(ts);
CREATE INDEX IF NOT EXISTS ix_signals_ts ON signals(created_at);
CREATE INDEX IF NOT EXISTS ix_predictions_model ON model_predictions(model_id, ts);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_logs(created_at);
"""

# --------------------------------------------------------------------------
# PostgreSQL DDL — same tables, native types. Used when ZEPAY_DB_BACKEND=postgres.
# --------------------------------------------------------------------------
POSTGRES_DDL = (
    SQLITE_DDL.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    .replace(
        "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);",
        "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);",
    )
    .replace("id TEXT PRIMARY KEY", "id TEXT PRIMARY KEY")
)

# kv-style migration columns for forward compatibility (v2 → v3 upgrades).
MIGRATION_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "orders": [
        ("client_order_id", "TEXT"),
        ("venue", "TEXT"),
        ("model_id", "TEXT"),
        ("risk_decision_id", "TEXT"),
        ("time_in_force", "TEXT"),
        ("reduce_only", "INTEGER DEFAULT 0"),
        ("post_only", "INTEGER DEFAULT 0"),
        ("stop_price", "REAL"),
        ("reject_reason", "TEXT"),
    ],
    "positions": [
        ("venue", "TEXT"),
        ("mode", "TEXT"),
        ("leverage", "REAL DEFAULT 1"),
        ("margin_used", "REAL DEFAULT 0"),
        ("liquidation_price", "REAL"),
        ("model_version", "TEXT"),
        ("decision_id", "TEXT"),
    ],
    "fills": [
        ("venue", "TEXT"),
        ("fee_asset", "TEXT"),
        ("maker", "INTEGER DEFAULT 0"),
        ("latency_ms", "INTEGER"),
    ],
    "candles": [("venue", "TEXT DEFAULT 'binance_spot'")],
    "decisions": [("venue", "TEXT"), ("stage", "TEXT")],
    "accounts": [("fees_paid", "REAL DEFAULT 0"), ("funding_paid", "REAL DEFAULT 0")],
}


def ddl_for(backend: str) -> str:
    return POSTGRES_DDL if backend == "postgres" else SQLITE_DDL
