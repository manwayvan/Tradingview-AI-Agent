"""Set-and-forget strategy engine.

Runs inside the web server as a background thread. You register strategies
(ticker + mode + when to run) in the GUI; the engine then:

- runs each due strategy automatically (full multi-agent research for
  ``analyze``, or a fixed directional signal for ``buy``/``sell``),
- marks every open position to market on a fixed cadence during US market
  hours and applies the profit-target / stop-loss / expiry exits,
- journals everything so the GUI's activity feed shows what happened while
  you were away.

Strategies persist to JSON (default ``~/.tradingagents/strategies.json``),
so a server restart picks up right where it left off.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

DEFAULT_STRATEGIES_FILE = os.path.join(
    os.path.expanduser("~"), ".tradingagents", "strategies.json"
)


def market_open_now(now: datetime | None = None) -> bool:
    """US equity regular session, with a grace tail for end-of-day marks."""
    now = (now or datetime.now(tz=ET)).astimezone(ET)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60 + 15


@dataclass
class Strategy:
    """One automated trading rule the engine executes on your behalf."""

    id: str
    ticker: str
    mode: str                       # day | swing
    trigger: str                    # daily | interval | webhook
    signal: str = "analyze"         # analyze | buy | sell
    run_time: str = "10:00"         # ET HH:MM, for trigger=daily
    interval_minutes: int = 60      # for trigger=interval
    enabled: bool = True
    last_run: str | None = None     # ISO timestamp (ET)
    last_result: str | None = None  # short human-readable outcome
    running: bool = False           # transient; not persisted as True

    def due(self, now: datetime | None = None) -> bool:
        if not self.enabled or self.running or self.trigger == "webhook":
            return False
        now = (now or datetime.now(tz=ET)).astimezone(ET)
        if now.weekday() >= 5:
            return False
        last = datetime.fromisoformat(self.last_run) if self.last_run else None
        if self.trigger == "daily":
            hh, mm = (int(x) for x in self.run_time.split(":"))
            if (now.hour, now.minute) < (hh, mm):
                return False
            return last is None or last.astimezone(ET).date() < now.date()
        if self.trigger == "interval":
            if not market_open_now(now):
                return False
            if last is None:
                return True
            return now - last.astimezone(ET) >= timedelta(minutes=self.interval_minutes)
        return False

    def describe_schedule(self) -> str:
        if self.trigger == "daily":
            return f"daily at {self.run_time} ET"
        if self.trigger == "interval":
            return f"every {self.interval_minutes} min (market hours)"
        return "on TradingView webhook"


@dataclass
class EngineEvent:
    time: str
    kind: str        # run | exit | error | info
    message: str


class StrategyEngine:
    """Background scheduler that owns the strategy list.

    ``run_strategy`` and ``check_positions`` are injected so the engine has
    no direct dependency on pipelines/LLMs — the web server wires them in,
    and tests can pass stubs.
    """

    def __init__(
        self,
        run_strategy: Callable[[Strategy], str],
        check_positions: Callable[[], list],
        strategies_file: str = DEFAULT_STRATEGIES_FILE,
        mark_interval_seconds: int = 300,
        tick_seconds: int = 20,
    ):
        self._run_strategy = run_strategy
        self._check_positions = check_positions
        self.strategies_file = strategies_file
        self.mark_interval_seconds = mark_interval_seconds
        self.tick_seconds = tick_seconds

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_mark: datetime | None = None
        self.events: deque[EngineEvent] = deque(maxlen=200)
        self.strategies: list[Strategy] = self._load()

    # ---- persistence ---------------------------------------------------

    def _load(self) -> list[Strategy]:
        if not os.path.exists(self.strategies_file):
            return []
        try:
            with open(self.strategies_file) as f:
                raw = json.load(f)
            out = []
            for item in raw:
                item.pop("running", None)
                out.append(Strategy(**item))
            return out
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("could not load strategies file: %s", exc)
            return []

    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.strategies_file)), exist_ok=True)
        with self._lock:
            data = []
            for s in self.strategies:
                d = asdict(s)
                d["running"] = False
                data.append(d)
        tmp = self.strategies_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.strategies_file)

    def _event(self, kind: str, message: str) -> None:
        self.events.appendleft(EngineEvent(
            time=datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M:%S ET"),
            kind=kind, message=message,
        ))
        logger.info("[engine:%s] %s", kind, message)

    # ---- strategy management (called by the API) -------------------------

    def add(self, **kwargs) -> Strategy:
        strat = Strategy(id=uuid.uuid4().hex[:8], **kwargs)
        with self._lock:
            self.strategies.append(strat)
        self._save()
        self._event("info", f"Strategy added: {strat.signal} {strat.ticker} "
                            f"({strat.mode}, {strat.describe_schedule()})")
        return strat

    def get(self, strategy_id: str) -> Strategy | None:
        with self._lock:
            for s in self.strategies:
                if s.id == strategy_id:
                    return s
        return None

    def remove(self, strategy_id: str) -> bool:
        with self._lock:
            before = len(self.strategies)
            self.strategies = [s for s in self.strategies if s.id != strategy_id]
            changed = len(self.strategies) != before
        if changed:
            self._save()
            self._event("info", f"Strategy {strategy_id} removed")
        return changed

    def set_enabled(self, strategy_id: str, enabled: bool) -> Strategy | None:
        strat = self.get(strategy_id)
        if strat:
            strat.enabled = enabled
            self._save()
            self._event("info", f"Strategy {strategy_id} {'resumed' if enabled else 'paused'}")
        return strat

    # ---- execution -------------------------------------------------------

    def run_now(self, strategy_id: str) -> bool:
        """Kick a strategy immediately (GUI 'Run now' button)."""
        strat = self.get(strategy_id)
        if strat is None or strat.running:
            return False
        threading.Thread(target=self._execute, args=(strat,), daemon=True).start()
        return True

    def _execute(self, strat: Strategy) -> None:
        strat.running = True
        strat.last_run = datetime.now(tz=ET).isoformat()
        self._save()
        self._event("run", f"Running {strat.signal} {strat.ticker} ({strat.mode})...")
        try:
            outcome = self._run_strategy(strat)
            strat.last_result = outcome
            self._event("run", f"{strat.ticker}: {outcome}")
        except Exception as exc:
            strat.last_result = f"error: {exc}"
            self._event("error", f"{strat.ticker}: {exc}")
        finally:
            strat.running = False
            self._save()

    def _tick(self) -> None:
        now = datetime.now(tz=ET)

        with self._lock:
            due = [s for s in self.strategies if s.due(now)]
        for strat in due:
            threading.Thread(target=self._execute, args=(strat,), daemon=True).start()

        # Mark positions / enforce exits on a fixed cadence during the session.
        if market_open_now(now) and (
            self._last_mark is None
            or (now - self._last_mark).total_seconds() >= self.mark_interval_seconds
        ):
            self._last_mark = now
            try:
                closed = self._check_positions()
                for p in closed:
                    self._event(
                        "exit",
                        f"Auto-closed {p.underlying} {p.strategy} "
                        f"({p.exit_reason}): P&L ${p.realized_pnl:,.2f}",
                    )
            except Exception as exc:
                self._event("error", f"position check failed: {exc}")

    def _loop(self) -> None:
        self._event("info", "Engine started")
        while not self._stop.wait(self.tick_seconds):
            try:
                self._tick()
            except Exception:
                logger.exception("engine tick failed")

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="strategy-engine")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> dict:
        with self._lock:
            strategies = [
                {**asdict(s), "schedule": s.describe_schedule(), "due": s.due()}
                for s in self.strategies
            ]
        return {
            "running": self.running,
            "market_open": market_open_now(),
            "strategies": strategies,
            "events": [asdict(e) for e in list(self.events)[:100]],
        }
