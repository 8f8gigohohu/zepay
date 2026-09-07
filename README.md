# ZEPAY — Ultimate Edition v2.0.0

**Real-data, real-AI, multi-asset crypto trading platform.** One file. Zero
dependencies. Python 3.10+.

```bash
python3 zepay.py          # → http://localhost:8000
```

> PAPER = real market data + real AI + real risk + **simulated** execution.
> LIVE = real signed exchange orders, behind a multi-step safety gate.
> **No fake data. No fake AI. No fake fills. No fake balances.**
> If the exchange API is unreachable, ZEPAY says **REAL DATA UNAVAILABLE**
> and blocks new trades — by design.

## What's inside

| Layer | Components |
|---|---|
| Data | Provider registry (health/rate-limits/audit) · dynamic universe from live exchange metadata · REST + optional WebSocket · normalized records (exchange/symbol/ts/source/type/quality, UTC) · funding/OI context |
| Intelligence | Feature engine · cross-asset intelligence (BTC influence, breadth, correlation, spillover) · regime engine · 6 strategies · **quant AI ensemble** (direction/return/vol/quality, trained on real candles, strict mode never fabricates) · LLM provider manager (OpenAI/Anthropic/Google/DeepSeek/Qwen/xAI/OpenRouter/**Ollama/vLLM local**) · 9 deterministic research agents + optional TradingAgents-style LLM bull/bear debate (advisory, schema-validated) |
| Trading | Opportunity ranker (net edge after costs) · portfolio intelligence · **Global Risk Engine (final authority)** · exchange-filter-aware sizer · execution engine with idempotency, partial fills, limit orders, latency model · **live signed exchange adapter with post-trade verification** · reconciliation (startup + periodic, mismatch blocks live) |
| Trust | Decision ledger (every decision explainable) · audit log · alerts · kill switch · encrypted secrets (ChaCha20-Poly1305, RFC 8439 — validated at every boot) · withdrawal-enabled keys refused · MCP manager with **no trading permissions by construction** |
| UI | Dashboard · Assets · AI Signals · Portfolio · Positions & Orders · Backtesting · Risk · AI Models · Agents & Research · Decision Ledger · Exchange · API Manager · AI Manager · MCP Manager · Diagnostics · Settings · Help — always showing `PAPER — REAL DATA` or `LIVE — REAL MONEY` |

## Quick start

1. `python3 zepay.py` → open http://localhost:8000
2. Enter your ZEPAY key (offline format check is clearly labeled; cloud
   verification activates when an endpoint is configured in Settings — no spec
   is invented here).
3. Assets → search/select markets (live exchange catalogue; applies instantly).
4. The engine trains on real candles and ranks opportunities; PAPER trading
   runs automatically (everything simulated is labeled).
5. Optional LIVE: Exchange → connect a **trade-only** API key (withdrawals
   disabled), pass every real gate check, type `ENABLE LIVE TRADING`.

## ZEPAY license key — honesty note

No ZEPAY cloud verification API specification ships with this build, so the
cloud adapter reports **BLOCKED** instead of pretending. Keys verify via
OFFLINE format validation (clearly labeled) until an operator configures a
real endpoint (Settings → ZEPAY license).

## Tests

```bash
python3 zepay.py selftest                  # 21 boot checks (crypto vectors, risk gates, …)
python3 -m unittest discover -s tests -v   # 59-test suite
```

Covers: RFC 8439/5869 crypto vectors + tamper rejection, license honesty,
provider registry states, universe fallback, features/no-lookahead, quant AI
strict mode, risk gates (stale data, kill switch, loss limits, AI failure
policy, position caps), paper order lifecycle, **partial fills**, idempotency,
min-notional rejection, limit orders, SL/TP exits, full engine cycles, agents,
LLM output validation, backtest integrity (refuses synthetic data),
reconciliation (orphans, missing credentials), MCP trading refusals, AI
provider key encryption, secret redaction, restart recovery, dashboard
contract.

## Docs

[Architecture](docs/ARCHITECTURE.md) · [Security](docs/SECURITY.md) ·
[REST API](docs/API.md) · [Operations & acceptance test](docs/OPERATIONS.md) ·
[Third-party](docs/THIRD_PARTY.md) · [Resource decisions](docs/RESOURCE_DECISIONS.md)

## Safety

Defaults: `PAPER`, live **disabled**, withdrawals **forbidden**. The Risk
Engine outranks every AI component. Kill switch always visible. LLMs and MCP
tools can never place orders. See [docs/SECURITY.md](docs/SECURITY.md).

---
PAST PERFORMANCE IS NOT A GUARANTEE OF FUTURE RESULTS. Crypto trading is
risky. Paper-trade first. Never risk money you cannot afford to lose.
