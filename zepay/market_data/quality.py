"""Data quality engine (§43 Data Honesty).

Tracks freshness, gaps, malformed messages, sequence resets and venue
outages. Trading gates consult this before acting; every notable degradation
is persisted to `data_quality_events` — the system never silently uses bad
data and never invents good data.
"""

from __future__ import annotations

import logging
import threading
from itertools import pairwise

from zepay.core.events import BUS, E
from zepay.core.util import ts_ms, utcnow_iso

log = logging.getLogger("zepay.market_data.quality")

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


class DataQualityEngine:
    def __init__(self, storage, config):
        self.db = storage
        self.config = config
        self._lock = threading.RLock()
        self._markets: dict[str, dict] = {}  # market -> {status, ts, reason, errors}
        self._venues: dict[str, dict] = {}  # venue -> {status, last_error, ts}
        self._notes: list[str] = []

    # ---- per-market ----
    def mark_ok(self, market: str, ts: int) -> None:
        with self._lock:
            m = self._markets.setdefault(market, {"errors": 0})
            m.update({"status": "OK", "ts": ts, "reason": ""})

    def mark_error(self, market: str, err: str) -> None:
        with self._lock:
            m = self._markets.setdefault(market, {"errors": 0})
            m["errors"] = m.get("errors", 0) + 1
            m["status"] = "DEGRADED" if m["errors"] < 3 else "UNAVAILABLE"
            m["reason"] = err[:200]
            m["ts"] = ts_ms()

    def mark_stale(self, market: str, reason: str) -> None:
        with self._lock:
            m = self._markets.setdefault(market, {"errors": 0})
            m["status"] = "STALE"
            m["reason"] = reason[:200]
            m["ts"] = ts_ms()
        self._event(market, "", "STALE", reason)

    def market_quality(self, market: str) -> dict:
        stale_secs = float(self.config.get("stale_data_secs", 120))
        with self._lock:
            m = dict(
                self._markets.get(
                    market,
                    {
                        "status": "UNAVAILABLE",
                        "reason": "no data received yet",
                        "ts": 0,
                        "errors": 0,
                    },
                )
            )
        age = (ts_ms() - m.get("ts", 0)) / 1000.0 if m.get("ts") else 1e9
        if m["status"] == "OK" and age > stale_secs:
            m["status"] = "STALE"
            m["reason"] = f"data age {age:.0f}s exceeds {stale_secs:.0f}s limit"
        m["age_secs"] = round(age, 1) if age < 1e8 else None
        return m

    # ---- candle gap detection ----
    def check_candle_gaps(self, market: str, interval: str, rows: list[dict]) -> None:
        step = INTERVAL_MS.get(interval)
        if not step or len(rows) < 3:
            return
        gaps = 0
        for a, b in pairwise(rows):
            if b["open_time"] - a["open_time"] != step:
                gaps += 1
        if gaps:
            self._event(
                market, "", "GAP", f"{gaps} candle gap(s) in {interval} series ({len(rows)} rows)"
            )
            with self._lock:
                m = self._markets.setdefault(market, {"errors": 0})
                m["gaps"] = gaps

    # ---- venue / ws ----
    def mark_ws_stale(self, venue: str, streams: list[str]) -> None:
        self._event("", venue, "STALE", f"ws streams stale: {len(streams)}")

    def mark_outage(self, venue: str, err: str) -> None:
        with self._lock:
            self._venues[venue] = {"status": "OUTAGE", "last_error": err[:200], "ts": ts_ms()}
        self._event("", venue, "OUTAGE", err)
        BUS.publish(
            E.DATA_QUALITY, {"venue": venue, "kind": "OUTAGE", "detail": err}, source="quality"
        )

    def mark_malformed(self, venue: str, detail: str) -> None:
        self._event("", venue, "MALFORMED", detail)

    def mark_seq_reset(self, venue: str, symbol: str, event_ts: int, prev_ts: int) -> None:
        self._event(symbol, venue, "SEQ_RESET", f"event time regression {prev_ts} -> {event_ts}")

    def sys_note(self, note: str) -> None:
        with self._lock:
            self._notes.append(f"{utcnow_iso()} {note}")
            del self._notes[:-50]

    def venue_status(self, venue: str) -> dict:
        with self._lock:
            v = dict(self._venues.get(venue, {"status": "OK", "last_error": "", "ts": 0}))
        if v["status"] == "OUTAGE" and ts_ms() - v.get("ts", 0) > 300_000:
            v["status"] = "RECOVERED?"  # will flip back on next real event
        return v

    def _event(self, market: str, venue: str, kind: str, detail: str) -> None:
        try:
            self.db.execute(
                "INSERT INTO data_quality_events (venue, market, kind, detail, stream,"
                " created_at) VALUES (?,?,?,?,?,?)",
                (venue, market, kind, str(detail)[:500], "", utcnow_iso()),
            )
        except Exception as e:
            log.debug("quality event persist failed: %s", e)

    def summary(self) -> dict:
        with self._lock:
            markets = {k: dict(v) for k, v in self._markets.items()}
            venues = {k: dict(v) for k, v in self._venues.items()}
            notes = list(self._notes[-10:])
        counts: dict[str, int] = {}
        for m in markets.values():
            counts[m.get("status", "?")] = counts.get(m.get("status", "?"), 0) + 1
        blocked = counts.get("UNAVAILABLE", 0) + counts.get("STALE", 0)
        overall = "OK" if not blocked else ("DEGRADED" if blocked < len(markets) else "UNAVAILABLE")
        return {
            "overall": overall,
            "markets": markets,
            "venues": venues,
            "status_counts": counts,
            "notes": notes,
            "ts": ts_ms(),
        }

    def recent_events(self, limit: int = 50) -> list[dict]:
        return self.db.query("SELECT * FROM data_quality_events ORDER BY id DESC LIMIT ?", (limit,))
