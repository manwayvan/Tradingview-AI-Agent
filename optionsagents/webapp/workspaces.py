"""Per-user trading workspaces: broker, engine, orchestrator, pipelines."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import asdict, replace

from optionsagents.autonomous.brain import StrategyBrain, TradeDirective
from optionsagents.autonomous.config import AutonomousConfig
from optionsagents.autonomous.orchestrator import AutonomousOrchestrator
from optionsagents.engine import Strategy, StrategyEngine
from optionsagents.paper_broker import PaperBroker
from optionsagents.paths import data_root
from optionsagents.pipeline import OptionsPipeline
from optionsagents.schemas import TradingViewAlert
from optionsagents.signals.free_engine import FreeSignalEngine
from optionsagents.webapp.auth import User, set_autonomous_enabled

logger = logging.getLogger(__name__)

_USERS_ROOT = os.path.join(data_root(), "users")


def _user_dir(user_id: str) -> str:
    path = os.path.join(_USERS_ROOT, user_id)
    os.makedirs(path, exist_ok=True)
    return path


class UserWorkspace:
    """Isolated paper account, strategies, and autonomous loop for one user."""

    def __init__(self, user: User):
        self.user = user
        base = _user_dir(user.id)
        self.account_file = os.path.join(base, "paper_account.json")
        self.strategies_file = os.path.join(base, "strategies.json")
        self.autonomous_state_file = os.path.join(base, "autonomous_state.json")
        self.free_signals_file = os.path.join(base, "free_signals.json")

        self.broker = PaperBroker(self.account_file, starting_cash=user.starting_cash)
        self._pipelines: dict[str, OptionsPipeline] = {}
        self._lock = threading.RLock()

        self.engine = StrategyEngine(
            run_strategy=self._run_strategy,
            check_positions=self._check_positions,
            strategies_file=self.strategies_file,
        )
        self.orchestrator = AutonomousOrchestrator(
            execute_trade=self._execute_autonomous_trade,
            get_portfolio_summary=self.broker.summary,
            get_open_tickers=self._open_tickers,
            get_broker=lambda: self.broker,
            config=AutonomousConfig.from_env().with_overrides(
                enabled=user.autonomous_enabled,
                state_file=self.autonomous_state_file,
            ),
            brain=self._build_brain(),
            memory_context_fn=self._memory_context,
        )
        self.free_signals = FreeSignalEngine(
            on_signal=self.handle_alert,
            state_file=self.free_signals_file,
        )
        self._started = False

    def _build_brain(self) -> StrategyBrain:
        try:
            from tradingagents.default_config import DEFAULT_CONFIG
            from tradingagents.graph.trading_graph import TradingAgentsGraph

            graph = TradingAgentsGraph(config=DEFAULT_CONFIG.copy())
            return StrategyBrain(graph.deep_thinking_llm)
        except Exception as exc:
            logger.warning("LLM unavailable for user %s (%s)", self.user.id, exc)
            return StrategyBrain(None)

    def _memory_context(self) -> str:
        try:
            from tradingagents.agents.utils.memory import TradingMemoryLog
            from tradingagents.default_config import DEFAULT_CONFIG

            mem = TradingMemoryLog(DEFAULT_CONFIG)
            return mem.get_past_context("", n_same=3, n_cross=5)
        except Exception:
            return ""

    def get_pipeline(self, mode: str) -> OptionsPipeline:
        with self._lock:
            if mode not in self._pipelines:
                self._pipelines[mode] = OptionsPipeline(mode=mode, broker=self.broker)
            return self._pipelines[mode]

    def _run_strategy(self, strat: Strategy) -> str:
        pipeline = self.get_pipeline(strat.mode)
        if strat.signal in ("buy", "sell"):
            result = pipeline.run_signal(
                strat.ticker, strat.signal,
                context=f"Scheduled {strat.signal.upper()} ({strat.describe_schedule()})",
            )
        else:
            result = pipeline.run(strat.ticker)
        if result.position:
            return (
                f"opened {result.plan.strategy.value} "
                f"(id {result.position.id}, max risk ${result.position.max_risk:,.0f})"
            )
        reason = result.warnings[0] if result.warnings else result.plan.rationale
        return f"no trade — {reason}" if reason else "no trade"

    def _check_positions(self) -> list:
        return self.get_pipeline("day").check_positions()

    def _execute_autonomous_trade(self, directive: TradeDirective) -> str:
        pipeline = self.get_pipeline(directive.mode)
        context = (
            f"Autonomous AI: {directive.ticker} "
            f"({directive.mode}/{directive.signal}, {directive.conviction:.0%}). "
            f"{directive.rationale}"
        )
        if directive.signal in ("buy", "sell"):
            result = pipeline.run_signal(directive.ticker, directive.signal, context=context)
        else:
            result = pipeline.run(directive.ticker)
        if result.position:
            return (
                f"opened {result.plan.strategy.value} "
                f"(id {result.position.id}, max risk ${result.position.max_risk:,.0f})"
            )
        reason = result.warnings[0] if result.warnings else result.plan.rationale
        return f"no trade — {reason}" if reason else "no trade"

    def _open_tickers(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.broker.positions("open"):
            counts[p.underlying] = counts.get(p.underlying, 0) + 1
        return counts

    def handle_alert(self, alert: TradingViewAlert) -> None:
        """Process a TradingView or built-in free signal alert."""
        try:
            pipeline = self.get_pipeline(alert.mode)
            if alert.signal in ("buy", "sell"):
                source = "Free signal" if alert.secret == "free" else "TradingView alert"
                context = (
                    f"{source}: {alert.signal.upper()} {alert.ticker}"
                    + (f" at {alert.price}" if alert.price else "")
                    + (f" on the {alert.interval} chart" if alert.interval else "")
                    + (f". Note: {alert.note}" if alert.note else "")
                )
                result = pipeline.run_signal(alert.ticker, alert.signal, context)
            else:
                result = pipeline.run(alert.ticker)
            logger.info(
                "user %s alert %s %s -> %s",
                self.user.id, alert.ticker, alert.signal, result.plan.strategy.value,
            )
        except Exception:
            logger.exception(
                "alert failed for user %s ticker %s", self.user.id, alert.ticker,
            )

    def start(self) -> None:
        if self._started:
            return
        self.engine.start()
        self.orchestrator.start()
        self.free_signals.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self.free_signals.stop()
        self.orchestrator.stop()
        self.engine.stop()
        self._started = False

    def refresh_user(self, user: User) -> None:
        self.user = user
        self.orchestrator.set_enabled(user.autonomous_enabled)

    def set_autonomous(self, enabled: bool) -> None:
        set_autonomous_enabled(self.user.id, enabled)
        self.user = replace(self.user, autonomous_enabled=enabled)
        self.orchestrator.set_enabled(enabled)

    def check_positions(self) -> list:
        return self._check_positions()

    def snapshot_state(self) -> dict:
        return {
            "account": self.broker.summary(),
            "positions": [asdict(p) for p in self.broker.positions()],
            "journal": self.broker._state["journal"][-50:][::-1],
            "engine": self.engine.snapshot(),
            "autonomous": self.orchestrator.snapshot(broker=self.broker),
            "free_signals": self.free_signals.snapshot(),
        }


class WorkspaceManager:
    """Registry of per-user workspaces."""

    def __init__(self):
        self._workspaces: dict[str, UserWorkspace] = {}
        self._lock = threading.RLock()

    def get(self, user: User) -> UserWorkspace:
        with self._lock:
            ws = self._workspaces.get(user.id)
            if ws is None:
                ws = UserWorkspace(user)
                ws.start()
                self._workspaces[user.id] = ws
            else:
                ws.refresh_user(user)
            return ws

    def start_all(self, users: list[User]) -> None:
        for user in users:
            self.get(user)

    def stop_all(self) -> None:
        with self._lock:
            for ws in self._workspaces.values():
                ws.stop()
            self._workspaces.clear()


_manager: WorkspaceManager | None = None


def get_workspace_manager() -> WorkspaceManager:
    global _manager
    if _manager is None:
        _manager = WorkspaceManager()
    return _manager
