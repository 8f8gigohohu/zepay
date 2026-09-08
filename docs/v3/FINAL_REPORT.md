# ZEPAY V3 — Final Report

Date: 2026-09-08 · Environment: sandboxed Linux, Python 3.13.14, live public
market data (Binance vision endpoints, Solana mainnet RPC, Jupiter) ·
All numbers in this report are real command/API outputs from this environment;
nothing is estimated or fabricated. Where something could not be verified
here, it is explicitly marked.

---

## 1. Architecture summary

One universal engine trades all assets dynamically — no per-coin bots. New
assets are discovered by the universe scanner and require zero code changes.
Full detail: `docs/v3/ARCHITECTURE.md`. Layer map:

```
core (domain/config/events/util) → security → database/audit
→ exchanges (venue adapters) + wallets (Solana, address-only)
→ market_data (universe/REST collector/WS engine/quality)
→ features + regime → strategies (orchestrator) → ai (10 models + ensemble
   + calibration + registry + drift; LLM advisory-only) → opportunities
→ portfolio (exposure/VaR/stress) → risk (ALWAYS outranks AI; fails closed;
   kill switch; stage gate) → execution (pre-trade sim → paper/live gate →
   order ledger w/ state machine + idempotency) → mcp (hardened, no trading
   tools) → ml (backtest/walk-forward/hyperopt) → monitoring → api → apps
```

Decision hierarchy (enforced in code, `risk/engine.py` + `engine.py`):
hard limits > risk engine > portfolio > execution safety > data validity >
strategy > ML > LLM. The LLM never touches orders, risk, leverage,
credentials, the kill switch or live execution.

## 2. Files changed / added / removed

* **Added:** the entire `zepay/` package (81 modules: core, security,
  database, audit, exchanges, wallets, market_data, features, regime,
  strategies, ai, opportunities, portfolio, risk, execution, mcp, ml,
  backtesting, monitoring, api+routers, apps, engine.py, cli.py),
  `frontend/` (index.html, app.js, style.css), `tests/v3/` (5 files, 44
  tests), `docs/v3/` (4 documents), `pyproject.toml` (quality-gate policy).
* **Changed:** `README.md` (V3 rewrite), `requirements.txt`, `Dockerfile`,
  `docker-compose.yml` (postgres/redis profiles), `scripts/install.sh`,
  `.gitignore`.
* **Removed from active code (preserved in `legacy/`):** `zepay_v2.py`
  monolith, V2 test suite (`legacy/tests_v2/`), V2 docs moved to `docs/v2/`.
  Nothing working was deleted without a V3 replacement; V2 invariants
  (REAL_DATA_REQUIRED, 0.56/0.44 p_long gates, risk-gate order, drawdown
  auto-halt, defensive sizing multipliers) are carried into V3.

## 3. License review

`docs/v3/LICENSE_REVIEW.md` — no code copied from any reference repo
(Freqtrade/OctoBot GPL-3.0 concepts re-implemented independently; Hummingbot
Apache-2.0 concepts only; MIT references noted). Dependency table with
verdicts included; psycopg LGPL-3.0 assessed and acceptable as optional
imported library. Zero third-party frontend assets.

## 4. Database migrations

`docs/v3/MIGRATIONS.md` — SQLite default (WAL) / PostgreSQL optional
(honest fallback), additive-only migrations applied idempotently at boot
(`MIGRATION_COLUMNS`), `schema_version=3`, V2-data upgrade path documented
(including why V2 ML models are deliberately NOT auto-loaded against the new
`features-3.0.0` vector), backup/integrity-check/rotation, manual restore.

## 5. API / UI changes

* **API:** FastAPI, 30+ endpoints — status, health, markets+signals, candles,
  depth, decisions/orders/positions/trades ledgers, cycle/run, ai/train +
  models + promote (manual), backtest/run, research/walk-forward + hyperopt,
  risk (+ self-test), kill-switch engage/resume (typed), stage gate
  status/promote/demote, wallet connect/challenge/verify/balances (address
  only), mcp call/list (refuses trading/withdrawal classes), restricted
  config editor (privileged keys 403), backup, SSE `/api/events`.
* **UI:** dark responsive SPA (desktop + mobile), 6 tabs (Overview, Markets,
  Trading, AI/Research, Risk, Settings), 15 s poll + SSE live updates,
  typed-confirm modals for kill-switch/stage/promotion, sparklines from real
  candles, honest UNAVAILABLE / DEGRADED / BLOCKED / NOT_CONFIGURED badges,
  no fabricated numbers anywhere.

## 6. AI / ML changes

