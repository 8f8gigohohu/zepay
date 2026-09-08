# ZEPAY — Architecture

Version 2.0.0 (Ultimate, all-in-one edition)

## 1. Design philosophy

1. **No fake anything.** `REAL_DATA_REQUIRED = True` is a source constant, not a flag.
   If real data is unavailable the platform displays *REAL DATA UNAVAILABLE*,
   marks assets `STALE/BLOCKED` and refuses new trades. Missing API specifications
   are marked `BLOCKED` — never faked as connected.
2. **One engine, many markets.** A single deterministic pipeline processes the whole
   user-selected universe. There are no per-coin bots.
3. **The Risk Engine is the final authority.** AI (quant or LLM), agents and MCP
   tools are structurally unable to place orders or change limits.
4. **Paper mirrors live.** Paper execution uses real prices, real order books,
   real fee/slippage/latency models and simulates *only* the fill and the account.
5. **Smallest reliable architecture.** A single stdlib-only Python file runs the
   entire platform. Optional production infrastructure (Postgres, Redis, FastAPI)
   can be layered without changing the REST contract (see §8).

## 2. Component map

```
                       ┌────────────────────────────────┐
                       │   Embedded Dashboard (SPA)     │
                       └───────────────┬────────────────┘
                                       │ REST (JSON) + session token
                       ┌───────────────▼────────────────┐
                       │        HTTP API layer          │  (stdlib http.server,
                       │  auth · validation · redaction │   FastAPI-compatible)
                       └───────────────┬────────────────┘
   ┌──────────────┬────────────────────┼─────────────────────┬──────────────┐
   ▼              ▼                    ▼                     ▼              ▼
┌──────────┐ ┌─────────────┐  ┌────────────────┐  ┌──────────────┐ ┌─────────────┐
│ Provider │ │ Market Data │  │ Feature Engine │  │ AI Provider  │ │ MCP Manager │
│ Registry │ │ Manager     │  │ + Cross-Asset  │  │ Manager(LLM) │ │ (registry,  │
│ (health) │ │ REST+WS     │  │ Intelligence   │  │ advisory only│ │  no trading)│
└────┬─────┘ └──────┬──────┘  └───────┬────────┘  └──────┬───────┘ └──────┬──────┘
     │        ┌─────▼─────────────┐   │                  │                │
     │        │ Dynamic Universe  │   │            ┌─────▼──────────┐     │
     │        │ (exchangeInfo)    │   │            │ Research Agents│     │
     │        └─────┬─────────────┘   │            │ (deterministic │     │
     │              │                 ▼            │ + LLM debate)  │     │
     │              │        ┌────────────────┐    └─────┬──────────┘     │
     │              │        │ Quant AI       │          │ advisory only
     │              │        │ Ensemble       │          ▼
     │              │        │ (trained on    │    ┌─────────────┐
     │              │        │  real data)    │    │  Decision   │
     │              │        └───────┬────────┘    │  Ledger     │
     │              │                ▼             └─────────────┘
     │              │        ┌────────────────┐
     │              │        │ Strategy Engine│  trend/momentum/breakout/
     │              │        └───────┬────────┘  mean-reversion/vol/orderflow
     │              │                ▼
     │              │        ┌────────────────┐
     │              │        │ Opportunity    │
     │              │        │ Ranker         │
     │              │        └───────┬────────┘
     │              │                ▼
     │              │        ┌────────────────┐
     │              │        │ Portfolio      │
     │              │        │ Intelligence   │
     │              │        └───────┬────────┘
     │              │                ▼
     │              │        ┌────────────────┐
     │              │        │ GLOBAL RISK    │  ◄── FINAL AUTHORITY
     │              │        │ ENGINE         │
     │              │        └───────┬────────┘
     │              │                ▼
     │              │        ┌────────────────┐      ┌──────────────────┐
     │              │        │ Sizer (real    │      │ Reconciliation   │
     │              │        │  exchange      │      │ Engine (startup  │
     │              │        │  filters)      │      │ + periodic)      │
     │              │        └───────┬────────┘      └────────┬─────────┘
     │              │                ▼                        │
     │              │        ┌────────────────┐               │
     │              └───────►│ EXECUTION      │◄──────────────┘
     │                       │ ENGINE         │  pre-flight checklist,
     │                       └───┬────────┬───┘  idempotency, post-trade verify
     │                           │        │
     │                  ┌────────▼──┐  ┌──▼──────────────┐
     └─────────────────►│ PAPER     │  │ LIVE adapter    │
                        │ exchange  │  │ (signed REST)   │
                        │ (real px) │  └─────────────────┘
                        └───────────┘
Supporting systems: SQLite DB · encrypted secrets store · audit log · alerts ·
monitoring/diagnostics · model registry · backups · kill switch
```

