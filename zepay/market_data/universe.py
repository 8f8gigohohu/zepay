"""Dynamic multi-venue universe (§8: new assets discovered dynamically;
adding a coin never requires code changes).

Sources, in priority order:
  1. live venue metadata (exchangeInfo)      → source=exchange_metadata
  2. previously persisted instruments (DB)   → source=cached_metadata
  3. small built-in fallback list, CLEARLY
     labeled FALLBACK (never presented as a
     live catalogue)                          → source=default_fallback

Fix over v2: the fallback path is guaranteed to contain the default
instruments (the v2 offline-fallback test failure came from an empty cache
path bypassing the default list).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from zepay.core.domain import ContractType, Instrument, TradingMode, Venue
from zepay.core.util import safe_float, utcnow_iso

log = logging.getLogger("zepay.market_data.universe")

DEFAULT_INSTRUMENTS = [
    ("BTC", "USDT"),
    ("ETH", "USDT"),
    ("SOL", "USDT"),
    ("BNB", "USDT"),
    ("XRP", "USDT"),
    ("DOGE", "USDT"),
    ("TON", "USDT"),
    ("ADA", "USDT"),
    ("AVAX", "USDT"),
    ("LINK", "USDT"),
]


def _fallback_instruments(venue_id: str) -> dict[str, Instrument]:
    out = {}
    for base, quote in DEFAULT_INSTRUMENTS:
        inst = Instrument(
            instrument_id=f"{venue_id}:{base}/{quote}",
            venue=venue_id,
            symbol=f"{base}/{quote}",
            exchange_symbol=f"{base}{quote}",
            base_asset=base,
            quote_asset=quote,
            status="FALLBACK",
            source="default_fallback",
            discovered_at=utcnow_iso(),
        )
        out[inst.symbol] = inst
    return out


class UniverseManager:
    """Instrument catalogue keyed by canonical symbol (e.g. 'BTC/USDT').

    V3.1 multi-venue: instruments are discovered from EVERY enabled data venue
    (binance_spot always; binance_futures when futures scanning is on — public
    metadata, trading still gated). The ACTIVE instrument per symbol is chosen
    by: `market_venue_overrides[symbol]` → `default_trading_venue` config →
    primary venue. A futures instrument carries trading_mode=PERPETUAL and the
    venue's real filters; nothing is hard-coded.
    """

    def __init__(self, storage, registry, config, primary_venue: str = Venue.BINANCE_SPOT.value):
        self.db = storage
        self.registry = registry
        self.config = config
        self.primary = primary_venue
        self.catalog: dict[str, Instrument] = {}  # ACTIVE instrument per symbol
        self.by_venue: dict[str, dict[str, Instrument]] = {}  # venue → symbol → instrument
        self.source = "uninitialized"
        self.last_refresh: str | None = None
        self.last_error: str | None = None
        self._lock = threading.RLock()

    # ---- discovery ----
    def refresh(self, quote_filter: str = "USDT", force: bool = False) -> int:
        venues: list[str] = [self.primary]
        if self._futures_scan_enabled():
            venues.append(Venue.BINANCE_FUTURES.value)
        collected: dict[str, dict[str, Instrument]] = {}
        errors: list[str] = []
        for vid in venues:
            try:
                collected[vid] = self._scan_venue(vid, quote_filter)
            except Exception as e:
                errors.append(f"{vid}: {type(e).__name__}: {e}"[:160])
                log.warning("universe scan failed for venue %s: %s", vid, e)
        if collected.get(self.primary):
            with self._lock:
                self.by_venue = {k: v for k, v in collected.items() if v}
                self._reselect_active_locked()
                self.source = "exchange_metadata"
                self.last_refresh = utcnow_iso()
                self.last_error = ("; ".join(errors) or None) if errors else None
            self._persist_all()
            return len(self.catalog)
        # primary venue unreachable → cached/default fallback (labeled)
        self.last_error = "; ".join(errors) or self.last_error
        log.warning("universe refresh failed (%s) — falling back", self.last_error)
        self._load_db_fallback()
        return len(self.catalog)

    def _futures_scan_enabled(self) -> bool:
        flags = self.config.get("venues_enabled") or {}
        return bool(flags.get("binance_futures")) or bool(
            self.config.get("futures_universe_scan", True)
        )

    def _scan_venue(self, vid: str, quote_filter: str) -> dict[str, Instrument]:
        """Scan one venue's exchangeInfo into Instruments (REAL metadata only)."""
        adapter = self.registry.get(vid)
        if adapter is None:
            raise RuntimeError(f"venue {vid} not registered")
        info = adapter.exchange_info()
        is_futures = vid == Venue.BINANCE_FUTURES.value
        syms: dict[str, Instrument] = {}
        for s in info.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            if is_futures:
                # USDT-M perpetuals only (no deliveries, no quarterly contracts)
                if s.get("contractType") not in ("PERPETUAL",):
                    continue
                if s.get("quoteAsset") != quote_filter:
                    continue
                base = s.get("baseAsset", "")
            else:
                if s.get("quoteAsset") != quote_filter:
                    continue
                base = s.get("baseAsset", "")
            flt: dict[str, Any] = {
                "tick_size": None,
                "step_size": None,
                "min_qty": None,
                "min_notional": None,
            }
            for f in s.get("filters", []):
                ft = f.get("filterType")
                if ft == "PRICE_FILTER":
                    flt["tick_size"] = safe_float(f.get("tickSize"))
                elif ft == "LOT_SIZE":
                    flt["step_size"] = safe_float(f.get("stepSize"))
                    flt["min_qty"] = safe_float(f.get("minQty"))
                elif ft in ("NOTIONAL", "MIN_NOTIONAL"):
                    flt["min_notional"] = safe_float(f.get("minNotional") or f.get("notional"))
            inst = Instrument(
                instrument_id=f"{vid}:{base}/{quote_filter}",
                venue=vid,
                symbol=f"{base}/{quote_filter}",
                exchange_symbol=s["symbol"],
                base_asset=base,
                quote_asset=quote_filter,
                trading_mode=(TradingMode.FUTURES.value if is_futures else TradingMode.SPOT.value),
                contract_type=(
                    ContractType.PERPETUAL.value if is_futures else ContractType.SPOT.value
                ),
                status="TRADING",
                margin_mode=(
                    self.config.get("futures_margin_mode", "ISOLATED") if is_futures else None
                ),
                source="exchange_metadata",
                discovered_at=utcnow_iso(),
                **flt,
            )
            syms[inst.symbol] = inst
        if not syms:
            raise ValueError(f"exchangeInfo returned no TRADING symbols for {vid}")
        # 24h volumes (single call) — best effort, never fatal
        try:
            tickers = adapter.ticker_24h()
            vol = {t.get("symbol"): safe_float(t.get("quoteVolume")) for t in tickers}
            for inst in syms.values():
                inst.metadata["volume24h_usd"] = vol.get(inst.exchange_symbol, 0.0)
        except Exception as e:
            log.warning("volume refresh failed for %s: %s", vid, e)
        # futures: real max leverage when credentials allow the signed call
        if is_futures and getattr(adapter, "has_credentials", lambda: False)():
            try:
                for inst in list(syms.values())[:120]:
                    lev = adapter.max_leverage(inst.exchange_symbol)
                    if lev:
                        inst.max_leverage = lev
            except Exception as e:
                log.warning("leverage bracket refresh failed: %s", e)
        return syms

    def _reselect_active_locked(self) -> None:
        """Choose the ACTIVE instrument per symbol (override → default venue)."""
        overrides = self.config.get("market_venue_overrides") or {}
        default_venue = self.config.get("default_trading_venue") or self.primary
        catalog: dict[str, Instrument] = {}
        for _vid, insts in self.by_venue.items():
            for symbol, inst in insts.items():
                catalog.setdefault(symbol, inst)
        for symbol in list(catalog.keys()):
            want = overrides.get(symbol) or default_venue
            if want and want in self.by_venue and symbol in self.by_venue[want]:
                catalog[symbol] = self.by_venue[want][symbol]
        self.catalog = catalog

    def set_market_venue(self, symbol: str, venue: str | None) -> bool:
        """Route one market to a venue (None → clear override). Applies live."""
        overrides = dict(self.config.get("market_venue_overrides") or {})
        if venue is None:
            overrides.pop(symbol, None)
        else:
            if venue not in self.by_venue:
                return False
            if symbol not in self.by_venue[venue]:
                return False
            overrides[symbol] = venue
        self.config.update({"market_venue_overrides": overrides})
        with self._lock:
            self._reselect_active_locked()
        return True

    def venue_of(self, market: str) -> str:
        inst = self.get(market)
        return inst.venue if inst else (self.config.get("market_data_venue") or self.primary)

    def _persist(self, syms: dict[str, Instrument]) -> None:
        try:
            import json

            rows = []
            for inst in syms.values():
                rows.append(
                    (
                        inst.instrument_id,
                        inst.venue,
                        inst.symbol,
                        inst.exchange_symbol,
                        inst.base_asset,
                        inst.quote_asset,
                        inst.trading_mode,
                        inst.contract_type,
                        inst.status,
                        inst.tick_size,
                        inst.step_size,
                        inst.min_qty,
                        inst.min_notional,
                        inst.price_precision,
                        inst.qty_precision,
                        inst.maker_fee_bps,
                        inst.taker_fee_bps,
                        inst.max_leverage,
                        inst.margin_mode,
                        json.dumps(inst.metadata, default=str),
                        inst.discovered_at,
                        inst.source,
                        utcnow_iso(),
                    )
                )
            self.db.executemany(
                "INSERT INTO instruments (instrument_id, venue, symbol, exchange_symbol,"
                " base_asset, quote_asset, trading_mode, contract_type, status, tick_size,"
                " step_size, min_qty, min_notional, price_precision, qty_precision,"
                " maker_fee_bps, taker_fee_bps, max_leverage, margin_mode, metadata,"
                " discovered_at, source, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(instrument_id) DO UPDATE SET status=excluded.status,"
                " tick_size=excluded.tick_size, step_size=excluded.step_size,"
                " min_qty=excluded.min_qty, min_notional=excluded.min_notional,"
                " metadata=excluded.metadata, updated_at=excluded.updated_at,"
                " source=excluded.source",
                rows,
            )
        except Exception as e:
            log.warning("instrument persistence failed: %s", e)

    def _persist_all(self) -> None:
        for insts in self.by_venue.values():
            self._persist(insts)

    def _load_db_fallback(self) -> None:
        with self._lock:
            rows = self.db.query("SELECT * FROM instruments LIMIT 6000")
            by_venue: dict[str, dict[str, Instrument]] = {}
            for r in rows:
                import json

                try:
                    meta = json.loads(r.get("metadata") or "{}")
                except Exception:
                    meta = {}
                inst = Instrument(
                    instrument_id=r["instrument_id"],
                    venue=r["venue"],
                    symbol=r["symbol"],
                    exchange_symbol=r["exchange_symbol"] or "",
                    base_asset=r["base_asset"] or "",
                    quote_asset=r["quote_asset"] or "",
                    trading_mode=r["trading_mode"] or "SPOT",
                    contract_type=r["contract_type"] or "SPOT",
                    status=r["status"] or "UNKNOWN",
                    tick_size=r.get("tick_size"),
                    step_size=r.get("step_size"),
                    min_qty=r.get("min_qty"),
                    min_notional=r.get("min_notional"),
                    price_precision=r.get("price_precision"),
                    qty_precision=r.get("qty_precision"),
                    maker_fee_bps=r.get("maker_fee_bps"),
                    taker_fee_bps=r.get("taker_fee_bps"),
                    max_leverage=r.get("max_leverage"),
                    margin_mode=r.get("margin_mode"),
                    metadata=meta,
                    discovered_at=r.get("discovered_at") or "",
                    source="cached_metadata",
                )
                by_venue.setdefault(inst.venue, {})[inst.symbol] = inst
            if by_venue.get(self.primary):
                self.by_venue = by_venue
                self._reselect_active_locked()
                self.source = "cached_metadata"
                return
            # GUARANTEED labeled fallback (fixes v2's empty-fallback hole)
            self.by_venue = {self.primary: _fallback_instruments(self.primary)}
            self._reselect_active_locked()
            self.source = "default_fallback"

    def ensure_loaded(self) -> None:
        with self._lock:
            if self.catalog:
                return
        self.refresh()

    # ---- accessors ----
    def all_assets(self, search: str | None = None, limit: int = 200) -> list[dict]:
        self.ensure_loaded()
        with self._lock:
            items = list(self.catalog.values())
        if search:
            s = search.upper()
            items = [i for i in items if s in i.symbol.upper()]
        items.sort(key=lambda i: -safe_float(i.metadata.get("volume24h_usd")))
        return [self._asset_dict(i) for i in items[:limit]]

    @staticmethod
    def _asset_dict(inst: Instrument) -> dict:
        d = inst.to_dict()
        d["volume24h_usd"] = safe_float(inst.metadata.get("volume24h_usd"))
        return d

    def get(self, symbol: str) -> Instrument | None:
        self.ensure_loaded()
        with self._lock:
            return self.catalog.get(symbol)

    def exchange_symbol(self, market: str, venue: str | None = None) -> str:
        inst = self.get(market)
        if inst and (venue in (None, inst.venue)):
            return inst.exchange_symbol
        return market.replace("/", "")

    def filters(self, market: str) -> dict | None:
        inst = self.get(market)
        if not inst:
            return None
        return {
            "tick_size": inst.tick_size,
            "step_size": inst.step_size,
            "min_qty": inst.min_qty,
            "min_notional": inst.min_notional,
        }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "source": self.source,
                "instruments": len(self.catalog),
                "primary_venue": self.primary,
                "venues": {vid: len(insts) for vid, insts in self.by_venue.items()},
                "default_trading_venue": self.config.get("default_trading_venue") or self.primary,
                "market_venue_overrides": dict(self.config.get("market_venue_overrides") or {}),
                "last_refresh": self.last_refresh,
                "last_error": self.last_error,
            }
