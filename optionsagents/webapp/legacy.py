"""Legacy single-tenant workspace for CLI and env-based webhook secret."""

from __future__ import annotations

import os
import threading

from optionsagents.autonomous.brain import StrategyBrain, TradeDirective
from optionsagents.autonomous.config import AutonomousConfig
from optionsagents.autonomous.orchestrator import AutonomousOrchestrator
from optionsagents.engine import DEFAULT_STRATEGIES_FILE, StrategyEngine
from optionsagents.paper_broker import PaperBroker
from optionsagents.pipeline import DEFAULT_ACCOUNT_FILE, OptionsPipeline

_lock = threading.Lock()
_broker: PaperBroker | None = None
_pipelines: dict[str, OptionsPipeline] = {}
_engine: StrategyEngine | None = None
_orchestrator: AutonomousOrchestrator | None = None


def _account_file() -> str:
    return os.environ.get("OPTIONS_ACCOUNT_FILE", DEFAULT_ACCOUNT_FILE)


def get_broker() -> PaperBroker:
    global _broker
    with _lock:
        if _broker is None:
            _broker = PaperBroker(_account_file())
        return _broker


def get_pipeline(mode: str) -> OptionsPipeline:
    broker = get_broker()
    with _lock:
        if mode not in _pipelines:
            _pipelines[mode] = OptionsPipeline(mode=mode, broker=broker)
        return _pipelines[mode]


def _run_strategy(strat) -> str:
    pipeline = get_pipeline(strat.mode)
    if strat.signal in ("buy", "sell"):
        result = pipeline.run_signal(strat.ticker, strat.signal, context=f"Scheduled {strat.signal}")
    else:
        result = pipeline.run(strat.ticker)
    if result.position:
        return f"opened {result.plan.strategy.value} (id {result.position.id})"
    reason = result.warnings[0] if result.warnings else result.plan.rationale
    return f"no trade — {reason}" if reason else "no trade"


def _check_positions() -> list:
    return get_pipeline("day").check_positions()


def _execute_autonomous_trade(directive: TradeDirective) -> str:
    pipeline = get_pipeline(directive.mode)
    if directive.signal in ("buy", "sell"):
        result = pipeline.run_signal(directive.ticker, directive.signal, context=directive.rationale)
    else:
        result = pipeline.run(directive.ticker)
    if result.position:
        return f"opened {result.plan.strategy.value} (id {result.position.id})"
    return "no trade"


def _open_tickers() -> dict[str, int]:
    return {p.underlying: 1 for p in get_broker().positions("open")}


def get_engine() -> StrategyEngine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = StrategyEngine(
                run_strategy=_run_strategy,
                check_positions=_check_positions,
                strategies_file=os.environ.get("OPTIONS_STRATEGIES_FILE", DEFAULT_STRATEGIES_FILE),
            )
        return _engine


def get_orchestrator() -> AutonomousOrchestrator:
    global _orchestrator
    with _lock:
        if _orchestrator is None:
            brain = StrategyBrain(None)
            _orchestrator = AutonomousOrchestrator(
                execute_trade=_execute_autonomous_trade,
                get_portfolio_summary=lambda: get_broker().summary(),
                get_open_tickers=_open_tickers,
                get_broker=get_broker,
                config=AutonomousConfig.from_env(),
                brain=brain,
            )
        return _orchestrator


class LegacyWorkspace:
    """Adapter matching UserWorkspace surface for webhook routing."""

    user = type("LegacyUser", (), {"id": "legacy"})()

    @property
    def broker(self) -> PaperBroker:
        return get_broker()

    @property
    def engine(self) -> StrategyEngine:
        return get_engine()

    @property
    def orchestrator(self) -> AutonomousOrchestrator:
        return get_orchestrator()

    def get_pipeline(self, mode: str) -> OptionsPipeline:
        return get_pipeline(mode)


_legacy: LegacyWorkspace | None = None


def get_legacy_workspace() -> LegacyWorkspace:
    global _legacy
    if _legacy is None:
        _legacy = LegacyWorkspace()
    return _legacy
