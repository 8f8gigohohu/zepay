"""Markets router: universe, live signals, candles, ticker, depth, derivs,
data quality — every value sourced from real feeds or labeled UNAVAILABLE."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from zepay.api.deps import guard_mutate, zapp_of

router = APIRouter(prefix="/api", tags=["markets"])


@router.get("/universe")
def universe(
    request: Request, search: str | None = None, limit: int = 100, venue: str | None = None
) -> dict:
    z = zapp_of(request)
    assets = z.universe.all_assets(search=search, limit=limit)
    if venue:
        # instruments available on a specific venue (e.g. binance_futures)
        insts = (z.universe.by_venue or {}).get(venue) or {}
        assets = [z.universe._asset_dict(i) for i in insts.values()][:limit]
    return {"source": z.universe.snapshot(), "assets": assets}


class VenueBody(BaseModel):
    venue: str  # binance_spot | binance_futures | zepay | "" (clear override)


@router.post("/markets/{market:path}/venue", dependencies=[Depends(guard_mutate)])
def set_market_venue(market: str, body: VenueBody, request: Request) -> dict:
    """Route one market to a venue (futures or spot). Applies without restart."""
    z = zapp_of(request)
    ok = z.universe.set_market_venue(market, body.venue or None)
    if not ok:
        raise HTTPException(
            400,
            f"venue {body.venue!r} does not offer {market} (or is not scanned). "
            "Check /api/universe?venue=… for available instruments.",
        )
    inst = z.universe.get(market)
    return {
        "ok": True,
        "market": market,
        "venue": inst.venue if inst else None,
        "trading_mode": inst.trading_mode if inst else None,
        "note": "venue routing is live — next cycle fetches and trades this market on the set venue",
    }


@router.get("/markets")
def markets(request: Request) -> dict:
    """Latest cycle signals per market (from the engine's persisted report)."""
    z = zapp_of(request)
    try:
        raw = z.storage.kv_get("last_signals")
        sig = json.loads(raw) if raw else {}
    except Exception:
        sig = {}
    return {
        "signals": sig.get("markets", []),
        "cycle_id": sig.get("cycle_id"),
        "ts": sig.get("ts"),
        "note": "populated after the first engine cycle" if not sig else "",
    }


@router.get("/markets/{market:path}/candles")
def candles(market: str, request: Request, interval: str | None = None, limit: int = 200) -> dict:
    z = zapp_of(request)
    rows = z.collector.get_klines(market, interval) or []
    if not rows:
        # try one real fetch before declaring unavailable (honest path)
        try:
            rows = z.collector.fetch_klines(market, interval, limit) or []
        except Exception as e:
            raise HTTPException(502, f"klines unavailable: {e}") from e
    if not rows:
        return {
            "market": market,
            "candles": [],
            "status": "UNAVAILABLE",
            "note": "no real candles cached or fetchable for this market",
        }
    rows = rows[-limit:]
    return {
        "market": market,
        "interval": interval or z.config.get("candle_interval"),
        "count": len(rows),
        "candles": rows,
        "stale": z.collector.is_stale(market),
        "age_s": round(z.collector.age_secs(market), 1),
    }


@router.get("/markets/{market:path}/ticker")
def ticker(market: str, request: Request) -> dict:
    z = zapp_of(request)
    t = z.collector.get_ticker(market)
    if not t:
        return {"market": market, "status": "UNAVAILABLE"}
    return {
        "market": market,
        "status": "OK",
        "ticker": t,
        "age_s": round(z.collector.age_secs(market), 1),
    }


@router.get("/markets/{market:path}/depth")
def depth(market: str, request: Request) -> dict:
    z = zapp_of(request)
    d = z.collector.get_depth(market)
    if not d:
        return {"market": market, "status": "UNAVAILABLE"}
    return {"market": market, "status": "OK", "depth": d}


@router.get("/markets/{market:path}/derivs")
def derivs(market: str, request: Request) -> dict:
    z = zapp_of(request)
    d = z.collector.get_derivs(market)
    if not d or not d.get("available"):
        return {
            "market": market,
            "status": "UNAVAILABLE",
            "note": "funding/OI not available for this market/venue",
        }
    return {"market": market, "status": "OK", "derivs": d}


@router.get("/markets/{market:path}/explanation")
def explanation(market: str, request: Request) -> dict:
    z = zapp_of(request)
    raw = z.storage.kv_get(f"explain:{market}")
    if not raw:
        return {
            "market": market,
            "status": "NO_DATA",
            "note": "no explanation yet — runs each engine cycle",
        }
    try:
        return json.loads(raw)
    except Exception:
        return {"market": market, "status": "ERROR"}


@router.get("/quality")
def quality(request: Request) -> dict:
    z = zapp_of(request)
    return {
        "summary": z.quality.summary(),
        "feed_health": z.collector.feed_health(),
        "events": z.quality.recent_events(limit=50),
    }
