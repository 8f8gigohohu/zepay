"""Audit log, system events and alerts (ported from v2, dependency-injected).

Append-only decision/audit trail: who did what, when, with which data.
Secrets are redacted before persistence — guaranteed by util.redact.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

from zepay.core.util import redact, utcnow_iso

log = logging.getLogger("zepay.audit")


class AuditLog:
    def __init__(self, storage):
        self.db = storage

    def audit(self, category: str, action: str, details: Any = "", actor: str = "system") -> None:
        try:
            if not isinstance(details, str):
                details = json.dumps(redact(details), default=str)[:4000]
            else:
                details = str(redact(details))[:4000]
            self.db.execute(
                "INSERT INTO audit_logs (category, action, details, actor, created_at)"
                " VALUES (?,?,?,?,?)",
                (category, action, details, actor, utcnow_iso()),
            )
        except Exception as e:
            log.warning("audit write failed: %s", e)

    def sys_event(self, component: str, level: str, message: str) -> None:
        try:
            self.db.execute(
                "INSERT INTO system_events (component, level, message, created_at)"
                " VALUES (?,?,?,?)",
                (component, level, str(redact(message))[:1000], utcnow_iso()),
            )
        except Exception as e:
            log.warning("sys_event write failed: %s", e)
        if level in ("ERROR", "CRITICAL"):
            log.error("[%s] %s", component, message)

    def recent_audit(self, limit: int = 100) -> list[dict]:
        return self.db.query("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))

    def recent_events(self, limit: int = 100) -> list[dict]:
        return self.db.query("SELECT * FROM system_events ORDER BY id DESC LIMIT ?", (limit,))


class Alerts:
    """Level-tagged alerts persisted + optionally delivered to a webhook.
    Delivery failures never affect trading."""

    def __init__(self, storage, config, audit: AuditLog):
        self.db = storage
        self.config = config
        self.audit = audit
        self._lock = threading.RLock()

    def notify(self, level: str, title: str, body: str) -> None:
        try:
            self.db.execute(
                "INSERT INTO alerts (level, title, body, delivered, created_at) VALUES (?,?,?,?,?)",
                (level, title, str(body)[:2000], 0, utcnow_iso()),
            )
        except Exception:
            pass
        log.log(
            {"CRITICAL": logging.CRITICAL, "WARN": logging.WARNING}.get(level, logging.INFO),
            "ALERT[%s] %s — %s",
            level,
            title,
            body,
        )
        if not self.config.get("notifications"):
            return
        url = self.config.get("webhook_url")
        if not url:
            return
        threading.Thread(target=self._deliver, args=(url, level, title, body), daemon=True).start()

    @staticmethod
    def _deliver(url: str, level: str, title: str, body: str) -> None:
        import urllib.request

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps({"level": level, "title": title, "body": body}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=8)
        except Exception as e:
            log.warning("alert webhook delivery failed: %s", e)

    def recent(self, limit: int = 50) -> list[dict]:
        return self.db.query("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))
