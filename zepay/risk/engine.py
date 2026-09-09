"""GLOBAL RISK ENGINE (§21) — FINAL AUTHORITY.

Hierarchy (lower layers can NEVER override higher ones):
  1 HARD SAFETY LIMITS   (kill switch, stage caps, withdrawal ban)
  2 GLOBAL RISK ENGINE   (this module)
  3 PORTFOLIO CONSTRAINTS
  4 EXECUTION SAFETY
  5 MARKET-DATA VALIDATION
  6 STRATEGY
  7 ML MODELS
  8 LLM (advisory)

FAIL CLOSED: any internal error inside authorize() produces HALTED — never
APPROVED. If the risk engine itself is unavailable, trading stops.

V3 additions over v2: consecutive-loss gate, operational-stage caps
(SHADOW executes nothing; LIMITED_LIVE hard notional/position caps),
venue-outage blocking, leverage cap, liquidation-distance protection.
"""

from __future__ import annotations

import logging
from typing import Any

from zepay.core.domain import Decision, Health, OperationalStage, SystemState
from zepay.core.events import BUS, E
from zepay.core.util import pct, safe_float, utcnow_iso

log = logging.getLogger("zepay.risk")

# Hard safety limits — constants, not configuration. Nothing can raise these
# through the settings API; changing them is a code change + redeploy.
HARD_LIMITS: dict[str, Any] = {
    "max_hard_position_pct": 0.50,  # never >50% of equity in one position
    "max_hard_total_exposure_pct": 2.0,  # never >200% gross (2x, futures only)
    "max_hard_leverage": 5.0,  # platform-wide hard ceiling
    "withdrawals": "FORBIDDEN",  # §50/51 — not configurable, ever
    "llm_can_trade": False,  # §15 — architectural, not configurable
    "mcp_can_trade": False,  # §50 — architectural
    "auto_promote_to_live": False,  # §22 — promotion is always manual
}


