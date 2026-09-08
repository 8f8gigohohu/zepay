# Research Resource Decisions (spec §39)

Each listed resource was inspected for relevance, license, security, maintenance
and dependency weight, then classified: **INTEGRATED** (wired into the product),
**ADAPTED** (architecture/ideas implemented natively), **REFERENCE** (consulted
only), or **REJECTED** (not used, with reason). ZEPAY's hard constraints:
stdlib-only runtime, no supply-chain bloat, no agent may trade, no fake data.

## A. Trading / research frameworks

| Resource | License | Decision | Reasoning |
|---|---|---|---|
| TauricResearch/TradingAgents | MIT | **ADAPTED** | Analyst→research→risk→portfolio decomposition and bull/bear debate reimplemented natively (Agents + ResearchEngineLLM) with ZEPAY's own deterministic engines as the trading authority. Its simulated exchange is deliberately NOT used; live execution belongs to ZEPAY's signed exchange adapter. |
| AI4Finance-Foundation/FinRL | MIT | REFERENCE | Reinforcement-learning trading agents are out of scope for a deterministic, explainable risk-first platform. |
| freqtrade/freqtrade | MIT | REFERENCE | Mature crypto bot framework; importing it would conflict with the single-engine architecture and stdlib constraint. |

## B. LLM / agent frameworks

| Resource | License | Decision | Reasoning |
|---|---|---|---|
| langchain-ai/langchain | MIT | **REJECTED** (for runtime) | Heavy dependency tree; our provider calls are simple HTTP and implemented natively (AIProviderManager). |
| langflow-ai/langflow | MIT | REFERENCE | Visual workflow tooling adds a service + latency; the trading loop must stay deterministic and observable. Documented as optional future extension; not integrated. |
| run-llama/llama_index | MIT | REFERENCE | RAG would require a document corpus we don't have; research inputs are structured market data instead. |
| mem0ai/mem0 | Apache-2.0 | **ADAPTED (concept)** | Research history/observations persist in `research_reports`/`decisions`/`experiments` tables (queryable memory) without the service dependency. Vector RAG deliberately not included (dependency weight vs. value). |
| crewAIInc/crewAI | MIT | REFERENCE | Agent orchestration reimplemented in ~200 deterministic lines. |
| microsoft/autogen | MIT | REFERENCE | Same as above; multi-agent debate is one function with schema validation. |
| geekan/MetaGPT | MIT | REFERENCE | Software-company agent metaphor not applicable. |
| vllm-project/vllm | Apache-2.0 | **INTEGRATED (adapter)** | Supported as a LOCAL AI provider endpoint (OpenAI-compatible) — user runs vLLM separately; ZEPAY only calls it. |
| ollama/ollama | MIT | **INTEGRATED (adapter)** | LOCAL AI provider (`/api/chat`); health-checked, advisory only. |
| ggml-org/llama.cpp | MIT | REFERENCE | Consumed via Ollama/vLLM adapters; no direct integration. |
| huggingface/transformers | Apache-2.0 | REJECTED | No in-process transformers use justified; would break stdlib-only rule. Deep learning "for marketing" is explicitly avoided. LightGBM/XGBoost/CatBoost: excellent libraries, but the from-scratch logistic/ridge ensemble keeps the artifact dependency-free; models are honest about their simplicity and validated walk-forward. |
| openai/openai-cookbook etc. | MIT | REFERENCE | API shapes for the OpenAI-compatible adapter. |
| infiniflow/ragflow | Apache-2.0 | REFERENCE | Not needed without document RAG. |
| dolanmiu/docx (MarkItDown) | MIT | REJECTED | No document ingestion path. |
| ollama-webui / Open WebUI | MIT | REFERENCE | ZEPAY has its own dashboard. |
| LobeChat | MIT | REFERENCE | Chat UX not required. |
| nanochat | MIT | REFERENCE | Educational; not production-trading relevant. |
| ComfyUI | GPL-3.0 | REJECTED | Image-workflow tool; GPL + irrelevant domain. |
| Bumblebee (elixir) | Apache-2.0 | REJECTED | Wrong runtime. |

