# Changelog

## V3.0.0 — multi-asset, multi-venue, AI-assisted platform rewrite

Complete architectural upgrade per the ZEPAY V3 specification (§1–§70).
The V2 monolith (`zepay_v2.py`, ~9.5k lines, single engine loop, one venue,
heuristic scorer) is preserved under `legacy/` for reference; V3 is a modular
package with one universal engine. See `docs/v3/ARCHITECTURE.md` and
`docs/v3/FINAL_REPORT.md`.

### Added

* **Core** — domain model (Market/Instrument/Venue/Decision/Order/Position
  state machines with illegal-transition enforcement), typed config store with
  privileged/immutable keys, event bus (SSE-backed), structured JSON logging,
  health aggregation.
* **Market data** — dynamic universe discovery (487 real instruments from the
  Binance vision endpoint in this environment; venue-agnostic), REST collector
  with per-market freshness/staleness marking, professional WS engine with
  states CONNECTED/DEGRADED/STALE/DISCONNECTED/RECOVERING/FAILED, dedupe,
  auto-resubscribe, data-quality checks (gap/outlier/spread/cross-venue).
* **Intelligence** — 40+ feature vector (`features-3.0.0`), regime detector,
  10 real statistical models (technical, quant, momentum, mean-reversion,
  volatility, regime, order-flow, cross-asset, sentiment, trade-quality) +
  regime-weighted ensemble with probability calibration (isotonic), ECE/Brier
  OOS reporting, model registry with champion/challenger lifecycle (promotion
  manual-only), drift monitoring (PSI + calibration drift).
* **Decisioning** — strategy orchestrator (trend/mean-reversion/breakout/
  momentum/market-making/grid/stat-arb/cross-venue-arb — one engine, all
  assets, new assets need no code), opportunity scoring, mandatory pre-trade
  simulation (spread, slippage, fees, funding, latency, MAE/MFE, expected net)
  that can reject any trade before execution.
* **Risk** — RiskEngine that ALWAYS outranks AI (hierarchy: hard limits >
  risk > portfolio > execution safety > data validity > strategy > ML > LLM),
  fails closed, self-testable; position sizer with $50 LIMITED-LIVE notional
  cap, portfolio exposure/VaR/CVaR/stress, kill switch with typed
  `RESUME TRADING`, reconciliation (paper + live), 4-stage operational gate
  (PAPER → SHADOW → LIMITED LIVE → FULL LIVE) with measured prerequisites and
  typed confirmations — never auto-promoted.
* **Execution** — venue adapter contract + Binance Spot, Binance Futures
  (signed endpoints report BLOCKED honestly where geo-restricted), ZEPAY INR
  venue, Solana/Phantom wallet (public address + signature challenge only —
  seed phrases/private keys are never requested, stored or accepted),
  paper engine walking the real order book with participation caps,
  order-transition ledger with idempotency keys, execution-quality metrics.
* **AI governance** — MCP manager exposing a hardened tool surface (no
  trading, withdrawal, credential or kill-switch tools exist for MCP by
  construction), LLM advisory-only (never controls orders/risk/leverage/
  credentials/live execution), untrained-AI strict mode = WAIT.
* **Research** — backtester with full cost model + MAE/MFE/exit-reason
  accounting, walk-forward analysis with stability scoring, hyperopt that
  penalizes drawdown/OOS-gap/low-trade-count (not just profit).
* **Platform** — FastAPI app (30+ endpoints, restricted config editor, SSE
  events), dark responsive SPA (desktop + mobile, 6 tabs, typed-confirm
  modals, real-candle sparklines, UNAVAILABLE/DEGRADED badges), PostgreSQL +
  optional Redis backends with honest fallback, audit trail, alerting,
  22-check safety self-test, CLI, Dockerfile, docker-compose (postgres/redis
  profiles), install script.
* **Quality gates** — `pyproject.toml` pins ruff/black/mypy/pytest policy;
  mypy clean (81 files); 37 offline tests + 7 live-network acceptance tests.

### Changed

* Database schema v3: additive migrations only (new tables + new columns on
  V2 tables); `schema_version=3` stamped at boot — `docs/v3/MIGRATIONS.md`.
* V2 invariants preserved by design: REAL_DATA_REQUIRED, p_long thresholds
  0.56/0.44, risk-gate order, max-drawdown auto-halt, defensive sizing
  multipliers.
* README rewritten for V3; V2 docs moved to `docs/v2/`.

### Removed (from the active codebase — preserved in `legacy/`)

* The `zepay_v2.py` monolith as the runtime (moved to `legacy/`), fake-data
  fallbacks, per-coin special-casing, silent error swallowing, and the v2
  test suite (kept at `legacy/tests_v2/`; superseded by `tests/v3/`).

### Security

* Credentials: encrypted at rest (ChaCha20-Poly1305, per-purpose keys, master
  key file 0600 outside the DB), never logged, never exposed via API or MCP;
  withdrawal permission forbidden at hard-limits, capability and adapter
  level; privileged config keys immutable via API; live trading disabled by
  default; the credential previously pasted in chat is treated as compromised
  and was NOT copied anywhere in this repo.
