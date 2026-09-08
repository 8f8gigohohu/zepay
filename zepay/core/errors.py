"""ZEPAY V3 exception hierarchy."""

from __future__ import annotations


class ZepayError(Exception):
    """Base for all platform errors."""


class ConfigError(ZepayError):
    pass


class DataUnavailableError(ZepayError):
    """Real data required but unavailable — callers must BLOCK, never fake."""


class StaleDataError(DataUnavailableError):
    pass


class ExchangeError(ZepayError):
    def __init__(self, message: str, venue: str = "", status: str = "", retryable: bool = False):
        super().__init__(message)
        self.venue = venue
        self.status = status  # OK | DEGRADED | UNREACHABLE | BLOCKED | NOT_CONFIGURED
        self.retryable = retryable


class GeoBlockedError(ExchangeError):
    """HTTP 451 — venue refuses this jurisdiction. Honestly reported, never bypassed."""

    def __init__(self, message: str, venue: str = ""):
        super().__init__(message, venue=venue, status="BLOCKED", retryable=False)


class RateLimitedError(ExchangeError):
    def __init__(self, message: str, venue: str = "", retry_after: float = 1.0):
        super().__init__(message, venue=venue, status="DEGRADED", retryable=True)
        self.retry_after = retry_after


class AuthError(ExchangeError):
    pass


class WithdrawalPermissionError(AuthError):
    """A credential with withdrawal permission was offered — ALWAYS refused (§50-51)."""


class OrderStateError(ZepayError):
    """Illegal order state transition."""


class UnknownOrderStateError(OrderStateError):
    """Order outcome UNKNOWN — reconcile before any resubmission (§33)."""


class RiskError(ZepayError):
    """Risk engine refusal. Fail closed: callers must not proceed."""


class KillSwitchActiveError(RiskError):
    pass


class StorageError(ZepayError):
    pass


class ReconciliationError(ZepayError):
    pass


class ModelError(ZepayError):
    pass


class UntrainedModelError(ModelError):
    """Strict mode: models not trained on real data → no fabricated signals."""
