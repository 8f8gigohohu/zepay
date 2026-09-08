# ZEPAY V3 — Database & Migration Guide

## Backends

| Backend | When | Notes |
|---|---|---|
| **SQLite (default)** | single-node, always works | WAL journal, busy_timeout 15 s, `synchronous=NORMAL`. File: `$ZEPAY_DATA_DIR/zepay.db` |
| **PostgreSQL** | production multi-service | `ZEPAY_DB_BACKEND=postgres` + `ZEPAY_POSTGRES_DSN=postgresql://…`. SQL is auto-translated (`?` → `%s`, `INSERT OR REPLACE` → upsert). If unreachable at boot the system **falls back to SQLite with a logged error** — it never pretends Postgres is in use |
| **Redis (optional)** | faster cache/pub-sub | `ZEPAY_REDIS_URL=redis://…`; health-checked at boot; falls back to a labeled in-memory cache |

## Migration model

Migrations are **idempotent and run at every boot** (`database/base.py`):

1. `CREATE TABLE IF NOT EXISTS` for the full V3 schema (`database/schema.py`).
2. `MIGRATION_COLUMNS` applies additive `ALTER TABLE … ADD COLUMN` for tables
   that existed in V2 (SQLite: try/ignore-if-exists; Postgres:
   `ADD COLUMN IF NOT EXISTS`). **Additive only — no destructive migration
   exists anywhere in V3.**
3. `kv.schema_version = '3'` is stamped.

### Upgrading a V2 database (in place)

```bash
# v2 data dir contains zepay.db with v2 tables
export ZEPAY_DATA_DIR=/path/to/old/zepay_data
python -m zepay.apps.server      # boot applies V3 DDL + additive columns
```

What happens to V2 data:

* **Kept as-is:** accounts/balances history, candles, orders, fills, trades,
  positions, signals, decisions, models, backtests, risk_events, audit_logs,
  alerts, secrets (still decryptable — same ChaCha20-Poly1305 master key at
  `$ZEPAY_DATA_DIR/.master.key`), kv store, config.json (unknown/removed v2
  keys ignored; privileged keys never silently re-enabled — `operational_stage`
  resets to the DEFAULT_CONFIG value **PAPER** only if absent; an existing
  stored stage is preserved but `live_enabled` must still pass the V3 gate
  checks before any live order).
* **New V3 tables:** instruments, order_transitions, orderbook_snapshots,
  execution_metrics, data_quality_events, model_promotions,
  walk_forward_runs, portfolio_exposures, alpha_factors, ws_stream_state,
  provider_events, research_reports, mcp_calls.
* **Extended V2 tables (additive columns):**
  * orders: client_order_id, venue, model_id, risk_decision_id,
    time_in_force, reduce_only, post_only, stop_price, reject_reason
    (+ idempotency_key UNIQUE in fresh schema)
  * positions: venue, mode, leverage, margin_used, liquidation_price,
    model_version, decision_id
  * fills: venue, fee_asset, maker, latency_ms
  * decisions: venue, stage
  * models: params_blob (ensemble reload), calibration, feature_importance,
    drift_status
  * portfolio_snapshots: var_95, cvar_95, state

V2 models are **not** auto-loaded as trained state: the V3 ensemble has a new
feature vector (`features-3.0.0`, 40+ dims). On first V3 boot the AI reports
`UNTRAINED` and strict mode emits `WAIT` until you retrain on real data
(`POST /api/ai/train` or `python -m zepay.cli train`). This is deliberate —
a V2 model scoring V3 features would be silently wrong.

### Downgrade

Not supported (new columns are ignored by V2 code, but V2 cannot read V3
tables). Keep a backup before upgrading: `python -m zepay.cli backup`.

## Backup / restore (§56)

* `POST /api/backup/run` / `python -m zepay.cli backup` — SQLite online backup
  API (consistent while running) + `PRAGMA integrity_check` **on the backup**;
  Postgres via `pg_dump` when the binary exists, otherwise an honest
  `NOT AVAILABLE` failure — a backup is never reported as done when it isn't.
* Rotation: keeps `backup_keep` (default 14) newest files.
* Restore is manual by design (stop → replace `zepay.db` or `psql < dump` →
  start): an automated overwrite path for the audit trail would violate its
  immutability requirement.

## Verification

Migration correctness is covered by booting V3 on a fresh dir in the offline
suite (schema creation) and by the live acceptance run (real inserts into
every new table: decisions, risk_events, execution_metrics,
data_quality_events, ws_stream_state, model_predictions).
