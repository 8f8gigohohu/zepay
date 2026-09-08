# ZEPAY V3 — Reference & Dependency License Review (§5/§64)

**No code was copied from any reference repository.** Concepts were studied
and re-implemented in the ZEPAY V3 architecture. Every concept passed the
six-gate review: SECURITY · LICENSE · MAINTAINABILITY · TESTABILITY ·
PERFORMANCE · ARCHITECTURAL compatibility. GPL code was never vendored, so no
copyleft obligation attaches to this repository.

## Reference repositories

| Repo | License | Concepts studied | Adopted in V3? | Review verdict |
|---|---|---|---|---|
| freqtrade/freqtrade | GPL-3.0 | hyperopt objective design (penalize drawdown/overfitting, not profit), walk-forward split discipline, exchange-filter-aware order sizing, protective stop logic | Concepts only, re-implemented in `ml/hyperopt.py`, `ml/walk_forward.py`, `risk/sizer.py` | ADOPT-CONCEPT. GPL forbids copying into this codebase; independent implementation is clean |
| Drakkar-Software/OctoBot | GPL-3.0 | multi-exchange adapter contract, staged evaluation pipeline, tentacle/plugin separation | Concepts only → `exchanges/base.py` adapter contract, strategy orchestrator | ADOPT-CONCEPT, same GPL boundary |
| asavinov/intelligent-trading-bot | MIT | signal→decision→execution separation, per-market continuous monitoring loop | Aligned with existing V3 cycle design | ADOPT-CONCEPT (MIT permits copying but none was needed) |
| binance/ai-trading-prototype | MIT (Binance sample) | ML-assisted direction scoring with honest "not financial advice" framing, feature snapshots per decision | V3 goes further: calibrated probabilities, OOS reporting, drift monitoring | ADOPT-CONCEPT |
| hummingbot/hummingbot | Apache-2.0 | market-making & grid strategy structure, order-book participation caps, execution-quality measurement | Concepts only → `strategies/advanced.py` (market_making, grid), `execution/paper.py` participation cap, `execution/quality.py` | ADOPT-CONCEPT (Apache-2.0 compatible; no code taken) |
| botcrypto-io/awesome-crypto-trading-bots | curated list | ecosystem survey, library vetting | Reading list only | REFERENCE |
| modelcontextprotocol/servers (via MCP spec) | MIT | MCP JSON-RPC tool protocol, permission model | Protocol client in `mcp/manager.py` with ZEPAY-hardened permission classes (trading/withdrawal/credential classes do not exist for MCP) | ADOPT-PROTOCOL |

Rejected concepts (gate failures): copy-trading/social signals (SECURITY:
unverifiable third-party intent), auto-promotion to live on profit targets
(ARCHITECTURAL: violates §22), direct LLM order placement (SECURITY: §15
hierarchy), exchange-credential sharing with plugins (SECURITY: §52),
backtest result caching without cost model (TESTABILITY: overfitting risk).

## Runtime dependencies

| Package | License | Used for | Verdict |
|---|---|---|---|
| Python stdlib (urllib, sqlite3, hashlib, hmac, secrets, asyncio…) | PSF-2.0 | HTTP, DB, crypto primitives (RFC 8439 implemented on stdlib) | OK |
| fastapi | MIT | API layer | OK |
| uvicorn | BSD-3-Clause | ASGI server | OK |
| httpx | BSD-3-Clause | test client / async HTTP where used | OK |
| websockets | BSD-3-Clause | WS engine | OK |
| numpy | BSD-3-Clause | model math, PSI, Monte Carlo | OK |
| redis-py | MIT | optional cache backend | OK (optional) |
| psycopg[binary] | LGPL-3.0 | optional Postgres backend | OK — used strictly as an imported library (dynamic linking); no LGPL code is modified or statically linked; Docker/OS-package distribution satisfies LGPL terms. Optional: system falls back to SQLite without it |
| pytest / ruff / black / mypy | MIT / MIT / MIT / MIT(+PSF bits) | dev gates only, not shipped at runtime | OK |

Deliberately **not** used: `solana-py`/`solders` (installed in the dev
sandbox but unused at runtime — 0.40.x dropped the sync client; ZEPAY uses raw
JSON-RPC + Jupiter REST, which is smaller and auditable), `ccxt` (huge
surface, slower to audit than the 3 hand-written adapters), TA-Lib (C
dependency; indicators re-implemented and unit-tested), pandas (numpy +
stdlib cover the math with a smaller footprint).

## Assets

Frontend uses no external fonts, icons or CDNs (system font stack, inline
SVG) — zero third-party asset licensing. The favicon is an emoji glyph.
