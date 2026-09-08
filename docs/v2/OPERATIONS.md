# ZEPAY — Operations & Acceptance Test

## Install & run (no infrastructure needed)

```bash
python3 zepay.py            # → http://localhost:8000  (0.0.0.0)
```

Requirements: Python 3.10+. No pip packages. Data lives in `./zepay_data/`
(or `$ZEPAY_DATA_DIR`).

CLI: `start | status | doctor | selftest | check | logs | backup | version`.
`doctor`/`selftest` run 21 offline unit checks (crypto RFC vectors, risk
gates, paper partial-fill math, MCP refusals, ledger, reconciliation…).
Full suite: `python3 -m unittest discover -s tests -v` (59 tests).

## Docker

```bash
docker compose up --build        # port 8000, data volume zepay_data/
```

## The user workflow

```
INSTALL → OPEN → ENTER ZEPAY KEY → VERIFY (offline-format or cloud adapter)
→ CONNECT EXCHANGE (optional; real signed test) → SELECT ASSETS (live catalogue)
→ PAPER TRADING (real data + real AI + simulated execution)
→ VERIFY (reconciliation, ledger, backtests) → EXPLICIT LIVE GATE (optional)
```

## Acceptance walkthrough (spec §50) — what to expect

Steps 1–6 (install → key → exchange): work anywhere. The key verifies
OFFLINE-format or via a configured cloud endpoint (the ZEPAY cloud spec is not
bundled — the adapter honestly reports BLOCKED until configured). Exchange
connection requires a **trade-only** API key; withdrawal-enabled keys are
refused.

Steps 7–17 (real data → paper): require outbound HTTPS to
`api.binance.com` / `data-api.binance.vision` (or `fapi.binance.com`). **If
that egress is blocked** (corporate firewall, geo-block, sandboxed CI), ZEPAY
shows `REAL DATA UNAVAILABLE`, marks assets BLOCKED with the exact network
error (API Manager), blocks all new trades, and the backtester refuses to run
on synthetic data. This is the specified behavior — never a demo fallback.

Steps 18–29 (backtest → risk rejection → kill switch → disconnect → AI failure
→ data failure → restart → reconcile → paper/live switch → live gate → typed
confirmation): all testable offline except the live-exchange interactions:

| Check | How verified |
|---|---|
| Risk rejection | tests: `TestRiskEngine` (7 tests) |
| Kill switch | tests + UI topbar; entries halt instantly, alert recorded |
| Exchange disconnect | provider status → UNREACHABLE with error string; entries blocked |
| AI failure | strict mode ⇒ WAIT signals; policy reduce_risk/stop_trading |
| Market-data failure | feed banner + per-asset BLOCKED + entries blocked |
| Restart recovery | SQLite state + persisted sessions; `Reconciler.startup()` on boot |
| Reconciliation | `/api/reconcile`; orphans/mismatches detected (tests) |
| Paper/Live switch | adapter-level switch in `ExecutionEngine.place` (tests) |
| Live safety gate | real checks only; typed phrase; tests cover refusals |

## Monitoring in-app

- **Diagnostics**: DB, crypto, feed, universe, AI, license, disk, memory.
- **API Manager**: per-provider health, latency, last success/error.
- **Decision Ledger**: every decision with data status, features, model,
  expected edge, confidence, portfolio/risk state, reasons.
- **Audit log**: config changes, credential events, orders, safety events.
- **Alerts**: critical events (halts, live orders, reconciliation mismatches).

## Backups & recovery

- `python3 zepay.py backup` → `zepay_data/backups/zepay-backup-<ts>.db` +
  config snapshot. The `.master.key` is NOT included — store it separately
  (without it, encrypted secrets cannot be restored).
- Restore: stop server, copy DB back into `zepay_data/`, start; boot runs
  schema migrations + startup reconciliation automatically.

## Hardening notes for production deployments

- Run behind a TLS-terminating reverse proxy; ZEPAY itself binds plain HTTP.
- Restrict network egress to the exact provider endpoints you enabled.
- Keep `.master.key` on the same trusted host; consider a filesystem-level
  secret manager for multi-host deployments.
- Review `docs/SECURITY.md` before enabling LIVE trading.
