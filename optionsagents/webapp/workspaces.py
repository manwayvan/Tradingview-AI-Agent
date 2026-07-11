"""Per-user trading workspaces: broker, engine, orchestrator, pipelines."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import asdict, replace

from optionsagents.autonomous.brain import StrategyBrain, TradeDirective
from optionsagents.autonomous.config import AutonomousConfig
from optionsagents.autonomous.market_context import build_market_context
from optionsagents.autonomous.orchestrator import AutonomousOrchestrator
from optionsagents.autonomous.portfolio_risk import PortfolioRiskManager
from optionsagents.engine import Strategy, StrategyEngine
from optionsagents.paper_broker import PaperBroker
from optionsagents.paths import data_root
from optionsagents.orders import OrderContext
from optionsagents.pipeline import OptionsPipeline, PipelineResult
from optionsagents.risk import (
    daily_loss_cap,
    mode_for_budget,
    portfolio_risk_cap,
    trade_risk_budget,
)
from optionsagents.schemas import TradingViewAlert
from optionsagents.signals.free_engine import FreeSignalEngine
from optionsagents.stats import compute_stats
from optionsagents.trade_gates import TradeRequest, check_all_gates, direction_from_signal
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
        self._lock = threading.RLock()
        self._risk_manager = self._build_risk_manager()

        self.engine = StrategyEngine(
            run_strategy=self._run_strategy,
            check_positions=self._check_positions,
            strategies_file=self.strategies_file,
        )
        self.orchestrator = AutonomousOrchestrator(
            execute_trade=self._execute_autonomous_trade,
            get_portfolio_summary=self._portfolio_summary,
            get_open_tickers=self._open_tickers,
            get_broker=lambda: self.broker,
            config=AutonomousConfig.from_env().with_overrides(
                enabled=user.autonomous_enabled,
                state_file=self.autonomous_state_file,
            ),
            brain=self._build_brain(),
            memory_context_fn=self._memory_context,
            risk_manager=self._risk_manager,
            get_trade_risk_budget=self.trade_risk_budget,
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

    def _portfolio_summary(self) -> dict:
        summary = self.broker.summary()
        budget = self.trade_risk_budget()
        summary["risk_pct_per_trade"] = self.user.risk_pct_per_trade
        summary["trade_risk_budget_usd"] = budget
        summary["max_portfolio_risk_pct"] = self.user.max_portfolio_risk_pct
        return summary

    def _build_risk_manager(self) -> PortfolioRiskManager:
        summary = self.broker.summary()
        equity = summary["equity"]
        return PortfolioRiskManager(
            max_daily_loss=daily_loss_cap(equity, self.user.risk_pct_per_trade),
            max_total_open_risk=portfolio_risk_cap(
                equity, self.user.max_portfolio_risk_pct,
            ),
            max_positions_per_ticker=1,
        )

    def trade_risk_budget(self, mode: str = "swing") -> float:
        """USD max loss for the next trade from the user's risk % setting."""
        summary = self.broker.summary()
        return trade_risk_budget(
            summary["equity"],
            summary["cash"],
            self.user.risk_pct_per_trade,
        )

    def refresh_risk_limits(self) -> None:
        """Recompute portfolio risk caps from live equity."""
        summary = self.broker.summary()
        equity = summary["equity"]
        self._risk_manager.refresh_limits(
            max_daily_loss=daily_loss_cap(equity, self.user.risk_pct_per_trade),
            max_total_open_risk=portfolio_risk_cap(
                equity, self.user.max_portfolio_risk_pct,
            ),
        )
        self.orchestrator.risk = self._risk_manager

    def _preflight_trade(
        self,
        *,
        ticker: str,
        mode: str,
        signal: str,
        direction: str = "neutral",
        conviction: float | None = None,
    ) -> tuple[bool, str]:
        self.refresh_risk_limits()
        budget = self.trade_risk_budget(mode)
        mode_obj = mode_for_budget(mode, budget)
        request = TradeRequest(
            ticker=ticker.upper(),
            mode=mode,
            signal=signal,
            direction=direction or direction_from_signal(signal),
            conviction=conviction,
        )
        try:
            market = build_market_context()
        except Exception as exc:
            logger.warning("market context unavailable for gate: %s", exc)
            market = None
        verdict = check_all_gates(
            request,
            self.broker,
            self._risk_manager,
            mode_obj.max_risk_per_trade,
            market=market,
        )
        return verdict.allowed, verdict.reason

    def _record_gate_skip(self, ctx: OrderContext, reason: str) -> None:
        self.broker.record_order(
            ctx,
            status="skipped",
            plan_rationale=reason,
            warnings=[reason],
        )
        self.broker._save()

    def _execute_pipeline(
        self,
        pipeline: OptionsPipeline,
        *,
        ticker: str,
        signal: str,
        ctx: OrderContext,
        direction_hint: str | None = None,
        conviction: float | None = None,
    ) -> PipelineResult:
        allowed, reason = self._preflight_trade(
            ticker=ticker,
            mode=ctx.mode,
            signal=signal,
            direction=ctx.direction or direction_from_signal(signal),
            conviction=conviction or ctx.conviction,
        )
        if not allowed:
            self._record_gate_skip(ctx, reason)
            from optionsagents.schemas import OptionsTradePlan, StrategyType
            plan = OptionsTradePlan(
                strategy=StrategyType.NO_TRADE,
                underlying=ticker.upper(),
                direction="neutral",
                rationale=reason,
            )
            return PipelineResult(
                ticker=ticker.upper(),
                mode=ctx.mode,
                direction=ctx.direction or "neutral",
                rating=ctx.rating,
                plan=plan,
                position=None,
                chain_report="",
                decision_context=ctx.decision_context,
                warnings=[reason],
            )

        if signal in ("buy", "sell"):
            return pipeline.run_signal(
                ticker, signal, context=ctx.decision_context, order_ctx=ctx,
            )
        return pipeline.run(
            ticker, order_ctx=ctx, direction_hint=direction_hint,
        )

    def get_pipeline(self, mode: str) -> OptionsPipeline:
        budget = self.trade_risk_budget(mode)
        mode_obj = mode_for_budget(mode, budget)
        return OptionsPipeline(mode=mode_obj, broker=self.broker)

    def _run_strategy(self, strat: Strategy) -> str:
        pipeline = self.get_pipeline(strat.mode)
        ctx = OrderContext(
            source="strategy",
            ticker=strat.ticker,
            mode=strat.mode,
            signal=strat.signal,
            source_label="Scheduled plan",
            source_rationale=f"Automated rule: {strat.signal} {strat.ticker} ({strat.describe_schedule()})",
            decision_context=f"Scheduled {strat.signal.upper()} on {strat.ticker} ({strat.describe_schedule()})",
            source_ref=strat.id,
            direction=direction_from_signal(strat.signal) if strat.signal in ("buy", "sell") else "",
        )
        result = self._execute_pipeline(
            pipeline,
            ticker=strat.ticker,
            signal=strat.signal,
            ctx=ctx,
        )
        if result.position:
            self.refresh_risk_limits()
            return (
                f"opened {result.plan.strategy.value} "
                f"(id {result.position.id}, max risk ${result.position.max_risk:,.0f})"
            )
        reason = result.warnings[0] if result.warnings else result.plan.rationale
        return f"no trade — {reason}" if reason else "no trade"

    def _check_positions(self) -> list:
        closed = self.get_pipeline("day").check_positions()
        if closed:
            self.refresh_risk_limits()
        return closed

    def performance_stats(self) -> dict:
        return compute_stats(self.broker, self.broker.list_orders(500))

    def _execute_autonomous_trade(self, directive: TradeDirective) -> str:
        pipeline = self.get_pipeline(directive.mode)
        ctx = OrderContext(
            source="autonomous",
            ticker=directive.ticker,
            mode=directive.mode,
            signal=directive.signal,
            source_label="Autonomous AI",
            source_rationale=directive.rationale,
            decision_context=(
                f"Autonomous AI selected {directive.ticker}: "
                f"{directive.mode} / {directive.signal} at {directive.conviction:.0%} conviction. "
                f"{directive.rationale}"
            ),
            conviction=directive.conviction,
            direction=direction_from_signal(directive.signal) if directive.signal in ("buy", "sell") else "",
        )
        result = self._execute_pipeline(
            pipeline,
            ticker=directive.ticker,
            signal=directive.signal,
            ctx=ctx,
            conviction=directive.conviction,
        )
        if result.position:
            self.refresh_risk_limits()
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
            is_free = alert.secret == "free"
            source = "free_signal" if is_free else "tradingview"
            label = "Free built-in signal" if is_free else "TradingView alert"
            note = alert.note or ""
            direction = alert.direction_hint or direction_from_signal(alert.signal)
            ctx = OrderContext(
                source=source,
                ticker=alert.ticker,
                mode=alert.mode,
                signal=alert.signal,
                source_label=label,
                source_rationale=note,
                decision_context=(
                    f"{label}: {alert.signal.upper()} {alert.ticker}"
                    + (f" at {alert.price}" if alert.price else "")
                    + (f" on the {alert.interval} chart" if alert.interval else "")
                    + (f". {note}" if note else "")
                    + (
                        f" Technical bias: {alert.direction_hint}."
                        if alert.direction_hint else ""
                    )
                ),
                direction=direction if direction != "neutral" else "",
            )
            result = self._execute_pipeline(
                pipeline,
                ticker=alert.ticker,
                signal=alert.signal,
                ctx=ctx,
                direction_hint=alert.direction_hint,
            )
            if result.position:
                self.refresh_risk_limits()
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
        self.refresh_risk_limits()

    def update_account(
        self,
        user: User,
        *,
        reset_paper: bool = False,
        clear_history: bool = True,
    ) -> None:
        self.user = user
        if reset_paper:
            self.broker.reset_account(user.starting_cash, clear_history=clear_history)
        self.refresh_risk_limits()

    def set_autonomous(self, enabled: bool) -> None:
        set_autonomous_enabled(self.user.id, enabled)
        self.user = replace(self.user, autonomous_enabled=enabled)
        self.orchestrator.set_enabled(enabled)

    def check_positions(self) -> list:
        return self._check_positions()

    def snapshot_state(self) -> dict:
        summary = self.broker.summary()
        budget = self.trade_risk_budget()
        risk_snap = self._risk_manager.snapshot(self.broker)
        return {
            "account": summary,
            "risk": {
                "risk_pct_per_trade": self.user.risk_pct_per_trade,
                "max_portfolio_risk_pct": self.user.max_portfolio_risk_pct,
                "trade_budget_usd": budget,
                "portfolio_risk_cap_usd": portfolio_risk_cap(
                    summary["equity"], self.user.max_portfolio_risk_pct,
                ),
                "daily_loss_cap_usd": daily_loss_cap(
                    summary["equity"], self.user.risk_pct_per_trade,
                ),
                **risk_snap,
            },
            "stats": self.performance_stats(),
            "positions": [asdict(p) for p in self.broker.positions()],
            "orders": [o.to_dict() for o in self.broker.list_orders(100)],
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
