# ZEPAY REST API

Base: `http://localhost:8000` (binds 0.0.0.0; `ZEPAY_PORT` env or `--port`).

Auth: after setup, every request needs a session token — `?token=` query param,
`X-ZEPAY-Token` header, or the `zepay_token` HttpOnly cookie (set by
`/api/setup/verify`). Responses: `{"ok": true, ...}` or
`{"ok": false, "error": "..."}`. 401 when unauthenticated.

## Setup & system

| Method | Path | Description |
|---|---|---|
| GET | `/health`, `/api/health` | liveness, mode, data feed status (no auth) |
| GET | `/api/setup/status` | setup state, license mode, cloud-license adapter status |
| POST | `/api/setup/verify` | `{key, mode?}` → verify key, create session (mode: auto/offline/cloud) |
| POST | `/api/license/endpoint` | `{endpoint}` — configure ZEPAY cloud verification endpoint (https) |
| GET | `/api/status` | full status: banners, feed, risk state, equity, top opportunities |
| GET | `/api/diagnostics` | subsystem diagnostics (incl. crypto self-test) |
| GET | `/api/system_check` | full system check with verdict |
| GET | `/api/build_status` | module map |

## Markets & assets

| Method | Path | Description |
|---|---|---|
| GET | `/api/assets?search=&limit=` | asset-management table (live exchange catalogue, price/24h/volume/liquidity/spread/vol/regime/AI signal/confidence/quality/position/P&L/health) + universe meta |
| POST | `/api/universe` | `{universe: [symbols]}` — set trading universe (validated; applies without restart; triggers data refresh + retrain) |
| GET | `/api/candles?market=` | cached real candles |
| GET | `/api/opportunities` | last analysis cycle (ranked, with agents, decisions, reasons) |
| POST | `/api/analyze` | run a full analysis cycle now |

## Portfolio, orders, trades

| Method | Path | Description |
|---|---|---|
| GET | `/api/portfolio` | account, positions, allocation, risk contribution, correlation matrix |
| GET | `/api/positions` | open + recent positions |
| GET | `/api/trades` | orders + fills |
| POST | `/api/execute` | `{market, mode?}` — execute an APPROVED opportunity (PAPER default; LIVE only when enabled) |
| POST | `/api/close` | `{market, mode?}` — close position |
| POST | `/api/order/limit` | `{market, direction, qty, limit_price}` — paper limit order |
| POST | `/api/order/cancel` | `{order_id}` |

## Risk & safety

| Method | Path | Description |
|---|---|---|
| GET | `/api/risk` | risk state, budgets, limits, hierarchy |
| POST | `/api/kill` | `{on}` — kill switch (records + alerts; no auto-resume) |
| POST | `/api/halt` | `{on, reason}` |
| POST | `/api/resume` | explicit user resume |
| POST | `/api/reconcile` | run paper (+ live when relevant) reconciliation now |
| GET | `/api/decisions` | decision ledger (explainable decisions) |

## AI

| Method | Path | Description |
|---|---|---|
| GET | `/api/models` | current quant model, registry, features |
| POST | `/api/train` | retrain quant ensemble on real candles |
| GET | `/api/experiments` | champion/challenger experiments (promotion manual) |
| POST | `/api/experiment` | run walk-forward experiment |
| GET | `/api/ai` | AI manager: active provider, usage, health, quant status |
| POST | `/api/ai/provider` | `{provider, enabled, base_url?, model?, api_key?}` |
| POST | `/api/ai/check` | `{provider}` — real health-check probe |
| POST | `/api/ai/research` | `{market}` — run deterministic agents + LLM research (advisory) |
| GET | `/api/agents` | guardrails + last-cycle agents + LLM report |
| GET | `/api/research` | research report history |

## Integrations

| Method | Path | Description |
|---|---|---|
| GET | `/api/providers` | integrated providers (health) + catalogue |
| POST | `/api/providers/check` | `{id?}` — real health check(s) |
| GET | `/api/public_apis` | public API registry (references, NOT integrated) |
| GET | `/api/exchange` | connection status (masked key, permissions, live gate) |
| POST | `/api/exchange/test` | `{api_key, api_secret}` — REAL signed credential test (withdrawal keys refused) |
| POST | `/api/exchange/disconnect` | delete stored credentials |
| POST | `/api/live/connect` | store credentials + verify + update gate steps |
| POST | `/api/live/enable` | multi-step live gate (see below) |
| POST | `/api/live/disable` | return to PAPER |
| GET | `/api/mcp` | MCP registry + call log + permission classes |
| POST | `/api/mcp/upsert` | register/update server (trading permissions refused) |
| POST | `/api/mcp/remove` | `{id}` |
| POST | `/api/mcp/tools` | `{id}` — list tools (JSON-RPC initialize + tools/list) |
| POST | `/api/mcp/call` | `{id, tool, args}` — call tool (trading-looking calls refused) |

## Live gate steps

`open_live, risk_ack, exchange_credentials, connection_verified,
permissions_verified, withdrawals_disabled, market_data_healthy, reconciled,
risk_test, paper_ready (≥5 fills), explicit_confirm` → plus the typed phrase
`ENABLE LIVE TRADING`. All checks are real; missing steps are returned.

## Misc

`/api/signals`, `/api/backtests`, `/api/audit`, `/api/alerts`,
`/api/alerts/read` (POST), `/api/snapshots`, `/api/config` (GET/POST
whitelisted keys), `/api/backup` (POST), `/api/help`, `/api/logout`.