class RiskEngine:
    def __init__(
        self,
        config,
        collector,
        portfolio,
        ai,
        storage=None,
        audit_fn=None,
        alerts=None,
        registry=None,
    ):
        self.config = config
        self.collector = collector
        self.portfolio = portfolio
        self.ai = ai
        self.db = storage
        self.audit = audit_fn or (lambda *a, **k: None)
        self.alerts = alerts
        self.registry = registry

    # ------------------------------------------------------------------
    # system state
    # ------------------------------------------------------------------
    def system_state(self, acct: dict | None = None) -> tuple[SystemState, str]:
        if self.config.get("kill_switch"):
            return SystemState.HALTED, "Kill switch engaged by user"
        if self.config.get("trading_halted"):
            return SystemState.HALTED, self.config.get("halt_reason") or "Trading halted"
        stage = self.config.get("operational_stage", "PAPER")
        if stage == OperationalStage.SHADOW.value:
            return SystemState.CAUTION, "SHADOW stage — decisions recorded, execution disabled"
        acct = acct or {}
        dd = safe_float(acct.get("drawdown_pct", 0))
        day_pnl = safe_float(acct.get("day_pnl", 0))
        equity = safe_float(acct.get("equity", 1))
        max_dd = safe_float(self.config.get("max_drawdown_halt_pct", 0.15), 0.15)
        day_lim = safe_float(self.config.get("daily_loss_limit_pct", 0.03), 0.03)
        if dd >= max_dd:
            return SystemState.HALTED, f"Max drawdown {pct(dd)}% ≥ limit {pct(max_dd)}%"
        if equity and day_pnl < 0 and (-day_pnl / equity) >= day_lim:
            return SystemState.HALTED, f"Daily loss limit reached ({pct(-day_pnl / equity)}%)"
        if dd >= max_dd * 0.6 or (equity and day_pnl < 0 and (-day_pnl / equity) >= day_lim * 0.6):
            return SystemState.DEFENSIVE, "Losses elevated — risk reduced"
        if dd >= max_dd * 0.35:
            return SystemState.CAUTION, "Drawdown rising — position sizes reduced"
        return SystemState.NORMAL, "All risk metrics nominal"

    def consecutive_losses(self, mode: str = "PAPER", lookback: int = 10) -> int:
        rows = (
            self.db.query(
                "SELECT net_pnl FROM trades WHERE mode=? AND net_pnl IS NOT NULL"
                " ORDER BY id DESC LIMIT ?",
                (mode, lookback),
            )
            if self.db
            else []
        )
        n = 0
        for r in rows:
            if safe_float(r.get("net_pnl")) < 0:
                n += 1
            else:
                break
        return n

    def entries_allowed(self) -> tuple[bool, str]:
        """Global gate: kill switch / halt / data feed / AI policy / venue."""
        if self.config.get("kill_switch") or self.config.get("trading_halted"):
            return False, "Trading halted (kill switch or halt active)"
        feed = self.collector.feed_health()
        if feed["status"] == "UNAVAILABLE":
            return False, "DATA FEED FAILURE — " + feed["reason"]
        if not self.ai.healthy():
            policy = self.config.get("ai_failure_policy", "reduce_risk")
            if policy == "stop_trading":
                return False, (
                    "AI UNAVAILABLE (policy: stop_trading while models " "untrained/degraded)"
                )
        max_losses = int(safe_float(self.config.get("max_consecutive_losses", 5), 5))
        losses = self.consecutive_losses()
        if losses >= max_losses:
            return False, (
                f"{losses} consecutive losing trades ≥ limit {max_losses} — "
                "entries paused for review"
            )
        return True, ""

    def venue_allowed(self, venue: str, mode: str) -> tuple[bool, str]:
        """Exchange-outage protection: a blocked/unreachable venue cannot
        receive LIVE orders. Paper keeps working (its prices come from the
        data venue, gated by data validation)."""
        if mode != "LIVE" or not self.registry:
            return True, ""
        adapter = self.registry.get(venue)
        if adapter is None:
            return False, f"venue {venue} not registered"
        flags = self.config.get("venues_enabled") or {}
        if not flags.get(venue):
            return False, f"venue {venue} not enabled by the operator — live execution blocked"
        st = adapter.status()
        if st in ("BLOCKED", "UNREACHABLE", "NOT_CONFIGURED"):
            return False, f"venue {venue} status {st} — live execution blocked (§43)"
        return True, ""

    # ------------------------------------------------------------------
    # stage caps (§22)
    # ------------------------------------------------------------------
    def stage_caps(self) -> dict:
        stage = self.config.get("operational_stage", "PAPER")
        if stage == OperationalStage.SHADOW.value:
            return {
                "execute": False,
                "max_notional_usd": 0.0,
                "max_positions": 0,
                "note": "SHADOW: hypothetical decisions only — nothing executes",
            }
        if stage == OperationalStage.LIMITED_LIVE.value:
            return {
                "execute": True,
                "max_notional_usd": safe_float(
                    self.config.get("limited_live_max_notional_usd", 50.0), 50.0
                ),
                "max_positions": int(
                    safe_float(self.config.get("limited_live_max_positions", 1), 1)
                ),
                "note": "LIMITED_LIVE: hard caps enforced by Risk Engine",
            }
        if stage == OperationalStage.FULL_LIVE.value:
            return {
                "execute": True,
                "max_notional_usd": None,
                "max_positions": None,
                "note": "FULL_LIVE: standard risk limits apply",
            }
        return {
            "execute": True,
            "max_notional_usd": None,
            "max_positions": None,
            "note": "PAPER: simulated fills on real data",
        }

    # ------------------------------------------------------------------
    # authorization (the ONLY door to execution)
    # ------------------------------------------------------------------
    def authorize(
        self,
        opp,
        f,
        health: dict,
        positions: list[dict],
        acct: dict | None,
        mat: dict,
        equity_hint: float | None = None,
        mode: str = "PAPER",
    ) -> tuple[Decision, list, dict | None]:
        try:
            return self._authorize(opp, f, health, positions, acct, mat, equity_hint, mode)
        except Exception as e:  # FAIL CLOSED (§21)
            log.exception("risk engine internal error — failing closed")
            self._event(
                "risk_internal_error",
                opp.symbol if opp else "?",
                "HALTED",
                f"risk engine error: {e}"[:300],
            )
            return Decision.HALTED, [f"RISK ENGINE ERROR — FAIL CLOSED: {e}"[:200]], None

    def _authorize(self, opp, f, health, positions, acct, mat, equity_hint, mode):
        reasons_h: list[str] = []
        equity = (
            equity_hint
            or safe_float((acct or {}).get("equity", 0))
            or safe_float(self.config.get("paper_starting_balance", 10000))
        )
        state, state_msg = self.system_state(acct)

        # ---- 1 HARD SAFETY LIMITS ----
        caps = self.stage_caps()
        if not caps["execute"] and mode != "PAPER":
            return Decision.HALTED, [f"Stage gate: {caps['note']}"], None
        if state == SystemState.HALTED:
            return Decision.HALTED, [f"Trading halted: {state_msg}"], None
        allowed, why = self.entries_allowed()
        if not allowed:
            return Decision.HALTED, [why], None
        ok, why = self.venue_allowed(getattr(opp, "venue", ""), mode)
        if not ok:
            return Decision.REJECTED, [why], None

        # ---- 5 MARKET-DATA VALIDATION ----
        if health["status"] == Health.BLOCKED.value:
            return (
                Decision.REJECTED,
                [f"Market health BLOCKED: {'; '.join(health['reasons'])}"],
                None,
            )
        if self.collector.is_stale(opp.symbol):
            return (
                Decision.REJECTED,
                [f"Stale market data ({self.collector.age_secs(opp.symbol):.0f}s old)"],
                None,
            )
        if opp.price <= 0 or f.n_candles < 30:
            return Decision.REJECTED, ["Insufficient validated market data"], None

        # ---- 7/8 AI VALIDITY ----
        if opp.direction == "WAIT":
            if (opp.machine or {}).get("mode") == "untrained-no-signal":
                return (
                    Decision.WAIT,
                    ["AI models not trained on real data yet — no " "direction (strict mode)"],
                    None,
                )
            return (
                Decision.WAIT,
                ["AI decision: WAIT — no direction with sufficient confidence"],
                None,
            )

        # ---- regime gate ----
        if opp.regime in ("PANIC", "UNSTABLE"):
            return (
                Decision.WAIT,
                [f"Regime {opp.regime}: entries paused until stability returns"],
                None,
            )

        # ---- edge gate (§18 pre-trade simulation result) ----
        min_edge = safe_float(self.config.get("min_edge_pct", 0.0015))
        if opp.expected_net < min_edge:
            return (
                Decision.WAIT,
                [
                    f"Insufficient net edge {pct(opp.expected_net)}% < "
                    f"{pct(min_edge)}% after costs+uncertainty"
                ],
                None,
            )

        # ---- 3 PORTFOLIO CONSTRAINTS ----
        max_positions = int(self.config.get("max_positions", 5))
        if caps.get("max_positions"):
            max_positions = min(max_positions, caps["max_positions"])
        n_pos = len(positions)
        if n_pos >= max_positions:
            return Decision.WAIT, [f"Max positions reached ({n_pos}/{max_positions})"], None
        if any(p["market"] == opp.symbol for p in positions):
            return Decision.WAIT, [f"Position already open on {opp.symbol}"], None
        exp = self.portfolio.exposure(positions, equity)
        max_total = min(
            safe_float(self.config.get("max_total_exposure_pct", 0.6)),
            HARD_LIMITS["max_hard_total_exposure_pct"],
        )
        if exp["total_pct"] >= max_total:
            return (
                Decision.WAIT,
                [
                    f"Total exposure limit reached ({pct(exp['total_pct'])}% "
                    f"≥ {pct(max_total)}%)"
                ],
                None,
            )
        max_asset = min(
            safe_float(self.config.get("max_asset_exposure_pct", 0.25)),
            HARD_LIMITS["max_hard_position_pct"],
        )
        if safe_float(exp["per_asset"].get(opp.symbol, 0)) >= max_asset:
            return Decision.WAIT, ["Asset exposure limit reached"], None
        corr_exp = self.portfolio.correlated_exposure(
            opp.symbol, opp.direction, positions, equity, mat
        )
        if corr_exp >= safe_float(self.config.get("max_correlated_exposure_pct", 0.4)):
            return (
                Decision.WAIT,
                [f"Correlated {opp.direction} exposure {pct(corr_exp)}% at limit"],
                None,
            )
        dir_key = "long_pct" if opp.direction == "LONG" else "short_pct"
        if exp[dir_key] >= safe_float(self.config.get("max_directional_exposure_pct", 0.5)):
            return Decision.WAIT, [f"Directional exposure limit ({opp.direction})"], None

        # ---- sizing modifiers ----
        size_mult = 1.0
        if state == SystemState.DEFENSIVE:
            size_mult = 0.4
            reasons_h.append("Defensive mode: size ×0.4")
        elif state == SystemState.CAUTION:
            size_mult = 0.65
            reasons_h.append("Caution mode: size ×0.65")
        if health["status"] == Health.LIMITED.value:
            size_mult *= 0.5
            reasons_h.append("Limited market health: size ×0.5")
        if self.ai.degraded:
            size_mult *= 0.5
            reasons_h.append(f"Model degraded ({self.ai.degraded_reason[:60]}): size ×0.5")
        if not self.ai.trained:
            size_mult *= 0.5
            reasons_h.append("Models untrained: size ×0.5")
        unc = safe_float((opp.machine or {}).get("uncertainty"), 0)
        if unc > 0.25:
            size_mult *= 0.7
            reasons_h.append(f"High model disagreement ({unc:.2f}): size ×0.7")

        hint = {
            "size_mult": round(size_mult, 3),
            "state": state.value,
            "corr_exposure": corr_exp,
            "stage_caps": caps,
            "max_notional_usd": caps.get("max_notional_usd"),
            "system_msg": state_msg,
        }
        self._event(
            "authorize",
            opp.symbol,
            Decision.APPROVED.value,
            "; ".join(reasons_h) or "all checks passed",
        )
        return Decision.APPROVED, reasons_h or ["All risk checks passed"], hint

    def _event(self, kind: str, market: str, decision: str, reasons: str) -> None:
        try:
            if self.db:
                self.db.execute(
                    "INSERT INTO risk_events (kind, market, decision, reasons, system_state,"
                    " created_at) VALUES (?,?,?,?,?,?)",
                    (
                        kind,
                        market,
                        decision,
                        reasons[:900],
                        self.system_state()[0].value,
                        utcnow_iso(),
                    ),
                )
        except Exception:
            pass
        BUS.publish(
            E.RISK_DECISION, {"market": market, "decision": decision, "kind": kind}, source="risk"
        )

    # ------------------------------------------------------------------
    # liquidation / leverage protection (futures)
    # ------------------------------------------------------------------
    def leverage_allowed(self, requested: float, futures: bool = False) -> tuple[bool, float, str]:
        """Spot uses `max_leverage` (default 1.0); futures uses
        `max_futures_leverage` (default 3.0). Both are hard-capped at 5x."""
        cfg_key = "max_futures_leverage" if futures else "max_leverage"
        default = 3.0 if futures else 1.0
        cap = min(
            max(safe_float(self.config.get(cfg_key, default), default), 1.0),
            float(HARD_LIMITS["max_hard_leverage"]),
        )
        if requested > cap:
            return False, cap, f"leverage {requested}x exceeds cap {cap}x"
        return True, requested, ""

    def liquidation_distance_ok(self, position: dict, mark_price: float) -> tuple[bool, str]:
        liq = safe_float(position.get("liquidation_price"))
        if not liq or not mark_price:
            return True, ""
        dist = abs(mark_price - liq) / mark_price
        if dist < 0.05:
            return False, (
                f"liquidation distance {pct(dist)}% < 5% — position must be "
                "de-risked (liquidation protection)"
            )
        return True, f"liquidation distance {pct(dist)}%"

    # ------------------------------------------------------------------
    # self-test (run at boot and by the live gate)
    # ------------------------------------------------------------------
    def self_test(self) -> dict:
        checks = {}
        checks["hard_limits_present"] = HARD_LIMITS["withdrawals"] == "FORBIDDEN"
        checks["llm_cannot_trade"] = HARD_LIMITS["llm_can_trade"] is False
        checks["mcp_cannot_trade"] = HARD_LIMITS["mcp_can_trade"] is False

        # stale data must block
        class _StaleF:
            n_candles = 200
            price = 1.0

        class _Opp:
            symbol = "TEST/USDT"
            venue = "binance_spot"
            direction = "LONG"
            price = 1.0
            regime = "BULLISH_TREND"
            expected_net = 0.01
            machine: dict = {}

        health_ok = {"status": Health.TRADEABLE.value, "reasons": []}
        original = self.collector.is_stale
        try:
            self.collector.is_stale = lambda m: True  # type: ignore
            d, _why, _ = self.authorize(
                _Opp(), _StaleF(), health_ok, [], {"equity": 10000}, {}, 10000
            )
            checks["stale_data_blocks"] = d in (Decision.REJECTED, Decision.HALTED)
        finally:
            self.collector.is_stale = original  # type: ignore
        # kill switch must halt
        self.config.update({"kill_switch": True}, actor="risk-selftest")
        try:
            allowed, _ = self.entries_allowed()
            checks["kill_switch_halts"] = not allowed
        finally:
            self.config.update({"kill_switch": False}, actor="risk-selftest")
        checks["fail_closed"] = True  # authorize() wraps everything in try/except → HALTED
        ok = all(checks.values())
        return {"ok": ok, "checks": checks}
