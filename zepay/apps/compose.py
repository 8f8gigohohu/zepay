"""ZEPAY V3 composition root (§7-§11).

Builds the ONE coherent object graph used by the API server, the CLI and the
tests. Dependency injection everywhere: no module imports another module's
globals; every collaborator is passed explicitly, so each layer is testable
in isolation and there is exactly one engine.
"""

from __future__ import annotations

import json
import logging
import threading

from zepay.ai.agents import Agents, ResearchEngineLLM
from zepay.ai.engine import AIEngine
from zepay.ai.llm import AIProviderManager
from zepay.audit.log import Alerts, AuditLog
from zepay.backtesting.engine import Backtester
from zepay.core.config import ConfigStore, Settings
from zepay.core.events import BUS, E
from zepay.database.base import PostgresStorage, SQLiteStorage, Storage
from zepay.database.cache import MemoryCache
from zepay.engine import TradingEngine
from zepay.exchanges.binance_futures import BinanceFuturesAdapter
from zepay.exchanges.binance_spot import BinanceSpotAdapter
from zepay.exchanges.registry import VenueRegistry
from zepay.exchanges.zepay_venue import ZepayVenueAdapter
from zepay.execution.costs import CostModel
from zepay.execution.engine import ExecutionEngine
from zepay.execution.live_gate import LiveGate
from zepay.execution.paper import PaperExchange
from zepay.execution.quality import ExecutionQuality
from zepay.execution.reconciler import Reconciler
from zepay.features.engine import CrossAssetEngine, FeatureEngine
from zepay.market_data.collector import MarketDataCollector
from zepay.market_data.health import MarketHealthEngine
from zepay.market_data.quality import DataQualityEngine
from zepay.market_data.universe import UniverseManager
from zepay.market_data.ws_manager import WebSocketManager
from zepay.mcp.manager import MCPManager
from zepay.ml.drift import DriftMonitor
from zepay.ml.hyperopt import HyperoptEngine
from zepay.ml.registry import ModelRegistry
from zepay.ml.research import ResearchPipeline
from zepay.ml.walk_forward import WalkForwardEngine
from zepay.monitoring.backup import BackupManager
from zepay.monitoring.health import MetricsCollector, SystemHealthMonitor
from zepay.opportunities.ranker import OpportunityRanker, PreTradeSimulator
from zepay.portfolio.intelligence import AccountsRepository, PortfolioEngine
from zepay.regime.engine import RegimeEngine
from zepay.risk.engine import RiskEngine
from zepay.risk.killswitch import KillSwitch
from zepay.risk.sizer import Sizer
from zepay.security.crypto import SecretBox
from zepay.security.secrets import SecretsStore
from zepay.strategies.orchestrator import StrategyOrchestrator
from zepay.wallets.solana import SolanaWalletAdapter

log = logging.getLogger("zepay.app")


def make_storage(settings: Settings) -> Storage:
    if settings.db_backend == "postgres" and settings.postgres_dsn:
        try:
            return PostgresStorage(settings.postgres_dsn)
        except Exception as e:
            log.error(
                "postgres unavailable (%s) — falling back to SQLite so the "
                "system stays honest and operational",
                e,
            )
    return SQLiteStorage(settings.db_path)


def make_cache(settings: Settings):
    """Redis when configured AND reachable; otherwise in-memory, labeled."""
    if settings.redis_url:
        try:
            from zepay.database.cache import RedisCache

            c = RedisCache(settings.redis_url)
            if c.health().get("ok"):
                return c
        except Exception as e:
            log.warning("redis unavailable: %s", e)
    return MemoryCache(status_note="redis not configured — in-memory cache")


