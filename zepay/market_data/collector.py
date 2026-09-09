"""Market data collector (§11: Exchange → Collector → Normalizer → Bus).

Multi-venue, thread-pooled REST collection with normalized records. Every
cached record carries venue/symbol/timestamp/source/type/quality so nothing
downstream has to guess provenance. REST is the source of truth; the WS
manager overlays real-time updates on the same normalized cache.

DATA HONESTY: if a fetch fails the previous value is NEVER silently reused —
the record is marked stale and `is_stale()` gates trading. The only reuse
path is explicitly labeled (`stale_db=True` candles from the local store when
the live API is unreachable).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from zepay.core.events import BUS, E
from zepay.core.util import jdump, safe_float, ts_ms, utcnow_iso

log = logging.getLogger("zepay.market_data.collector")


class MarketDataCollector:
    def __init__(self, registry, universe, config, storage, quality, max_workers: int = 8):
        self.registry = registry
        self.universe = universe
        self.config = config
        self.db = storage
        self.quality = quality
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="zepay-md")
        self.cache: dict[str, dict] = {}  # market -> normalized ticker
        self.klines_cache: dict[tuple, dict] = {}  # (market, interval) -> {rows, ts}
        self.depth_cache: dict[str, dict] = {}
        self.derivs_cache: dict[str, dict] = {}
        self.errors: dict[str, dict] = {}
        self.latency_ms: dict[str, int] = {}
        self._lock = threading.RLock()
        self._last_book_snapshot = 0.0

    # ---- normalization (§11) ----
    @staticmethod
    def norm_ticker(venue: str, market: str, d: dict, source: str) -> dict:
        return {
            "venue": venue,
            "exchange": venue,
            "symbol": market,
            "timestamp": ts_ms(),
            "source": source,
            "data_type": "ticker",
            "quality": "OK",
            "price": safe_float(d.get("lastPrice")),
            "change24": safe_float(d.get("priceChangePercent")),
            "volume24": safe_float(d.get("volume")),
            "quote_vol": safe_float(d.get("quoteVolume")),
            "high": safe_float(d.get("highPrice")),
            "low": safe_float(d.get("lowPrice")),
            "exchange_ts": int(safe_float(d.get("closeTime"), 0)),
            "ts": ts_ms(),
        }

    def _record_error(self, market: str, e: Exception) -> None:
        with self._lock:
            er = self.errors.get(market, {"count": 0})
            er.update(
                {
                    "msg": f"{type(e).__name__}: {e}"[:200],
                    "ts": ts_ms(),
                    "count": er.get("count", 0) + 1,
                }
            )
            self.errors[market] = er

    # ---- fetchers (venue resolved PER INSTRUMENT; global config is fallback) ----
    def _venue_for(self, market: str) -> tuple[str, Any]:
        vid = None
        try:
            inst = self.universe.get(market)
            if inst is not None:
                vid = inst.venue
        except Exception:
            vid = None
        if not vid:
            vid = self.config.get("market_data_venue") or self.universe.primary
        adapter = self.registry.get(vid)
        return vid, adapter

    def fetch_ticker(self, market: str) -> dict:
        vid, adapter = self._venue_for(market)
        if adapter is None:
            raise RuntimeError(f"venue {vid} not registered")
        sym = self.universe.exchange_symbol(market)
        t0 = time.time()
        try:
            d = adapter.ticker_24h(sym)
            price = safe_float(d.get("lastPrice"))
            if price <= 0:
                raise ValueError("bad price in ticker response")
            out = self.norm_ticker(vid, market, d, f"{vid}-rest")
            with self._lock:
                self.cache[market] = out
                self.latency_ms[market] = int((time.time() - t0) * 1000)
                self.errors.pop(market, None)
            self.quality.mark_ok(market, out["ts"])
            BUS.publish(
                E.MARKET_TICK,
                {
                    "market": market,
                    "venue": vid,
                    "price": price,
                    "ts": out["ts"],
                    "source": out["source"],
                },
                source="collector",
            )
            return out
        except Exception as e:
            self._record_error(market, e)
            self.quality.mark_error(market, str(e)[:200])
            raise

    def fetch_klines(
        self, market: str, interval: str | None = None, limit: int | None = None
    ) -> list[dict]:
        interval = interval or self.config.get("candle_interval", "1h")
        limit = int(limit or self.config.get("candle_limit", 200))
        vid, adapter = self._venue_for(market)
        sym = self.universe.exchange_symbol(market)
        try:
            d = adapter.klines(sym, interval, limit)
            rows = [
                {
                    "open_time": int(k[0]),
                    "o": safe_float(k[1]),
                    "h": safe_float(k[2]),
                    "l": safe_float(k[3]),
                    "c": safe_float(k[4]),
                    "v": safe_float(k[5]),
                    "quote_vol": safe_float(k[7]),
                    "trades": int(safe_float(k[8])),
                }
                for k in d
            ]
            if not rows:
                raise ValueError("empty klines")
            self.quality.check_candle_gaps(market, interval, rows)
            with self._lock:
                prev = self.klines_cache.get((market, interval))
                self.klines_cache[(market, interval)] = {"rows": rows, "ts": ts_ms()}
                if (
                    prev
                    and prev["rows"]
                    and rows
                    and prev["rows"][-1]["open_time"] != rows[-1]["open_time"]
                ):
                    BUS.publish(
                        E.CANDLE_CLOSED,
                        {"market": market, "interval": interval, "candle": prev["rows"][-1]},
                        source="collector",
                    )
            self._persist_candles(vid, market, interval, rows)
            return rows
        except Exception as e:
            self._record_error(market, e)
            self.quality.mark_error(market, str(e)[:200])
            # last resort: previously persisted REAL candles — clearly marked
            q = self.db.query(
                "SELECT open_time,o,h,l,c,v,quote_vol,trades FROM candles"
                " WHERE market=? AND interval_tf=? ORDER BY open_time DESC LIMIT ?",
                (market, interval, limit),
            )
            if q:
                rows = [
                    {
                        "open_time": r["open_time"],
                        "o": r["o"],
                        "h": r["h"],
                        "l": r["l"],
                        "c": r["c"],
                        "v": r["v"],
                        "quote_vol": r["quote_vol"] or 0,
                        "trades": r["trades"] or 0,
                    }
                    for r in reversed(q)
                ]
                with self._lock:
                    self.klines_cache[(market, interval)] = {
                        "rows": rows,
                        "ts": ts_ms(),
                        "stale_db": True,
                    }
                self.quality.mark_stale(market, "candles served from local DB (API down)")
                return rows
            raise

    def _persist_candles(self, venue: str, market: str, interval: str, rows: list[dict]) -> None:
        try:
            self.db.executemany(
                "INSERT OR IGNORE INTO candles (venue, market, interval_tf, open_time,"
                " o,h,l,c,v,quote_vol,trades,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        venue,
                        market,
                        interval,
                        r["open_time"],
                        r["o"],
                        r["h"],
                        r["l"],
                        r["c"],
                        r["v"],
                        r["quote_vol"],
                        r["trades"],
                        utcnow_iso(),
                    )
                    for r in rows
                ],
            )
        except Exception as e:
            log.debug("candle persist failed: %s", e)

    def fetch_depth(self, market: str, limit: int = 20) -> dict:
        vid, adapter = self._venue_for(market)
        sym = self.universe.exchange_symbol(market)
        d = adapter.depth(sym, limit)
        bids = [[safe_float(p), safe_float(q)] for p, q in d.get("bids", [])]
        asks = [[safe_float(p), safe_float(q)] for p, q in d.get("asks", [])]
        if not bids or not asks:
            raise ValueError("empty book")
        out = {
            "venue": vid,
            "exchange": vid,
            "symbol": market,
            "timestamp": ts_ms(),
            "source": f"{vid}-rest",
            "data_type": "depth",
            "quality": "OK",
            "bids": bids,
            "asks": asks,
            "ts": ts_ms(),
            "last_update_id": d.get("lastUpdateId"),
        }
        with self._lock:
            self.depth_cache[market] = out
        BUS.publish(
            E.ORDERBOOK_UPDATED,
            {
                "market": market,
                "venue": vid,
                "ts": out["ts"],
                "best_bid": bids[0][0],
                "best_ask": asks[0][0],
            },
            source="collector",
        )
        self._maybe_snapshot_book(market, out)
        return out

    def _maybe_snapshot_book(self, market: str, book: dict, interval_s: float = 60.0):
        now = time.time()
        if now - self._last_book_snapshot < interval_s:
            return
        self._last_book_snapshot = now
        try:
            bids, asks = book["bids"], book["asks"]
            spread_bps = (
                (asks[0][0] - bids[0][0]) / ((asks[0][0] + bids[0][0]) / 2) * 1e4
                if bids and asks
                else 0
            )
            bv = sum(p * q for p, q in bids)
            av = sum(p * q for p, q in asks)
            self.db.execute(
                "INSERT INTO orderbook_snapshots (venue, market, ts, bids, asks, spread_bps,"
                " imbalance, depth_usd) VALUES (?,?,?,?,?,?,?,?)",
                (
                    book["venue"],
                    market,
                    book["ts"],
                    jdump(bids[:10]),
                    jdump(asks[:10]),
                    round(spread_bps, 3),
                    round((bv - av) / (bv + av), 4) if bv + av else 0,
                    round(bv + av, 2),
                ),
            )
        except Exception as e:
            log.debug("book snapshot failed: %s", e)

    def fetch_derivs(self, market: str) -> dict:
        """Funding/mark/OI context from the futures venue when reachable.
        BLOCKED (e.g. HTTP 451) → available=False, shown as UNAVAILABLE."""
        fad = self.registry.get("binance_futures")
        sym = self.universe.exchange_symbol(market)
        out = {
            "symbol": market,
            "funding_rate": None,
            "mark_price": None,
            "index_price": None,
            "open_interest": None,
            "available": False,
            "source": "binance_futures",
            "ts": ts_ms(),
        }
        if fad is None or not (self.config.get("derivs_enabled")):
            out["reason"] = "derivatives context disabled"
            with self._lock:
                self.derivs_cache[market] = out
            return out
        try:
            info = fad.funding_info(sym)
            out.update(
                {
                    k: info.get(k)
                    for k in (
                        "funding_rate",
                        "mark_price",
                        "index_price",
                        "open_interest",
                        "next_funding_time",
                        "available",
                    )
                }
            )
            if not info.get("available"):
                out["reason"] = info.get("error", "derivatives API unavailable")
        except Exception as e:
            out["reason"] = f"{type(e).__name__}: {e}"[:200]
        with self._lock:
            self.derivs_cache[market] = out
        if out["available"]:
            BUS.publish(E.FUNDING_UPDATED, {"market": market, **out}, source="collector")
        return out

    # ---- batch refresh ----
    def refresh_all(
        self, markets: list[str], with_klines: bool = True, with_depth: bool = True
    ) -> dict:
        futs = {}
        for m in markets:
            futs[self.pool.submit(self._safe, self.fetch_ticker, m)] = ("ticker", m)
            if with_klines:
                futs[self.pool.submit(self._safe, self.fetch_klines, m)] = ("klines", m)
            if with_depth:
                futs[self.pool.submit(self._safe, self.fetch_depth, m)] = ("depth", m)
        results = {}
        for f in futs:
            kind, m = futs[f]
            try:
                results[(kind, m)] = f.result(timeout=45)
            except Exception as e:
                results[(kind, m)] = e
        return results

    @staticmethod
    def _safe(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            return e

    # ---- accessors ----
    def get_ticker(self, market: str) -> dict | None:
        with self._lock:
            return self.cache.get(market)

    def get_klines(self, market: str, interval: str | None = None) -> list[dict] | None:
        interval = interval or self.config.get("candle_interval", "1h")
        with self._lock:
            e = self.klines_cache.get((market, interval))
            return e["rows"] if e else None

    def klines_stale_db(self, market: str, interval: str | None = None) -> bool:
        interval = interval or self.config.get("candle_interval", "1h")
        with self._lock:
            e = self.klines_cache.get((market, interval))
            return bool(e and e.get("stale_db"))

    def get_depth(self, market: str) -> dict | None:
        with self._lock:
            return self.depth_cache.get(market)

    def get_derivs(self, market: str) -> dict | None:
        with self._lock:
            return self.derivs_cache.get(market)

    def age_secs(self, market: str) -> float:
        with self._lock:
            t = self.cache.get(market)
            if not t:
                return 1e9
            return (ts_ms() - t["ts"]) / 1000.0

    def is_stale(self, market: str) -> bool:
        return self.age_secs(market) > safe_float(self.config.get("stale_data_secs", 120), 120)

    def feed_health(self, markets: list[str] | None = None) -> dict:
        """Global feed status: LIVE / DEGRADED / UNAVAILABLE (v2 semantics)."""
        markets = markets or (self.config.get("universe") or [])
        if not markets:
            return {"status": "UNAVAILABLE", "reason": "no markets selected"}
        fresh = [m for m in markets if not self.is_stale(m)]
        if not fresh:
            return {
                "status": "UNAVAILABLE",
                "reason": "REAL DATA UNAVAILABLE — all market feeds stale/unreachable. "
                "New trades are blocked until real data returns.",
            }
        if len(fresh) < len(markets):
            return {
                "status": "DEGRADED",
                "reason": f"{len(markets) - len(fresh)}/{len(markets)} feeds stale",
            }
        return {"status": "LIVE", "reason": "real-time market data"}

    # ---- WS overlay (called by the WebSocket manager) ----
    def ws_update_ticker(self, venue: str, exchange_symbol: str, data: dict) -> None:
        """miniTicker overlay: real-time price/volume between REST refreshes."""
        if not exchange_symbol.endswith("USDT"):
            return
        market = f"{exchange_symbol[:-4]}/USDT"
        with self._lock:
            prev = self.cache.get(market) or {}
            self.cache[market] = {
                "venue": venue,
                "exchange": venue,
                "symbol": market,
                "timestamp": ts_ms(),
                "source": f"{venue}-ws",
                "data_type": "ticker",
                "quality": "OK",
                "price": safe_float(data.get("c")) or prev.get("price", 0),
                "change24": prev.get("change24", 0),
                "volume24": safe_float(data.get("v")) or prev.get("volume24", 0),
                "quote_vol": safe_float(data.get("q")) or prev.get("quote_vol", 0),
                "high": safe_float(data.get("h")) or prev.get("high", 0),
                "low": safe_float(data.get("l")) or prev.get("low", 0),
                "exchange_ts": int(safe_float(data.get("E"), 0)),
                "ts": ts_ms(),
            }
            price = self.cache[market]["price"]
        if price > 0:
            self.quality.mark_ok(market, ts_ms())
            BUS.publish(
                E.MARKET_TICK,
                {
                    "market": market,
                    "venue": venue,
                    "price": price,
                    "ts": ts_ms(),
                    "source": f"{venue}-ws",
                },
                source="ws",
            )
