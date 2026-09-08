"""LLM provider manager (§15: LLM Research Assistant — ADVISORY ONLY).

Ported from v2 with dependency injection. Hard invariants, enforced by
architecture (not by prompt):
  * the LLM has NO access to the ExecutionEngine, RiskEngine limits,
    credentials, kill switch or any order-placement API — there is no import
    path from this module to them
  * output is schema-validated; failures are DISCARDED, never guessed
  * providers activate only with real configuration; keys live encrypted
"""

from __future__ import annotations

import copy
import logging
import time
from collections import defaultdict

from zepay.core.util import safe_float, utcnow_iso
from zepay.exchanges.http import http_error_str, http_json

log = logging.getLogger("zepay.ai.llm")

AI_PROVIDER_DEFS = {
    "openai": {
        "name": "OpenAI",
        "kind": "openai_compatible",
        "default_base": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
        "local": False,
        "key_required": True,
        "docs": "https://platform.openai.com/docs",
    },
    "anthropic": {
        "name": "Anthropic",
        "kind": "anthropic",
        "default_base": "https://api.anthropic.com",
        "default_model": "claude-3-5-haiku-latest",
        "local": False,
        "key_required": True,
        "docs": "https://docs.anthropic.com",
    },
    "google": {
        "name": "Google Gemini",
        "kind": "google",
        "default_base": "https://generativelanguage.googleapis.com",
        "default_model": "gemini-2.0-flash",
        "local": False,
        "key_required": True,
        "docs": "https://ai.google.dev/docs",
    },
    "deepseek": {
        "name": "DeepSeek",
        "kind": "openai_compatible",
        "default_base": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
        "local": False,
        "key_required": True,
        "docs": "https://api-docs.deepseek.com",
    },
    "qwen": {
        "name": "Qwen (DashScope)",
        "kind": "openai_compatible",
        "default_base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
        "local": False,
        "key_required": True,
        "docs": "https://help.aliyun.com/zh/dashscope/",
    },
    "xai": {
        "name": "xAI Grok",
        "kind": "openai_compatible",
        "default_base": "https://api.x.ai/v1",
        "default_model": "grok-2-latest",
        "local": False,
        "key_required": True,
        "docs": "https://docs.x.ai",
    },
    "openrouter": {
        "name": "OpenRouter",
        "kind": "openai_compatible",
        "default_base": "https://openrouter.ai/api/v1",
        "default_model": "openai/gpt-4o-mini",
        "local": False,
        "key_required": True,
        "docs": "https://openrouter.ai/docs",
    },
    "ollama": {
        "name": "Ollama (LOCAL)",
        "kind": "ollama",
        "default_base": "http://localhost:11434",
        "default_model": "llama3.1",
        "local": True,
        "key_required": False,
        "docs": "https://ollama.com",
    },
    "vllm": {
        "name": "vLLM (LOCAL)",
        "kind": "openai_compatible",
        "default_base": "http://localhost:8001/v1",
        "default_model": "local-model",
        "local": True,
        "key_required": False,
        "docs": "https://docs.vllm.ai",
    },
}


