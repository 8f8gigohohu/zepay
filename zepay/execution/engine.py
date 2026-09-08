"""Execution engine (§32/§33) — the ONLY path to any order.

One engine, every venue, every strategy. Orders carry client_order_id,
exchange_order_id, strategy_id, model_id, decision_id, risk_decision_id.
Lifecycle runs through the OrderStateMachine; UNKNOWN outcomes are recorded
and reconciled — never blindly resubmitted.

Stage behavior (§22):
  PAPER        — real prices/books, simulated fills + balances (labeled)
  SHADOW       — decisions recorded; fills simulated into a SHADOW ledger
                 that never touches PAPER/LIVE balances; nothing is sent out
  LIMITED_LIVE — real orders through the venue adapter under hard caps
  FULL_LIVE    — real orders, standard limits

Live activation additionally requires `live_enabled` (set only by the
multi-step live gate) — the stage alone never executes real orders.
"""

from __future__ import annotations

import logging
import secrets as pysecrets
import threading
import time
from collections import defaultdict

from zepay.core.domain import OperationalStage, OrderStatus, OrderType
from zepay.core.events import BUS, E
from zepay.core.ids import client_order_id, idempotency_key, new_id
from zepay.core.util import safe_float, ts_ms, utcnow_iso
from zepay.execution.order_state import OrderStateMachine

log = logging.getLogger("zepay.execution")