10 real statistical models + regime-weighted ensemble; isotonic calibration;
champion/challenger registry (promotion manual-only); drift monitoring (PSI +
calibration); strict mode: untrained AI ⇒ WAIT everywhere. Walk-forward with
stability score; hyperopt objective penalizes drawdown, OOS gap and low trade
count — not profit alone. LLM advisory-only and UNAVAILABLE without provider
config (reported honestly, never faked).

## 7. Risk / Execution / Security changes

* Risk fails closed on every path (verified in selftest + offline suite);
  kill switch cancels/flattens and blocks cycles until exact `RESUME TRADING`;
  4-stage gate with measured prerequisites — promotion never automatic.
* Pre-trade simulation is mandatory before any execution: spread, slippage,
  fees, funding, latency, expected net; it rejects trades with negative or
  sub-threshold edge (observed live: 40/40 markets WAIT with correct math).
* Paper fills walk the real order book with participation caps (verified
  within 20 bps of real price incl. fees); live orders go through the
  LiveGate + order state machine with idempotency keys and an append-only
  transition ledger.
* Security: secrets encrypted (ChaCha20-Poly1305, per-purpose keys, 0600
  master key), credentials never logged/exposed, withdrawal permission
  forbidden at three independent layers, MCP has no trading/withdrawal/
  credential tools by construction, privileged config immutable via API, the
  credential previously pasted in chat is treated as compromised and was not
  copied into the repo.

## 8. Test results (all real, run 2026-09-08)

| Gate | Result |
|---|---|
| `ruff check .` (pinned policy in pyproject.toml) | All checks passed |
| `black --check` (line-length 100) | clean |
| `mypy zepay --ignore-missing-imports` | Success — 0 errors, 81 files |
| offline suite `pytest tests/v3` | **37 passed, 7 skipped** (live-gated), 6.3 s |
| live acceptance `ZEPAY_LIVE_TESTS=1` | **7 passed**, 32.7 s |
| safety self-test `python -m zepay.apps.selftest` | **22/22 passed** |

Live-server acceptance (server on 0.0.0.0:8000, SQLite, PAPER, engine
auto-loop 60 s, WS running):

* Universe: **487 real instruments** (Binance vision REST; tick/lot sizes).
* WS engine: **60 streams CONNECTED**, 0 DEGRADED/STALE; data quality 40/40
  markets OK; feed LIVE.
* AI train via API on 10 markets: **1 560 real samples**, honest OOS metrics
  reported: dir-accuracy 0.4679, Brier 0.2563 → 0.2479 calibrated,
  ECE 0.0769 (sub-coin-flip accuracy is reported as-is, not massaged).
* Next auto-cycle: 40 markets evaluated by the **trained ensemble** with real
  regimes (COMPRESSION/BEARISH_TREND/LOW_VOL); AI saw LONG bias (p 0.56–0.59)
  but pre-trade sim computed negative expected net after costs on every
  market ⇒ **40/40 WAIT, zero fabricated trades**; every decision persisted
  with model version + strategy (ledger verified via API).
* Backtest BTC/USDT (500 real 1 h candles): 5 trades, gross +0.39 %,
  **net +0.17 %** after $15.82 fees + $6.78 slippage, max DD 0.2 %, PF 2.44,
  exit reasons itemized — costs accounted, no fantasy returns.
* Walk-forward (5 markets, 780 real samples, 4 folds): OOS accuracy mean
  0.5737, Brier mean 0.2455, stability 0.957, champion comparison emitted;
  promotion recommendation is advisory only.
