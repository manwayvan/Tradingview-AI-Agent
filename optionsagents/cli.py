"""Command-line interface for the options trading agent.

Examples:
    # Full multi-agent research, then open a swing options trade on paper
    python run_options.py analyze NVDA --mode swing

    # Fast path: act on a directional signal without the full debate
    python run_options.py signal NVDA buy --mode day

    # Account maintenance
    python run_options.py account
    python run_options.py positions
    python run_options.py mark          # mark-to-market + stop/target exits
    python run_options.py close <id>

    # Run the web GUI + strategy engine + TradingView webhook server
    python run_options.py serve --port 8000     # open http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict

from optionsagents.pipeline import DEFAULT_ACCOUNT_FILE, OptionsPipeline


def _build_pipeline(args) -> OptionsPipeline:
    return OptionsPipeline(
        mode=args.mode,
        account_file=args.account_file,
        use_llm_strategist=not getattr(args, "no_llm", False),
        debug=getattr(args, "debug", False),
    )


def _print_result(result) -> None:
    print(result.report_markdown())
    if result.position:
        print(f"\nOpened paper position {result.position.id}.")
    elif result.plan.strategy.value == "no_trade":
        print("\nNo trade this round.")


def cmd_analyze(args) -> int:
    pipeline = _build_pipeline(args)
    result = pipeline.run(args.ticker, trade_date=args.date)
    _print_result(result)
    return 0


def cmd_signal(args) -> int:
    pipeline = _build_pipeline(args)
    result = pipeline.run_signal(args.ticker, args.direction)
    _print_result(result)
    return 0


def cmd_account(args) -> int:
    pipeline = _build_pipeline(args)
    print(json.dumps(pipeline.broker.summary(), indent=2))
    return 0


def cmd_positions(args) -> int:
    pipeline = _build_pipeline(args)
    positions = pipeline.broker.positions(args.status)
    if not positions:
        print("No positions.")
        return 0
    for p in positions:
        print(json.dumps(asdict(p), indent=2))
    return 0


def cmd_mark(args) -> int:
    pipeline = _build_pipeline(args)
    closed = pipeline.check_positions()
    for p in closed:
        print(f"Closed {p.id} {p.underlying} {p.strategy}: "
              f"P&L ${p.realized_pnl:,.2f} ({p.exit_reason})")
    still_open = pipeline.broker.positions("open")
    for p in still_open:
        mark = f"${p.unrealized_pnl:,.2f}" if p.unrealized_pnl is not None else "unmarked"
        print(f"Open   {p.id} {p.underlying} {p.strategy}: unrealized {mark}")
    if not closed and not still_open:
        print("No open positions.")
    return 0


def cmd_close(args) -> int:
    pipeline = _build_pipeline(args)
    pos = pipeline.broker.get_position(args.position_id)
    if pos is None or pos.status != "open":
        print(f"No open position matching {args.position_id!r}", file=sys.stderr)
        return 1
    snapshot = pipeline._snapshot_for_positions(pos.underlying, [pos])
    net = pipeline.broker.mark_position(pos, snapshot)
    if net is None:
        print("No quotes available to price the close.", file=sys.stderr)
        return 1
    closed = pipeline.broker.close_position(pos.id, net, reason="manual")
    print(f"Closed {closed.id}: P&L ${closed.realized_pnl:,.2f}")
    return 0


def cmd_serve(args) -> int:
    import os

    import uvicorn

    from optionsagents.webhook_server import app

    os.environ.setdefault("OPTIONS_ACCOUNT_FILE", args.account_file)
    if getattr(args, "autonomous", False):
        os.environ["AUTONOMOUS_ENABLED"] = "true"
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def cmd_autonomous(args) -> int:
    from optionsagents.autonomous.brain import StrategyBrain
    from optionsagents.autonomous.config import AutonomousConfig
    from optionsagents.autonomous.market_context import build_market_context
    from optionsagents.autonomous.orchestrator import AutonomousOrchestrator
    from optionsagents.autonomous.scanner import MarketScanner
    from optionsagents.webhook_server import (
        _execute_autonomous_trade,
        _memory_context,
        _open_tickers,
        get_broker,
        get_orchestrator,
    )

    os.environ.setdefault("OPTIONS_ACCOUNT_FILE", args.account_file)
    config = AutonomousConfig.from_env()

    if args.autonomous_cmd == "scan":
        scanner = MarketScanner(universe=config.universe)
        candidates = scanner.scan(top_n=args.top)
        if not candidates:
            print("No candidates found.")
            return 0
        print(f"Top {len(candidates)} candidates:\n")
        for i, c in enumerate(candidates, 1):
            print(f"{i}. {c.to_summary_line()}")
        market = build_market_context()
        print(f"\nMarket: {market.regime} — {market.assessment}")
        return 0

    if args.autonomous_cmd == "run":
        orch = AutonomousOrchestrator(
            execute_trade=_execute_autonomous_trade,
            get_portfolio_summary=lambda: get_broker().summary(),
            get_open_tickers=_open_tickers,
            get_broker=get_broker,
            config=config.with_overrides(enabled=True),
            brain=StrategyBrain(None),
            memory_context_fn=_memory_context,
        )
        orch._run_cycle()
        snap = orch.snapshot(broker=get_broker())
        print(json.dumps(snap.get("last_result"), indent=2))
        return 0

    orch = get_orchestrator()
    if args.autonomous_cmd == "enable":
        orch.set_enabled(True)
        print("Autonomous brain enabled.")
        return 0
    if args.autonomous_cmd == "disable":
        orch.set_enabled(False)
        print("Autonomous brain paused.")
        return 0

    snap = orch.snapshot(broker=get_broker())
    print(json.dumps(snap, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(
        prog="optionsagents",
        description="LLM multi-agent options trading on a local paper account, "
                    "driven manually or by TradingView alerts.",
    )
    parser.add_argument(
        "--account-file", default=DEFAULT_ACCOUNT_FILE,
        help="Paper account JSON file (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="Full multi-agent research, then trade options")
    p.add_argument("ticker")
    p.add_argument("--mode", choices=["day", "swing"], default="swing")
    p.add_argument("--date", default=None, help="Trade date YYYY-MM-DD (default: today)")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--no-llm", action="store_true",
                   help="Skip the LLM strategist; deterministic strike selection only")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("signal", help="Trade on an external buy/sell signal (fast path)")
    p.add_argument("ticker")
    p.add_argument("direction", choices=["buy", "sell"])
    p.add_argument("--mode", choices=["day", "swing"], default="day")
    p.add_argument("--no-llm", action="store_true",
                   help="Skip the LLM strategist; deterministic strike selection only")
    p.set_defaults(func=cmd_signal)

    p = sub.add_parser("account", help="Show paper account summary")
    p.add_argument("--mode", choices=["day", "swing"], default="day")
    p.set_defaults(func=cmd_account)

    p = sub.add_parser("positions", help="List paper positions")
    p.add_argument("--status", choices=["open", "closed"], default=None)
    p.add_argument("--mode", choices=["day", "swing"], default="day")
    p.set_defaults(func=cmd_positions)

    p = sub.add_parser("mark", help="Mark positions to market and apply exit rules")
    p.add_argument("--mode", choices=["day", "swing"], default="day")
    p.set_defaults(func=cmd_mark)

    p = sub.add_parser("close", help="Close a position at the current mid")
    p.add_argument("position_id")
    p.add_argument("--mode", choices=["day", "swing"], default="day")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser(
        "serve",
        help="Run the web GUI + set-and-forget strategy engine + TradingView "
             "webhook server (open http://localhost:8000)",
    )
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--mode", choices=["day", "swing"], default="day")
    p.add_argument(
        "--autonomous", action="store_true",
        help="Enable the autonomous AI brain on server start (or set AUTONOMOUS_ENABLED=true)",
    )
    p.set_defaults(func=cmd_serve)

    auto = sub.add_parser(
        "autonomous",
        help="Autonomous AI: scan universe, pick strategies, execute on paper",
    )
    auto_sub = auto.add_subparsers(dest="autonomous_cmd", required=True)

    p = auto_sub.add_parser("scan", help="Run the quantitative screener once")
    p.add_argument("--top", type=int, default=12, help="Number of candidates to show")
    p.set_defaults(func=cmd_autonomous)

    p = auto_sub.add_parser("run", help="Run one full autonomous cycle now")
    p.set_defaults(func=cmd_autonomous)

    p = auto_sub.add_parser("status", help="Show autonomous brain state")
    p.set_defaults(func=cmd_autonomous)

    p = auto_sub.add_parser("enable", help="Enable autonomous trading loop")
    p.set_defaults(func=cmd_autonomous)

    p = auto_sub.add_parser("disable", help="Pause autonomous trading loop")
    p.set_defaults(func=cmd_autonomous)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