class ExecutionEngine:
    def __init__(
        self,
        storage,
        config,
        collector,
        universe,
        accounts,
        portfolio,
        risk,
        cost_model,
        paper,
        quality,
        registry,
        audit_fn,
        alerts=None,
    ):
        self.db = storage
        self.config = config
        self.collector = collector
        self.universe = universe
        self.accounts = accounts
        self.portfolio = portfolio
        self.risk = risk
        self.costs = cost_model
        self.paper = paper
        self.quality = quality
        self.registry = registry
        self.audit = audit_fn
        self.alerts = alerts
        self.sm = OrderStateMachine(storage, audit_fn)
        self._locks = defaultdict(threading.Lock)
        self._live_block = None  # set when an order outcome is UNKNOWN

    # ------------------------------------------------------------------
    # order record helpers
    # ------------------------------------------------------------------
    def _new_order(
        self,
        market: str,
        venue: str,
        side: str,
        direction: str,
        qty: float,
        price: float,
        order_type: str,
        mode: str,
        strategy: str,
        reason: str,
        idem: str,
        decision_id=None,
        risk_decision_id=None,
        model_id=None,
        limit_price=None,
        stop_price=None,
        tif="GTC",
        coid=None,
    ) -> str:
        oid = new_id("ord")
        coid = coid or client_order_id(market, side)
        now = utcnow_iso()
        self.db.execute(
            "INSERT INTO orders (id, client_order_id, venue, market, side, direction, qty,"
            " filled_qty, price, limit_price, stop_price, order_type, time_in_force, status,"
            " mode, strategy, model_version, model_id, decision_id, risk_decision_id,"
            " idempotency_key, reason, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                oid,
                coid,
                venue,
                market,
                side,
                direction,
                qty,
                0.0,
                price,
                limit_price,
                stop_price,
                order_type,
                tif,
                OrderStatus.CREATED.value,
                mode,
                strategy,
                model_id or "",
                model_id or "",
                decision_id,
                risk_decision_id,
                idem,
                (reason or "")[:1000],
                now,
                now,
            ),
        )
        BUS.publish(
            E.ORDER_CREATED,
            {
                "order_id": oid,
                "market": market,
                "venue": venue,
                "side": side,
                "qty": qty,
                "mode": mode,
            },
            source="execution",
        )
        return oid

    def _record_fill(
        self,
        oid: str,
        venue: str,
        market: str,
        side: str,
        qty: float,
        price: float,
        fee: float,
        slip_bps: float,
        pnl: float = 0.0,
        maker: bool = False,
        latency_ms: int = 0,
    ) -> str:
        fid = new_id("fill")
        self.db.execute(
            "INSERT INTO fills (id, order_id, venue, market, side, qty, price, fee,"
            " fee_asset, slippage, maker, pnl, latency_ms, filled_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                fid,
                oid,
                venue,
                market,
                side,
                qty,
                price,
                fee,
                "",
                slip_bps,
                int(maker),
                pnl,
                latency_ms,
                utcnow_iso(),
            ),
        )
        BUS.publish(
            E.ORDER_FILLED,
            {"order_id": oid, "market": market, "side": side, "qty": qty, "price": price},
            source="execution",
        )
        return fid

    def _open_position(
        self,
        market: str,
        venue: str,
        mode: str,
        direction: str,
        qty: float,
        price: float,
        stop=None,
        tp=None,
        confidence=0.0,
        strategy="",
        decision_id=None,
        model_id=None,
    ) -> str:
        pid = new_id("pos")
        self.db.execute(
            "INSERT INTO positions (id, venue, mode, market, direction, qty, entry_price,"
            " current_price, stop_loss, take_profit, exposure, unrealized_pnl,"
            " realized_pnl, confidence, strategy, model_version, decision_id, opened_at,"
            " updated_at, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                pid,
                venue,
                mode,
                market,
                direction,
                qty,
                price,
                price,
                stop,
                tp,
                qty * price,
                0,
                0,
                confidence,
                strategy,
                model_id or "",
                decision_id,
                utcnow_iso(),
                utcnow_iso(),
                "OPEN",
            ),
        )
        BUS.publish(
            E.POSITION_CHANGED,
            {
                "position_id": pid,
                "market": market,
                "direction": direction,
                "qty": qty,
                "action": "OPENED",
            },
            source="execution",
        )
        return pid

    # ------------------------------------------------------------------
    # stage / mode resolution
    # ------------------------------------------------------------------
    def effective_mode(self) -> str:
        stage = self.config.get("operational_stage", "PAPER")
        if stage == OperationalStage.SHADOW.value:
            return "SHADOW"
        if stage in (
            OperationalStage.LIMITED_LIVE.value,
            OperationalStage.FULL_LIVE.value,
        ) and self.config.get("live_enabled"):
            return "LIVE"
        return "PAPER"

    # ------------------------------------------------------------------
    # pre-flight + routing
    # ------------------------------------------------------------------
    def place(
        self,
        opp,
        size: dict,
        f,
        mode: str | None = None,
        strategy: str | None = None,
        idempotency: str | None = None,
        decision_id: str | None = None,
        risk_decision_id: str | None = None,
        model_id: str | None = None,
    ) -> dict:
        mode = mode or self.effective_mode()
        if mode == "LIVE" and not self.config.get("live_enabled"):
            return {
                "ok": False,
                "error": "Live trading is disabled. Complete the Live " "Trading gate first.",
            }
        if self.config.get("kill_switch") or self.config.get("trading_halted"):
            return {"ok": False, "error": "Trading halted — order refused (fail-safe)."}
        if mode == "LIVE" and self._live_block:
            return {
                "ok": False,
                "error": f"Live trading blocked pending reconciliation: {self._live_block}",
            }
        if (size or {}).get("reject"):
            return {
                "ok": False,
                "error": "Order rejected by sizer: " + size.get("reject_reason", "below minimum"),
            }
        if not size or safe_float(size.get("qty")) <= 0:
            return {"ok": False, "error": "Zero quantity after precision/min-size rules."}
        allowed, why = self.risk.entries_allowed()
        if not allowed and mode != "SHADOW":
            return {"ok": False, "error": why}
        if mode == "SHADOW" and not self.risk.stage_caps()["execute"]:
            pass  # shadow records hypothetical fills — allowed by definition
        key = idempotency or idempotency_key(
            mode, opp.symbol, opp.direction, decision_id or str(ts_ms()), pysecrets.token_hex(3)
        )
        with self._locks[opp.symbol]:
            dup = self.db.query_one("SELECT id FROM orders WHERE idempotency_key=?", (key,))
            if dup:
                return {"ok": False, "error": "Duplicate order (idempotency key used)."}
            if self.collector.is_stale(opp.symbol):
                self.risk._event("execution", opp.symbol, "REJECTED", "Stale price at execution")
                return {
                    "ok": False,
                    "error": "Stale price — execution aborted (REAL DATA REQUIRED).",
                }
            ex = self.db.query_one(
                "SELECT id FROM positions WHERE market=? AND status='OPEN' AND mode=?",
                (opp.symbol, mode if mode != "SHADOW" else "SHADOW"),
            )
            if ex:
                return {"ok": False, "error": f"Position already open on {opp.symbol}."}
            acct_mode = "PAPER" if mode in ("PAPER", "SHADOW") else "LIVE"
            acct = self.accounts.get(acct_mode)
            equity = safe_float((acct or {}).get("equity", 0))
            if (
                mode != "SHADOW"
                and size["notional"]
                > equity * safe_float(self.config.get("max_position_pct", 0.2)) * 1.001
            ):
                return {"ok": False, "error": "Position exceeds max size at execution."}
            if mode == "PAPER":
                return self._place_paper(
                    opp, size, f, strategy, key, decision_id, risk_decision_id, model_id
                )
            if mode == "SHADOW":
                return self._place_shadow(
                    opp, size, f, strategy, key, decision_id, risk_decision_id, model_id
                )
            return self._place_live(
                opp, size, f, strategy, key, decision_id, risk_decision_id, model_id
            )

    # ------------------------------------------------------------------
    # PAPER (real data, simulated fills)
    # ------------------------------------------------------------------
    def _place_paper(
        self, opp, size, f, strategy, key, decision_id, risk_decision_id, model_id
    ) -> dict:
        t_expected = time.time()
        fill, err = self.paper.market_fill(opp.symbol, opp.direction, size["qty"])
        if not fill:
            return {"ok": False, "error": f"Paper fill failed: {err}"}
        latency = int((time.time() - t_expected) * 1000) + int(fill.get("latency_ms", 0))
        side = "BUY" if opp.direction == "LONG" else "SELL"
        oid = self._new_order(
            opp.symbol,
            opp.venue,
            side,
            opp.direction,
            size["qty"],
            fill["price"],
            OrderType.MARKET.value,
            "PAPER",
            strategy or opp.strategy,
            "; ".join(opp.reasons),
            key,
            decision_id,
            risk_decision_id,
            model_id,
        )
        self.sm.transition(oid, OrderStatus.VALIDATED.value, "pre-flight passed", opp.venue)
        self.sm.transition(oid, OrderStatus.SUBMITTED.value, "paper submission", opp.venue)
        status = OrderStatus.PARTIALLY_FILLED.value if fill["partial"] else OrderStatus.FILLED.value
        self.sm.transition(
            oid,
            status,
            "paper fill vs real book",
            opp.venue,
            extra_sets={"filled_qty": fill["filled_qty"], "avg_fill_price": fill["price"]},
        )
        self._record_fill(
            oid,
            opp.venue,
            opp.symbol,
            side,
            fill["filled_qty"],
            fill["price"],
            fill["fee"],
            fill["slippage_bps"],
            maker=False,
            latency_ms=latency,
        )
        self.quality.record(
            oid,
            opp.venue,
            opp.symbol,
            "PAPER",
            fill.get("expected_price") or fill["price"],
            fill["price"],
            size["qty"],
            fill["filled_qty"],
            latency,
            fill.get("spread_bps", 0),
            maker=False,
        )
        qty = fill["filled_qty"]
        if qty <= 0:
            return {"ok": False, "error": "No fillable quantity."}
        self._open_position(
            opp.symbol,
            opp.venue,
            "PAPER",
            opp.direction,
            qty,
            fill["price"],
            size.get("stop"),
            size.get("take_profit"),
            opp.confidence,
            strategy or opp.strategy,
            decision_id,
            model_id,
        )
        acct = self.accounts.get("PAPER")
        self.accounts.update(
            "PAPER",
            realized_pnl=safe_float(acct.get("realized_pnl")) - fill["fee"],
            day_pnl=safe_float(acct.get("day_pnl")) - fill["fee"],
            fees_paid=safe_float(acct.get("fees_paid")) + fill["fee"],
        )
        self.portfolio.revalue("PAPER")
        self.audit(
            "trading",
            "paper_order_filled",
            {
                "order": oid,
                "market": opp.symbol,
                "dir": opp.direction,
                "qty": qty,
                "price": fill["price"],
                "fee": round(fill["fee"], 4),
                "partial": fill["partial"],
                "remaining": fill["remaining_qty"],
                "book_source": fill.get("book_source"),
            },
        )
        return {
            "ok": True,
            "order_id": oid,
            "fill_price": fill["price"],
            "fee": fill["fee"],
            "filled_qty": qty,
            "partial": fill["partial"],
            "remaining_qty": fill["remaining_qty"],
            "mode": "PAPER",
            "simulated": True,
            "note": "PAPER fill — simulated execution against REAL market data",
        }

    # ------------------------------------------------------------------
    # SHADOW (hypothetical live decisions — nothing executes anywhere)
    # ------------------------------------------------------------------
    def _place_shadow(
        self, opp, size, f, strategy, key, decision_id, risk_decision_id, model_id
    ) -> dict:
        fill, err = self.paper.market_fill(opp.symbol, opp.direction, size["qty"])
        if not fill:
            return {"ok": False, "error": f"Shadow fill simulation failed: {err}"}
        side = "BUY" if opp.direction == "LONG" else "SELL"
        oid = self._new_order(
            opp.symbol,
            opp.venue,
            side,
            opp.direction,
            size["qty"],
            fill["price"],
            OrderType.MARKET.value,
            "SHADOW",
            strategy or opp.strategy,
            "SHADOW hypothetical live decision; " + "; ".join(opp.reasons),
            key,
            decision_id,
            risk_decision_id,
            model_id,
        )
        self.sm.transition(oid, OrderStatus.VALIDATED.value, "shadow pre-flight", opp.venue)
        self.sm.transition(
            oid, OrderStatus.SUBMITTED.value, "shadow (not sent anywhere)", opp.venue
        )
        self.sm.transition(
            oid,
            OrderStatus.FILLED.value,
            "shadow hypothetical fill",
            opp.venue,
            extra_sets={"filled_qty": fill["filled_qty"], "avg_fill_price": fill["price"]},
        )
        self._record_fill(
            oid,
            opp.venue,
            opp.symbol,
            side,
            fill["filled_qty"],
            fill["price"],
            fill["fee"],
            fill["slippage_bps"],
        )
        self._open_position(
            opp.symbol,
            opp.venue,
            "SHADOW",
            opp.direction,
            fill["filled_qty"],
            fill["price"],
            size.get("stop"),
            size.get("take_profit"),
            opp.confidence,
            strategy or opp.strategy,
            decision_id,
            model_id,
        )
        self.audit(
            "trading",
            "shadow_decision_recorded",
            {
                "order": oid,
                "market": opp.symbol,
                "dir": opp.direction,
                "hypothetical_price": fill["price"],
                "note": "SHADOW — no order left this machine, no balance changed",
            },
        )
        return {
            "ok": True,
            "order_id": oid,
            "mode": "SHADOW",
            "simulated": True,
            "fill_price": fill["price"],
            "fee": fill["fee"],
            "filled_qty": fill["filled_qty"],
            "partial": fill["partial"],
            "note": "SHADOW: hypothetical live decision recorded against real "
            "market state. NOTHING was executed and no balance changed.",
        }

    # ------------------------------------------------------------------
    # LIVE (real signed orders; confirmation required; UNKNOWN → reconcile)
    # ------------------------------------------------------------------
    def _place_live(
        self, opp, size, f, strategy, key, decision_id, risk_decision_id, model_id
    ) -> dict:
        venue = self.config.get("exchange") or "binance_spot"
        adapter = self.registry.get(venue)
        if adapter is None or not adapter.has_credentials():
            return {"ok": False, "error": f"venue {venue} has no credentials configured"}
        ok, why = self.risk.venue_allowed(venue, "LIVE")
        if not ok:
            return {"ok": False, "error": why}
        sym = self.universe.exchange_symbol(opp.symbol)
        side = "BUY" if opp.direction == "LONG" else "SELL"
        qty = size["qty"]
        coid = client_order_id(opp.symbol, side)
        oid = self._new_order(
            opp.symbol,
            venue,
            side,
            opp.direction,
            qty,
            opp.price,
            OrderType.MARKET.value,
            "LIVE",
            strategy or opp.strategy,
            "; ".join(opp.reasons),
            key,
            decision_id,
            risk_decision_id,
            model_id,
            coid=coid,
        )
        self.sm.transition(oid, OrderStatus.VALIDATED.value, "live pre-flight", venue)
        self.sm.transition(oid, OrderStatus.SUBMITTED.value, "sending to venue", venue)
        t0 = time.time()
        try:
            res = adapter.place_order(sym, side, qty, order_type="MARKET", client_order_id=coid)
        except Exception as e:
            # §33: submission outcome UNKNOWN — record, block live, reconcile
            self.sm.transition(
                oid,
                OrderStatus.UNKNOWN.value,
                f"submission error: {e}"[:300],
                venue,
                extra_sets={"reject_reason": str(e)[:300]},
            )
            self._live_block = (
                f"order {oid} outcome UNKNOWN ({str(e)[:120]}) — "
                "reconciliation required before further live orders"
            )
            self.config.update({"live_block_reason": self._live_block})
            self.quality.record(
                oid,
                venue,
                opp.symbol,
                "LIVE",
                opp.price,
                0,
                qty,
                0,
                int((time.time() - t0) * 1000),
                f.spread_bps,
                False,
                rejected=True,
            )
            if self.alerts:
                self.alerts.notify(
                    "CRITICAL",
                    "LIVE order UNKNOWN",
                    f"{oid} on {opp.symbol}: {str(e)[:150]}. Live blocked "
                    "pending reconciliation.",
                )
            self.audit("execution", "live_order_unknown", {"order": oid, "error": str(e)[:300]})
            return {"ok": False, "order_id": oid, "error": self._live_block, "state": "UNKNOWN"}
        latency = int((time.time() - t0) * 1000)
        ex_oid = str(res.get("orderId", ""))
        # VERIFY against the exchange — success is never assumed
        try:
            confirm = adapter.get_order(
                sym, order_id=ex_oid or None, client_order_id=None if ex_oid else coid
            )
        except Exception as e:
            self.sm.transition(
                oid,
                OrderStatus.UNKNOWN.value,
                f"confirmation failed: {e}"[:300],
                venue,
                extra_sets={"exchange_order_id": ex_oid},
            )
            self._live_block = (
                f"order {oid} submitted (exchange id {ex_oid}) but "
                "confirmation failed — reconciliation required"
            )
            self.config.update({"live_block_reason": self._live_block})
            if self.alerts:
                self.alerts.notify("CRITICAL", "LIVE order unconfirmed", self._live_block)
            return {
                "ok": True,
                "order_id": oid,
                "confirmed": False,
                "state": "UNKNOWN",
                "warning": "order submitted but confirmation failed — " "reconciliation required",
            }
        status = str(confirm.get("status", "NEW"))
        executed = safe_float(confirm.get("executedQty"))
        avg = (safe_float(confirm.get("cummulativeQuoteQty")) / executed) if executed else 0
        fee_est = avg * executed * (self.costs.refresh()["fee_bps"] / 10000.0) if executed else 0
        mapping = {
            "NEW": OrderStatus.ACKNOWLEDGED.value,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED.value,
            "FILLED": OrderStatus.FILLED.value,
            "CANCELED": OrderStatus.CANCELLED.value,
            "REJECTED": OrderStatus.REJECTED.value,
            "EXPIRED": OrderStatus.CANCELLED.value,
        }
        new_state = mapping.get(status, OrderStatus.ACKNOWLEDGED.value)
        self.sm.transition(
            oid,
            new_state,
            f"venue status={status}",
            venue,
            force=True,
            extra_sets={
                "exchange_order_id": ex_oid,
                "filled_qty": executed,
                "avg_fill_price": avg or None,
            },
        )
        self.quality.record(
            oid,
            venue,
            opp.symbol,
            "LIVE",
            opp.price,
            avg or opp.price,
            qty,
            executed,
            latency,
            f.spread_bps,
            maker=str(confirm.get("type", "")) == "LIMIT_MAKER",
        )
        if executed > 0:
            self._record_fill(
                oid, venue, opp.symbol, side, executed, avg or 0, fee_est, 0, latency_ms=latency
            )
            if status == "FILLED":
                self._open_position(
                    opp.symbol,
                    venue,
                    "LIVE",
                    opp.direction,
                    executed,
                    avg,
                    size.get("stop"),
                    size.get("take_profit"),
                    opp.confidence,
                    strategy or opp.strategy,
                    decision_id,
                    model_id,
                )
            try:
                self._sync_live_balance()
            except Exception as e:
                log.warning("live balance sync failed: %s", e)
        self.audit(
            "trading",
            "live_order_placed",
            {
                "order": oid,
                "exchange_order": ex_oid,
                "market": opp.symbol,
                "side": side,
                "qty": qty,
                "status": status,
                "executed": executed,
                "avg": avg,
                "latency_ms": latency,
            },
        )
        if self.alerts:
            self.alerts.notify(
                "WARN",
                "LIVE order executed",
                f"{side} {qty} {opp.symbol} @ ~{avg or 'pending'} " f"status={status}",
            )
        return {
            "ok": True,
            "order_id": oid,
            "exchange_order_id": ex_oid,
            "status": status,
            "state": new_state,
            "executed_qty": executed,
            "avg_price": avg,
            "mode": "LIVE",
            "latency_ms": latency,
        }

    def _sync_live_balance(self) -> None:
        venue = self.config.get("exchange") or "binance_spot"
        adapter = self.registry.get(venue)
        acct = adapter.account(timeout=12)
        quote = self.config.get("quote_currency", "USDT")
        ex_bal = {
            b["asset"]: safe_float(b["free"]) + safe_float(b["locked"])
            for b in acct.get("balances", [])
        }
        equity = ex_bal.get(quote, 0.0)
        self.accounts.update("LIVE", equity=equity, available=equity)
        BUS.publish(E.BALANCE_CHANGED, {"mode": "LIVE", "equity": equity}, source="execution")

    # ------------------------------------------------------------------
    # limit orders (paper) + resting order management
    # ------------------------------------------------------------------
    def place_limit_paper(
        self,
        market: str,
        direction: str,
        qty: float,
        limit_price: float,
        strategy: str = "manual",
        decision_id: str | None = None,
    ) -> dict:
        if self.config.get("kill_switch") or self.config.get("trading_halted"):
            return {"ok": False, "error": "Trading halted."}
        if qty <= 0 or limit_price <= 0:
            return {"ok": False, "error": "qty and limit price must be > 0"}
        side = "BUY" if direction == "LONG" else "SELL"
        key = idempotency_key("LIMIT", market, direction, str(ts_ms()), pysecrets.token_hex(3))
        oid = self._new_order(
            market,
            "binance_spot",
            side,
            direction,
            qty,
            limit_price,
            OrderType.LIMIT.value,
            "PAPER",
            strategy,
            f"user limit {side} {qty} @ {limit_price}",
            key,
            decision_id,
            limit_price=limit_price,
        )
        self.sm.transition(oid, OrderStatus.VALIDATED.value, "limit validated", "binance_spot")
        self.sm.transition(
            oid, OrderStatus.ACKNOWLEDGED.value, "resting in paper book", "binance_spot"
        )
        self.audit(
            "trading",
            "paper_limit_placed",
            {"order": oid, "market": market, "side": side, "qty": qty, "limit": limit_price},
        )
        return {"ok": True, "order_id": oid}

    def check_open_orders(self) -> list[dict]:
        """Fill/cancel resting paper LIMIT orders against REAL price moves."""
        out = []
        rows = self.db.query(
            "SELECT * FROM orders WHERE status IN ('ACKNOWLEDGED','PARTIALLY_FILLED')"
            " AND order_type='LIMIT' AND mode='PAPER'"
        )
        for o in rows:
            if not self.paper.limit_cross(o["market"], o["side"], safe_float(o["limit_price"])):
                continue
            lim = safe_float(o["limit_price"])
            qty = safe_float(o["qty"])
            comps = self.costs.refresh()
            fee = lim * qty * (comps["fee_bps"] / 10000.0)
            oid = o["id"]
            self.sm.transition(
                oid,
                OrderStatus.FILLED.value,
                "limit crossed by real price",
                o.get("venue", ""),
                force=True,
                extra_sets={"filled_qty": qty, "avg_fill_price": lim},
            )
            self._record_fill(
                oid,
                o.get("venue", "binance_spot"),
                o["market"],
                o["side"],
                qty,
                lim,
                fee,
                0,
                maker=True,
            )
            ex = self.db.query_one(
                "SELECT id FROM positions WHERE market=? AND status='OPEN' AND mode='PAPER'",
                (o["market"],),
            )
            if not ex:
                self._open_position(
                    o["market"],
                    o.get("venue", "binance_spot"),
                    "PAPER",
                    o["direction"],
                    qty,
                    lim,
                    None,
                    None,
                    0,
                    o.get("strategy") or "manual",
                )
                acct = self.accounts.get("PAPER")
                self.accounts.update(
                    "PAPER",
                    realized_pnl=safe_float(acct.get("realized_pnl")) - fee,
                    fees_paid=safe_float(acct.get("fees_paid")) + fee,
                )
            out.append({"order": oid, "market": o["market"], "filled_at": lim})
        return out

    def cancel_order(self, order_id: str, mode: str = "PAPER") -> dict:
        o = self.db.query_one("SELECT * FROM orders WHERE id=?", (order_id,))
        if not o:
            return {"ok": False, "error": "order not found"}
        if o["status"] not in (
            OrderStatus.CREATED.value,
            OrderStatus.VALIDATED.value,
            OrderStatus.SUBMITTED.value,
            OrderStatus.ACKNOWLEDGED.value,
            OrderStatus.PARTIALLY_FILLED.value,
        ):
            return {"ok": False, "error": f"cannot cancel order in status {o['status']}"}
        if mode == "LIVE" and o.get("exchange_order_id"):
            adapter = self.registry.get(self.config.get("exchange") or "binance_spot")
            try:
                self.sm.transition(
                    order_id, OrderStatus.CANCEL_REQUESTED.value, "user cancel", o.get("venue", "")
                )
                adapter.cancel_order(
                    self.universe.exchange_symbol(o["market"]), order_id=o["exchange_order_id"]
                )
            except Exception as e:
                return {"ok": False, "error": f"exchange cancel failed: {e}"}
        self.sm.transition(
            order_id, OrderStatus.CANCELLED.value, "cancelled", o.get("venue", ""), force=True
        )
        self.audit("trading", "order_cancelled", {"order": order_id, "mode": mode}, actor="user")
        return {"ok": True}

    def cancel_all_open(self, reason: str = "") -> int:
        rows = self.db.query(
            "SELECT id, mode FROM orders WHERE status IN ('CREATED','VALIDATED','SUBMITTED',"
            "'ACKNOWLEDGED','PARTIALLY_FILLED','CANCEL_REQUESTED')"
        )
        n = 0
        for o in rows:
            r = self.cancel_order(o["id"], mode=o.get("mode", "PAPER"))
            if r.get("ok"):
                n += 1
        if n:
            self.audit("execution", "cancel_all", {"cancelled": n, "reason": reason})
        return n

    def emergency_flatten(self, policy: str = "flatten") -> int:
        """Kill-switch flatten (§39). 'reduce' halves positions; 'flatten'
        closes everything. Uses the same close_position path (real prices)."""
        modes = (
            ("PAPER", "SHADOW")
            if not self.config.get("live_enabled")
            else ("PAPER", "SHADOW", "LIVE")
        )
        n = 0
        for mode in modes:
            for p in self.portfolio.positions(mode):
                if policy == "reduce":
                    half = safe_float(p["qty"]) / 2
                    if half <= 0:
                        continue
                    r = self.close_position(
                        p["market"], mode, reason=f"emergency-{policy}", qty_override=half
                    )
                else:
                    r = self.close_position(p["market"], mode, reason=f"emergency-{policy}")
                if r.get("ok"):
                    n += 1
        if n:
            self.audit("execution", "emergency_flatten", {"closed": n, "policy": policy})
        return n

    # ------------------------------------------------------------------
    # close position (SL/TP/manual/emergency) with trade record incl. MAE/MFE
    # ------------------------------------------------------------------
    def close_position(
        self,
        market: str,
        mode: str = "PAPER",
        reason: str = "manual",
        qty_override: float | None = None,
    ) -> dict:
        with self._locks[market]:
            pos = self.db.query_one(
                "SELECT * FROM positions WHERE market=? AND status='OPEN' AND mode=?",
                (market, mode),
            )
            if not pos:
                return {"ok": False, "error": "No open position."}
            direction = pos["direction"]
            qty = safe_float(qty_override) if qty_override else safe_float(pos["qty"])
            qty = min(qty, safe_float(pos["qty"]))
            entry = safe_float(pos["entry_price"])
            fill = None
            if mode == "LIVE":
                adapter = self.registry.get(self.config.get("exchange") or "binance_spot")
                sym = self.universe.exchange_symbol(market)
                side = "SELL" if direction == "LONG" else "BUY"
                coid = client_order_id(market, side)
                try:
                    res = adapter.place_order(
                        sym, side, qty, order_type="MARKET", client_order_id=coid
                    )
                    confirm = adapter.get_order(sym, order_id=str(res.get("orderId")))
                    executed = safe_float(confirm.get("executedQty"))
                    avg = (
                        safe_float(confirm.get("cummulativeQuoteQty")) / executed if executed else 0
                    )
                    px = avg or entry
                    fee = avg * executed * (self.costs.refresh()["fee_bps"] / 10000.0)
                except Exception as e:
                    return {"ok": False, "error": f"live close failed: {e}"}
            else:
                fill, err = self.paper.market_fill(
                    market, "SHORT" if direction == "LONG" else "LONG", qty
                )
                if not fill:
                    return {"ok": False, "error": f"Quote failed: {err}"}
                px = fill["price"]
                fee = fill["fee"]
            gross = (px - entry) * qty if direction == "LONG" else (entry - px) * qty
            net = gross - fee
            now = utcnow_iso()
            # MAE/MFE + bars held from REAL candles since entry (no fabrication)
            mae = mfe = 0.0
            bars = 0
            try:
                kl = self.collector.get_klines(market) or []
                opened = pos.get("opened_at") or ""
                if kl and opened:
                    import datetime as _dt

                    t_open = _dt.datetime.strptime(opened, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                        tzinfo=_dt.UTC
                    )
                    ms_open = int(t_open.timestamp() * 1000)
                    fwd = [r for r in kl if r["open_time"] >= ms_open]
                    if fwd:
                        highs = [r["h"] for r in fwd]
                        lows = [r["l"] for r in fwd]
                        mfe = (
                            (max(highs) - entry) / entry
                            if direction == "LONG"
                            else (entry - min(lows)) / entry
                        )
                        mae = (
                            (min(lows) - entry) / entry
                            if direction == "LONG"
                            else (entry - max(highs)) / entry
                        )
                        bars = len(fwd)
            except Exception:
                pass
            remaining = safe_float(pos["qty"]) - qty
            if remaining > 1e-12:
                self.db.execute(
                    "UPDATE positions SET qty=?, current_price=?, exposure=?,"
                    " updated_at=? WHERE id=?",
                    (remaining, px, remaining * px, now, pos["id"]),
                )
            else:
                self.db.execute(
                    "UPDATE positions SET status='CLOSED', qty=0, current_price=?,"
                    " realized_pnl=?, updated_at=? WHERE id=?",
                    (px, net, now, pos["id"]),
                )
                BUS.publish(
                    E.POSITION_CHANGED,
                    {
                        "position_id": pos["id"],
                        "market": market,
                        "action": "CLOSED",
                        "pnl": round(net, 4),
                    },
                    source="execution",
                )
            acct = self.accounts.get("PAPER" if mode in ("PAPER", "SHADOW") else "LIVE")
            if mode != "SHADOW":
                self.accounts.update(
                    "PAPER" if mode == "PAPER" else "LIVE",
                    realized_pnl=safe_float(acct.get("realized_pnl")) + net,
                    day_pnl=safe_float(acct.get("day_pnl")) + net,
                    fees_paid=safe_float(acct.get("fees_paid")) + fee,
                )
            oid = self._new_order(
                market,
                pos.get("venue", ""),
                "SELL" if direction == "LONG" else "BUY",
                direction,
                qty,
                px,
                OrderType.MARKET.value,
                mode,
                pos.get("strategy") or "",
                f"close: {reason}",
                idempotency_key("close", pos["id"], str(qty), str(ts_ms())),
                pos.get("decision_id"),
            )
            self.sm.transition(oid, OrderStatus.VALIDATED.value, "close validated", "")
            self.sm.transition(oid, OrderStatus.SUBMITTED.value, "close submitted", "")
            self.sm.transition(
                oid,
                OrderStatus.FILLED.value,
                f"close filled @ {px}",
                "",
                force=True,
                extra_sets={"filled_qty": qty, "avg_fill_price": px},
            )
            self._record_fill(
                oid,
                pos.get("venue", ""),
                market,
                "SELL" if direction == "LONG" else "BUY",
                qty,
                px,
                fee,
                fill["slippage_bps"] if fill else 0,
                pnl=net,
            )
            try:
                slip_bps = fill["slippage_bps"] if fill else 0
                self.db.execute(
                    "INSERT INTO trades (venue, mode, market, direction, entry_price,"
                    " exit_price, qty, gross_pnl, fees, funding, slippage, net_pnl, mae,"
                    " mfe, bars_held, strategy, model_version, opened_at, closed_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        pos.get("venue", ""),
                        mode,
                        market,
                        direction,
                        entry,
                        px,
                        qty,
                        round(gross, 6),
                        round(fee, 6),
                        0,
                        round(slip_bps, 3),
                        round(net, 6),
                        round(mae, 6),
                        round(mfe, 6),
                        bars,
                        pos.get("strategy", ""),
                        pos.get("model_version", ""),
                        pos.get("opened_at"),
                        now,
                    ),
                )
            except Exception as e:
                log.warning("trade record failed: %s", e)
            if mode != "SHADOW":
                self.portfolio.revalue("PAPER" if mode == "PAPER" else "LIVE")
            self.audit(
                "trading",
                "position_closed",
                {
                    "market": market,
                    "pnl": round(net, 2),
                    "reason": reason,
                    "mode": mode,
                    "qty": qty,
                    "mae": round(mae, 5),
                    "mfe": round(mfe, 5),
                },
            )
            # automatic drawdown halt check (v2 behavior)
            if mode != "SHADOW":
                acct2 = self.accounts.get("PAPER" if mode == "PAPER" else "LIVE")
                dd = safe_float((acct2 or {}).get("drawdown_pct", 0))
                if dd >= safe_float(self.config.get("max_drawdown_halt_pct", 0.15)):
                    self.config.update(
                        {
                            "trading_halted": True,
                            "halt_reason": f"Max drawdown {dd * 100:.2f}% — automatic halt",
                        }
                    )
                    if self.alerts:
                        self.alerts.notify(
                            "CRITICAL", "Trading auto-halted", f"Max drawdown {dd * 100:.2f}%"
                        )
            return {
                "ok": True,
                "pnl": round(net, 2),
                "price": px,
                "mode": mode,
                "mae": round(mae, 5),
                "mfe": round(mfe, 5),
                "bars_held": bars,
            }

    # ------------------------------------------------------------------
    # SL/TP management across open positions
    # ------------------------------------------------------------------
    def manage_positions(self, mode: str | None = None) -> list[dict]:
        exits = []
        modes = (
            [mode]
            if mode
            else ["PAPER", "SHADOW"] + (["LIVE"] if self.config.get("live_enabled") else [])
        )
        for md in modes:
            for p in self.portfolio.positions(md):
                t = self.collector.get_ticker(p["market"])
                px = safe_float(t.get("price")) if t else 0
                if px <= 0:
                    continue
                sl = safe_float(p.get("stop_loss"))
                tp = safe_float(p.get("take_profit"))
                hit = None
                if p["direction"] == "LONG":
                    if sl and px <= sl:
                        hit = "stop-loss"
                    elif tp and px >= tp:
                        hit = "take-profit"
                else:
                    if sl and px >= sl:
                        hit = "stop-loss"
                    elif tp and px <= tp:
                        hit = "take-profit"
                if hit:
                    r = self.close_position(p["market"], md, reason=hit)
                    exits.append({"market": p["market"], "mode": md, "reason": hit, "result": r})
        return exits

    def clear_live_block(self) -> None:
        """Only the reconciler may clear an UNKNOWN-order live block."""
        self._live_block = None
        self.config.update({"live_block_reason": ""})

    def set_live_block(self, reason: str) -> None:
        self._live_block = reason
        self.config.update({"live_block_reason": reason})

    def status(self) -> dict:
        """Operator/API view of execution state — real flags only."""
        return {
            "mode": self.effective_mode(),
            "live_block": self._live_block,
            "live_block_reason": self.config.get("live_block_reason") or "",
            "kill_switch": bool(self.config.get("kill_switch")),
            "trading_halted": bool(self.config.get("trading_halted")),
            "live_enabled": bool(self.config.get("live_enabled")),
            "stage": self.config.get("operational_stage"),
        }
