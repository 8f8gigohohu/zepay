# V3 Third-Party Dependencies & Reference-Repo Decisions

## Runtime dependencies (requirements.txt)

| Dependency | Version | License | Purpose | Decision |
|---|---|---|---|---|
| fastapi | >=0.110 | MIT | HTTP API framework | integrate |
| uvicorn | >=0.29 | BSD-3 | ASGI server | integrate |
| httpx | >=0.27 | BSD-3 | HTTP client (tests/live probes) | integrate |
| websockets | >=12 | BSD-3 | market-data WS engine | integrate |
| numpy | >=1.26 | BSD-3 | numeric feature/model math | integrate |
| redis | >=5 | MIT | optional cache backend (memory fallback) | integrate (optional) |
| psycopg | >=3 | LGPL-3 | optional PostgreSQL backend (SQLite default) | integrate (optional) |

## V3.1 additions

**None.** The license adapter, multi-venue routing, futures prechecks and all
V3.1 features are implemented on the standard library + the dependencies
above. No new third-party code was introduced.

## Reference repositories (inspected, not copied)

| Repo | Decision | Reason |
|---|---|---|
| TradingAgents | reference only | research/analysis patterns; its simulated exchange is NOT used as ZEPAY's live exchange (spec constraint) |
| Langflow / MCP ecosystem | adapt (protocol only) | MCP manager implemented natively; no Langflow runtime dependency, no dependency bloat |
| OpenHands / Aider style dev agents | reject (as production components) | development tools must never hold production credentials or trading authority |
| public-apis catalogue | reference | PUBLIC_API_CATALOG in `zepay/exchanges/registry.py`; scraping/bypass restrictions never violated |

## Preserved heritage

* V2 ultimate build: `legacy/zepay_v2.py` + `legacy/tests_v2/` (reference; the
  license adapter is the one deliberate v2→v3.1 port, reimplemented in
  `zepay/security/license.py` with the same honesty contract).
