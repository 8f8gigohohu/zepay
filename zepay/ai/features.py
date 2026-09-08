"""Model feature space. The 12 core keys are IDENTICAL to v2 (models trained
on this space keep their semantics); V3 appends optional extended features
which are versioned separately and only used by v3 models."""

from __future__ import annotations

from zepay.core.util import clamp
from zepay.features.engine import AssetFeatures

FEATURE_KEYS = [
    "mom_10",
    "mom_20",
    "rsi14",
    "macd_hist_n",
    "bb_pctb",
    "atr_pct",
    "vol_ratio",
    "ob_imbalance",
    "rel_strength",
    "market_mom",
    "btc_mom",
    "trend_gap",
]

FEATURE_KEYS_V3 = [
    *FEATURE_KEYS,
    "supertrend_dir",
    "structure_code",
    "bb_width",
    "funding_n",
    "breakout_pos",
    "spread_n",
    "mom_10_20_gap",
]

_STRUCTURE_CODES = {
    "UPTREND": 1.0,
    "BULLISH_BIAS": 0.5,
    "RANGE": 0.0,
    "BEARISH_BIAS": -0.5,
    "DOWNTREND": -1.0,
    "UNKNOWN": 0.0,
}


def feature_vector(f: AssetFeatures, version: str = "v2") -> list[float]:
    atr_ref = f.atr14 or (f.price * 0.01 if f.price else 1)
    base = [
        clamp(f.mom_10 * 5, -2, 2),
        clamp(f.mom_20 * 4, -2, 2),
        (f.rsi14 - 50) / 25.0,
        clamp(f.macd_hist / atr_ref, -2, 2),
        (f.bb_pctb - 0.5) * 2,
        clamp(f.atr_pct * 20, 0, 3),
        clamp((f.vol_ratio - 1), -2, 3),
        clamp(f.ob_imbalance * 2, -1.5, 1.5),
        clamp(f.rel_strength * 5, -2, 2),
        clamp(f.market_mom * 5, -2, 2),
        clamp(f.btc_mom * 5, -2, 2),
        clamp(((f.price - f.sma50) / atr_ref) if f.sma50 else 0, -3, 3),
    ]
    if version == "v3":
        base += [
            float(f.supertrend_dir),
            _STRUCTURE_CODES.get(f.structure, 0.0),
            clamp(f.bb_width * 10, 0, 3),
            clamp(f.funding_rate * 500, -2, 2),
            clamp(f.breakout_up * 40 if f.breakout_up > 0 else f.breakout_dn * 40, -1, 1),
            clamp(f.spread_bps / 10.0, 0, 3),
            clamp((f.mom_10 - f.mom_20 / 2) * 6, -2, 2),
        ]
    return base


def feature_keys(version: str = "v2") -> list[str]:
    return FEATURE_KEYS_V3 if version == "v3" else FEATURE_KEYS
