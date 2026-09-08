"""Technical indicators (§14). Core set ported unchanged from the audited v2
monolith; V3 adds VWAP, Supertrend, support/resistance, market structure and
liquidity-zone helpers used by the chart engine and strategies.

All functions are pure: they take lists of floats and return values — no
global state, no I/O, fully unit-testable.
"""

from __future__ import annotations

import math
import statistics
from itertools import pairwise

from zepay.core.util import clamp


def ema(values: list[float], n: int) -> list[float]:
    if not values or n <= 1:
        return list(values)
    k = 2.0 / (n + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def sma(values: list[float], n: int) -> list[float]:
    if len(values) < n or n <= 0:
        return list(values)
    out = []
    for i in range(len(values)):
        if i + 1 >= n:
            out.append(statistics.fmean(values[i + 1 - n : i + 1]))
        else:
            out.append(values[i])
    return out


def rsi(closes: list[float], n: int = 14) -> float:
    if len(closes) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0))
        losses.append(max(-ch, 0))
    ag = statistics.fmean(gains[-n:])
    al = statistics.fmean(losses[-n:])
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return clamp(100 - 100 / (1 + rs), 0, 100)


def true_ranges(h: list[float], l: list[float], c: list[float]) -> list[float]:
    out = []
    for i in range(1, len(c)):
        out.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    return out


def atr(h: list[float], l: list[float], c: list[float], n: int = 14) -> float:
    tr = true_ranges(h, l, c)
    if len(tr) < n:
        return statistics.fmean(tr) if tr else 0.0
    return statistics.fmean(tr[-n:])


def atr_series(h: list[float], l: list[float], c: list[float], n: int = 14) -> list[float]:
    tr = true_ranges(h, l, c)
    if not tr:
        return []
    out = [statistics.fmean(tr[: min(n, len(tr))])]
    for i in range(1, len(tr)):
        if i < n:
            out.append(statistics.fmean(tr[: i + 1]))
        else:
            out.append((out[-1] * (n - 1) + tr[i]) / n)
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, sig: int = 9) -> dict:
    if len(closes) < slow + sig:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    f = ema(closes, fast)
    s = ema(closes, slow)
    line = [a - b for a, b in zip(f, s, strict=False)]
    sig_line = ema(line, sig)
    return {"macd": line[-1], "signal": sig_line[-1], "hist": line[-1] - sig_line[-1]}


def bollinger(closes: list[float], n: int = 20, k: float = 2.0) -> dict:
    if len(closes) < n:
        m = statistics.fmean(closes) if closes else 0
        return {"mid": m, "upper": m, "lower": m, "pctb": 0.5, "width": 0}
    w = closes[-n:]
    m = statistics.fmean(w)
    sd = statistics.pstdev(w)
    upper, lower = m + k * sd, m - k * sd
    last = closes[-1]
    pctb = (last - lower) / (upper - lower) if upper > lower else 0.5
    width = (upper - lower) / m if m else 0
    return {
        "mid": m,
        "upper": upper,
        "lower": lower,
        "pctb": clamp(pctb, -0.5, 1.5),
        "width": width,
    }


def returns(closes: list[float]) -> list[float]:
    return [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1]]


def realized_vol(rets: list[float], annualize: int = 365 * 24) -> float:
    if len(rets) < 2:
        return 0.0
    return statistics.pstdev(rets) * math.sqrt(annualize)


def vwap(rows: list[dict]) -> float:
    """Session VWAP over candle rows ({h,l,c,v})."""
    if not rows:
        return 0.0
    num = den = 0.0
    for r in rows:
        tp = (r["h"] + r["l"] + r["c"]) / 3.0
        num += tp * r["v"]
        den += r["v"]
    return num / den if den else 0.0