class AIProviderManager:
    def __init__(self, config, secrets, audit_fn, ai_engine=None):
        self.config = config
        self.secrets = secrets
        self.audit = audit_fn
        self.ai = ai_engine
        self.usage = defaultdict(
            lambda: {
                "calls": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "errors": 0,
                "total_latency_ms": 0,
                "last_call": None,
                "last_error": None,
                "last_ok": None,
            }
        )

    # ---- config helpers ----
    def provider_config(self, pid: str) -> dict:
        cfgs = self.config.get("ai_providers") or {}
        return cfgs.get(pid) or {}

    def configured(self, pid: str) -> bool:
        d = AI_PROVIDER_DEFS.get(pid)
        if not d:
            return False
        c = self.provider_config(pid)
        if not c.get("enabled"):
            return False
        if d["key_required"]:
            try:
                return bool(self.secrets.get(f"ai_key_{pid}"))
            except Exception:
                return False
        return True

    def active_provider(self):
        for pid in AI_PROVIDER_DEFS:
            if self.configured(pid):
                return pid
        return None

    def set_provider(
        self, pid: str, enabled: bool, base_url=None, model=None, api_key=None
    ) -> dict:
        if pid not in AI_PROVIDER_DEFS:
            return {"ok": False, "error": "unknown provider"}
        cfgs = copy.deepcopy(self.config.get("ai_providers") or {})
        c = cfgs.get(pid) or {}
        c["enabled"] = bool(enabled)
        if base_url is not None:
            c["base_url"] = str(base_url)
        if model:
            c["model"] = str(model)
        cfgs[pid] = c
        if api_key is not None:
            if api_key == "":
                self.secrets.delete(f"ai_key_{pid}")
            else:
                self.secrets.put(f"ai_key_{pid}", api_key, purpose="general")
        if c["enabled"]:
            for k in cfgs:  # one active LLM at a time
                if k != pid:
                    cfgs[k]["enabled"] = False
            d = AI_PROVIDER_DEFS[pid]
            patch = {"ai_providers": cfgs, "ai_mode": "LOCAL_LLM" if d["local"] else "CLOUD_LLM"}
        elif not any(x.get("enabled") for x in cfgs.values()):
            patch = {"ai_providers": cfgs, "ai_mode": "LOCAL_QUANT_ONLY"}
        else:
            patch = {"ai_providers": cfgs}
        self.config.update(patch, actor="user")
        self.audit(
            "ai",
            "provider_configured",
            {"provider": pid, "enabled": c["enabled"], "model": c.get("model")},
            actor="user",
        )
        return {"ok": True}

    # ---- transport (real API calls) ----
    def chat(self, system: str, user: str, max_tokens: int = 800, timeout: float = 45) -> dict:
        pid = self.active_provider()
        if not pid:
            raise RuntimeError("no AI provider configured (quant ensemble still active)")
        d = AI_PROVIDER_DEFS[pid]
        c = self.provider_config(pid)
        base = c.get("base_url") or d["default_base"]
        model = c.get("model") or d["default_model"]
        t0 = time.time()
        try:
            if d["kind"] == "openai_compatible":
                headers = {"Content-Type": "application/json"}
                key = self.secrets.get(f"ai_key_{pid}") if d["key_required"] else None
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                body = {
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                }
                r = http_json(
                    f"{base}/chat/completions",
                    timeout=timeout,
                    method="POST",
                    data=body,
                    headers=headers,
                    venue=f"llm:{pid}",
                )
                text = (r.get("choices") or [{}])[0].get("message", {}).get("content", "")
                usage = r.get("usage") or {}
            elif d["kind"] == "anthropic":
                key = self.secrets.get(f"ai_key_{pid}")
                if not key:
                    raise RuntimeError("missing API key")
                headers = {
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                }
                body = {
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                }
                r = http_json(
                    f"{base}/v1/messages",
                    timeout=timeout,
                    method="POST",
                    data=body,
                    headers=headers,
                    venue=f"llm:{pid}",
                )
                text = "".join(
                    b.get("text", "") for b in r.get("content", []) if b.get("type") == "text"
                )
                usage = r.get("usage") or {}
            elif d["kind"] == "google":
                key = self.secrets.get(f"ai_key_{pid}")
                if not key:
                    raise RuntimeError("missing API key")
                body = {
                    "contents": [{"parts": [{"text": system + "\n\n" + user}]}],
                    "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2},
                }
                r = http_json(
                    f"{base}/v1beta/models/{model}:generateContent?key={key}",
                    timeout=timeout,
                    method="POST",
                    data=body,
                    venue=f"llm:{pid}",
                )
                parts = ((r.get("candidates") or [{}])[0].get("content", {}) or {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                usage = r.get("usageMetadata") or {}
            elif d["kind"] == "ollama":
                body = {
                    "model": model,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "options": {"temperature": 0.2, "num_predict": max_tokens},
                }
                r = http_json(
                    f"{base}/api/chat",
                    timeout=timeout,
                    method="POST",
                    data=body,
                    venue=f"llm:{pid}",
                )
                text = (r.get("message") or {}).get("content", "")
                usage = {}
            else:
                raise RuntimeError("unknown provider kind")
            u = self.usage[pid]
            u["calls"] += 1
            u["tokens_in"] += int(
                safe_float(
                    usage.get("input_tokens")
                    or usage.get("prompt_tokens")
                    or usage.get("promptTokenCount"),
                    0,
                )
            )
            u["tokens_out"] += int(
                safe_float(
                    usage.get("output_tokens")
                    or usage.get("completion_tokens")
                    or usage.get("candidatesTokenCount"),
                    0,
                )
            )
            u["total_latency_ms"] += int((time.time() - t0) * 1000)
            u["last_call"] = utcnow_iso()
            u["last_ok"] = utcnow_iso()
            u["last_error"] = None
            return {
                "provider": pid,
                "model": model,
                "text": text,
                "latency_ms": int((time.time() - t0) * 1000),
                "local": bool(d["local"]),
                "advisory_only": True,
            }
        except Exception as e:
            u = self.usage[pid]
            u["errors"] += 1
            u["last_call"] = utcnow_iso()
            u["last_error"] = (
                http_error_str(e)[:200] if not isinstance(e, RuntimeError) else str(e)[:200]
            )
            raise

    def health_check(self, pid: str) -> dict:
        if pid not in AI_PROVIDER_DEFS:
            return {"ok": False, "error": "unknown provider"}
        if not self.configured(pid):
            return {"ok": False, "status": "NOT_CONFIGURED"}
        try:
            r = self.chat(
                "You are a connectivity probe. Reply with the word: ok",
                "ping",
                max_tokens=8,
                timeout=20,
            )
            return {
                "ok": True,
                "status": "OK",
                "model": r["model"],
                "local": r["local"],
                "latency_ms": r["latency_ms"],
                "sample": (r["text"] or "")[:40],
            }
        except Exception as e:
            return {
                "ok": False,
                "status": "ERROR",
                "error": (http_error_str(e) if not isinstance(e, RuntimeError) else str(e))[:200],
            }

    def status(self) -> dict:
        active = self.active_provider()
        rows = []
        for pid, d in AI_PROVIDER_DEFS.items():
            c = self.provider_config(pid)
            u = dict(self.usage[pid])
            try:
                key_stored = bool(self.secrets.get(f"ai_key_{pid}"))
            except Exception:
                key_stored = False
            rows.append(
                {
                    "id": pid,
                    "name": d["name"],
                    "local": d["local"],
                    "kind": d["kind"],
                    "configured": self.configured(pid),
                    "enabled": bool(c.get("enabled")),
                    "active": pid == active,
                    "base_url": c.get("base_url") or d["default_base"],
                    "model": c.get("model") or d["default_model"],
                    "key_status": (
                        "stored-encrypted"
                        if key_stored
                        else ("not needed" if not d["key_required"] else "MISSING")
                    ),
                    "key_required": d["key_required"],
                    "docs": d["docs"],
                    "usage": {
                        "calls": u.get("calls", 0),
                        "tokens_in": u.get("tokens_in", 0),
                        "tokens_out": u.get("tokens_out", 0),
                        "errors": u.get("errors", 0),
                        "avg_latency_ms": (
                            (u.get("total_latency_ms", 0) // u["calls"]) if u.get("calls") else None
                        ),
                        "last_call": u.get("last_call"),
                        "last_error": u.get("last_error"),
                        "last_ok": u.get("last_ok"),
                    },
                }
            )
        ai = self.ai
        quant = {}
        if ai is not None:
            quant = {
                "version": ai.metrics.get("version", ""),
                "trained": ai.trained,
                "degraded": ai.degraded,
                "metrics": ai.metrics,
                "inferences": ai.inference_count,
                "last_inference": ai.last_inference,
                "healthy": ai.healthy(),
                "strict_mode": bool(self.config.get("strict_ai_mode", True)),
            }
        return {
            "active": active,
            "mode": self.config.get("ai_mode", "LOCAL_QUANT_ONLY"),
            "quant": quant,
            "providers": rows,
            "authority_note": "LLM providers are ADVISORY ONLY — no code path from "
            "this module can place orders, change risk limits, "
            "touch credentials of exchanges, or flip the kill switch.",
        }
