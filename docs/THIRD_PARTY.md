# Third-Party Dependencies & Attribution

## Runtime dependencies

**None.** ZEPAY v2.0.0 is implemented entirely on the Python 3.10+ standard
library. There are no pip requirements, no vendored third-party code, and no
CDN/web assets. Every subsystem — HTTP server, database layer, crypto
(ChaCha20-Poly1305, HKDF), WebSocket client, ML models — is original code in
`zepay.py`, validated where possible against published RFC test vectors.

This is a deliberate decision (smallest reliable architecture, minimal supply
chain, auditable single artifact). See RESOURCE_DECISIONS.md for the full
evaluation of the considered open-source ecosystem.

## Cryptography references (specs implemented, not copied)

- ChaCha20, Poly1305, AEAD construction: **RFC 8439** (test vectors §2.3.2,
  §2.5.2, §A.5 asserted at boot and in tests).
- HKDF: **RFC 5869** (Test Case 1 asserted).

## External services (called at runtime, with their terms)

| Service | Use | Auth | Notes |
|---|---|---|---|
| Binance Spot public REST (`api.binance.com`, `data-api.binance.vision`) | market data, exchange metadata | none | public endpoints; Binance API Terms apply |
| Binance Futures public REST (`fapi.binance.com`) | funding/mark/index/OI context | none | public endpoints |
| Binance WebSocket (`stream.binance.com:9443`) | optional live tickers | none | disabled by default |
| Binance Spot signed REST | live trading (user-enabled) | user's API key | trade-only key enforced; withdrawals refused |
| User-configured LLM providers (OpenAI, Anthropic, Google, DeepSeek, Qwen, xAI, OpenRouter, Ollama, vLLM) | advisory research only | user's API key | each provider's terms apply; keys encrypted at rest |
| ZEPAY cloud license verification | key verification | ZEPAY key | **specification not provided — BLOCKED** |

## Reference resources consulted (not imported)

The following repositories/catalogues were used as engineering references and
discovery sources only. No code from them is included in this project:

- public-apis/public-apis (CC0-1.0 / MIT) — API catalogue metadata.
- TauricResearch/TradingAgents (MIT) — multi-agent research architecture ideas.
- langflow-ai/langflow (MIT) — visual AI workflow concepts (not integrated).
- punkpeye/awesome-mcp-servers (CC0-1.0) — MCP server discovery reference.
- mem0ai/mem0, run-llama/llama_index (Apache-2.0) — memory/RAG concepts.

Full per-repository decisions: [RESOURCE_DECISIONS.md](RESOURCE_DECISIONS.md).

## License of this project

Provided as-is for education and research. Trading cryptocurrencies involves
substantial risk; past performance is not a guarantee of future results.
