"""Structured logging (§56). Console + rotating file. Secrets never reach
logs: the redaction filter is applied to every record."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys

from zepay.core.util import redact, utcnow_iso

_CONFIGURED = False


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str) and (
                "secret" in record.msg.lower() or "key=" in record.msg
            ):
                record.msg = redact(record.msg)
            if record.args:
                record.args = tuple(
                    redact(a) if isinstance(a, (dict, list, str)) else a
                    for a in (record.args if isinstance(record.args, tuple) else (record.args,))
                )
        except Exception:
            pass
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": utcnow_iso(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str)


def setup_logging(level: str = "INFO", log_file: str | None = None, as_json: bool = False) -> None:
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED:
        root.setLevel(level)
        return
    root.setLevel(level)
    fmt = (
        JsonFormatter()
        if as_json
        else logging.Formatter("%(asctime)s %(levelname)-7s %(name)-22s %(message)s", "%H:%M:%S")
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.addFilter(RedactionFilter())
    root.addHandler(console)
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=8_000_000, backupCount=3)
        fh.setFormatter(fmt)
        fh.addFilter(RedactionFilter())
        root.addHandler(fh)
    # quiet noisy libraries
    for noisy in ("websockets", "websockets.client", "asyncio", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True