class ZepayApp:
    """Holds the full object graph. build() constructs; startup() activates
    background subsystems (WS, engine loop) — kept separate so tests and the
    CLI can construct without spawning threads."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.built = False
        self._engine_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    def build(self) -> ZepayApp:
        s = self.settings
        s.ensure_dirs()
        self.storage = make_storage(s)
        self.cache = make_cache(s)
        self.auditlog = AuditLog(self.storage)
        audit = self.auditlog.audit
        self.audit = audit
        self.config = ConfigStore(s.config_path, audit_fn=audit)
        self.config.load()
        self.alerts = Alerts(self.storage, self.config, self.auditlog)

        # ---- security ----
        self.secret_box = SecretBox(s.master_key_path)
        self.secrets = SecretsStore(self.secret_box, self.storage)

        # ---- venues (§13): one registry, adapters are peers ----
        self.registry = VenueRegistry()
        self.binance_spot = BinanceSpotAdapter(self.secrets, self.config, audit)
        self.binance_futures = BinanceFuturesAdapter(self.secrets, self.config, audit)
        self.zepay_venue = ZepayVenueAdapter(self.secrets, self.config, audit)
        self.solana = SolanaWalletAdapter(self.secrets, self.config, audit)
        for a in (self.binance_spot, self.binance_futures, self.zepay_venue, self.solana):
            self.registry.register(a)

        # ---- market data (§14-§17) ----
        self.universe = UniverseManager(self.storage, self.registry, self.config)
        self.quality = DataQualityEngine(self.storage, self.config)
        self.collector = MarketDataCollector(
            self.registry, self.universe, self.config, self.storage, self.quality
        )
        self.ws = WebSocketManager(
            self.registry,
            self.config,
            self.storage,
            self.quality,
            on_ticker=self.collector.ws_update_ticker,
            on_book=getattr(self.collector, "ws_update_book", None),
            on_trade=getattr(self.collector, "ws_update_trade", None),
        )
        self.health_mkt = MarketHealthEngine(self.collector, self.config)

        # ---- intelligence (§19-§21, §26) ----
        self.features = FeatureEngine(self.collector)
        self.cross_asset = CrossAssetEngine(self.collector, self.storage)
        self.regime = RegimeEngine()
        self.orchestrator = StrategyOrchestrator(self.config)
        self.ai = AIEngine(self.config, self.storage, audit)
        self.models = ModelRegistry(self.storage, audit)
        self.llm = AIProviderManager(self.config, self.secrets, audit, self.ai)
        self.research_llm = ResearchEngineLLM(self.config, self.llm, audit)

        # ---- opportunities & portfolio (§18-§20) ----
        self.costs = CostModel(self.config)
        self.simulator = PreTradeSimulator(self.costs, self.config)
        self.ranker = OpportunityRanker(self.config, self.costs, self.simulator)
        self.accounts = AccountsRepository(self.storage, self.config)
        self.accounts.ensure("PAPER")
        self.accounts.ensure("SHADOW")
        self.portfolio = PortfolioEngine(self.storage, self.collector, self.config, self.accounts)

        # ---- risk & execution (§21-§25, §30-§35) ----
        self.risk = RiskEngine(
            self.config,
            self.collector,
            self.portfolio,
            self.ai,
            self.storage,
            audit,
            self.alerts,
            self.registry,
        )
        self.sizer = Sizer(self.universe, self.config)
        self.kill = KillSwitch(self.config, self.storage, audit, self.alerts)
        self.paper = PaperExchange(self.collector, self.costs, self.config)
        self.exec_quality = ExecutionQuality(self.storage)
        self.execution = ExecutionEngine(
            self.storage,
            self.config,
            self.collector,
            self.universe,
            self.accounts,
            self.portfolio,
            self.risk,
            self.costs,
            self.paper,
            self.exec_quality,
            self.registry,
            audit,
            self.alerts,
        )
        self.kill.execution = self.execution  # resolve cycle explicitly
        self.recon = Reconciler(
            self.storage,
            self.config,
            self.accounts,
            self.portfolio,
            self.execution,
            self.registry,
            self.universe,
            audit,
            self.alerts,
        )
        self.gate = LiveGate(
            self.config, self.storage, self.registry, self.risk, self.recon, audit, self.alerts
        )

        # ---- ML research (§24-§28, §59) ----
        self.drift = DriftMonitor(
            self.storage, self.ai, self.models, self.config, audit, self.alerts
        )
        self.walk_forward = WalkForwardEngine(self.ai, self.storage, audit)
        self.backtester = Backtester(
            self.collector,
            self.ai,
            self.regime,
            self.orchestrator,
            self.costs,
            self.config,
            self.storage,
            audit,
        )
        self.hyperopt = HyperoptEngine(
            self.backtester, self.collector, self.config, self.storage, audit
        )
        self.research = ResearchPipeline(
            self.ai,
            self.collector,
            self.universe,
            self.config,
            self.models,
            self.walk_forward,
            self.backtester,
            self.storage,
            audit,
        )

        # ---- integrations & ops (§50, §56, §45-§46) ----
        self.mcp = MCPManager(self.config, self.storage, audit)
        self.backups = BackupManager(self.storage, self.config, s, audit)
        self.metrics = MetricsCollector()
        self.sys_health = SystemHealthMonitor(
            storage=self.storage,
            config=self.config,
            collector=self.collector,
            quality=self.quality,
            ws=self.ws,
            registry=self.registry,
            risk=self.risk,
            kill=self.kill,
            execution=self.execution,
            ai=self.ai,
            model_registry=self.models,
            drift=self.drift,
            metrics=self.metrics,
        )

        # ---- the ONE engine ----
        self.engine = TradingEngine(
            collector=self.collector,
            universe=self.universe,
            ws=self.ws,
            quality=self.quality,
            health_mkt=self.health_mkt,
            features=self.features,
            cross_asset=self.cross_asset,
            regime=self.regime,
            orchestrator=self.orchestrator,
            ai=self.ai,
            registry=self.models,
            agents=Agents,
            research_llm=self.research_llm,
            ranker=self.ranker,
            portfolio=self.portfolio,
            risk=self.risk,
            sizer=self.sizer,
            kill=self.kill,
            execution=self.execution,
            recon=self.recon,
            gate=self.gate,
            drift=self.drift,
            config=self.config,
            storage=self.storage,
            audit_fn=audit,
            alerts=self.alerts,
            cost_model=self.costs,
            accounts=self.accounts,
            backtester=self.backtester,
            research=self.research,
            mcp=self.mcp,
        )
        self.sys_health.engine = self.engine

        # metrics observe every engine cycle via the event bus (§46)
        def _on_cycle(ev) -> None:
            self.metrics.observe_cycle(float(ev.payload.get("elapsed_s") or 0))
            if not ev.payload.get("ok"):
                self.metrics.observe_error()

        BUS.subscribe(E.CYCLE_ENDED, _on_cycle, background=True)

        self.built = True
        return self

    # ------------------------------------------------------------------
    def startup(self, start_ws: bool = True, start_engine: bool | None = None) -> dict:
        assert self.built, "build() first"
        out: dict = {"db": self.storage.health(), "cache": self.cache.health()}
        # restore persisted models (REAL trained artifacts only)
        try:
            row = self.storage.query_one(
                "SELECT params_blob, metrics FROM models WHERE kind='quant-ensemble'"
                " AND status='active' ORDER BY id DESC LIMIT 1"
            )
            if row and row.get("params_blob"):
                blob = json.loads(row["params_blob"])
                if self.ai.import_models(blob):
                    self.ai.metrics = json.loads(row.get("metrics") or "{}")
                    out["models"] = "restored from database"
                else:
                    out["models"] = "persisted models failed integrity import — UNTRAINED"
            else:
                out["models"] = "none persisted — UNTRAINED (strict mode: WAIT)"
        except Exception as e:
            out["models"] = f"restore error: {e}"
        self.models.ensure_champion_from_production()
        # universe + subscriptions
        try:
            n = self.universe.refresh()
            out["universe"] = n
        except Exception as e:
            out["universe"] = f"refresh failed: {e}"
        if start_ws:
            try:
                markets = [a.get("symbol", "") for a in self.universe.all_assets(limit=30)]
                streams = []
                for mk in markets:
                    ex = self.universe.exchange_symbol(mk, "binance_spot").lower()
                    streams.append(f"{ex}@miniTicker")
                    streams.append(f"{ex}@bookTicker")
                self.ws.set_subscriptions("binance_spot", streams)
                out["ws_started"] = self.ws.start()
            except Exception as e:
                out["ws_started"] = False
                out["ws_error"] = str(e)
        # engine loop
        want_engine = self.settings.engine_autostart if start_engine is None else start_engine
        if want_engine and (self._engine_thread is None or not self._engine_thread.is_alive()):
            self._engine_thread = threading.Thread(
                target=self.engine.auto_loop, daemon=True, name="zepay-engine"
            )
            self._engine_thread.start()
            out["engine_loop"] = "started"
        else:
            out["engine_loop"] = "not started"
        self.audit("runtime", "startup", {"out": str(out)[:400]})
        return out

    def shutdown(self) -> None:
        try:
            self.engine.stop()
        except Exception:
            pass
        try:
            self.ws.stop()
        except Exception:
            pass
        try:
            self.storage.close()
        except Exception:
            pass
