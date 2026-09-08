"""ZEPAY V3 CLI (§58): operator tooling without the web UI.

python -m zepay.cli serve            # API server + frontend + engine
python -m zepay.cli status           # stage, mode, health, kill switch
python -m zepay.cli health           # full system health report
python -m zepay.cli cycle            # run ONE engine cycle, print report
python -m zepay.cli train            # refresh data + train ensemble
python -m zepay.cli backtest BTC/USDT
python -m zepay.cli research         # full research pipeline (§28)
python -m zepay.cli walk-forward
python -m zepay.cli hyperopt BTC/USDT --method random --trials 8
python -m zepay.cli kill "reason"    # engage kill switch NOW
python -m zepay.cli resume           # typed-confirmation resume
python -m zepay.cli stage            # show gate requirements/status
python -m zepay.cli promote "ENABLE SHADOW MODE"
python -m zepay.cli backup
python -m zepay.cli selftest         # wiring + risk fail-closed proofs
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from zepay.apps.compose import ZepayApp
from zepay.core.config import Settings


def _p(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _app(settings: Settings, engine: bool = False) -> ZepayApp:
    app = ZepayApp(settings).build()
    app.startup(start_ws=False, start_engine=engine)
    return app


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zepay", description="ZEPAY V3 operator CLI")
    ap.add_argument("command")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--market", default=None)
    ap.add_argument("--method", default="random")
    ap.add_argument("--trials", type=int, default=12)
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ns = ap.parse_args(argv)
    if ns.data_dir:
        os.environ["ZEPAY_DATA_DIR"] = ns.data_dir
    settings = Settings()
    if ns.host:
        settings.host = ns.host
    if ns.port:
        settings.port = ns.port
    cmd = ns.command

    if cmd == "serve":
        from zepay.apps.server import main as serve_main

        serve_main()
        return 0

    app = _app(settings)
    try:
        if cmd == "status":
            _p(
                {
                    "engine": app.engine.status(),
                    "execution": app.execution.status(),
                    "stage_gate": app.gate.status(),
                    "kill_switch": app.kill.status(),
                }
            )
        elif cmd == "health":
            _p(app.sys_health.check())
        elif cmd == "cycle":
            rep = app.engine.run_cycle()
            _p({k: v for k, v in rep.items() if k != "markets"})
            for mk in rep.get("markets", []):
                print(
                    f"  {mk['market']:<12} {mk.get('decision','?'):<8} "
                    f"regime={mk.get('regime','-')} ai={mk.get('ai',{}).get('direction','-')}"
                )
        elif cmd == "train":
            markets = list(ns.args or []) or app.engine.active_markets(limit=10)
            app.engine.refresh_data(markets)
            _p(app.ai.train(app.collector, markets))
            app.models.ensure_champion_from_production()
        elif cmd == "backtest":
            markets = list(ns.args) if ns.args else ([ns.market] if ns.market else [])
            if not markets:
                print("usage: zepay backtest BTC/USDT [ETH/USDT ...]", file=sys.stderr)
                return 2
            app.engine.refresh_data(markets)
            if len(markets) == 1:
                _p(app.backtester.run_single(markets[0]))
            else:
                _p(app.backtester.run_multi(markets))
        elif cmd == "research":
            _p(app.research.run())
        elif cmd in ("walk-forward", "wf"):
            markets = ns.args or app.engine.active_markets(limit=6)
            app.engine.refresh_data(markets)
            _p(app.walk_forward.run(app.collector, markets))
        elif cmd == "hyperopt":
            if not ns.args:
                print("usage: zepay hyperopt BTC/USDT --method random --trials 8", file=sys.stderr)
                return 2
            app.engine.refresh_data([ns.args[0]])
            _p(app.hyperopt.run(ns.args[0], method=ns.method, n_trials=ns.trials))
        elif cmd == "kill":
            _p(app.kill.engage(actor="cli", reason=" ".join(ns.args) or "cli kill"))
        elif cmd == "resume":
            print("Type exactly: RESUME TRADING")
            conf = input("> ").strip()
            _p(app.kill.resume(actor="cli", confirmation=conf))
        elif cmd == "stage":
            _p(app.gate.status())
        elif cmd == "promote":
            conf = " ".join(ns.args)
            if not conf:
                print('usage: zepay promote "ENABLE SHADOW MODE"', file=sys.stderr)
                return 2
            _p(app.gate.promote(actor="cli", confirmation=conf))
        elif cmd == "demote":
            _p(app.gate.demote(actor="cli", reason=" ".join(ns.args) or "cli demote"))
        elif cmd == "backup":
            _p(app.backups.run(label="cli"))
        elif cmd == "selftest":
            from zepay.apps.selftest import run_selftest

            ok = run_selftest(app)
            return 0 if ok else 1
        else:
            print(__doc__)
            return 2
    finally:
        app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
