# ZEPAY V3 — Production-Grade Multi-Asset AI-Assisted Trading Platform

**Real data. Real AI. Real risk control. No fake anything.**

ZEPAY V3 replaces the v2 single-file bot (preserved in `legacy/zepay_v2.py`)
with a modular, production architecture: one universal engine trading every
discovered asset across multiple venues, with a risk engine that **always**
outranks the AI, and live trading that is **disabled by default** and can only
be enabled through a manual, typed-confirmation stage gate.

```
PAPER → SHADOW → LIMITED LIVE → FULL LIVE      (promotion is ALWAYS manual)
```

> **Honesty contract.** If an exchange, feed, model or wallet is unreachable,
> ZEPAY reports `UNAVAILABLE / DEGRADED / BLOCKED / NOT CONFIGURED` and blocks
> trading. It never fabricates prices, balances, fills, predictions, backtests
> or profits. Untrained AI emits `WAIT` (strict mode). Withdrawals are
> forbidden platform-wide. LLMs and MCP tools can never touch orders, risk,
> leverage, credentials or the kill switch.

---

## Quick start

```bash
# 1. install (venv + deps + safety selftest)
bash scripts/install.sh

# 2. run the server (API + frontend + engine)
source .venv/bin/activate
python -m zepay.apps.server          # → http://localhost:8000

# or Docker
docker compose up zepay              # SQLite default
docker compose --profile postgres --profile redis up   # full stack
```

CLI (same engine, no browser):

```bash
python -m zepay.cli status | health | cycle | train | backtest BTC/USDT
python -m zepay.cli research | walk-forward | hyperopt BTC/USDT --method bayes
python -m zepay.cli kill "reason"    # engage kill switch NOW
python -m zepay.cli resume           # requires typing: RESUME TRADING
python -m zepay.cli stage | promote "ENABLE SHADOW MODE" | backup | selftest
```

Environment: `ZEPAY_DATA_DIR`, `ZEPAY_HOST`, `ZEPAY_PORT`, `ZEPAY_DB_BACKEND`
(`sqlite`|`postgres`), `ZEPAY_POSTGRES_DSN`, `ZEPAY_REDIS_URL`, `ZEPAY_LOG_LEVEL`,
`ZEPAY_LOG_JSON`, `ZEPAY_AUTOSTART`.

---

## What's inside

| Layer | Components |
|---|---|
| **Core** | Event bus (§55) · typed domain model (Market/Instrument/Venue/Order/Position/Decision) · thread-safe persistent config with privileged-key protection · IDs · errors |
| **Data** | Dynamic universe from live exchange metadata (487+ instruments discovered, zero per-asset code) · REST collector with host failover (binance.vision mirror when api.binance.com is geo-blocked) · professional WS engine (CONNECTED/DEGRADED/STALE/DISCONNECTED/RECOVERING/FAILED, dedupe, sequence checks, backoff, heartbeat) · data-quality engine · market-health engine |
| **Intelligence** | 40+ real indicators · cross-asset engine (BTC influence, breadth, correlations, spillover) · 11-regime engine · 12 strategies · **quant AI ensemble** (direction/return/vol/MAE/MFE/trade-quality, chronological OOS split, Platt calibration + Brier/ECE, regime-weighted fusion, disagreement/uncertainty) · model registry with lifecycle + champion/challenger (**manual promotion only**) · drift monitor (PSI/prediction/calibration → degrade → disable) · walk-forward · penalized hyperopt (grid/random/Bayesian-GP/evolutionary) · research pipeline · advisory-only LLM manager (8 providers incl. local Ollama/vLLM) · 9 deterministic analyst agents |
| **Venues** | Binance Spot · Binance USDT-M Futures (funding/OI/mark) · ZEPAY native (INR) · Solana/Phantom (**wallet-connect only — seed phrases/private keys never touch this process**; Jupiter real quotes + unsigned-tx relay) · venue registry with capabilities/health/test-connection |
| **Execution** | Pre-trade simulation (spread, slippage, fees, funding, latency, net edge) · opportunity ranker · exchange-filter-aware sizer · paper exchange walking the **real order book** (participation cap → explicit partial fills) · shadow mode (records, sends nothing) · live adapter with post-trade verification, UNKNOWN-state handling that **blocks live until reconciled** · order state machine (§33) · execution-quality metrics · reconciler (paper + live, startup + periodic) |
| **Risk** | Global Risk Engine — **final authority, fails closed** (§21) · hierarchy: hard limits > risk > portfolio > execution safety > data validity > strategy > ML > LLM · stage caps · venue/market gating · consecutive-loss gate · leverage + liquidation-distance protection · VaR/CVaR/stress/beta/risk-contribution · kill switch (persisted, typed resume) |
| **Trust** | Decision ledger (every decision explainable, §36/§40) · immutable audit log · alerts · encrypted secrets (ChaCha20-Poly1305, RFC 8439, validated at boot) · withdrawal-enabled API keys **refused** · MCP manager with **no trading permission class in existence** (§50) |
| **Ops** | FastAPI backend (§49) · responsive vanilla-JS frontend (desktop + mobile, SSE live events) · system-health monitor · internal metrics · SQLite default / PostgreSQL + Redis optional · online backups with integrity checks + rotation · Docker · 22-check safety selftest |

