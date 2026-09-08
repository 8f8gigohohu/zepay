"""Portfolio intelligence (§20).

The AI must consider the WHOLE portfolio before opening a position:
exposure (total/directional/per-asset), correlation-weighted exposure,
volatility contribution, historical VaR/CVaR from REAL returns, beta to BTC,
stress scenarios, margin utilization and liquidation distance (futures).

Example rule enforced downstream by the Risk Engine: an ETH long does not get
full independent risk budget when a BTC long already saturates market
exposure — correlated_exposure() measures exactly that from real returns.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from zepay.core.util import clamp, safe_float, utcnow_iso
from zepay.features.indicators import returns


class PortfolioEngine:
    def __init__(self, storage, collector, config, accounts):
        self.db = storage
        self.collector = collector
        self.config = config
        self.accounts = accounts  # AccountsRepository (balances table)

    # ---- positions ----
    def positions(self, mode: str | None = None) -> list[dict]:
        if mode:
            return self.db.query("SELECT * FROM positions WHERE status='OPEN' AND mode=?", (mode,))
        return self.db.query("SELECT * FROM positions WHERE status='OPEN'")

    def all_positions(self, limit: int = 100) -> list[dict]:
        return self.db.query("SELECT * FROM positions ORDER BY updated_at DESC LIMIT ?", (limit,))

    # ---- exposure ----
    def exposure(self, positions: list[dict], equity: float) -> dict:
        if not equity:
            return {"total_pct": 0, "long_pct": 0, "short_pct": 0, "per_asset": {}}
        tot = sum(abs(safe_float(p.get("exposure"))) for p in positions)
        lng = sum(
            abs(safe_float(p.get("exposure"))) for p in positions if p.get("direction") == "LONG"
        )
        short = tot - lng
        per = {
            p["market"]: round(abs(safe_float(p.get("exposure"))) / equity, 4) for p in positions
        }
        return {
            "total_pct": round(tot / equity, 4),
            "long_pct": round(lng / equity, 4),
            "short_pct": round(short / equity, 4),
            "per_asset": per,
        }

    def correlated_exposure(
        self, market: str, direction: str, positions: list[dict], equity: float, mat: dict
    ) -> float:
        if not equity or not positions:
            return 0.0
        same_dir = [p for p in positions if p.get("direction") == direction]
        tot = 0.0
        for p in same_dir:
            c = safe_float((mat.get(market) or {}).get(p["market"], 0))
            if c > 0.5:
                tot += abs(safe_float(p.get("exposure"))) * c
        return round(tot / equity, 4)

    def risk_contribution(self, positions: list[dict]) -> dict:
        vols = {}
        for p in positions:
            kl = self.collector.get_klines(p["market"]) or []
            if len(kl) > 20:
                r = returns([x["c"] for x in kl[-30:]])
                vols[p["market"]] = statistics.pstdev(r) if len(r) > 1 else 0.01
            else:
                vols[p["market"]] = 0.02
        weights = {
            p["market"]: abs(safe_float(p.get("exposure"))) * vols.get(p["market"], 0.02)
            for p in positions
        }
        tot = sum(weights.values()) or 1
        return {k: round(v / tot, 4) for k, v in weights.items()}

    # ---- VaR / CVaR (historical, REAL returns of held positions) ----
    def var_cvar(
        self, positions: list[dict], equity: float, confidence: float = 0.95, window: int = 120
    ) -> dict:
        if not positions or not equity:
            return {
                "var_95": 0.0,
                "cvar_95": 0.0,
                "method": "historical",
                "samples": 0,
                "note": "no open positions",
            }
        per_asset_rets: dict[str, list[float]] = {}
        for p in positions:
            kl = self.collector.get_klines(p["market"]) or []
            closes = [r["c"] for r in kl[-window:]]
            r = returns(closes)
            if len(r) >= 10:
                per_asset_rets[p["market"]] = r
        if not per_asset_rets:
            return {
                "var_95": None,
                "cvar_95": None,
                "method": "historical",
                "samples": 0,
                "note": "insufficient real return history — UNAVAILABLE",
            }
        n = min(len(v) for v in per_asset_rets.values())
        port_rets = []
        total_w = sum(abs(safe_float(p.get("exposure"))) for p in positions) or 1
        for i in range(n):
            day = 0.0
            for p in positions:
                rr = per_asset_rets.get(p["market"])
                if not rr:
                    continue
                w = abs(safe_float(p.get("exposure"))) / total_w
                sign = 1 if p.get("direction") == "LONG" else -1
                day += w * sign * rr[-(n - i)]
            port_rets.append(day * total_w / equity)  # portfolio-return space
        port_rets.sort()
        k = max(1, math.floor((1 - confidence) * len(port_rets)))
        var = abs(port_rets[k - 1]) if k <= len(port_rets) else abs(port_rets[0])
        cvar = abs(statistics.fmean(port_rets[:k])) if k else var
        return {
            "var_95": round(var * equity, 2),
            "cvar_95": round(cvar * equity, 2),
            "var_95_pct": round(var, 4),
            "cvar_95_pct": round(cvar, 4),
            "method": "historical",
            "samples": len(port_rets),
            "confidence": confidence,
        }

    # ---- beta to BTC ----
    def beta(self, market: str, window: int = 60) -> float | None:
        if market == "BTC/USDT":
            return 1.0
        ka = self.collector.get_klines(market) or []
        kb = self.collector.get_klines("BTC/USDT") or []
        ra, rb = returns([r["c"] for r in ka]), returns([r["c"] for r in kb])
        n = min(len(ra), len(rb), window)
        if n < 20:
            return None
        ra, rb = ra[-n:], rb[-n:]
        mb = statistics.fmean(rb)
        var_b = statistics.pvariance(rb) or 1e-12
        cov = statistics.fmean(
            [(a - statistics.fmean(ra)) * (b - mb) for a, b in zip(ra, rb, strict=False)]
        )
        return round(cov / var_b, 3)

    # ---- stress scenarios (§20) ----
    def stress(self, positions: list[dict], equity: float, mat: dict) -> list[dict]:
        scenarios: list[dict[str, Any]] = [
            {"name": "BTC -10% crash", "btc_move": -0.10, "alt_multiplier": 1.4},
            {"name": "BTC -20% crash", "btc_move": -0.20, "alt_multiplier": 1.6},
            {"name": "BTC +10% squeeze (short pain)", "btc_move": 0.10, "alt_multiplier": 1.2},
            {
                "name": "vol spike (positions -1.5x ATR)",
                "btc_move": -0.03,
                "alt_multiplier": 1.0,
                "atr_shock": True,
            },
        ]
        out = []
        for s in scenarios:
            pnl = 0.0
            for p in positions:
                expo = safe_float(p.get("exposure"))
                direction = 1 if p.get("direction") == "LONG" else -1
                m = p["market"]
                if s.get("atr_shock"):
                    kl = self.collector.get_klines(m) or []
                    closes = [r["c"] for r in kl[-30:]]
                    vol = statistics.pstdev(returns(closes)) if len(closes) > 5 else 0.02
                    move = -1.5 * vol * math.sqrt(24)  # 1-day-ish shock
                else:
                    c = (
                        safe_float((mat.get(m) or {}).get("BTC/USDT", 0.6))
                        if m != "BTC/USDT"
                        else 1.0
                    )
                    move = (
                        safe_float(s["btc_move"])
                        * (safe_float(c) if m != "BTC/USDT" else 1.0)
                        * (safe_float(s["alt_multiplier"]) if m != "BTC/USDT" else 1.0)
                    )
                pnl += direction * expo * move
            out.append(
                {
                    "scenario": s["name"],
                    "est_pnl": round(pnl, 2),
                    "est_pnl_pct": round(pnl / equity, 4) if equity else 0,
                    "post_equity": round(equity + pnl, 2),
                }
            )
        return out

    # ---- revaluation (v2 logic, multi-mode) ----
    def revalue(self, mode: str = "PAPER") -> dict | None:
        acct = self.accounts.get(mode)
        if not acct:
            return None
        positions = self.positions(mode)
        unreal = invested = 0.0
        for p in positions:
            t = self.collector.get_ticker(p["market"])
            px = safe_float(t.get("price")) if t else 0
            if px <= 0:
                px = safe_float(p.get("current_price"))
            if px <= 0:
                px = safe_float(p.get("entry_price"))
            qty = safe_float(p.get("qty"))
            entry = safe_float(p.get("entry_price"))
            expo = qty * px
            pnl = (px - entry) * qty if p.get("direction") == "LONG" else (entry - px) * qty
            unreal += pnl
            invested += abs(expo)
            self.db.execute(
                "UPDATE positions SET current_price=?, exposure=?, unrealized_pnl=?,"
                " updated_at=? WHERE id=?",
                (px, expo, pnl, utcnow_iso(), p["id"]),
            )
        realized = safe_float(acct.get("realized_pnl"))
        start = safe_float(self.config.get("paper_starting_balance", 10000.0), 10000.0)
        equity = (
            (start + realized + unreal)
            if mode == "PAPER"
            else safe_float(acct.get("equity")) + unreal
        )
        peak = max(safe_float(acct.get("peak_equity", equity)), equity)
        dd = (peak - equity) / peak if peak else 0
        self.accounts.update(
            mode,
            equity=equity,
            available=max(equity - invested, 0),
            invested=invested,
            unrealized_pnl=unreal,
            drawdown_pct=dd,
            peak_equity=peak,
        )
        return self.accounts.get(mode)

    # ---- snapshots & persistence ----
    def snapshot(self, mode: str = "PAPER") -> dict:
        acct = self.accounts.get(mode) or {}
        equity = safe_float(acct.get("equity"))
        positions = self.positions(mode)
        exp = self.exposure(positions, equity)
        mat = self._quick_matrix(positions)
        vc = self.var_cvar(positions, equity)
        snap: dict[str, Any] = {
            "mode": mode,
            "equity": round(equity, 2),
            "available": round(safe_float(acct.get("available")), 2),
            "invested": round(safe_float(acct.get("invested")), 2),
            "realized_pnl": round(safe_float(acct.get("realized_pnl")), 2),
            "unrealized_pnl": round(safe_float(acct.get("unrealized_pnl")), 2),
            "fees_paid": round(safe_float(acct.get("fees_paid")), 2),
            "funding_paid": round(safe_float(acct.get("funding_paid")), 2),
            "day_pnl": round(safe_float(acct.get("day_pnl")), 2),
            "drawdown_pct": round(safe_float(acct.get("drawdown_pct")), 4),
            "exposure": exp,
            "positions": len(positions),
            "risk_contribution": self.risk_contribution(positions),
            "var": vc,
            "stress": self.stress(positions, equity, mat),
            "ts": utcnow_iso(),
        }
        try:
            self.db.execute(
                "INSERT INTO portfolio_snapshots (mode, equity, available, exposure_pct,"
                " long_pct, short_pct, positions, day_pnl, drawdown_pct, var_95, cvar_95,"
                " state, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    mode,
                    snap["equity"],
                    snap["available"],
                    exp["total_pct"],
                    exp["long_pct"],
                    exp["short_pct"],
                    len(positions),
                    snap["day_pnl"],
                    snap["drawdown_pct"],
                    vc.get("var_95"),
                    vc.get("cvar_95"),
                    "",
                    snap["ts"],
                ),
            )
            for m, w in exp["per_asset"].items():
                rc = snap["risk_contribution"].get(m)
                self.db.execute(
                    "INSERT INTO portfolio_exposures (mode, market, direction, exposure,"
                    " weight, vol_contribution, beta, ts) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        mode,
                        m,
                        next((p["direction"] for p in positions if p["market"] == m), ""),
                        safe_float(
                            next((p.get("exposure") for p in positions if p["market"] == m), 0)
                        ),
                        w,
                        rc,
                        self.beta(m),
                        snap["ts"],
                    ),
                )
        except Exception:
            pass
        return snap

    def _quick_matrix(self, positions: list[dict]) -> dict:
        markets = sorted({p["market"] for p in positions} | {"BTC/USDT"})
        rets = {}
        for m in markets:
            kl = self.collector.get_klines(m) or []
            if len(kl) >= 30:
                rets[m] = returns([r["c"] for r in kl])
        mat: dict[str, dict[str, float]] = {}
        for a in markets:
            mat[a] = {}
            for b in markets:
                if a == b:
                    mat[a][b] = 1.0
                elif a in rets and b in rets:
                    n = min(len(rets[a]), len(rets[b]), 60)
                    try:
                        ma = statistics.fmean(rets[a][-n:])
                        mb = statistics.fmean(rets[b][-n:])
                        num = sum(
                            (x - ma) * (y - mb)
                            for x, y in zip(rets[a][-n:], rets[b][-n:], strict=False)
                        )
                        da = math.sqrt(sum((x - ma) ** 2 for x in rets[a][-n:]))
                        db = math.sqrt(sum((y - mb) ** 2 for y in rets[b][-n:]))
                        mat[a][b] = clamp(num / (da * db), -1, 1) if da and db else 0
                    except Exception:
                        mat[a][b] = 0
                else:
                    mat[a][b] = 0
        return mat


class AccountsRepository:
    """accounts table access (per-mode account summary; v2 semantics)."""

    def __init__(self, storage, config):
        self.db = storage
        self.config = config

    def ensure(self, mode: str = "PAPER") -> None:
        row = self.db.query_one(
            "SELECT id FROM accounts WHERE mode=? ORDER BY id DESC LIMIT 1", (mode,)
        )
        if row:
            return
        start = safe_float(self.config.get("paper_starting_balance", 10000.0), 10000.0)
        today = utcnow_iso()[:10]
        self.db.execute(
            "INSERT INTO accounts (mode, equity, available, invested, realized_pnl,"
            " unrealized_pnl, drawdown_pct, peak_equity, day_pnl, day, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (mode, start, start, 0, 0, 0, 0, start, 0, today, utcnow_iso()),
        )

    def get(self, mode: str = "PAPER") -> dict | None:
        self.ensure(mode)
        row = self.db.query_one(
            "SELECT * FROM accounts WHERE mode=? ORDER BY id DESC LIMIT 1", (mode,)
        )
        if not row:
            return None
        today = utcnow_iso()[:10]
        if row.get("day") != today:  # daily reset of day_pnl (real calendar day, UTC)
            self.db.execute("UPDATE accounts SET day=?, day_pnl=0 WHERE id=?", (today, row["id"]))
            row["day"] = today
            row["day_pnl"] = 0
        return row

    def update(self, mode: str, **fields) -> None:
        self.ensure(mode)
        row = self.get(mode)
        if not row:
            return
        sets, params = [], []
        for k, v in fields.items():
            if k in (
                "equity",
                "available",
                "invested",
                "realized_pnl",
                "unrealized_pnl",
                "drawdown_pct",
                "peak_equity",
                "day_pnl",
                "fees_paid",
                "funding_paid",
            ):
                sets.append(f"{k}=?")
                params.append(v)
        if not sets:
            return
        params.append(utcnow_iso())
        params.append(row["id"])
        self.db.execute(
            f"UPDATE accounts SET {', '.join(sets)}, updated_at=? WHERE id=?", tuple(params)
        )
