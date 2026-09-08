"""MCP manager (§50): registry + JSON-RPC client, permission-controlled.

General MCP tools can NEVER hold trading permissions — that permission class
does not exist for MCP in ZEPAY (architectural refusal, not a policy flag).
Every call is rate-limited, logged and audited. All suggested servers are
DISABLED and UNCONFIGURED until the operator explicitly registers a URL.

Ported from v2 with dependency injection; refusal semantics unchanged and
covered by tests.
"""

from __future__ import annotations

import logging
import re
import secrets as pysecrets
import time
from collections import defaultdict

from zepay.core.util import utcnow_iso
from zepay.exchanges.http import http_error_str, http_json

log = logging.getLogger("zepay.mcp")

MCP_PERMISSION_CLASSES = {
    "research.readonly": "Fetch/read research material (web pages, docs, data). No writes.",
    "notes.write": "Write to a notes/memory store.",
}
MCP_FORBIDDEN_CLASSES = {
    "trading": "HARD REFUSED — trading is reserved to ZEPAY's own execution engine.",
    "withdrawals": "HARD REFUSED — withdrawals are forbidden platform-wide.",
    "credentials": "HARD REFUSED — MCP tools may never touch credentials.",
    "private_keys": "HARD REFUSED — wallet keys never exist in this process.",
}
MCP_TRADE_KEYWORDS = re.compile(
    r"(?:^|[^a-z0-9])(orders?|trades?|trading|buy|buying|sell|selling|withdraw|withdrawals?|"
    r"positions?|leverage|kill_?switch|risk_?limits?)(?:$|[^a-z0-9])",
    re.I,
)

MCP_SUGGESTED = [
    {
        "id": "mcp-fetch",
        "name": "Fetch (web retrieval)",
        "url": "",
        "purpose": "Fetch web pages for research",
        "permissions": ["research.readonly"],
        "source": "github.com/modelcontextprotocol/servers",
    },
    {
        "id": "mcp-memory",
        "name": "Memory (knowledge graph)",
        "url": "",
        "purpose": "Persistent research notes",
        "permissions": ["research.readonly", "notes.write"],
        "source": "github.com/modelcontextprotocol/servers",
    },
    {
        "id": "mcp-filesystem",
        "name": "Filesystem (sandboxed)",
        "url": "",
        "purpose": "Read research files",
        "permissions": ["research.readonly"],
        "source": "github.com/modelcontextprotocol/servers",
    },
    {
        "id": "mcp-search",
        "name": "Web Search",
        "url": "",
        "purpose": "Search the web for research",
        "permissions": ["research.readonly"],
        "source": "awesome-mcp-servers catalogue",
    },
]