## Architecture

One composition root (`zepay/apps/compose.py`) wires every subsystem by
dependency injection — there is exactly **one** engine (`zepay/engine.py`),
one risk authority (`zepay/risk/engine.py`), one execution path
(`zepay/execution/engine.py`). See `docs/v3/ARCHITECTURE.md`.

```
zepay/
  core/        events, domain, config, ids, errors, util
  security/    crypto (RFC 8439), secrets store
  database/    schema (+migrations), sqlite/postgres, redis/memory cache
  audit/       audit log, alerts
  exchanges/   base, ratelimit, http, binance_spot, binance_futures, zepay_venue, registry
  wallets/     solana (Phantom connect + Jupiter)
  market_data/ universe, collector, ws_manager, quality, health
  features/    indicators, feature+cross-asset engines
  regime/      11-regime engine
  strategies/  base, core(6), advanced(6), orchestrator
  ai/          features, models, calibration, engine, explain, llm, agents
  opportunities/ pre-trade simulation + ranker
  portfolio/   intelligence (VaR/CVaR/stress) + accounts
  risk/        engine, sizer, killswitch
  execution/   costs, order_state, paper, quality, engine, reconciler, live_gate
  ml/          registry, drift, walk_forward, hyperopt, research
  backtesting/ engine, analysis (Monte Carlo, robustness)
  mcp/         manager (research-only by construction)
  monitoring/  health, metrics, backup
  api/         FastAPI app + routers (system/markets/trading/intelligence)
  apps/        compose (DI root), server, selftest
  engine.py    the ONE trading engine (cycle orchestrator)
  cli.py       operator CLI
frontend/      index.html + app.js + style.css (no external deps)
legacy/        zepay_v2.py monolith + its test suite (preserved, not imported)
tests/v3/      37 offline tests + 7 gated live-network acceptance tests
docs/v3/       architecture, migrations, license review, final report
```

## Testing & gates

```bash
python -m zepay.apps.selftest                     # 22/22 safety proofs
python -m pytest tests/v3 -q                      # 37 offline tests
ZEPAY_LIVE_TESTS=1 python -m pytest tests/v3 -q   # + 7 real-network acceptance
ruff check zepay tests/v3 && black --check zepay tests/v3 && mypy zepay --ignore-missing-imports
```

Live acceptance (run 2026-09-08 from this environment): 487 real instruments
discovered · real BTC/ETH/SOL/XRP/DOGE cycles with health/regime/AI/simulation/
risk decisions · real WS messages from `data-stream.binance.vision` · paper
fills within 20 bps of real book prices · kill switch halts a real cycle.

## Known environment limitations (reported, not hidden)

* `api.binance.com` / `fapi.binance.com` return HTTP 451 here → public data via
  `data-api.binance.vision` / `data-stream.binance.vision`; **signed** endpoints
  are unreachable from this sandbox, so LIVE order paths are exercised by
  unit-level state-machine/reconciliation tests only.
* Bybit returns 403 here → no Bybit adapter claims.
* Redis/Postgres binaries absent in this sandbox → SQLite + in-memory cache are
  used and labeled; Docker profiles provide the real services.

## Security notes

* Never commit API keys; the previously chat-exposed key is treated as
  compromised and is **not** in this repo. Rotate it.
* Keys with withdrawal permission are refused at connection test.
* Solana integration never asks for, stores or handles seed phrases — Phantom
  signs locally; ZEPAY only relays unsigned transactions.
* Set `api_token` in config to require a bearer token on all mutating API calls
  (empty = local-open; bind to localhost when exposed).

## History

v2.0.0 (single-file, 6393 lines) is preserved under `legacy/` for reference and
diff-based verification of ported behavior. V3 changelog: `CHANGELOG.md`.
