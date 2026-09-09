# Changelog

## V3.1.0 — real futures trading, multi-venue routing, license gate (v2+v3 merge)

Combines the V2 ultimate build (preserved under `legacy/`) with the V3 modular
architecture and completes REAL end-to-end futures trading against the REAL
Binance USDT-M Futures API (fapi.binance.com). The ZEPAY native venue carries
the full futures contract surface but stays honestly NOT_CONFIGURED until a
real ZEPAY API specification is provided — nothing is invented.

### Added

* **Per-instrument venue routing (spot ↔ futures, no restart)** — the universe
  scans both binance_spot and binance_futures (USDT-M perpetuals, public
  metadata) when the venue is enabled; every market resolves its own venue via
  `market_venue_overrides` → `default_trading_venue` → primary. Data collection,
  feature building, paper fills and live execution all follow the instrument's
  venue. `POST /api/markets/{m}/venue` reroutes a market live; the UI shows a
  Route column with a spot↔perp toggle.
* **REAL futures execution path (Binance USDT-M)** — live entries on futures
  venues run `_futures_prechecks` before any order: leverage validated by the
  Risk Engine (`max_futures_leverage`, hard ceiling 5x, request auto-capped to
  the risk-approved value and audited), then capped by the instrument's real
  leverage brackets (`/fapi/v1/leverageBracket`), HEDGE/dual-side position mode
  refused (one-way required), margin type set (default ISOLATED, -4046
  tolerated), `set_leverage` confirmed — only then is the order submitted.
  Position closes on futures venues are submitted reduce-only.
* **Futures account integration** — balance sync reads the real futures wallet
  shape (cross/isolated wallet balances), positions/funding come from the
  signed API, and connection tests REFUSE withdrawal-enabled keys and keys
  without trade permission (canTrade=false → BLOCKED).
* **ZEPAY license gate (§6, ported from v2)** — `zepay/security/license.py`
  with two honest adapters: offline format+checksum validation (clearly labeled
  "NOT cloud verification") and a real cloud verifier that reports BLOCKED
  until an operator-configured endpoint exists. Trading cycles are gated until
  a key is verified; only the SHA-256 hash is stored (privileged key).
  Endpoints: GET `/api/license/status`, POST `/verify`, `/clear`, `/endpoint`.
* **Venue activation gate** — `venues_enabled` is privileged; enabling a venue
  (e.g. binance_futures) now requires `POST /api/venues/{venue}/enable` with
  the typed confirmation `ENABLE <VENUE>`, is audited and alerted. The Risk
  Engine's `venue_allowed` refuses LIVE execution on venues not enabled.
* **Futures-aware risk limits** — `max_futures_leverage` (default 3.0, hard cap
  5x) separate from spot `max_leverage` (1.0); both enforced by the Risk
  Engine, which remains the final authority.

### Changed

* Universe snapshot exposes per-venue instrument counts; DB fallback loads all
  venues; futures scan is public-metadata-only and gated by venue enablement.
* `/api/status` venues now include the operator `enabled` flag.
* Frontend: license panel (verify/deactivate/cloud endpoint), venue
  enable/disable with typed confirmation, per-market Route column, futures
  settings in the configuration editor.

### Fixed

* Modal dialog bug: `#modal-backdrop` had `display:flex` which overrode the
  HTML `hidden` attribute, rendering an empty, non-functional Confirm/Cancel
  dialog over the whole UI at all times. `#modal-backdrop[hidden]{display:none}`
  restores it; modals also now autofocus their input, close on Escape, and
  guard against double-resolve. Empty typed confirmations toast an error
  instead of failing silently.

### Honesty guarantees (unchanged, re-verified)

No fake market data, balances, trades, or verification states. Futures venues
report honest UNREACHABLE/BLOCKED without network; the ZEPAY venue reports
NOT_CONFIGURED for every futures method until a real spec exists; withdrawal
permission is refused by construction.

## V3.0.0 — multi-asset, multi-venue, AI-assisted platform rewrite — multi-asset, multi-venue, AI-assisted platform rewrite

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