class MCPManager:
    def __init__(self, config, storage, audit_fn):
        self.config = config
        self.db = storage
        self.audit = audit_fn
        self._calls: dict[str, list[float]] = defaultdict(list)
        self.status: dict[str, dict] = {}
        self._ensure_registry()

    def _ensure_registry(self) -> None:
        servers = self.config.get("mcp_servers")
        if not isinstance(servers, list):
            servers = []
        have = {s.get("id") for s in servers}
        changed = False
        for s in MCP_SUGGESTED:
            if s["id"] not in have:
                servers.append(
                    {**s, "enabled": False, "rate_limit_per_min": 10, "configured": False}
                )
                changed = True
        if changed:
            self.config.update({"mcp_servers": servers}, actor="system")

    def registry(self) -> list[dict]:
        self._ensure_registry()
        out = []
        for s in self.config.get("mcp_servers") or []:
            st = self.status.get(s.get("id"), {})
            out.append(
                {
                    **{
                        k: s.get(k)
                        for k in (
                            "id",
                            "name",
                            "url",
                            "purpose",
                            "permissions",
                            "enabled",
                            "rate_limit_per_min",
                        )
                    },
                    "configured": bool(s.get("url")),
                    "status": (
                        "DISABLED"
                        if not s.get("enabled")
                        else ("NOT_CONFIGURED" if not s.get("url") else st.get("status", "UNKNOWN"))
                    ),
                    "last_call": st.get("last_call"),
                    "last_error": st.get("last_error"),
                    "calls_last_min": len(self._calls.get(s.get("id"), [])),
                    "forbidden_classes": list(MCP_FORBIDDEN_CLASSES.keys()),
                    "source": s.get("source", "user-registered"),
                }
            )
        return out

    def upsert(self, entry: dict) -> dict:
        perms_in = entry.get("permissions") or []
        perms = [p for p in perms_in if p in MCP_PERMISSION_CLASSES]
        rejected = [p for p in perms_in if p in MCP_FORBIDDEN_CLASSES]
        if rejected:
            self.audit(
                "security",
                "mcp_permission_refused",
                {"server": entry.get("id"), "rejected": rejected},
            )
            self._log_call(
                entry.get("id", "?"), "upsert", False, f"forbidden permission classes {rejected}"
            )
            return {
                "ok": False,
                "error": f"Permission classes refused: {rejected}. MCP tools can "
                "never receive trading/withdrawal/credential permissions.",
            }
        if MCP_TRADE_KEYWORDS.search(entry.get("purpose", "") or "") or MCP_TRADE_KEYWORDS.search(
            entry.get("name", "") or ""
        ):
            self._log_call(
                entry.get("id", "?"), "upsert", False, "trading keywords in purpose/name"
            )
            return {"ok": False, "error": "Purpose/name mentions trading actions — refused."}
        servers = list(self.config.get("mcp_servers") or [])
        found = False
        for i, s in enumerate(servers):
            if s.get("id") == entry.get("id"):
                servers[i] = {**s, **entry, "permissions": perms}
                found = True
                break
        if not found:
            servers.append(
                {
                    "id": entry.get("id") or f"mcp-{pysecrets.token_hex(3)}",
                    "name": entry.get("name", "unnamed"),
                    "url": entry.get("url", ""),
                    "purpose": entry.get("purpose", ""),
                    "permissions": perms,
                    "enabled": bool(entry.get("enabled", False)),
                    "rate_limit_per_min": int(entry.get("rate_limit_per_min", 10)),
                    "configured": bool(entry.get("url")),
                }
            )
        self.config.update({"mcp_servers": servers}, actor="user")
        self.audit(
            "mcp", "server_registered", {"id": entry.get("id"), "permissions": perms}, actor="user"
        )
        return {"ok": True}

    def remove(self, sid: str) -> dict:
        servers = [s for s in (self.config.get("mcp_servers") or []) if s.get("id") != sid]
        self.config.update({"mcp_servers": servers}, actor="user")
        self.audit("mcp", "server_removed", {"id": sid}, actor="user")
        return {"ok": True}

    def _rate_ok(self, sid: str, limit: int) -> bool:
        now = time.time()
        lst = [t for t in self._calls.get(sid, []) if now - t < 60]
        if len(lst) >= max(1, limit):
            self._calls[sid] = lst
            return False
        lst.append(now)
        self._calls[sid] = lst
        return True

    def _log_call(self, server: str, tool: str, allowed: bool, reason: str = "") -> None:
        try:
            self.db.execute(
                "INSERT INTO mcp_calls (server, tool, allowed, reason, created_at)"
                " VALUES (?,?,?,?,?)",
                (server, tool, int(allowed), reason[:300], utcnow_iso()),
            )
        except Exception:
            pass

    def call_tool(self, sid: str, tool: str, args: dict | None = None) -> dict:
        servers = {s.get("id"): s for s in (self.config.get("mcp_servers") or [])}
        s = servers.get(sid)
        if not s:
            self._log_call(sid, tool, False, "unknown server")
            return {"ok": False, "error": "unknown MCP server"}
        if not s.get("enabled"):
            self._log_call(sid, tool, False, "server disabled")
            return {"ok": False, "error": "server disabled (registry default is disabled)"}
        if not s.get("url"):
            self._log_call(sid, tool, False, "not configured")
            return {"ok": False, "error": "server NOT_CONFIGURED — no URL registered"}
        # hard refusal: any tool name that smells like trading
        if MCP_TRADE_KEYWORDS.search(tool or ""):
            self._log_call(sid, tool, False, "trading keyword in tool name — HARD REFUSED")
            self.audit("security", "mcp_trading_tool_refused", {"server": sid, "tool": tool})
            return {
                "ok": False,
                "error": f"tool '{tool}' refused: MCP tools can never touch trading, "
                "withdrawals, positions or credentials.",
            }
        if not self._rate_ok(sid, int(s.get("rate_limit_per_min", 10))):
            self._log_call(sid, tool, False, "rate limited")
            return {"ok": False, "error": "rate limit exceeded for this server"}
        try:
            body = {
                "jsonrpc": "2.0",
                "id": pysecrets.token_hex(4),
                "method": "tools/call",
                "params": {"name": tool, "arguments": args or {}},
            }
            r = http_json(
                s["url"],
                timeout=20,
                method="POST",
                data=body,
                headers={"Accept": "application/json"},
                venue=f"mcp:{sid}",
            )
            self.status[sid] = {"status": "OK", "last_call": utcnow_iso(), "last_error": None}
            self._log_call(sid, tool, True)
            return {"ok": True, "result": r}
        except Exception as e:
            self.status[sid] = {
                "status": "ERROR",
                "last_call": utcnow_iso(),
                "last_error": http_error_str(e)[:200],
            }
            self._log_call(sid, tool, False, http_error_str(e)[:200])
            return {"ok": False, "error": http_error_str(e)[:200]}

    def list_tools(self, sid: str) -> dict:
        servers = {s.get("id"): s for s in (self.config.get("mcp_servers") or [])}
        s = servers.get(sid)
        if not s or not s.get("url") or not s.get("enabled"):
            return {"ok": False, "error": "server disabled or NOT_CONFIGURED"}
        try:
            body = {"jsonrpc": "2.0", "id": pysecrets.token_hex(4), "method": "tools/list"}
            r = http_json(
                s["url"],
                timeout=15,
                method="POST",
                data=body,
                headers={"Accept": "application/json"},
                venue=f"mcp:{sid}",
            )
            tools = (r.get("result") or {}).get("tools") or []
            safe = [t for t in tools if not MCP_TRADE_KEYWORDS.search(str(t.get("name", "")))]
            refused = [
                t.get("name") for t in tools if MCP_TRADE_KEYWORDS.search(str(t.get("name", "")))
            ]
            if refused:
                self.audit("security", "mcp_tools_filtered", {"server": sid, "refused": refused})
            return {"ok": True, "tools": safe, "refused_tools": refused}
        except Exception as e:
            return {"ok": False, "error": http_error_str(e)[:200]}

    def recent_calls(self, limit: int = 50) -> list[dict]:
        return self.db.query("SELECT * FROM mcp_calls ORDER BY id DESC LIMIT ?", (limit,))
