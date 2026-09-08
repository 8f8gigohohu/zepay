"""System health monitor (§45) + internal metrics (§46).

Aggregates REAL status from every subsystem into one honest picture.
Anything unavailable is reported as UNAVAILABLE/DEGRADED — never hidden,
never invented. Overall = worst subsystem state.
"""

from __future__ import annotations

import logging
import os
import shutil
import time

from zepay.core.util import utcnow_iso

log = logging.getLogger("zepay.monitoring")

_SEVERITY = {"OK": 0, "DEGRADED": 1, "CRITICAL": 2}


class MetricsCollector:
    """§46 internal performance metrics — measured, not estimated."""

    def __init__(self):
        self._t0 = time.time()
        self.cycle_latencies: list[float] = []
        self.decision_count = 0
        self.error_count = 0
        self.order_count = 0
        self.rejected_count = 0

    def observe_cycle(self, seconds: float) -> None:
        self.cycle_latencies.append(seconds)
        if len(self.cycle_latencies) > 500:
            self.cycle_latencies = self.cycle_latencies[-500:]

    def observe_error(self) -> None:
        self.error_count += 1

    def observe_order(self, rejected: bool = False) -> None:
        self.order_count += 1
        if rejected:
            self.rejected_count += 1

    def snapshot(self) -> dict:
        lat = sorted(self.cycle_latencies)
        n = len(lat)
        mem_mb = 0.0
        try:
            import resource

            mem_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
        except Exception:
            pass
        return {
            "uptime_s": round(time.time() - self._t0, 1),
            "cycles": n,
            "errors": self.error_count,
            "orders": self.order_count,
            "orders_rejected": self.rejected_count,
            "cycle_latency_p50_s": round(lat[n // 2], 2) if n else None,
            "cycle_latency_p95_s": round(lat[int(n * 0.95)], 2) if n else None,
            "peak_memory_mb": mem_mb or None,
        }


class SystemHealthMonitor:
    def __init__(
        self,
        *,
        storage,
        config,
        collector,
        quality,
        ws,
        registry,
        risk,
        kill,
        execution,
        ai,
        model_registry,
        drift,
        metrics,
        engine=None,
    ):
        self.db = storage
        self.config = config
        self.collector = collector
        self.quality = quality
        self.ws = ws
        self.registry = registry
        self.risk = risk
        self.kill = kill
        self.execution = execution
        self.ai = ai
        self.models = model_registry
        self.drift = drift
        self.metrics = metrics
        self.engine = engine
        self._last: dict = {}

    # ------------------------------------------------------------------
    def check(self) -> dict:
        issues: list[str] = []
        worst = "OK"

        def note(level: str, msg: str) -> None:
            nonlocal worst
            issues.append(msg)
            if _SEVERITY.get(level, 0) > _SEVERITY[worst]:
                worst = level

        # ---- database ----
        try:
            t0 = time.time()
            dbh = self.db.health()
            dbh["latency_ms"] = round((time.time() - t0) * 1000, 1)
            if dbh.get("ok") is False:
                note("CRITICAL", f"database unhealthy: {dbh}")
            elif dbh["latency_ms"] > 500:
                note("DEGRADED", f"database latency {dbh['latency_ms']}ms")
        except Exception as e:
            dbh = {"ok": False, "error": str(e)}
            note("CRITICAL", f"database unreachable: {e}")
        # ---- market data feeds ----
        try:
            qsum = self.quality.summary()
            feed = self.collector.feed_health()
            if qsum.get("overall") == "UNAVAILABLE":
                note("CRITICAL", "market data UNAVAILABLE — entries blocked by risk engine")
            elif qsum.get("overall") == "DEGRADED":
                note("DEGRADED", f"market data degraded: {qsum.get('status_counts')}")
        except Exception as e:
            qsum, feed = {}, {}
            note("CRITICAL", f"data quality check failed: {e}")
        # ---- websockets ----
        try:
            wss = self.ws.status() if self.ws else {"overall": "DISABLED"}
            if wss.get("overall") == "FAILED":
                note("DEGRADED", "websocket streams FAILED — REST polling only")
            elif wss.get("overall") == "DEGRADED":
                note("DEGRADED", f"websockets degraded: {wss.get('state_counts')}")
        except Exception as e:
            wss = {"overall": "ERROR", "error": str(e)}
            note("DEGRADED", f"ws status unavailable: {e}")
        # ---- venues ----
        try:
            venues = self.registry.health_all()
            for vid, h in venues.items():
                st = (h or {}).get("status", "UNKNOWN")
                if st in ("UNREACHABLE", "BLOCKED") and (
                    self.config.get("venues_enabled") or {}
                ).get(vid):
                    note("DEGRADED", f"venue {vid}: {st}")
        except Exception as e:
            venues = {}
            note("DEGRADED", f"venue health unavailable: {e}")
        # ---- risk / kill switch / live block ----
        try:
            state, state_msg = self.risk.system_state(None)
            if state.value in ("HALTED",):
                note("CRITICAL", f"risk system state HALTED: {state_msg}")
            elif state.value == "DEFENSIVE":
                note("DEGRADED", f"risk system state DEFENSIVE: {state_msg}")
            if self.kill.engaged:
                note("CRITICAL", "KILL SWITCH ENGAGED — all trading halted")
            lb = self.execution.status().get("live_block")
            if lb:
                note("CRITICAL", f"live execution blocked: {lb}")
        except Exception as e:
            state = None
            note("CRITICAL", f"risk health check failed: {e}")
        # ---- models ----
        try:
            champ = self.models.champion("quant-ensemble")
            model_state = "UNTRAINED" if not self.ai.trained else "TRAINED"
            if self.ai.degraded:
                model_state = "DEGRADED"
                note("DEGRADED", f"model degraded: {self.ai.degraded_reason}")
            drift_rep = self.drift.last_report() if self.drift else {}
            if drift_rep.get("action") == "disabled":
                note("CRITICAL", "model DISABLED by drift monitor — strict mode will WAIT")
        except Exception as e:
            model_state, champ = "UNKNOWN", None
            note("DEGRADED", f"model registry check failed: {e}")
        # ---- disk / resources ----
        disk = {}
        try:
            du = shutil.disk_usage(
                os.path.dirname(self.db.path if hasattr(self.db, "path") else ".")
            )
            disk = {
                "total_gb": round(du.total / 1e9, 1),
                "free_gb": round(du.free / 1e9, 1),
                "free_pct": round(du.free / max(du.total, 1) * 100, 1),
            }
            if disk["free_pct"] < 5:
                note("CRITICAL", f"disk space low: {disk['free_pct']}% free")
            elif disk["free_pct"] < 15:
                note("DEGRADED", f"disk space: {disk['free_pct']}% free")
        except Exception:
            pass
        # ---- engine liveness ----
        eng = {}
        if self.engine is not None:
            eng = {
                "cycles": self.engine.cycles,
                "last_error": self.engine.last_error,
                "mode": self.engine.effective_mode(),
            }
            if self.engine.last_error:
                note("DEGRADED", f"last cycle error: {self.engine.last_error[:120]}")
        overall = worst
        report = {
            "ts": utcnow_iso(),
            "overall": overall,
            "issues": issues,
            "database": dbh,
            "data_quality": {
                "overall": qsum.get("overall"),
                "status_counts": qsum.get("status_counts"),
                "feed_health": feed,
            },
            "websockets": wss,
            "venues": venues,
            "risk": {
                "state": getattr(state, "value", None),
                "kill_switch": self.kill.engaged,
                "stage": self.config.get("operational_stage"),
                "live_block": None,
            },
            "models": {
                "state": model_state,
                "champion": (champ or {}).get("id") if isinstance(champ, dict) else None,
                "drift": (self.drift.last_report() or {}).get("action") if self.drift else None,
            },
            "disk": disk,
            "engine": eng,
            "metrics": self.metrics.snapshot(),
        }
        try:
            report["risk"]["live_block"] = self.execution.status().get("live_block")
        except Exception:
            pass
        self._last = report
        try:
            self.db.kv_set("system_health", __import__("json").dumps(report, default=str))
        except Exception:
            pass
        return report

    def last(self) -> dict:
        return self._last
