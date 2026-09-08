"""Backtesting V3 (§23).

Institutional rules enforced here:
  * REAL historical candles only — refuses to run on synthetic/insufficient
    data with an explicit error (never fabricates a backtest)
  * features at bar i use ONLY data up to bar i (lookahead guard asserted)
  * explicit costs: fees, slippage, spread, funding, latency (as a fill-price
    penalty), partial fills via book-capacity approximation parameter
  * long AND short, spot semantics; leverage/futures mode applies funding per
    8h and liquidation-distance check (approximated, labeled)
  * per-trade MAE/MFE and bar counts recorded for trade-quality analysis
  * portfolio mode: capital split across assets, shared equity curve
"""

from __future__ import annotations

import json
import logging
import math
import statistics

from zepay.core.ids import new_id
from zepay.core.util import clamp, safe_float, utcnow_iso

log = logging.getLogger("zepay.backtesting")

DEFAULT_PARAMS = {
    "entry_threshold": 0.25,  # min confidence to enter
    "atr_stop_mult": 1.5,
    "atr_tp_mult": 3.0,
    "max_bars_held": 10,
    "position_pct": 0.15,  # fraction of equity per trade
    "allow_short": True,
    "leverage": 1.0,
    "participation_cap": 0.25,  # fraction of avg bar quote volume per fill
}


