"""Feature engine (§11) + asset feature record.

Ported from v2 (same feature semantics the AI models were designed against)
with dependency injection instead of globals, plus V3 additions: EMA9/21/50/200,
VWAP, Supertrend, market structure, liquidity zones — feeding both the models
and the chart engine. Features are computed ONLY from data available at the
evaluation timestamp (no lookahead — asserted in tests).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field

from zepay.core.util import clamp, safe_float
from zepay.features.indicators import (
    atr,
    bollinger,
    book_imbalance,
    ema,
    macd,
    market_structure,
    realized_vol,
    returns,
    rsi,
    sma,
    supertrend,
    support_resistance,
    vwap,
)

FEATURE_VERSION = "features-3.0.0"


@dataclass
class AssetFeatures:
    market: str
    venue: str = "binance_spot"
    price: float = 0
    change24: float = 0
    sma20: float = 0
    sma50: float = 0
    ema9: float = 0
    ema21: float = 0
    ema12: float = 0
    ema26: float = 0
    ema50: float = 0
    ema200: float = 0
    vwap: float = 0
    rsi14: float = 50
    macd_hist: float = 0
    bb_pctb: float = 0.5
    bb_width: float = 0
    atr14: float = 0
    atr_pct: float = 0
    mom_10: float = 0
    mom_20: float = 0
    vol_ratio: float = 1.0
    realized_vol: float = 0
    high_20: float = 0
    low_20: float = 0
    breakout_up: float = 0
    breakout_dn: float = 0
    spread_bps: float = 0
    depth_usd: float = 0
    ob_imbalance: float = 0
    liquidity_usd: float = 0
    n_candles: int = 0
    supertrend_dir: int = 0
    structure: str = "UNKNOWN"
    support: list = field(default_factory=list)
    resistance: list = field(default_factory=list)
    funding_rate: float = 0  # real, from futures public API (0 = n/a)
    open_interest: float = 0
    derivs_available: bool = False
    data_stale: bool = False
    data_quality: str = "UNAVAILABLE"
    # cross-asset (filled by CrossAssetEngine)
    btc_mom: float = 0
    market_mom: float = 0
    rel_strength: float = 0
    corr_btc: float = 0
    vol_spillover: float = 0
    market_breadth: float = 0

    def to_dict(self) -> dict:
        return asdict(self)


class FeatureEngine:
    def __init__(self, collector):
        self.collector = collector

    def build(self, market: str, venue: str = "binance_spot") -> AssetFeatures:
        kl = self.collector.get_klines(market) or []
        t = self.collector.get_ticker(market) or {}
        f = AssetFeatures(market=market, venue=venue)
        f.price = safe_float(t.get("price"))
        f.change24 = safe_float(t.get("change24"))
        f.liquidity_usd = safe_float(t.get("quote_vol"))
        f.data_stale = self.collector.is_stale(market)
        derivs = self.collector.get_derivs(market)
        if derivs:
            f.funding_rate = safe_float(derivs.get("funding_rate"))
            f.open_interest = safe_float(derivs.get("open_interest"))
            f.derivs_available = bool(derivs.get("available"))
        closes = [r["c"] for r in kl]
        highs = [r["h"] for r in kl]
        lows = [r["l"] for r in kl]
        vols = [r["v"] for r in kl]
        f.n_candles = len(closes)
        if closes:
            if not f.price:
                f.price = closes[-1]
            s20 = sma(closes, 20)
            s50 = sma(closes, 50)
            e12 = ema(closes, 12)
            e26 = ema(closes, 26)
            f.sma20 = s20[-1]
            f.sma50 = s50[-1]
            f.ema12 = e12[-1]
            f.ema26 = e26[-1]
            f.ema9 = ema(closes, 9)[-1]
            f.ema21 = ema(closes, 21)[-1]
            f.ema50 = ema(closes, 50)[-1]
            f.ema200 = ema(closes, 200)[-1] if len(closes) >= 60 else 0
            f.vwap = vwap(kl[-24:]) if len(kl) >= 2 else closes[-1]
            f.rsi14 = rsi(closes)
            f.macd_hist = macd(closes)["hist"]
            bb = bollinger(closes)
            f.bb_pctb = bb["pctb"]
            f.bb_width = bb["width"]
            a = atr(highs, lows, closes)
            f.atr14 = a
            f.atr_pct = (a / closes[-1]) if closes[-1] else 0
            f.mom_10 = (closes[-1] / closes[-11] - 1) if len(closes) > 11 and closes[-11] else 0
            f.mom_20 = (closes[-1] / closes[-21] - 1) if len(closes) > 21 and closes[-21] else 0
            if len(vols) >= 20:
                base = statistics.fmean(vols[-20:-1]) if len(vols) > 1 else vols[-1]
                f.vol_ratio = (vols[-1] / base) if base else 1.0
            r = returns(closes[-60:] if len(closes) > 60 else closes)
            f.realized_vol = realized_vol(r)
            hw = max(highs[-20:]) if len(highs) >= 20 else max(highs)
            lw = min(lows[-20:]) if len(lows) >= 20 else min(lows)
            f.high_20 = hw
            f.low_20 = lw
            f.breakout_up = ((closes[-1] - hw) / closes[-1]) if closes[-1] else 0
            f.breakout_dn = ((closes[-1] - lw) / closes[-1]) if closes[-1] else 0
            st = supertrend(highs, lows, closes)
            f.supertrend_dir = st["direction"]
            sr = support_resistance(highs, lows, closes)
            f.support = [round(x, 8) for x in sr["support"]]
            f.resistance = [round(x, 8) for x in sr["resistance"]]
            f.structure = market_structure(highs, lows, closes)["structure"]
        depth = self.collector.get_depth(market)
        if depth:
            try:
                bbid = depth["bids"][0][0]
                bask = depth["asks"][0][0]
                mid = (bbid + bask) / 2
                f.spread_bps = ((bask - bbid) / mid * 10000) if mid else 0
                bv = sum(p * q for p, q in depth["bids"])
                av = sum(p * q for p, q in depth["asks"])
                f.depth_usd = bv + av
                f.ob_imbalance = book_imbalance(depth["bids"], depth["asks"])
            except Exception:
                pass
        f.data_quality = "STALE" if f.data_stale else ("OK" if f.price > 0 else "UNAVAILABLE")
        return f

    def build_many(
        self, markets: list[str], venue: str = "binance_spot"
    ) -> dict[str, AssetFeatures]:
        return {m: self.build(m, venue) for m in markets}


class CrossAssetEngine:
    """BTC influence, market momentum/breadth, correlations, spillover,
    relative strength — quantified from REAL closes, never assumed (v2)."""

    def __init__(self, collector, storage=None):
        self.collector = collector
        self.db = storage

    def closes_map(self, markets: list[str]) -> dict[str, list[float]]:
        out = {}
        for m in markets:
            kl = self.collector.get_klines(m) or []
            if len(kl) >= 30:
                out[m] = [r["c"] for r in kl]
        return out

    @staticmethod
    def corr(a: list[float], b: list[float]) -> float:
        n = min(len(a), len(b))
        if n < 10:
            return 0.0
        a = a[-n:]
        b = b[-n:]
        try:
            ma = statistics.fmean(a)
            mb = statistics.fmean(b)
            num = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=False))
            da = math.sqrt(sum((x - ma) ** 2 for x in a))
            db = math.sqrt(sum((y - mb) ** 2 for y in b))
            if da == 0 or db == 0:
                return 0.0
            return clamp(num / (da * db), -1, 1)
        except Exception:
            return 0.0

    def enrich(self, feats: dict[str, AssetFeatures]) -> dict[str, AssetFeatures]:
        markets = list(feats.keys())
        cmap = self.closes_map(markets)
        rets = {m: returns(c) for m, c in cmap.items()}
        moms = {}
        for m, c in cmap.items():
            moms[m] = (c[-1] / c[-21] - 1) if len(c) > 21 and c[-21] else 0
        market_mom = statistics.fmean(list(moms.values())) if moms else 0
        btc_mom = moms.get("BTC/USDT", 0)
        breadth = (sum(1 for v in moms.values() if v > 0) / len(moms)) if moms else 0.5
        vols = {m: (statistics.pstdev(r[-20:]) if len(r) >= 20 else 0) for m, r in rets.items()}
        avg_vol = statistics.fmean(list(vols.values())) if vols else 0
        for m, f in feats.items():
            f.btc_mom = btc_mom
            f.market_mom = market_mom
            f.market_breadth = breadth
            f.rel_strength = moms.get(m, 0) - market_mom
            if "BTC/USDT" in rets and m in rets and m != "BTC/USDT":
                f.corr_btc = self.corr(rets[m][-60:], rets["BTC/USDT"][-60:])
            elif m == "BTC/USDT":
                f.corr_btc = 1.0
            f.vol_spillover = avg_vol - vols.get(m, 0)
        if self.db is not None:
            try:
                from zepay.core.util import utcnow_iso

                ms = list(rets.keys())
                rows = []
                for i in range(len(ms)):
                    for j in range(i + 1, len(ms)):
                        cv = self.corr(rets[ms[i]][-60:], rets[ms[j]][-60:])
                        rows.append((ms[i], ms[j], cv, 60, utcnow_iso()))
                if rows:
                    self.db.executemany(
                        "INSERT INTO correlations (a,b,corr,window_n,created_at)"
                        " VALUES (?,?,?,?,?)",
                        rows,
                    )
            except Exception:
                pass
        return feats

    def matrix(self, markets: list[str]) -> dict[str, dict[str, float]]:
        cmap = self.closes_map(markets)
        rets = {m: returns(c) for m, c in cmap.items()}
        mat: dict[str, dict[str, float]] = {}
        for a in markets:
            mat[a] = {}
            for b in markets:
                if a == b:
                    mat[a][b] = 1.0
                elif a in rets and b in rets:
                    mat[a][b] = round(self.corr(rets[a][-60:], rets[b][-60:]), 3)
                else:
                    mat[a][b] = 0.0
        return mat