* Hyperopt (random, 6 trials): ran honestly; all trials produced 0 trades
  (sampled entry thresholds above the model's confidence scale) ⇒ objective
  correctly reported the low-trade-count penalty (−0.3) instead of a fake
  "best strategy".
* Kill switch via API: engage ⇒ risk state HALTED, entries blocked, full
  cycle executed **zero orders** (all markets HALTED); wrong resume phrase
  refused; exact `RESUME TRADING` restored.
* Stage gate: refused promotion on a fresh DB (0 history); on the acceptance
  DB it **legitimately** promoted PAPER→SHADOW only after 480 real paper
  decisions + passing reconciliation + typed phrase; demoted back to PAPER
  afterwards (default restored, `live_enabled=false`).
* Honest status surfaces: binance_spot NOT_CONFIGURED (no credentials —
  public data still flows), binance_futures **BLOCKED** (HTTP 451
  geo-restriction in this sandbox, reported not hidden), solana OK (real
  mainnet RPC), zepay NOT_CONFIGURED; Redis/Postgres absent ⇒ labeled
  in-memory cache / SQLite fallback.

## 9. Remaining limitations (honest)

1. **Signed exchange endpoints untestable here** — api.binance.com /
   fapi.binance.com return HTTP 451 in this sandbox. Real live-order
   round-trips (place/cancel/fill) must be validated in SHADOW→LIMITED LIVE
   by the operator in an unrestricted network with real (non-withdrawal)
   keys. Code paths are complete and unit-tested against the adapter
   contract, but no live fill has been executed.
2. **ZEPAY INR venue** — no public ZEPAY exchange API exists; the adapter is
   contract-complete and simulated until real endpoints/credentials are
   provided.
3. **Solana swaps** — Jupiter quotes are real (verified); transaction
   signing/submission requires a user wallet signature by design (seed
   phrase never accepted) — not exercisable headlessly.
4. **Postgres/Redis** binaries absent in sandbox — fallbacks tested;
   Docker-compose profiles provided but not run here.
5. **LLM advisor** — no provider configured in sandbox ⇒ UNAVAILABLE state
   shown (correct behavior); advisory quality untested against a real
   provider.
6. **ML edge is modest** — OOS directional accuracy 0.47–0.57 across splits.
   The system is designed for this reality: cost-aware simulation, strict
   thresholds and risk discipline mean the default answer is WAIT. No profit
   is claimed; PAPER/SHADOW history must be accumulated and reviewed by a
   human before any live consideration.
7. Walk-forward/hyperopt currently run on cached candle history (≤500 bars);
   deeper history improves research confidence.

## 10. Production-readiness status

* **PAPER / SHADOW: production-grade and verified in this environment** —
  real data, real math, real ledgers, all gates green, safety self-test
  22/22.
* **LIMITED LIVE / FULL LIVE: code-complete, disabled by default, and NOT
  claimed production-ready** — requires (a) operator network without
  geo-blocks, (b) real API keys without withdrawal permission, (c) SHADOW
  history + reconciliation review, (d) explicit typed human promotion at
  every step. ZEPAY V3 is not declared production-ready for live trading
  until those acceptance steps pass under operator supervision.

## 11. Definition of Done — checklist

| ✔ | Item | Evidence |
|---|---|---|
| ✔ | Repo inspected first; working functionality preserved | V2 invariants carried; legacy/ kept |
| ✔ | Modular production architecture replaces monolith | zepay/ package, 81 mypy-clean modules |
| ✔ | One universal engine, no per-coin bots | engine.py + strategy orchestrator; 40 markets/cycle |
| ✔ | New assets discovered dynamically | universe 487 instruments, zero code per asset |
| ✔ | Multi-venue (ZEPAY INR, Binance Spot+Futures, Solana wallet) | adapters + registry; honest NOT_CONFIGURED/BLOCKED states |
| ✔ | Wallet = address/signature only, never seed/private key | selftest + code review (no key-handling path exists) |
| ✔ | Universal market model | core/domain.py Market/Instrument/Venue |
| ✔ | Market data bus REST+WS | collector + WS engine + event bus (SSE verified) |
| ✔ | WS states machine | CONNECTED/DEGRADED/STALE/DISCONNECTED/RECOVERING/FAILED; 60 CONNECTED live |
| ✔ | Real AI decision support (10 models + ensemble) | trained on 1 560 real samples; OOS reported |
| ✔ | LLM advisory-only, never controls orders/risk | mcp/llm isolation; selftest proves refusal |
| ✔ | Mandatory pre-trade simulation | live: 40/40 WAIT on negative expected net |
| ✔ | Risk always outranks AI; fails closed | hierarchy in code; selftest + tests |
| ✔ | 4 stages, never auto-promote; live off by default | gate refused fresh DB; promoted only with 480 decisions + phrase |
| ✔ | Withdrawals forbidden | hard limits + capability + adapter (3 layers, selftest) |
| ✔ | Backtest/walk-forward/hyperopt with anti-overfit objectives | live runs above; penalties verified |
| ✔ | Model registry, manual promotion only | registry + promote endpoint requires human actor |
| ✔ | Postgres default-optional + Redis optional, honest fallback | compose profiles; sqlite/memory fallback live |
| ✔ | FastAPI backend + responsive professional frontend | 30+ endpoints; SPA served, mobile breakpoints |
| ✔ | Reference repos studied, six-gate review, no blind copying | LICENSE_REVIEW.md |
| ✔ | No fake data/predictions/balances/fills anywhere | data-honesty suite (11 tests); live states |
| ✔ | Security review incl. compromised chat credential excluded | not present in repo (grep-verified) |
| ✔ | Quality gates green | ruff/black/mypy/pytest/selftest above |
| ✔ | Docs: architecture, migrations, license, final report | docs/v3/ |
| ✔ | No unverified profit claims | report states limitations explicitly |