## C. MCP ecosystem

| Resource | License | Decision | Reasoning |
|---|---|---|---|
| punkpeye/awesome-mcp-servers | CC0-1.0 | **ADAPTED (catalogue)** | Used as the discovery reference for the MCP registry seeds (fetch/memory/filesystem/search — all shipped DISABLED with no URL). |
| modelcontextprotocol spec | MIT/CC | **INTEGRATED (client)** | Minimal JSON-RPC 2.0 over HTTP client (initialize/tools-list/tools-call) implemented natively; permission classes exclude trading by construction. |

## D. Web automation / research tooling

| Resource | License | Decision | Reasoning |
|---|---|---|---|
| browser-use/browser-use | MIT | REJECTED | Browser automation must not substitute for official exchange APIs (which exist); research data comes from registered providers. |
| browserbase/stagehand | MIT | REJECTED | Same policy. |
| firecrawl/firecrawl | AGPL-3.0 | REJECTED | AGPL + scraping risk; not needed. |
| ScrapeGraphAI/Scrapling | MIT | REJECTED | No scraping path — providers are called via documented APIs only, honoring ToS. |
| maigret (osint) | MIT | REJECTED | Out of scope. |

## E. API catalogues & developer resources

| Resource | License | Decision | Reasoning |
|---|---|---|---|
| public-apis/public-apis | CC0-1.0 | **ADAPTED (data)** | Seed data for ZEPAY's Public API Registry (25+ curated entries with auth/rate-limit/free-tier metadata), clearly split from *integrated* providers. |
| free-for-dev | CC-BY-4.0 | REFERENCE | Cross-checked free tiers. |
| awesome (sindresorhus) | CC0 | REFERENCE | Discovery only. |

## F. Engineering knowledge bases (reference only, no code)

system-design-primer (MIT), tech-interview-handbook (MIT), coding-interview-university (CC-BY-SA), javascript-algorithms (MIT), 30-seconds-of-code (CC0), the-art-of-command-line (CC-BY-SA), build-your-own-x (CC0), project-based-learning (MIT), you-dont-know-js (CC-BY-NC-4 — **note: NC license; never copy code**, reference only), free-programming-books (CC-BY), developer-roadmap (CC-BY-SA-4.0), gitignore (CC0 — conventions followed for .gitignore), the-book-of-secret-knowledge (CC0), freeCodeCamp (BSD-3) — all **REFERENCE**; used for engineering standards, never imported.

## G. Development agents & tooling

| Resource | License | Decision | Reasoning |
|---|---|---|---|
| All-Hands-AI/OpenHands, Aider, Claude Code-style agents | MIT et al. | REJECTED for production | Development-time assistants only; they must never receive production exchange credentials or live-trading authority (enforced by policy: secrets are encrypted at rest with a per-install key that is not committed anywhere). |
| n8n | AGPL-3.0 (fair-code) | REJECTED | Workflow automation not needed; AGPL and license restrictions. |
| Dify | Apache-2.0 (with restrictions) | REFERENCE | LLM app platform; overlaps AIProviderManager without adding value here. |
| OpenClaw / Ruflo | various | REFERENCE | Not applicable to this architecture. |

## H. Testing / safety

| Resource | License | Decision | Reasoning |
|---|---|---|---|
| iFixAi (AI safety testing concept) | — | **ADAPTED** | AI-safety test cases implemented in the suite: prompt-injection-adjacent guards (schema validation, keyword refusals), hallucinated-output rejection, unsafe-tool refusal, risk-bypass attempts, fake-confidence clamping. |
| karpathy/nanochat, Awesome Claude Skills, Claude Context, supermemory | various | REFERENCE | Conceptual only. |

## Dependency-bloat audit

Final runtime dependency count: **0** (stdlib only). Optional user-run external
processes: Ollama or vLLM (local LLM), any OpenAI-compatible cloud LLM,
registered MCP servers (HTTP). None are required for the core trading loop.
