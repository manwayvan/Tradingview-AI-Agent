"""Autonomous orchestrator — scan, decide, execute, repeat."""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from optionsagents.autonomous.brain import BrainDecision, StrategyBrain, TradeDirective
from optionsagents.autonomous.config import AutonomousConfig
from optionsagents.autonomous.market_context import build_market_context
from optionsagents.autonomous.portfolio_risk import PortfolioRiskManager
from optionsagents.autonomous.scanner import MarketScanner, StockCandidate
from optionsagents.engine import market_open_now
from optionsagents.modes import get_mode
from optionsagents.risk import mode_for_budget

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")


@dataclass
class CycleResult:
    cycle_id: str
    started_at: str
    finished_at: str
    candidates_scanned: int
    decision: BrainDecision | None
    trades_attempted: int
    trades_opened: int
    outcomes: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class OrchestratorEvent:
    time: str
    kind: str
    message: str


ExecuteTrade = Callable[[TradeDirective], str]
GetBroker = Callable[[], object]
GetTradeRiskBudget = Callable[[str], float]


class AutonomousOrchestrator:
    """Self-sufficient trading loop: scan universe, AI picks strategy, execute."""

    def __init__(
        self,
        execute_trade: ExecuteTrade,
        get_portfolio_summary: Callable[[], dict],
        get_open_tickers: Callable[[], dict[str, int]],
        get_broker: GetBroker,
        config: AutonomousConfig | None = None,
        scanner: MarketScanner | None = None,
        brain: StrategyBrain | None = None,
        risk_manager: PortfolioRiskManager | None = None,
        memory_context_fn: Callable[[], str] | None = None,
        get_trade_risk_budget: GetTradeRiskBudget | None = None,
    ):
        self.config = config or AutonomousConfig.from_env()
        self._execute_trade = execute_trade
        self._get_portfolio_summary = get_portfolio_summary
        self._get_open_tickers = get_open_tickers
        self._get_broker = get_broker
        self._memory_context_fn = memory_context_fn or (lambda: "")
        self._get_trade_risk_budget = get_trade_risk_budget

        self.scanner = scanner or MarketScanner(universe=self.config.universe)
        self.brain = brain or StrategyBrain()
        self.risk = risk_manager or PortfolioRiskManager(
            max_daily_loss=self.config.max_daily_loss,
            max_total_open_risk=self.config.max_total_open_risk,
            max_positions_per_ticker=self.config.max_positions_per_ticker,
        )

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running_cycle = False
        self._last_cycle: datetime | None = None
        self._last_result: CycleResult | None = None
        self._enabled = self.config.enabled
        self.events: deque[OrchestratorEvent] = deque(maxlen=200)
        self._load_state()

    # ---- persistence ---------------------------------------------------

    def _load_state(self) -> None:
        path = self.config.state_file
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._enabled = bool(data.get("enabled", self._enabled))
            if data.get("last_cycle"):
                self._last_cycle = datetime.fromisoformat(data["last_cycle"])
            if data.get("last_result"):
                self._last_result = _cycle_from_dict(data["last_result"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("could not load autonomous state: %s", exc)

    def _save_state(self) -> None:
        path = self.config.state_file
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self._lock:
            data = {
                "enabled": self._enabled,
                "last_cycle": self._last_cycle.isoformat() if self._last_cycle else None,
                "last_result": _cycle_to_dict(self._last_result) if self._last_result else None,
            }
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)

    def _event(self, kind: str, message: str) -> None:
        self.events.appendleft(OrchestratorEvent(
            time=datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M:%S ET"),
            kind=kind,
            message=message,
        ))
        logger.info("[autonomous:%s] %s", kind, message)

    # ---- lifecycle -----------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self._save_state()
        self._event("info", f"Autonomous brain {'enabled' if enabled else 'paused'}")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="autonomous-orchestrator",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def due(self, now: datetime | None = None) -> bool:
        if not self._enabled:
            return False
        now = (now or datetime.now(tz=ET)).astimezone(ET)
        if not market_open_now(now):
            return False
        if self._running_cycle:
            return False
        if self._last_cycle is None:
            return True
        elapsed = now - self._last_cycle.astimezone(ET)
        return elapsed >= timedelta(minutes=self.config.cycle_interval_minutes)

    def run_now(self) -> bool:
        if self._running_cycle:
            return False
        threading.Thread(target=self._run_cycle, daemon=True).start()
        return True

    def _universe_size(self) -> int:
        try:
            return len(self.scanner.current_universe())
        except Exception:
            return len(self.config.universe)

    # ---- core cycle ----------------------------------------------------

    def _loop(self) -> None:
        self._event("info", "Autonomous brain started")
        while not self._stop.wait(30):
            try:
                if self.due():
                    self._run_cycle()
            except Exception:
                logger.exception("autonomous loop tick failed")

    def _run_cycle(self) -> None:
        self._running_cycle = True
        cycle_id = uuid.uuid4().hex[:8]
        started = datetime.now(tz=ET)
        self._event("run", f"Cycle {cycle_id} starting — scanning {self._universe_size()} names...")

        outcomes: list[str] = []
        decision: BrainDecision | None = None
        candidates: list[StockCandidate] = []
        trades_opened = 0
        error: str | None = None

        try:
            candidates = self.scanner.scan(top_n=self.config.scan_top_n)
            market = build_market_context()
            memory = self._memory_context_fn()
            summary = self._get_portfolio_summary()
            open_tickers = self._get_open_tickers()

            decision = self.brain.decide(
                candidates=candidates,
                market=market,
                portfolio_summary=summary,
                memory_context=memory,
                open_tickers=open_tickers,
                max_trades=self.config.max_trades_per_cycle,
            )

            if decision.stand_aside or not decision.directives:
                outcomes.append("stood aside — no high-conviction setups")
                self._event("info", f"Cycle {cycle_id}: stood aside")
            else:
                for directive in decision.directives:
                    if directive.conviction < self.config.min_conviction:
                        outcomes.append(
                            f"skipped {directive.ticker}: conviction "
                            f"{directive.conviction:.2f} < {self.config.min_conviction}"
                        )
                        continue

                    mode_name = directive.mode
                    if self._get_trade_risk_budget:
                        budget = self._get_trade_risk_budget(mode_name)
                        mode = mode_for_budget(mode_name, budget)
                    else:
                        mode = get_mode(mode_name)
                    verdict = self.risk.check_directive(
                        directive,
                        self._get_broker(),
                        mode.max_risk_per_trade,
                    )
                    if not verdict.allowed:
                        outcomes.append(f"risk blocked {directive.ticker}: {verdict.reason}")
                        continue

                    self._event(
                        "run",
                        f"Executing {directive.signal} {directive.ticker} "
                        f"({directive.mode}, conviction {directive.conviction:.0%})",
                    )
                    try:
                        outcome = self._execute_trade(directive)
                        outcomes.append(f"{directive.ticker}: {outcome}")
                        if "opened" in outcome.lower():
                            trades_opened += 1
                    except Exception as exc:
                        outcomes.append(f"{directive.ticker}: error — {exc}")
                        self._event("error", f"{directive.ticker}: {exc}")

        except Exception as exc:
            error = str(exc)
            self._event("error", f"Cycle {cycle_id} failed: {exc}")
            logger.exception("autonomous cycle failed")

        finished = datetime.now(tz=ET)
        self._last_cycle = finished
        self._last_result = CycleResult(
            cycle_id=cycle_id,
            started_at=started.isoformat(),
            finished_at=finished.isoformat(),
            candidates_scanned=len(candidates),
            decision=decision,
            trades_attempted=len(decision.directives) if decision else 0,
            trades_opened=trades_opened,
            outcomes=outcomes,
            error=error,
        )
        self._save_state()
        self._running_cycle = False
        self._event(
            "run",
            f"Cycle {cycle_id} done — scanned {len(candidates)}, opened {trades_opened}",
        )

    def snapshot(self, broker=None) -> dict:
        risk_snap = {}
        if broker is not None:
            risk_snap = self.risk.snapshot(broker)
        with self._lock:
            last = _cycle_to_dict(self._last_result) if self._last_result else None
            events = [asdict(e) for e in list(self.events)[:80]]
        return {
            "running": self.running,
            "enabled": self._enabled,
            "cycle_running": self._running_cycle,
            "market_open": market_open_now(),
            "due": self.due(),
            "config": {
                "universe_size": self._universe_size(),
                "scan_top_n": self.config.scan_top_n,
                "cycle_interval_minutes": self.config.cycle_interval_minutes,
                "max_trades_per_cycle": self.config.max_trades_per_cycle,
                "min_conviction": self.config.min_conviction,
            },
            "last_cycle": self._last_cycle.isoformat() if self._last_cycle else None,
            "last_result": last,
            "risk": risk_snap,
            "events": events,
        }


def _cycle_to_dict(result: CycleResult) -> dict:
    decision = None
    if result.decision:
        decision = {
            "market_assessment": result.decision.market_assessment,
            "stand_aside": result.decision.stand_aside,
            "reasoning": result.decision.reasoning,
            "directives": [asdict(d) for d in result.decision.directives],
        }
    return {
        "cycle_id": result.cycle_id,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "candidates_scanned": result.candidates_scanned,
        "decision": decision,
        "trades_attempted": result.trades_attempted,
        "trades_opened": result.trades_opened,
        "outcomes": result.outcomes,
        "error": result.error,
    }


def _cycle_from_dict(data: dict) -> CycleResult:
    decision = None
    if data.get("decision"):
        d = data["decision"]
        directives = [TradeDirective(**item) for item in d.get("directives", [])]
        decision = BrainDecision(
            market_assessment=d.get("market_assessment", ""),
            stand_aside=bool(d.get("stand_aside")),
            reasoning=d.get("reasoning", ""),
            directives=directives,
        )
    return CycleResult(
        cycle_id=data.get("cycle_id", ""),
        started_at=data.get("started_at", ""),
        finished_at=data.get("finished_at", ""),
        candidates_scanned=int(data.get("candidates_scanned", 0)),
        decision=decision,
        trades_attempted=int(data.get("trades_attempted", 0)),
        trades_opened=int(data.get("trades_opened", 0)),
        outcomes=list(data.get("outcomes", [])),
        error=data.get("error"),
    )