## 3. Data flow per cycle (`run_cycle`)

1. `MarketDataManager.refresh_all` — REST (or optional WebSocket) fetch of
   tickers, klines, depth for the whole universe; every record normalized with
   `exchange/symbol/timestamp/source/data_type/quality` (UTC).
2. `FeatureEngine.build` → `CrossAssetEngine.enrich` (BTC influence, market
   breadth, correlation matrix, relative strength, spillover — all quantified).
3. `RegimeEngine.detect` → `StrategyEngine.evaluate` (regime-weighted).
4. `AIEngine.infer` — quant ensemble (direction/return/volatility/quality).
   Strict mode: untrained ⇒ `WAIT` with reason (never a fabricated signal).
5. `Agents.run_deterministic` — 9 analyst agents produce structured evidence.
   Optional `ResearchEngineLLM.run` on the top-ranked market (validated JSON,
   advisory only).
6. `OpportunityEngine.rank` → risk-adjusted net edge ordering.
7. `RiskEngine.authorize` — the safety hierarchy decides APPROVED/WAIT/REJECTED/HALTED.
8. `record_decision` — full decision ledger entry for every market every cycle.
9. Position management: SL/TP exits, paper limit-order fills.
10. `PortfolioEngine.revalue` + snapshot.

The `auto_loop` thread repeats this every `refresh_secs`, executes APPROVED
paper (or live) opportunities with execution-time re-validation, and periodically
runs reconciliation (paper every 20 cycles; live when enabled) and provider
health checks.

## 4. Paper vs Live

The switch is *the execution adapter*, not a UI style:

| Concern | PAPER | LIVE |
|---|---|---|
| Market data | real (REST/WS) | real (REST/WS) |
| AI / risk / sizing | real | real |
| Fill | simulated against real book (participation cap ⇒ partial fills) | signed exchange order |
| Fees/slippage | explicit cost model | exchange-reported |
| Account | local paper balance | exchange balance (`sync_live_balance`) |
| Verification | internal reconciliation | exchange order status + reconciliation |

LIVE requires the multi-step gate (`live_enable`): real credential test
(withdrawal-enabled keys refused), healthy market data, live reconciliation,
risk self-test, ≥5 paper fills, and the typed phrase `ENABLE LIVE TRADING`.
No automatic activation. Any reconciliation mismatch or unknown order outcome
sets `live_block_reason` and blocks live execution.

## 5. Data honesty rules

- Every market-health check fails closed: no data ⇒ `BLOCKED`.
- `RiskEngine.entries_allowed()` stops ALL new entries when the feed is
  `UNAVAILABLE` (banner: *DATA FEED FAILURE*) or when AI policy says stop.
- Backtests refuse to run without real candles.
- Provider registry statuses: `OK / DEGRADED / UNREACHABLE / NOT_CONFIGURED /
  BLOCKED / DISABLED` — each backed by a real request or a real configuration
  state.
- Klines may fall back to previously-fetched REAL candles persisted in SQLite,
  explicitly marked `stale_db`.

## 6. Storage

- **SQLite** (`zepay_data/zepay.db`): candles, orders, fills, positions,
  balances, signals, **decisions** (ledger), risk events, snapshots,
  correlations, backtests, models, experiments, alerts, audit logs,
  system events, provider events, assets, research reports, mcp_calls,
  encrypted secrets, kv (sessions).
- **config.json**: non-secret configuration only.
- **`.master.key`** (0600): install master key for the secrets store.
- Schema is portable to PostgreSQL (additive migration columns are applied on boot).

## 7. Security architecture

See [SECURITY.md](SECURITY.md). Summary: ChaCha20-Poly1305 (RFC 8439) secret
encryption with HKDF-derived subkeys; HMAC-SHA256 signed exchange requests;
recursive redaction; withdrawal-permission refusal; RBAC-lite (setup/session);
audit logging; per-provider rate limiting; MCP tools cannot trade by construction.

## 8. Scaling out (optional)

The single-file edition intentionally uses stdlib equivalents. Each has a clean
seam for a production swap with **no REST contract change**:

| Single-file | Production swap |
|---|---|
| `http.server` API | FastAPI/uvicorn (same routes) |
| SQLite | PostgreSQL |
| in-process caches | Redis |
| ThreadPoolExecutor | Celery/RQ workers |
| in-process engine thread | systemd-supervised service |

`Dockerfile` and `docker-compose.yml` are provided for containerized runs
(volume-mount `zepay_data/`).
