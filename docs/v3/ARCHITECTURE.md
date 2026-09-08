# ZEPAY V3 — Architecture Summary

Status: implemented and verified (see `FINAL_REPORT.md` for test evidence).
Every claim below corresponds to code in `zepay/` and is covered by the
selftest (22 checks) or the pytest suite (37 offline + 7 gated live tests).

## 1. Design principles

1. **One universal engine.** `zepay/engine.py` contains zero per-asset logic.
   Assets are discovered at runtime from live exchange metadata
   (`market_data/universe.py` — 487 instruments on first boot here) and flow
   through the identical pipeline. Adding an asset = adding data, not code.
2. **Authority hierarchy (never inverted).**
   `HARD LIMITS > Risk Engine > Portfolio > Execution safety > Data validity >
   Strategy > ML > LLM`. The risk engine (`risk/engine.py`) is the final
   authority on every order and **fails closed**: any internal exception
   returns `HALTED` (proved in selftest #1).
3. **Honesty by construction.** No component can invent data: untrained AI →
   `WAIT` (strict mode); missing book → paper fills refused or explicitly
   labeled `ticker-spread-estimate`; backtests refuse <80 real candles;
   execution quality returns `None` (never a fabricated score) with no data;
   venues report `NOT_CONFIGURED/UNREACHABLE/BLOCKED`.
4. **Live trading is opt-in, manual, typed.** Stage gate
   (`execution/live_gate.py`): PAPER → SHADOW → LIMITED_LIVE (≤ $50 notional,
   1 position) → FULL_LIVE. Promotion requires exact typed phrases
   (`ENABLE SHADOW MODE`, …) plus real prerequisites (decision counts,
   reconciliation, adapter connection test, risk self-test, no withdrawal
   permission). Auto-promotion does not exist (`HARD_LIMITS.auto_promote_to_live=False`).
5. **Dependency injection everywhere.** One composition root
   (`apps/compose.py`) builds the object graph; no module reaches for globals.
   Every layer is unit-testable in isolation (the offline suite proves it).

## 2. Data plane (§14–§17)

```
UniverseManager ──(exchange metadata)──► Instrument catalog (venues, filters)
        │
MarketDataCollector ── REST (host failover: data-api.binance.vision first) ──► cache + candles table
        │                derivatives best-effort (funding/mark/OI, labeled)
WebSocketManager ── combined streams, per-venue task ──► ticker overlay on collector cache
        │   states: CONNECTING/CONNECTED/DEGRADED/STALE/DISCONNECTED/RECOVERING/FAILED
        │   dedupe (trade ids, event-time), seq-reset detection, backoff+rotation, heartbeat
DataQualityEngine ── stale/gap/malformed/outage events ──► data_quality_events table
MarketHealthEngine ── per-market TRADEABLE/LIMITED/BLOCKED from real staleness/spread/liquidity/vol
```

All timestamps UTC epoch-ms; every record carries `source` + `quality`.
`feed_health()` gates new entries: all feeds stale → risk engine blocks entries
with `REAL DATA UNAVAILABLE` (v2 invariant preserved).

## 3. Intelligence plane (§19–§28)

* **Features** (`features/`): v2 indicator set + VWAP, Supertrend, market
  structure, support/resistance clusters, liquidity zones, book imbalance,
  cross-asset fields (BTC momentum, breadth, correlation, spillover, relative
  strength). Computed only from data ≤ evaluation bar (lookahead asserted).
* **Regime** (`regime/engine.py`): 11 regimes + UNSTABLE, each with a
  strategy-fit table used for ensemble weighting and gating (PANIC/UNSTABLE →
  no new entries, v2 invariant).
* **Strategies** (`strategies/`): 12 (trend, momentum, breakout, mean
  reversion, volatility, orderflow, funding basis, grid, DCA, market making,
  stat arb, cross-venue arb). Strategies without their required data return
  WAIT with an honest rationale. Orchestrator scores/weights by regime fit.
* **AI ensemble** (`ai/engine.py`): 6 models (direction logistic, return/vol/
  MAE/MFE ridge, trade-quality logistic) trained on real candles with a
  chronological 80/20 split; OOS metrics (accuracy, Brier, ECE) + Platt
  calibration fit on validation, reported OOS. Direction thresholds (≥0.56 /
  ≤0.44) applied to **calibrated** probability. Inference fuses members with
  regime weights, emits disagreement + uncertainty, and reports its mode:
  `trained-ensemble` | `heuristic-composite-LABELED` | `untrained-no-signal`.
* **Model registry** (`ml/registry.py`): lifecycle RESEARCH→VALIDATION→
  CANDIDATE→PAPER→SHADOW→APPROVED→LIMITED→PRODUCTION→DEGRADED→RETIRED with
  validated transitions; champion/challenger comparison; promotion manual with
  recorded evidence (`model_promotions` table).
* **Drift monitor** (`ml/drift.py`): feature PSI vs training snapshot,
  prediction distribution, real-outcome calibration drift (predictions are
  labeled from candles after the horizon elapses), regime distribution.
  Graduated response: warn → degrade (confidence/size already reduced via
  AI.degraded) → **disable model** on persistent severe drift.
* **Research** (`ml/`): walk-forward (anchored folds, stability, honest
  failure on insufficient samples), hyperopt (grid/random/GP-UCB Bayesian/
  evolutionary) with a **penalized objective** — drawdown, IS/OOS gap,
  instability, low trade count and churn are subtracted; raw profit is not the
  goal (§25/§69). Research pipeline (§28) discovers → hypothesizes → trains
  challengers → walk-forwards → cost-stress-tests → robustness-scans →
  compares vs champion → registers a CANDIDATE. It never deploys.
* **LLM & agents** (`ai/llm.py`, `ai/agents.py`): 8 providers (incl. local
  Ollama/vLLM), schema-validated outputs, advisory only. 9 deterministic
  analyst agents. `HARD_LIMITS.llm_can_trade=False` — no LLM output can reach
  the execution path; research reports are stored, never acted upon.

## 4. Decision cycle (§11) — `engine.run_cycle()`

```
refresh universe+REST(+derivs) → revalue accounts → features (all markets)
→ cross-asset enrich + correlation matrix → per market:
     health → regime → strategies → AI infer → pre-trade simulation
     → opportunity score → RISK GATE → sizer (stage caps, filters)
     → explanation (§40) → decision record (§36) → agents (advisory)
     → prediction log (drift source)
→ rank approved opportunities → execute (stage-aware) → SL/TP management
→ resting paper-order fills → periodic: portfolio snapshot (5), reconciliation
  (10), drift tick (20), LLM research (30) → adaptive auto_loop interval (§44)
```

Pre-trade simulation (`opportunities/ranker.py`) computes gross expectation
from model outputs and subtracts real cost components (fee bps, slippage bps,
half-spread, latency risk, funding for held futures) → `expected_net_pct` must
exceed `min_edge_pct` + uncertainty or the opportunity is rejected before the
risk engine ever sees it.

## 5. Execution plane (§30–§35)

* **Modes.** `effective_mode()` derives from stage + `live_enabled`:
  PAPER (real books, simulated fills, simulated balances), SHADOW (identical
  decisions, **nothing sent anywhere**, audited), LIVE (signed venue orders,
  only when the gate opened `live_enabled`).
* **Paper fills walk the real book** up to `paper_participation_max` (25% of
  book capacity) → explicit partial fills with VWAP + slippage + fee +
  latency. No book → labeled ticker-spread estimate. No price → refused.
* **Order state machine** (`execution/order_state.py`): legal transitions only
  (CREATED→VALIDATED→SUBMITTED→PARTIALLY_FILLED→FILLED/CANCELLED/REJECTED/
  EXPIRED/UNKNOWN), every transition persisted (`order_transitions`). UNKNOWN
  is a first-class state: resubmission is refused (`guard_resubmit`) until the
  reconciler resolves the true venue state (§33).
* **Live path**: venue gate → place → **confirm by query** (success never
  assumed). Any exception ⇒ order→UNKNOWN + `live_block` + CRITICAL alert;
  all further live orders refused until `Reconciler.resolve_unknown_orders()`
  clears the block. Balances synced from the venue, never trusted locally.
* **Reconciliation** (`execution/reconciler.py`): paper (equity identity ±0.02,
  every OPEN position has an entry fill) and live (canWithdraw must be False
  (§51), venue balances vs positions, UNKNOWN sweep). Mismatch ⇒ live block +
  CRITICAL alert.
* **Execution quality** (`execution/quality.py`): per-fill slippage vs spread,
  fill ratio, latency, maker rate → composite venue score; NO DATA returns
  None (never fabricated).

## 6. Risk plane (§21, §29)

Gate order in `RiskEngine.authorize()` (v2 order preserved, hardened):
stage caps → system state → entries allowed (kill switch/halt/feeds/AI policy/
consecutive losses) → venue allowed (BLOCKED/UNREACHABLE/NOT_CONFIGURED block
LIVE) → market health → AI validity (WAIT/untrained in strict mode) → regime
gate (PANIC/UNSTABLE) → min edge → portfolio caps (positions, duplicate,
total/asset/correlated/directional exposure) → sizing multipliers (DEFENSIVE
0.4, CAUTION 0.65, LIMITED health ×0.5, degraded/untrained ×0.5, high
uncertainty ×0.7). Returns `(Decision, reasons, hint)`; hint carries the
size multiplier + stage caps into the sizer.

Hard limits (code constants, not config): max position 50% equity, gross
exposure ≤200%, leverage ≤5×, **withdrawals FORBIDDEN**, LLM/MCP can never
trade, never auto-promote. Kill switch persists in config (survives restart),
resume requires typing `RESUME TRADING`, optional flatten/reduce on engage.

## 7. Venues & wallets (§9, §13, §52)

* Adapters share one contract: `capabilities() / status() / health() /
  test_connection() / place_order / cancel / balances …`. `test_connection`
  **hard-refuses keys with withdrawal permission**.
* Binance Spot/Futures: public host failover (`data-api.binance.vision` mirror
  first — `api.binance.com` is geo-blocked (451) in some regions); signed
  traffic only via the official base with HMAC-SHA256; rate limiter per
  endpoint class; 418/429 raised immediately (never host-hopping to dodge bans).
* ZEPAY venue (INR): full contract, `NOT_CONFIGURED` until an endpoint exists.
* Solana/Phantom: connect = challenge + ed25519 verify (address only — no
  secrets); swaps = Jupiter real quote → unsigned tx → Phantom signs locally →
  broadcast. Raw JSON-RPC (solana-py deliberately unused: no sync client).

## 8. Trust & ops (§45–§58)

* Decision ledger + explanations (§36/§40), immutable audit log, alerts,
  system events — all persisted, all queryable via API/UI.
* Secrets: ChaCha20-Poly1305 (RFC 8439, self-test at boot), HKDF purpose
  separation, encrypted at rest, master key file 0600.
* MCP (§50): registry ships 4 suggested servers **disabled + unconfigured**;
  permission classes are `research.readonly` / `notes.write` ONLY; trading/
  withdrawal/credential classes are refused at upsert; trade-keyword tool
  names refused at call; per-server rate limits; every call logged.
* Monitoring (§45/§46): system health aggregates DB latency, feeds, WS state,
  venues, risk state, model state, disk — overall = worst subsystem; internal
  metrics (cycle latency p50/p95, errors, memory) measured, not estimated.
* Backups (§56): SQLite online backup API + `PRAGMA integrity_check` on the
  backup; Postgres via pg_dump when present, honest failure otherwise;
  rotation; restore intentionally manual (audit-trail immutability).

## 9. API & frontend (§47, §49)

FastAPI (`api/app.py` + 4 routers): system (health/status/config/stage/kill/
backup/audit/venues/SSE events), markets (universe/signals/candles/ticker/
depth/derivs/explanation/quality), trading (decisions/orders/positions/trades/
account/portfolio/risk/execution-quality + manual close/cancel/flatten/cycle/
reconcile), intelligence (AI/models/promotion/experiments/research/walk-
forward/hyperopt/backtest/MCP/drift). Mutating endpoints share one guard
(optional bearer token via `api_token`; privileged config keys refused by
design — stage/kill/venues have dedicated audited flows).

Frontend (`frontend/`): dependency-free vanilla JS + dark responsive theme;
six tabs (Dashboard, Markets, Trading, Risk, Research, System); 6s status
poll, 15s tab refresh, SSE live event feed; typed-confirmation modals for
kill-switch resume, stage promotion, flatten; sparklines drawn from real
candles. Works desktop and mobile (bottom-scroll tabs, stacked cards).

## 10. Persistence (§12)

SQLite default (WAL), PostgreSQL supported (`ZEPAY_DB_BACKEND=postgres` +
DSN; SQL auto-translated, falls back to SQLite with a logged error). Redis
optional (falls back to labeled in-memory cache). Schema = v2 tables +
V3 additions (instruments, order_transitions, orderbook_snapshots,
execution_metrics, data_quality_events, model_promotions, walk_forward_runs,
portfolio_exposures, alpha_factors, ws_stream_state; orders/positions/
decisions/models extended). Migrations are idempotent at boot — see
`MIGRATIONS.md`.

## 11. What was removed vs v2 and why

* The 6393-line monolith (`zepay.py` → `legacy/zepay_v2.py`): unmaintainable,
  untestable in isolation; all behavior ported module-by-module with the
  invariants above preserved (gate order, thresholds, honesty rules).
* Global mutable state: replaced by DI through one composition root.
* `solana-py` dependency at runtime: dropped (no sync client in 0.40.x; raw
  JSON-RPC is smaller, auditable, and version-stable).
* Auto-anything on the live path: v2 already required manual promotion; V3
  makes skipping architecturally impossible (typed phrases + prerequisite
  checks + `auto_promote_to_live=False` constant).

Reference repos (Freqtrade, OctoBot, intelligent-trading-bot, binance/ai-
trading-prototype, Hummingbot, awesome-crypto-trading-bots) were studied for
concepts only; each adopted concept passed the security/license/
maintainability/testability/performance/architecture review — see
`LICENSE_REVIEW.md`. No code was copied.