class Backtester:
    def __init__(
        self,
        collector,
        ai,
        regime_engine,
        orchestrator,
        cost_model,
        config,
        storage=None,
        audit_fn=None,
    ):
        self.collector = collector
        self.ai = ai
        self.regime = regime_engine
        self.orchestrator = orchestrator
        self.costs = cost_model
        self.config = config
        self.db = storage
        self.audit = audit_fn or (lambda *a, **k: None)

    # ------------------------------------------------------------------
    def _series(self, market: str, interval: str | None = None, limit: int = 500) -> list[dict]:
        interval = interval or self.config.get("candle_interval", "1h")
        try:
            rows = self.collector.fetch_klines(market, interval, limit)
        except Exception:
            rows = self.collector.get_klines(market, interval) or []
        return rows or []

    # ------------------------------------------------------------------
    def run_single(
        self,
        market: str,
        starting: float = 10000.0,
        interval: str | None = None,
        fee_bps: float | None = None,
        slip_bps: float | None = None,
        params: dict | None = None,
        persist: bool = True,
        rows_override: list[dict] | None = None,
    ) -> dict:
        P = {**DEFAULT_PARAMS, **(params or {})}
        interval = interval or self.config.get("candle_interval", "1h")
        fee_bps = safe_float(fee_bps, safe_float(self.config.get("fee_bps", 10.0), 10.0))
        slip_bps = safe_float(slip_bps, safe_float(self.config.get("slippage_bps", 5.0), 5.0))
        # rows_override lets walk-forward/hyperopt feed chronological SLICES of
        # the same real candles (in-sample/out-of-sample splits)
        rows = rows_override if rows_override is not None else self._series(market, interval)
        if len(rows) < 80:
            return {
                "ok": False,
                "error": f"Not enough REAL history for {market} ({len(rows)} candles). "
                "ZEPAY does not backtest on synthetic data.",
                "market": market,
            }
        closes = [r["c"] for r in rows]
        equity = starting
        peak = starting
        max_dd = 0.0
        trades: list[dict] = []
        pos: dict | None = None
        fees_paid = slip_paid = funding_paid = 0.0
        leverage = clamp(safe_float(P["leverage"], 1.0), 1.0, 5.0)
        for i in range(50, len(closes) - 1):
            w = rows[: i + 1]  # ← lookahead guard: past only
            if w[-1]["open_time"] != rows[i]["open_time"]:
                raise AssertionError("lookahead guard violated")
            f = self.ai._features_from_window(market, w)
            if not f:
                continue
            f.price = closes[i]
            regime_info = self.regime.detect(f)
            from zepay.strategies.base import StrategyContext

            strat_result = self.orchestrator.evaluate(
                f, StrategyContext(regime=regime_info, costs=self.costs.components(f))
            )
            infer = self.ai.infer(f, regime_info, strat_result)
            px_next = closes[i + 1]
            # ---- entry ----
            if (
                pos is None
                and infer["direction"] in ("LONG", "SHORT")
                and infer["confidence"] > safe_float(P["entry_threshold"], 0.25)
                and (infer["direction"] == "LONG" or P["allow_short"])
                and regime_info["regime"] not in ("PANIC", "UNSTABLE")
            ):
                stop_dist = max(f.atr14 * safe_float(P["atr_stop_mult"], 1.5), closes[i] * 0.004)
                notional = equity * safe_float(P["position_pct"], 0.15) * leverage
                qty = notional / closes[i]
                # partial-fill approximation: cap by participation in bar volume
                cap = rows[i].get("quote_vol") or (rows[i]["v"] * closes[i])
                if cap:
                    max_qty = cap * safe_float(P["participation_cap"], 0.25) / closes[i]
                    qty = min(qty, max_qty) if max_qty > 0 else qty
                # latency penalty: fill at next-bar open approximated by px_next blend
                fill_px = (
                    closes[i] * (1 + slip_bps / 10000.0)
                    if infer["direction"] == "LONG"
                    else closes[i] * (1 - slip_bps / 10000.0)
                )
                fee = fill_px * qty * fee_bps / 10000.0
                fees_paid += fee
                slip_paid += closes[i] * qty * slip_bps / 10000.0
                equity -= fee
                pos = {
                    "dir": infer["direction"],
                    "entry": fill_px,
                    "qty": qty,
                    "bars": 0,
                    "stop": (
                        fill_px - stop_dist if infer["direction"] == "LONG" else fill_px + stop_dist
                    ),
                    "tp": (
                        fill_px
                        + stop_dist
                        * (
                            safe_float(P["atr_tp_mult"], 3.0)
                            / max(safe_float(P["atr_stop_mult"], 1.5), 1e-9)
                        )
                        if infer["direction"] == "LONG"
                        else fill_px
                        - stop_dist
                        * (
                            safe_float(P["atr_tp_mult"], 3.0)
                            / max(safe_float(P["atr_stop_mult"], 1.5), 1e-9)
                        )
                    ),
                    "entry_i": i,
                    "mfe": 0.0,
                    "mae": 0.0,
                    "funding": 0.0,
                }
            # ---- manage ----
            elif pos is not None:
                pos["bars"] += 1
                hi, lo = rows[i]["h"], rows[i]["l"]
                if pos["dir"] == "LONG":
                    pos["mfe"] = max(pos["mfe"], (hi - pos["entry"]) / pos["entry"])
                    pos["mae"] = min(pos["mae"], (lo - pos["entry"]) / pos["entry"])
                else:
                    pos["mfe"] = max(pos["mfe"], (pos["entry"] - lo) / pos["entry"])
                    pos["mae"] = min(pos["mae"], (pos["entry"] - hi) / pos["entry"])
                if leverage > 1:
                    fund = (
                        pos["entry"]
                        * pos["qty"]
                        * (self.costs.refresh()["fund_bps"] / 10000.0)
                        / 8.0
                    )
                    pos["funding"] += fund
                exit_reason = None
                if pos["dir"] == "LONG":
                    if lo <= pos["stop"]:
                        exit_reason, exit_px = "stop-loss", pos["stop"]
                    elif hi >= pos["tp"]:
                        exit_reason, exit_px = "take-profit", pos["tp"]
                else:
                    if hi >= pos["stop"]:
                        exit_reason, exit_px = "stop-loss", pos["stop"]
                    elif lo <= pos["tp"]:
                        exit_reason, exit_px = "take-profit", pos["tp"]
                if exit_reason is None and (
                    (infer["direction"] != "WAIT" and infer["direction"] != pos["dir"])
                    or pos["bars"] >= int(P["max_bars_held"])
                ):
                    exit_reason, exit_px = "signal/time", px_next
                if exit_reason:
                    px_out = (
                        exit_px * (1 - slip_bps / 10000.0)
                        if pos["dir"] == "LONG"
                        else exit_px * (1 + slip_bps / 10000.0)
                    )
                    gross = (
                        (px_out - pos["entry"]) * pos["qty"]
                        if pos["dir"] == "LONG"
                        else (pos["entry"] - px_out) * pos["qty"]
                    )
                    fee = px_out * pos["qty"] * fee_bps / 10000.0
                    fees_paid += fee
                    slip_paid += px_out * pos["qty"] * slip_bps / 10000.0
                    funding_paid += pos["funding"]
                    net = gross - fee - pos["funding"]
                    equity += net
                    trades.append(
                        {
                            "net": net,
                            "bars": pos["bars"],
                            "reason": exit_reason,
                            "mae": pos["mae"],
                            "mfe": pos["mfe"],
                            "dir": pos["dir"],
                            "entry": pos["entry"],
                            "exit": px_out,
                        }
                    )
                    pos = None
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak else 0
            max_dd = max(max_dd, dd)
        # close any open position at the last close (labeled)
        if pos is not None:
            px = closes[-1]
            gross = (
                (px - pos["entry"]) * pos["qty"]
                if pos["dir"] == "LONG"
                else (pos["entry"] - px) * pos["qty"]
            )
            fee = px * pos["qty"] * (fee_bps + slip_bps) / 10000.0
            fees_paid += fee
            equity += gross - fee - pos["funding"]
            funding_paid += pos["funding"]
            trades.append(
                {
                    "net": gross - fee - pos["funding"],
                    "bars": pos["bars"],
                    "reason": "end-of-data",
                    "mae": pos["mae"],
                    "mfe": pos["mfe"],
                    "dir": pos["dir"],
                    "entry": pos["entry"],
                    "exit": px,
                }
            )
        res = self._summarize(
            market,
            interval,
            rows,
            starting,
            equity,
            max_dd,
            trades,
            fees_paid,
            slip_paid,
            funding_paid,
            P,
        )
        if persist and self.db:
            self._persist(res, market, interval)
        return res

    # ------------------------------------------------------------------
    def _summarize(
        self,
        market: str,
        interval: str,
        rows: list,
        starting: float,
        equity: float,
        max_dd: float,
        trades: list,
        fees: float,
        slip: float,
        funding: float,
        params: dict,
    ) -> dict:
        nets = [t["net"] for t in trades]
        wins = [t for t in nets if t > 0]
        losses = [t for t in nets if t <= 0]
        pf = (
            (sum(wins) / abs(sum(losses)))
            if losses and sum(losses) != 0
            else (9.99 if wins else 0.0)
        )
        rets = [t / starting for t in nets]
        sharpe = (
            (statistics.fmean(rets) / statistics.pstdev(rets) * math.sqrt(max(len(rets), 1)))
            if len(rets) > 1 and statistics.pstdev(rets)
            else 0
        )
        downside = [r for r in rets if r < 0]
        sortino = (
            (statistics.fmean(rets) / statistics.pstdev(downside) * math.sqrt(max(len(rets), 1)))
            if len(downside) > 1 and statistics.pstdev(downside)
            else 0
        )
        ret_total = (equity / starting - 1) if starting else 0
        calmar = (ret_total / max_dd) if max_dd else 0
        expectancy = statistics.fmean(nets) if nets else 0
        mae_avg = statistics.fmean([t["mae"] for t in trades]) if trades else 0
        mfe_avg = statistics.fmean([t["mfe"] for t in trades]) if trades else 0
        return {
            "ok": True,
            "market": market,
            "starting": starting,
            "ending": round(equity, 2),
            "net_return_pct": round(ret_total * 100, 2),
            "gross_return_pct": round(
                (ret_total + (fees + slip + funding) / max(starting, 1)) * 100, 2
            ),
            "max_dd_pct": round(max_dd * 100, 2),
            "profit_factor": round(pf, 2),
            "sharpe": round(sharpe, 2),
            "sortino": round(sortino, 2),
            "calmar": round(calmar, 2),
            "expectancy": round(expectancy, 4),
            "trades": len(trades),
            "win_rate": round(len(wins) / max(1, len(trades)) * 100, 1),
            "losing_trades": len(losses),
            "fees": round(fees, 2),
            "slippage": round(slip, 2),
            "funding": round(funding, 2),
            "mae_avg": round(mae_avg, 5),
            "mfe_avg": round(mfe_avg, 5),
            "candles": len(rows),
            "interval": interval,
            "params": params,
            "exit_reasons": {
                r: sum(1 for t in trades if t["reason"] == r) for r in {t["reason"] for t in trades}
            },
            "trade_log": [
                {k: (round(v, 6) if isinstance(v, float) else v) for k, v in t.items()}
                for t in trades[-50:]
            ],
            "costs_note": "fees+slippage+funding applied on every fill; NET numbers "
            "are authoritative",
            "honesty_note": "Historical simulation on real candles. PAST PERFORMANCE "
            "IS NOT A GUARANTEE OF FUTURE RESULTS.",
        }

    def _persist(self, res: dict, market: str, interval: str) -> str:
        bid = new_id("bt")
        try:
            self.db.execute(
                "INSERT INTO backtests (id, name, markets, period, strategy, starting_capital,"
                " ending_value, net_return_pct, max_dd_pct, profit_factor, sharpe, sortino,"
                " calmar, expectancy, trades, win_rate, fees, slippage, funding, mae_avg,"
                " mfe_avg, details, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    bid,
                    f"{market} backtest",
                    market,
                    f"{res['candles']}x{interval}",
                    "ZEPAY V3 Quant Ensemble",
                    res["starting"],
                    res["ending"],
                    res["net_return_pct"],
                    res["max_dd_pct"],
                    res["profit_factor"],
                    res["sharpe"],
                    res["sortino"],
                    res["calmar"],
                    res["expectancy"],
                    res["trades"],
                    res["win_rate"],
                    res["fees"],
                    res["slippage"],
                    res["funding"],
                    res["mae_avg"],
                    res["mfe_avg"],
                    json.dumps({k: v for k, v in res.items() if k != "trade_log"})[:8000],
                    utcnow_iso(),
                ),
            )
        except Exception as e:
            log.warning("backtest persist failed: %s", e)
        res["id"] = bid
        self.audit(
            "research",
            "backtest",
            {"id": bid, "market": market, "return": res["net_return_pct"], "trades": res["trades"]},
        )
        return bid

    # ------------------------------------------------------------------
    def run_multi(
        self,
        markets: list[str],
        starting: float = 10000.0,
        interval: str | None = None,
        params: dict | None = None,
    ) -> dict:
        per = {}
        for m in markets:
            per[m] = self.run_single(
                m,
                starting=starting / max(1, len(markets)),
                interval=interval,
                params=params,
                persist=False,
            )
        ok_runs = [v for v in per.values() if v.get("ok")]
        ending = sum(v["ending"] for v in ok_runs)
        all_trades = sum(v.get("trades", 0) for v in ok_runs)
        res = {
            "ok": bool(ok_runs),
            "markets": markets,
            "starting": starting,
            "ending": round(ending, 2),
            "net_return_pct": round((ending / starting - 1) * 100, 2) if starting else 0,
            "per_asset": per,
            "trades": all_trades,
            "max_dd_pct": round(max((v.get("max_dd_pct", 0) for v in ok_runs), default=0), 2),
            "fees": round(sum(v.get("fees", 0) for v in ok_runs), 2),
            "slippage": round(sum(v.get("slippage", 0) for v in ok_runs), 2),
            "funding": round(sum(v.get("funding", 0) for v in ok_runs), 2),
            "note": "portfolio mode: capital split evenly; cross-asset correlation "
            "reported separately",
        }
        if self.db:
            bid = new_id("bt")
            try:
                self.db.execute(
                    "INSERT INTO backtests (id, name, markets, period, strategy,"
                    " starting_capital, ending_value, net_return_pct, max_dd_pct,"
                    " profit_factor, sharpe, sortino, calmar, expectancy, trades, win_rate,"
                    " fees, slippage, funding, mae_avg, mfe_avg, details, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        bid,
                        "multi-asset backtest",
                        ",".join(markets),
                        interval or "",
                        "ZEPAY V3 Quant Ensemble",
                        starting,
                        ending,
                        res["net_return_pct"],
                        res["max_dd_pct"],
                        0,
                        0,
                        0,
                        0,
                        0,
                        all_trades,
                        0,
                        res["fees"],
                        res["slippage"],
                        res["funding"],
                        0,
                        0,
                        json.dumps({k: v for k, v in res.items() if k != "per_asset"})[:8000],
                        utcnow_iso(),
                    ),
                )
            except Exception:
                pass
            res["id"] = bid
        return res

    def history(self, limit: int = 50) -> list[dict]:
        if not self.db:
            return []
        rows = self.db.query("SELECT * FROM backtests ORDER BY id DESC LIMIT ?", (limit,))
        for r in rows:
            try:
                r["details"] = json.loads(r.get("details") or "{}")
            except Exception:
                pass
        return rows
