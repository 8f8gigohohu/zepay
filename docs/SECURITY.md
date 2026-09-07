# ZEPAY — Security Model

## Non-negotiable rules (enforced in code)

- No hard-coded keys. No secrets in source, logs, or API responses.
- Withdrawal permission is **refused platform-wide**: an exchange key with
  `canWithdraw=true` fails the connection test with `SECURITY REFUSAL`.
- The MCP permission model has **no trading class**. `trading`, `withdrawals`
  and `credentials` are hard-refused at registration time; tool names/arguments
  matching trading actions are refused before any network call.
- LLMs and agents are advisory: they have no reference to the ExecutionEngine,
  cannot alter risk limits, cannot disable the kill switch, cannot touch
  credentials.
- No automatic live activation; live requires a typed confirmation phrase.
- Kill switch: instant, recorded, alerting, **never auto-resumes**.

## Secrets

- All secrets (exchange API key/secret, LLM provider keys) are stored in the
  `secrets` table **encrypted at rest** with ChaCha20-Poly1305 (RFC 8439),
  keys derived per-purpose via HKDF-SHA256 from a per-install master key
  (`zepay_data/.master.key`, generated 0600 on first boot).
- The crypto implementation is validated against RFC 8439 §A.5 and RFC 5869
  test vectors at **every boot** (a corrupted crypto layer refuses to start).
- Secrets are never returned by any API (public config masks them as
  `***STORED-ENCRYPTED***`; keys display only as `ABCD****XY` masks).
- `redact()` recursively removes secret-looking fields before audit/logging.

## Exchange credentials

- Stored only after a **real signed** `/api/v3/account` test succeeds and the
  key passes permission checks (canTrade, canWithdraw=false).
- Requests are HMAC-SHA256 signed with `timestamp`/`recvWindow`; the API key is
  sent only in the `X-MBX-APIKEY` header to the exchange.
- Clearing credentials deletes the encrypted records.

## Transport & app hardening

- Outbound calls use strict timeouts and a per-provider rate limiter.
- HTTP responses set `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Cache-Control: no-store`.
- Session tokens: 24-byte urlsafe, stored server-side with a 12h TTL,
  delivered as `HttpOnly` cookie + header token; 401 for everything except
  the setup wizard and `/api/health`.
- Input: JSON bodies capped at 1 MB; market symbols validated against the
  universe catalogue; config updates use a whitelist (privileged keys are
  unreachable through the public config endpoint).

## Live-trading safety gate

`/api/live/enable` verifies, in order, with **real** checks:
credentials → connection (signed) → permissions (trade=yes, withdraw=no) →
market data health → reconciliation (local vs exchange, incl. unknown-order
detection) → risk self-test → ≥5 paper fills → typed phrase.
Failures return the failing step and do not enable anything. An order whose
outcome is unknown (timeout) blocks all further live orders until reconciled.

## Failure policies (fail-safe defaults)

| Failure | Behavior |
|---|---|
| Market data unavailable | banner *REAL DATA UNAVAILABLE*; all new entries blocked |
| Data stale per-asset | asset `BLOCKED/LIMITED`; execution aborts on stale price |
| Quant AI untrained/degraded | strict mode ⇒ `WAIT` signals; policy `reduce_risk` halves sizing or `stop_trading` blocks entries |
| LLM unavailable | research layer disabled (quant ensemble unaffected); never fabricated |
| Reconciliation mismatch | live trading blocked with reason |
| Unknown live order outcome | live trading blocked until reconciled |
| Risk engine error | authorize fails closed (REJECTED/HALTED) |

## AI safety testing

Covered in the test suite: LLM output schema validation discards malformed
output; confidence clamped to [0,1]; bias coerced to enum; trading-looking MCP
tools refused; agent outputs are data-only structures. The LLM prompt pins the
model to provided data only, and its output never reaches execution.

## Backups

`python3 zepay.py backup` copies the DB + config to `zepay_data/backups/`.
The master key is intentionally **not** copied — store it separately; without
it encrypted secrets cannot be restored (by design).
