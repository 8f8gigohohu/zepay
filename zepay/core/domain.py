"""ZEPAY V3 core domain model (§10 Universal Market Model).

ONE engine trades every market. Nothing here is asset-specific: instruments
carry their own precision, filters, fees and contract rules, discovered from
the venue. Adding a coin must never require code changes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Venue(str, Enum):
    ZEPAY = "zepay"  # ZEPAY INR venue (adapter reports NOT_CONFIGURED until spec exists)
    BINANCE_SPOT = "binance_spot"
    BINANCE_FUTURES = "binance_futures"
    SOLANA = "solana"  # Phantom wallet + DEX data (Jupiter); signing always external
    PAPER = "paper"  # internal paper exchange (real prices, simulated fills)


class TradingMode(str, Enum):
    SPOT = "SPOT"
    FUTURES = "FUTURES"
    DEX = "DEX"


class ContractType(str, Enum):
    SPOT = "SPOT"
    PERPETUAL = "PERPETUAL"
    DELIVERY = "DELIVERY"


class OperationalStage(str, Enum):
    """§22 — four operational stages. Promotion is ALWAYS manual."""

    PAPER = "PAPER"  # real data, simulated orders
    SHADOW = "SHADOW"  # real data, hypothetical live decisions, no execution
    LIMITED_LIVE = "LIMITED_LIVE"  # heavily restricted capital/risk
    FULL_LIVE = "FULL_LIVE"  # only after explicit approval


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"


class Decision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    WAIT = "WAIT"
    HALTED = "HALTED"


class Health(str, Enum):
    TRADEABLE = "TRADEABLE"
    LIMITED = "LIMITED"
    BLOCKED = "BLOCKED"


class SystemState(str, Enum):
    NORMAL = "NORMAL"
    CAUTION = "CAUTION"
    DEFENSIVE = "DEFENSIVE"
    HALTED = "HALTED"


class ProviderStatus(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    UNREACHABLE = "UNREACHABLE"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    BLOCKED = "BLOCKED"  # e.g. geo-restriction (HTTP 451) or forbidden permission


class StreamState(str, Enum):
    """§12 — every WebSocket stream carries an explicit state."""

    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    RECOVERING = "RECOVERING"
    FAILED = "FAILED"


class OrderStatus(str, Enum):
    """§33 — order state machine. UNKNOWN is dangerous and never resubmitted blindly."""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"  # exchange accepted (NEW)
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    TAKE_PROFIT = "TAKE_PROFIT"


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    PO = "PO"  # post-only (maker) where supported


class ModelLifecycle(str, Enum):
    """§26 — model registry lifecycle. Promotion requires explicit approval."""

    RESEARCH = "RESEARCH"
    VALIDATION = "VALIDATION"
    CANDIDATE = "CANDIDATE"
    PAPER = "PAPER"
    SHADOW = "SHADOW"
    APPROVED = "APPROVED"
    LIMITED = "LIMITED"
    PRODUCTION = "PRODUCTION"
    DEGRADED = "DEGRADED"
    RETIRED = "RETIRED"


# --------------------------------------------------------------------------
# Instrument / market model (§10)
# --------------------------------------------------------------------------


@dataclass
class Instrument:
    """A tradable market on a venue. Everything the engine needs to trade it
    without hardcoded per-asset logic."""

    instrument_id: str  # "binance_spot:BTC/USDT"
    venue: str  # Venue value
    symbol: str  # canonical "BTC/USDT"
    exchange_symbol: str  # venue-native "BTCUSDT"
    base_asset: str
    quote_asset: str
    trading_mode: str = TradingMode.SPOT.value
    contract_type: str = ContractType.SPOT.value
    status: str = "TRADING"  # venue-reported status
    tick_size: float | None = None
    step_size: float | None = None
    min_qty: float | None = None
    min_notional: float | None = None
    price_precision: int | None = None
    qty_precision: int | None = None
    maker_fee_bps: float | None = None
    taker_fee_bps: float | None = None
    max_leverage: float | None = None
    margin_mode: str | None = None  # ISOLATED / CROSSED for futures
    metadata: dict = field(default_factory=dict)
    discovered_at: str = ""
    source: str = ""  # exchange_metadata | cached_metadata | default_fallback

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def market(self) -> str:
        return self.symbol


@dataclass
class Candle:
    open_time: int  # UTC ms
    o: float
    h: float
    l: float
    c: float
    v: float
    quote_vol: float = 0.0
    trades: int = 0
    closed: bool = True


@dataclass
class Ticker:
    venue: str
    symbol: str
    price: float
    change24: float = 0.0
    volume24: float = 0.0
    quote_vol: float = 0.0
    high: float = 0.0
    low: float = 0.0
    ts: int = 0  # local receive time (UTC ms)
    exchange_ts: int = 0  # exchange event time (UTC ms) when available
    source: str = ""  # e.g. binance-rest | binance-ws
    quality: str = "OK"  # OK | STALE | UNAVAILABLE


@dataclass
class OrderBook:
    venue: str
    symbol: str
    bids: list  # [[price, qty], ...] sorted best-first
    asks: list
    ts: int = 0
    source: str = ""
    last_update_id: int | None = None


@dataclass
class FundingInfo:
    symbol: str
    funding_rate: float | None = None
    mark_price: float | None = None
    index_price: float | None = None
    open_interest: float | None = None
    next_funding_time: int | None = None
    available: bool = False
    source: str = ""
    ts: int = 0


@dataclass
class StrategySignal:
    """§30 — every strategy emits the same shape."""

    strategy: str
    symbol: str
    venue: str
    direction: str  # Direction value
    confidence: float  # 0..1
    entry: float = 0.0
    stop: float = 0.0
    take_profit: float = 0.0
    expected_return: float = 0.0
    expected_vol: float = 0.0
    rationale: list = field(default_factory=list)
    required_data: list = field(default_factory=list)
    ts: str = ""
    model_version: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Opportunity:
    symbol: str
    venue: str
    direction: str
    score: float
    confidence: float
    quality: float
    regime: str
    strategy: str
    expected_return: float
    expected_net: float
    cost_pct: float
    price: float
    reasons: list = field(default_factory=list)
    machine: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskDecision:
    decision_id: str
    symbol: str
    decision: str  # Decision value
    reasons: list = field(default_factory=list)
    system_state: str = SystemState.NORMAL.value
    sizing_hint: dict | None = None
    ts: str = ""


@dataclass
class OrderIntent:
    """Execution request handed to the ExecutionEngine."""

    symbol: str
    venue: str
    direction: str  # LONG/SHORT (entry) — exit intents use reduce_only
    qty: float
    order_type: str = OrderType.MARKET.value
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = TimeInForce.GTC.value
    reduce_only: bool = False
    post_only: bool = False
    strategy: str = ""
    decision_id: str | None = None
    risk_decision_id: str | None = None
    model_id: str | None = None
    client_order_id: str | None = None
    idempotency_key: str | None = None


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: str  # BUY / SELL
    qty: float
    price: float
    fee: float = 0.0
    fee_asset: str = ""
    slippage_bps: float = 0.0
    pnl: float = 0.0
    maker: bool = False
    ts: str = ""


def to_dict(obj: Any) -> Any:
    """dataclass/dict passthrough for JSON layers."""
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if isinstance(obj, dict):
        return obj
    return asdict(obj) if hasattr(obj, "__dataclass_fields__") else obj
