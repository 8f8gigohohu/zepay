"""ZEPAY license key verification (v2 heritage, ported to v3).

HONESTY CONTRACT (§6 of the platform spec):
  * No ZEPAY cloud verification API specification is bundled with this build.
  * Two adapters:
      - CloudLicenseProvider: real HTTP verification against an operator-
        configured endpoint. Without an endpoint it reports BLOCKED — it NEVER
        fabricates a successful cloud verification.
      - OfflineKeyProvider: local format + checksum validation ONLY, clearly
        labeled as NOT cloud verification everywhere (API/UI/audit).
  * The verified state gates TRADING (engine entries), not read-only access.
  * The key itself is never stored — only its SHA-256 hash (privileged key).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("zepay.security.license")

KEY_RE = re.compile(r"^[A-Z0-9_]{4,}$")


class LicenseResult(dict):
    """Dict-like result: {ok, mode, message, details}."""

    @property
    def ok(self) -> bool:
        return bool(self.get("ok"))


class CloudLicenseProvider:
    """Verifies a ZEPAY key against a real ZEPAY endpoint (operator-configured).

    Expected minimal response contract (documented, tolerant parser):
        {"ok": true/false, "message": "...", "plan": "...", "expires": "..."}
    """

    def __init__(self, timeout: float = 10):
        self.timeout = timeout

    def verify(self, endpoint: str, key: str) -> LicenseResult:
        body = json.dumps({"key": key}).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "ZEPAY/3.1"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read().decode("utf-8", "replace") or "{}")
            return LicenseResult(
                ok=bool(data.get("ok")),
                mode="cloud",
                message=str(data.get("message", "verified by ZEPAY cloud")),
                details=data,
            )
        except urllib.error.HTTPError as e:
            return LicenseResult(
                ok=False,
                mode="cloud",
                message=f"ZEPAY cloud verification rejected the key (HTTP {e.code})",
            )
        except Exception as e:
            return LicenseResult(
                ok=False,
                mode="cloud",
                message=f"ZEPAY cloud endpoint unreachable: {e}",
            )


class OfflineKeyProvider:
    """LOCAL format validation. NOT cloud verification — labeled everywhere.

    Accepted shape: ZEPAY-XXXX-XXXX-… (charset [A-Z0-9_], total ≥ 16 chars).
    When the key has ≥ 4 groups after ZEPAY-, the LAST group must equal the
    first 4 hex chars of sha256 of the preceding part (typo protection).
    """

    mode = "offline"

    def verify(self, key: str) -> LicenseResult:
        k = (key or "").strip().upper()
        if not k:
            return LicenseResult(ok=False, mode="offline", message="Please enter your ZEPAY key.")
        if not k.startswith("ZEPAY-"):
            return LicenseResult(
                ok=False, mode="offline", message="Keys look like ZEPAY-XXXX-XXXX-…"
            )
        groups = [g for g in k.split("-") if g]
        if len(groups) < 3:
            return LicenseResult(
                ok=False, mode="offline", message="Key too short — expected ZEPAY-XXXX-XXXX-…"
            )
        core = groups[1:]
        for g in core:
            if not KEY_RE.fullmatch(g):
                return LicenseResult(
                    ok=False, mode="offline", message="Key contains invalid characters."
                )
        if len(k) < 16:
            return LicenseResult(
                ok=False, mode="offline", message="Key too short (min 16 characters)."
            )
        details = {
            "verification": "offline-format-check-only",
            "note": "This is NOT cloud verification. Configure a ZEPAY cloud endpoint "
            "in Settings for cloud verification.",
        }
        if len(core) >= 4:
            payload = "ZEPAY-" + "-".join(core[:-1])
            want = hashlib.sha256(payload.encode()).hexdigest()[:4].upper()
            if core[-1] != want:
                return LicenseResult(
                    ok=False,
                    mode="offline",
                    message="Key checksum invalid. Re-check for typos.",
                    details=details,
                )
        return LicenseResult(
            ok=True,
            mode="offline",
            message="OFFLINE key format valid (local validation only — no cloud verification performed).",
            details=details,
        )


CLOUD = CloudLicenseProvider()
OFFLINE = OfflineKeyProvider()


def license_status(config: Any) -> dict:
    endpoint = config.get("license_verify_endpoint")
    verified = bool(config.get("license_key_hash"))
    mode = config.get("license_mode")
    return {
        "verified": verified,
        "mode": mode,
        "required": bool(config.get("require_license_key", True)),
        "cloud_endpoint": endpoint,
        "cloud_adapter": (
            "OK"
            if endpoint
            else "BLOCKED — ZEPAY cloud verification API specification not provided; "
            "endpoint unconfigured"
        ),
        "trading_gated": bool(config.get("require_license_key", True)) and not verified,
    }


def license_ok(config: Any) -> tuple[bool, str]:
    """Gate used by the engine before any trading cycle."""
    if not bool(config.get("require_license_key", True)):
        return True, ""
    if bool(config.get("license_key_hash")):
        return True, ""
    return (
        False,
        "ZEPAY key not verified — trading is gated. Enter your key in Settings → License.",
    )


def verify_key(config: Any, key: str, mode: str | None = None) -> LicenseResult:
    """mode: None → auto (cloud when endpoint configured, else offline)."""
    endpoint = config.get("license_verify_endpoint")
    use_cloud = (mode == "cloud") or (mode is None and endpoint)
    if use_cloud:
        if not endpoint:
            return LicenseResult(
                ok=False,
                mode="cloud",
                message="Cloud verification unavailable: no ZEPAY endpoint is configured "
                "(specification missing). Use OFFLINE mode or configure an endpoint.",
            )
        return CLOUD.verify(endpoint, key)
    return OFFLINE.verify(key)


def activate_key(config: Any, audit_fn, key: str, mode: str | None = None) -> LicenseResult:
    res = verify_key(config, key, mode=mode)
    if not res["ok"]:
        try:
            audit_fn("auth", "license_rejected", {"mode": res["mode"], "message": res["message"]})
        except Exception:
            pass
        return res
    h = hashlib.sha256((key or "").strip().encode("utf-8")).hexdigest()
    # privileged keys — written directly under the config lock, never via API
    with config._lock:
        config.cfg["license_key_hash"] = h
        config.cfg["license_mode"] = res["mode"]
        config.save_locked()
    try:
        audit_fn("auth", "license_activated", {"mode": res["mode"], "message": res["message"]})
    except Exception:
        pass
    return res


def clear_key(config: Any, audit_fn) -> None:
    with config._lock:
        config.cfg["license_key_hash"] = None
        config.cfg["license_mode"] = None
        config.save_locked()
    try:
        audit_fn("auth", "license_cleared", {})
    except Exception:
        pass
