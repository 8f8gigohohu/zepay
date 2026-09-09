"""ZEPAY V3 Trading Engine — the single orchestrator (§8-§11).

One universal engine trades every discovered asset: it never contains
per-asset logic. Cycle flow (§11):

  refresh data → build features → cross-asset → regime → strategies → AI
  infer → pre-trade simulation → opportunity ranking → RISK GATE → sizing →
  decision record → (stage-permitted) execution → SL/TP management →
  resting-order fills → reconciliation → portfolio snapshot → drift tick

Execution obeys the operational stage: PAPER/SHADOW touch no real balance;
LIVE requires live_enabled (set only by the manual stage gate, §22).
"""

from __future__ import annotations

import json
import logging
import time

from zepay.ai.explain import build_explanation
from zepay.core.domain import Decision
from zepay.core.events import BUS, E
from zepay.core.ids import new_id
from zepay.core.util import clamp, safe_float, utcnow_iso
from zepay.security.license import license_ok
from zepay.strategies.base import StrategyContext

log = logging.getLogger("zepay.engine")


def _jd(obj) -> str:
    """Safe JSON dump for decision records (never raises into the hot path)."""
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return "{}"


class TradingEngine:
    def __init__(
        self,
        *,
        collector,
        universe,
        ws,
        quality,
        health_mkt,
        features,
        cross_asset,
        regime,
        orchestrator,
        ai,
        registry,
        agents,
        research_llm,
        ranker,
        portfolio,
        risk,
        sizer,
        kill,
        execution,
        recon,
        gate,
        drift,
        config,
        storage,
        audit_fn,
        alerts,
        cost_model,
        accounts,
        backtester=None,
        research=None,
        mcp=None,
    ):
        self.collector = collector
        self.universe = universe
        self.ws = ws
        self.quality = quality
        self.health_mkt = health_mkt
        self.features = features
        self.cross_asset = cross_asset
        self.regime = regime
        self.orchestrator = orchestrator
        self.ai = ai
        self.registry = registry
        self.agents = agents
        self.research_llm = research_llm
        self.ranker = ranker
        self.portfolio = portfolio
        self.risk = risk
        self.sizer = sizer
        self.kill = kill
        self.execution = execution
        self.recon = recon
        self.gate = gate
        self.drift = drift
        self.config = config
        self.db = storage
        self.audit = audit_fn
        self.alerts = alerts
        self.costs = cost_model
        self.accounts = accounts
        self.backtester = backtester
        self.research = research
        self.mcp = mcp

        self.cycles = 0
        self.last_cycle_id = ""
        self.last_error: str | None = None
        self._last_report: dict = {}
        self._stop = False

    # ------------------------------------------------------------------
    def effective_mode(self) -> str:
        return self.execution.effective_mode()

    def active_markets(self, limit: int = 40) -> list[str]:
        """Markets for this cycle: configured universe first, then the most
        liquid discovered assets — dynamic discovery, zero per-asset logic."""
        cfg = [str(m).upper() for m in (self.config.get("universe") or [])]
        out = list(dict.fromkeys(cfg))
        try:
            for a in self.universe.all_assets(limit=max(limit, len(out) + 20)):
                sym = a.get("symbol")
                if sym and sym not in out:
                    out.append(sym)
                if len(out) >= limit:
                    break
        except Exception as e:
            log.warning("universe enumeration failed: %s", e)
        return out[:limit] if len(out) > 1 else out or cfg

    # ------------------------------------------------------------------
    def refresh_data(self, markets: list[str]) -> dict:
        t0 = time.time()
        report: dict = {"ts": utcnow_iso()}
        try:
            self.universe.refresh()
        except Exception as e:
            report["universe"] = f"error: {e}"
        try:
            results = self.collector.refresh_all(markets)
            errs = [v for v in results.values() if isinstance(v, Exception)]
            report["fetches"] = len(results)
            report["errors"] = len(errs)
            for m in markets:
                if not (self.collector.get_klines(m) or []):
                    self.quality.mark_stale(m, "no candles after refresh")
        except Exception as e:
            report["refresh_error"] = str(e)
        if self.config.get("derivs_enabled"):
            for m in markets[:10]:
                try:
                    self.collector.fetch_derivs(m)
                except Exception:
                    pass  # derivatives are best-effort; labeled UNAVAILABLE downstream
        report["elapsed_s"] = round(time.time() - t0, 2)
        return report

    # ------------------------------------------------------------------
    def run_cycle(self) -> dict:
        t0 = time.time()
        self.cycles += 1
        cycle_id = new_id("cyc")
        self.last_cycle_id = cycle_id
        mode = self.effective_mode()
        report: dict = {
            "cycle_id": cycle_id,
            "ts": utcnow_iso(),
            "mode": mode,
            "cycle": self.cycles,
            "ok": False,
            "markets": [],
            "opportunities": [],
            "decisions": {},
            "executed": [],
        }
        BUS.publish(E.CYCLE_STARTED, {"cycle_id": cycle_id, "mode": mode}, source="engine")
        try:
            # §6: trading is gated until a ZEPAY key is verified (honest gate)
            lic_ok, lic_why = license_ok(self.config)
            if not lic_ok:
                report["ok"] = False
                report["error"] = lic_why
                report["license_gated"] = True
                return report
            markets = self.active_markets()
            report["markets_count"] = len(markets)
            report["refresh"] = self.refresh_data(markets)
            acct = self.portfolio.revalue(mode) or self.accounts.get(mode) or {}
            equity = safe_float(acct.get("equity")) or safe_float(
                self.config.get("paper_starting_balance", 10000.0), 10000.0
            )
            positions = self.portfolio.positions(mode)
            exposure = self.portfolio.exposure(positions, equity)
            # ---- features (real data only; venue resolved per instrument) ----
            feats = {m: self.features.build(m, venue=self.universe.venue_of(m)) for m in markets}
            feats = self.cross_asset.enrich(feats)
            mat = self.cross_asset.matrix(markets)
            champ = None
            try:
                champ = self.registry.champion("quant-ensemble")
            except Exception:
                pass
            champ_id = champ["id"] if champ else (self.ai.metrics.get("model_id") or "")
            opps: list[tuple] = []
            for m in markets:
                f = feats.get(m)
                mk: dict = {"market": m, "cycle_id": cycle_id}
                if f is None or f.price <= 0 or f.n_candles < 50:
                    mk["decision"] = "NO_DATA"
                    mk["why"] = (
                        "insufficient REAL data ("
                        f"{getattr(f, 'n_candles', 0)} candles, price "
                        f"{getattr(f, 'price', 0)}) — refusing to decide"
                    )
                    self.quality.mark_stale(m, mk["why"])
                    report["markets"].append(mk)
                    continue
                health = self.health_mkt.assess(m, f)
                regime_info = self.regime.detect(f)
                ctx = StrategyContext(
                    regime=regime_info,
                    derivs=self.collector.get_derivs(m),
                    depth=self.collector.get_depth(m),
                    costs=self.costs.components(f),
                    portfolio={"equity": equity, "exposure": exposure, "positions": len(positions)},
                    position=next((p for p in positions if p.get("market") == m), None),
                )
                strat_result = self.orchestrator.evaluate(f, ctx)
                infer = self.ai.infer(f, regime_info, strat_result)
                corr = self.portfolio.correlated_exposure(
                    m, infer["direction"], positions, equity, mat
                )
                opp, simulation = self.ranker.build(
                    m,
                    f,
                    regime_info,
                    strat_result,
                    infer,
                    venue=f.venue or "binance_spot",
                    corr_exposure=corr,
                )
                # ---- RISK GATE — always outranks AI (§21) ----
                d, reasons, hint = self.risk.authorize(
                    opp, f, health, positions, acct, mat, equity_hint=equity, mode=mode
                )
                size: dict = {}
                if d == Decision.APPROVED:
                    size = self.sizer.size(opp, f, acct, hint)
                    if size.get("reject"):
                        d = Decision.WAIT
                        reasons = [*reasons, f"sizer: {size.get('reject_reason')}"]
                        size = {}
                mk.update(
                    {
                        "health": health.get("status"),
                        "regime": regime_info.get("regime"),
                        "ai": {
                            "direction": infer["direction"],
                            "p_long": infer["p_long"],
                            "confidence": infer["confidence"],
                            "mode": infer["mode"],
                            "degraded": infer["model_degraded"],
                        },
                        "sim": {
                            "expected_net_pct": simulation.get("expected_net_pct"),
                            "passes": simulation.get("passes"),
                            "verdict": simulation.get("verdict"),
                        },
                        "score": opp.score,
                        "decision": d.name,
                        "reasons": reasons[:6],
                        "size": (
                            {k: size.get(k) for k in ("qty", "notional", "stop", "take_profit")}
                            if size
                            else {}
                        ),
                    }
                )
                # ---- explanation (§40) ----
                mk["explanation"] = build_explanation(
                    f,
                    regime_info,
                    infer,
                    strat_result,
                    d.name,
                    reasons,
                    self.costs.components(f),
                    portfolio={"equity": equity, "exposure": exposure},
                )
                try:
                    self.db.kv_set(f"explain:{m}", _jd(mk["explanation"]))
                except Exception:
                    pass
                # ---- decision record (§36) ----
                did = new_id("dec")
                self._record_decision(
                    cycle_id,
                    m,
                    mode,
                    f,
                    regime_info,
                    strat_result,
                    infer,
                    health,
                    simulation,
                    d,
                    reasons,
                    did,
                )
                mk["decision_id"] = did
                report["markets"].append(mk)
                report["decisions"][m] = d.name
                # ---- advisory agents (never gate execution) ----
                try:
                    self.agents.run_deterministic(
                        f, regime_info, infer, exposure, positions, self.costs
                    )
                except Exception:
                    pass
                # ---- prediction log (drift/labeling source) ----
                self.registry.record_prediction(champ_id, m, infer, cycle_id)
                if d == Decision.APPROVED and simulation.get("passes"):
                    opps.append((m, opp, size, f, strat_result, infer, did))
            # ---- rank approved opportunities (capital is scarce, §19) ----
            opps.sort(key=lambda o: -o[1].score)
            report["opportunities"] = [
                {
                    "market": m,
                    "score": o.score,
                    "direction": o.direction,
                    "expected_net_pct": sim.get("expected_net_pct"),
                }
                for m, o, _sz, _f, _st, _inf, _did in opps
                for sim in [o.machine.get("simulation", {})]
            ]
            # ---- execution (stage-aware) ----
            for m, opp, size, f, strat, infer, did in opps:
                if self.kill.engaged:
                    break
                best_id = strat.get("best")
                best_id = best_id.get("id", "") if isinstance(best_id, dict) else (best_id or "")
                res = self.execution.place(
                    opp,
                    size,
                    f,
                    mode=mode,
                    strategy=best_id,
                    idempotency=f"{cycle_id}:{m}:{opp.direction}",
                    decision_id=did,
                    risk_decision_id=did,
                    model_id=infer.get("model_version", ""),
                )
                report["executed"].append(
                    {
                        "market": m,
                        "ok": bool(res.get("ok")),
                        "order": res.get("order"),
                        "error": res.get("error"),
                    }
                )
            # ---- position management + resting paper fills ----
            report["exits"] = self.execution.manage_positions(mode)
            report["open_orders"] = self.execution.check_open_orders()
            # ---- periodic tasks ----
            if self.cycles % 5 == 0:
                report["portfolio"] = self.portfolio.snapshot(mode)
            if self.cycles % 10 == 0:
                report["recon"] = self.recon.paper_check()
                if mode == "LIVE":
                    report["live_recon"] = self.recon.live_check()
            if self.cycles % 20 == 0:
                try:
                    report["drift"] = self.drift.tick(self.collector, markets)
                except Exception as e:
                    log.warning("drift tick failed: %s", e)
            if self.cycles % 30 == 0 and self.research_llm is not None and opps:
                m0, opp0, _sz, f0, _st, infer0, _did = opps[0]
                try:
                    rep = self.research_llm.run(m0, f0, {"regime": opp0.regime}, infer0, {})
                    if rep.get("ok"):
                        report["llm_research"] = {
                            "market": m0,
                            "advisory": True,
                            "confidence": rep.get("confidence"),
                        }
                except Exception:
                    pass
            # persist latest signals for the UI
            try:
                self.db.kv_set(
                    "last_signals",
                    _jd(
                        {
                            "cycle_id": cycle_id,
                            "ts": report["ts"],
                            "markets": [
                                {k: v for k, v in mk.items() if k != "explanation"}
                                for mk in report["markets"]
                            ],
                        }
                    ),
                )
            except Exception:
                pass
            report["ok"] = True
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            report["ok"] = False
            report["error"] = self.last_error
            self.audit(
                "runtime", "cycle_error", {"cycle_id": cycle_id, "error": self.last_error[:400]}
            )
            log.exception("cycle failed")
        report["elapsed_s"] = round(time.time() - t0, 2)
        self._last_report = report
        BUS.publish(
            E.CYCLE_ENDED,
            {"cycle_id": cycle_id, "ok": report["ok"], "elapsed_s": report["elapsed_s"]},
            source="engine",
        )
        return report

    # ------------------------------------------------------------------
    def _record_decision(
        self,
        cycle_id: str,
        market: str,
        mode: str,
        f,
        regime_info: dict,
        strat_result: dict,
        infer: dict,
        health: dict,
        simulation: dict,
        d: Decision,
        reasons: list,
        did: str,
    ) -> None:
        best = strat_result.get("best")
        best_id = best.get("id", "") if isinstance(best, dict) else (best or "")
        try:
            self.db.execute(
                "INSERT INTO decisions (id, ts, market, venue, data_status, features,"
                " model_version, strategy, expected_return, expected_net, confidence,"
                " portfolio_state, risk_state, decision, reasons, order_id, cycle_id,"
                " stage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    did,
                    utcnow_iso(),
                    market,
                    f.venue or "binance_spot",
                    getattr(f, "data_quality", "OK"),
                    _jd(
                        {
                            "regime": regime_info.get("regime"),
                            "price": f.price,
                            "rsi": f.rsi14,
                            "atr_pct": f.atr_pct,
                            "structure": f.structure,
                            "mom_20": f.mom_20,
                            "spread_bps": f.spread_bps,
                            "depth_usd": f.depth_usd,
                            "best_strategy": best_id,
                            "ai_mode": infer.get("mode"),
                            "p_long": infer.get("p_long"),
                            "uncertainty": infer.get("uncertainty"),
                            "disagreement": infer.get("disagreement"),
                        }
                    ),
                    str(infer.get("model_version", "")),
                    best_id,
                    safe_float(infer.get("expected_return")),
                    safe_float(simulation.get("expected_net_pct")),
                    safe_float(infer.get("confidence")),
                    _jd({"health": health.get("status")}),
                    _jd({"reasons": reasons[:8]}),
                    d.name,
                    _jd(reasons),
                    "",
                    cycle_id,
                    mode,
                ),
            )
        except Exception as e:
            log.debug("decision record failed: %s", e)

    # ------------------------------------------------------------------
    def auto_loop(self, interval: float | None = None) -> None:
        """Blocking loop with adaptive interval (§44): slows down when data or
        execution is degraded, never spins on dead feeds."""
        base = safe_float(interval, safe_float(self.config.get("cycle_interval", 60), 60))
        self._stop = False
        log.info("auto_loop started (base %.0fs, mode %s)", base, self.effective_mode())
        while not self._stop:
            t0 = time.time()
            report = self.run_cycle()
            mult = 1.0
            try:
                qs = self.quality.summary()
                if qs.get("overall") in ("DEGRADED", "UNAVAILABLE"):
                    mult = 2.0
                ws = self.ws.status() if self.ws else {}
                if ws.get("overall") in ("DEGRADED", "FAILED"):
                    mult = max(mult, 1.5)
            except Exception:
                pass
            if not report.get("ok"):
                mult = max(mult, 2.0)
            sleep_s = clamp(base * mult - (time.time() - t0), 5, 900)
            time.sleep(sleep_s)

    def stop(self) -> None:
        self._stop = True

    def last_report(self) -> dict:
        return self._last_report

    def status(self) -> dict:
        return {
            "cycles": self.cycles,
            "last_cycle_id": self.last_cycle_id,
            "mode": self.effective_mode(),
            "last_error": self.last_error,
            "kill_switch": self.kill.engaged,
            "stage": self.config.get("operational_stage"),
            "ai_trained": self.ai.trained,
            "ai_degraded": self.ai.degraded,
        }