def supertrend(
    h: list[float], l: list[float], c: list[float], period: int = 10, multiplier: float = 3.0
) -> dict:
    """Returns {'value', 'direction' (+1/-1), 'series'} — classic Supertrend."""
    n = len(c)
    if n < period + 2:
        return {"value": c[-1] if c else 0.0, "direction": 0, "series": []}
    atrs = atr_series(h, l, c, period)
    # atrs is aligned to tr (len n-1)
    st = [0.0] * n
    direction = [1] * n
    upper = [0.0] * n
    lower = [0.0] * n
    for i in range(1, n):
        a = atrs[min(i - 1, len(atrs) - 1)] if atrs else 0.0
        hl2 = (h[i] + l[i]) / 2.0
        basic_upper = hl2 + multiplier * a
        basic_lower = hl2 - multiplier * a
        upper[i] = (
            basic_upper if (basic_upper < upper[i - 1] or c[i - 1] > upper[i - 1]) else upper[i - 1]
        )
        lower[i] = (
            basic_lower if (basic_lower > lower[i - 1] or c[i - 1] < lower[i - 1]) else lower[i - 1]
        )
        if i == 1:
            direction[i] = 1 if c[i] > upper[i] else -1
        else:
            prev = direction[i - 1]
            if prev == 1:
                direction[i] = -1 if c[i] < lower[i] else 1
            else:
                direction[i] = 1 if c[i] > upper[i] else -1
        st[i] = lower[i] if direction[i] == 1 else upper[i]
    return {"value": st[-1], "direction": direction[-1], "series": st}


def support_resistance(
    h: list[float], l: list[float], c: list[float], lookback: int = 60, n_levels: int = 3
) -> dict:
    """Pivot-based S/R: local maxima/minima clustered into levels."""
    n = len(c)
    if n < 10:
        return {"support": [], "resistance": []}
    w = min(lookback, n - 2)
    hs, ls = h[-w:], l[-w:]
    piv_hi, piv_lo = [], []
    for i in range(2, w - 2):
        if hs[i] == max(hs[i - 2 : i + 3]):
            piv_hi.append(hs[i])
        if ls[i] == min(ls[i - 2 : i + 3]):
            piv_lo.append(ls[i])
    last = c[-1]

    def cluster(levels: list[float], side: str) -> list[float]:
        if side == "res":
            cand = sorted(x for x in levels if x >= last)
        else:
            cand = sorted((x for x in levels if x <= last), reverse=True)
        out: list[float] = []
        tol = (max(levels) - min(levels)) * 0.15 if len(levels) > 1 else last * 0.005
        tol = max(tol, last * 0.002)
        for x in cand:
            if not any(abs(x - o) <= tol for o in out):
                out.append(x)
            if len(out) >= n_levels:
                break
        return out

    return {"support": cluster(piv_lo, "sup"), "resistance": cluster(piv_hi, "res")}


def market_structure(h: list[float], l: list[float], c: list[float], lookback: int = 30) -> dict:
    """Higher-highs/higher-lows classification over recent swing points."""
    n = len(c)
    if n < lookback or n < 10:
        return {"structure": "UNKNOWN", "hh": 0, "hl": 0, "lh": 0, "ll": 0}
    w = min(lookback, n - 2)
    hs, ls = h[-w:], l[-w:]
    swings_hi, swings_lo = [], []
    for i in range(2, w - 2):
        if hs[i] == max(hs[i - 2 : i + 3]):
            swings_hi.append(hs[i])
        if ls[i] == min(ls[i - 2 : i + 3]):
            swings_lo.append(ls[i])
    hh = sum(1 for a, b in pairwise(swings_hi) if b > a)
    lh = sum(1 for a, b in pairwise(swings_hi) if b < a)
    hl = sum(1 for a, b in pairwise(swings_lo) if b > a)
    ll = sum(1 for a, b in pairwise(swings_lo) if b < a)
    if hh >= 2 and hl >= 2 and lh == 0 and ll == 0:
        structure = "UPTREND"
    elif lh >= 2 and ll >= 2 and hh == 0 and hl == 0:
        structure = "DOWNTREND"
    elif hh + hl > lh + ll:
        structure = "BULLISH_BIAS"
    elif lh + ll > hh + hl:
        structure = "BEARISH_BIAS"
    else:
        structure = "RANGE"
    return {"structure": structure, "hh": hh, "hl": hl, "lh": lh, "ll": ll}


def liquidity_zones(bids: list[list], asks: list[list], top_n: int = 3) -> dict:
    """Clusters of resting size on each side of the book (real depth only)."""

    def zones(levels: list[list]) -> list[dict]:
        if not levels:
            return []
        total = sum(q for _, q in levels) or 1
        scored = sorted(levels, key=lambda pq: -pq[1])[:top_n]
        return [{"price": p, "qty": q, "pct_of_visible": round(q / total, 3)} for p, q in scored]

    return {"bid_zones": zones(bids), "ask_zones": zones(asks)}


def book_imbalance(bids: list[list], asks: list[list], depth: int = 10) -> float:
    bv = sum(p * q for p, q in bids[:depth])
    av = sum(p * q for p, q in asks[:depth])
    return (bv - av) / (bv + av) if (bv + av) else 0.0
