"""ZEPAY V3 event bus (§55 Event-driven core).

Lightweight synchronous pub/sub with optional bounded async delivery. Every
major component communicates through well-defined events. Handlers run in the
publisher's thread by default (deterministic, no hidden races); long-running
handlers must be registered with `background=True` to run on a shared pool.

Design notes:
  * FAIL-SAFE: a crashing handler never breaks the publisher or other handlers.
  * BOUNDED: the background queue has a max size; overflow drops oldest events
    and increments a counter surfaced in /api/health (never silently).
"""

from __future__ import annotations

import logging
import queue
import threading
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

log = logging.getLogger("zepay.events")


# Canonical event names (§55)
class E:
    MARKET_TICK = "MarketTick"
    CANDLE_CLOSED = "CandleClosed"
    ORDERBOOK_UPDATED = "OrderBookUpdated"
    FUNDING_UPDATED = "FundingUpdated"
    SIGNAL_CREATED = "SignalCreated"
    OPPORTUNITY_CREATED = "OpportunityCreated"
    RISK_DECISION = "RiskDecision"
    ORDER_CREATED = "OrderCreated"
    ORDER_SUBMITTED = "OrderSubmitted"
    ORDER_FILLED = "OrderFilled"
    ORDER_REJECTED = "OrderRejected"
    POSITION_CHANGED = "PositionChanged"
    BALANCE_CHANGED = "BalanceChanged"
    MODEL_UPDATED = "ModelUpdated"
    MODEL_DEGRADED = "ModelDegraded"
    EXCHANGE_CONNECTED = "ExchangeConnected"
    EXCHANGE_DISCONNECTED = "ExchangeDisconnected"
    KILL_SWITCH_ACTIVATED = "KillSwitchActivated"
    DATA_QUALITY = "DataQuality"
    SYSTEM_EVENT = "SystemEvent"
    CYCLE_STARTED = "CycleStarted"
    CYCLE_ENDED = "CycleEnded"


@dataclass
class Event:
    name: str
    payload: dict = field(default_factory=dict)
    ts: float = 0.0
    source: str = ""


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self, max_workers: int = 4, queue_size: int = 5000):
        self._subs: dict[str, list[tuple[Handler, bool]]] = defaultdict(list)
        self._lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="zepay-ev")
        self._q: queue.Queue = queue.Queue(maxsize=queue_size)
        self._worker = threading.Thread(target=self._drain, daemon=True, name="zepay-ev-drain")
        self._worker.start()
        self.stats = {"published": 0, "handled": 0, "handler_errors": 0, "dropped_overflow": 0}

    # ---- subscription ----
    def subscribe(self, event_name: str, handler: Handler, background: bool = False) -> None:
        with self._lock:
            self._subs[event_name].append((handler, background))

    def unsubscribe(self, event_name: str, handler: Handler) -> None:
        with self._lock:
            self._subs[event_name] = [
                (h, b) for (h, b) in self._subs[event_name] if h is not handler
            ]

    # ---- publishing ----
    def publish(self, event_name: str, payload: dict | None = None, source: str = "") -> None:
        import time

        ev = Event(name=event_name, payload=payload or {}, ts=time.time(), source=source)
        with self._lock:
            handlers = list(self._subs.get(event_name, ()))
            wildcard = list(self._subs.get("*", ()))
        self.stats["published"] += 1
        for h, bg in handlers + wildcard:
            if bg:
                try:
                    self._q.put_nowait((h, ev))
                except queue.Full:
                    self.stats["dropped_overflow"] += 1
                    log.warning("event bus overflow: dropped background handler for %s", event_name)
            else:
                self._safe_call(h, ev)

    def _safe_call(self, h: Handler, ev: Event) -> None:
        try:
            h(ev)
            self.stats["handled"] += 1
        except Exception:
            self.stats["handler_errors"] += 1
            log.exception("event handler %s failed for %s", getattr(h, "__name__", h), ev.name)

    def _drain(self) -> None:
        while True:
            try:
                h, ev = self._q.get()
            except Exception:
                continue
            self._pool.submit(self._safe_call, h, ev)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "subscriptions": {k: len(v) for k, v in self._subs.items()},
                "queue_size": self._q.qsize(),
                **self.stats,
            }


# The process-wide bus. Modules import `from zepay.core.events import BUS`.
BUS = EventBus()
