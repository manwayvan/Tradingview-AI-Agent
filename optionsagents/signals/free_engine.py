"""Built-in signal scanner — replaces paid TradingView webhooks for free users."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from optionsagents.autonomous.config import DEFAULT_UNIVERSE
from optionsagents.engine import market_open_now
from optionsagents.schemas import TradingViewAlert
from optionsagents.signals import discovery
from optionsagents.signals.technicals import (
    DaySignalResult,
    SwingSignalResult,
    scan_day_signal,
    scan_swing_signal,
)

logger = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

DEFAULT_DAY_SCAN_MINUTES = 5
DEFAULT_SWING_SCAN_TIME = "10:05"
MAX_FIRED_KEYS = 500
MAX_SCAN_TICKERS = 45


@dataclass
class SignalEvent:
    time: str
    kind: str  # signal | error | info
    message: str


OnSignal = Callable[[TradingViewAlert], None]


@dataclass
class FreeSignalConfig:
    enabled: bool = True
    # Optional pinned extras — the scan universe itself is auto-discovered.
    watchlist: list[str] = field(default_factory=list)
    day_scan_minutes: int = DEFAULT_DAY_SCAN_MINUTES
    swing_scan_time: str = DEFAULT_SWING_SCAN_TIME


class FreeSignalEngine:
    """Background scanner that fires the same alerts as TradingView Pine scripts."""

    def __init__(
        self,
        on_signal: OnSignal,
        state_file: str,
        config: FreeSignalConfig | None = None,
        discover: Callable[[], list[str]] | None = None,
    ):
        self._on_signal = on_signal
        self.state_file = state_file
        self.config = config or FreeSignalConfig()
        self._discover = discover or discovery.discover_universe

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_day_scan: datetime | None = None
        self._last_swing_scan_date: str | None = None
        self._fired_keys: dict[str, str] = {}
        self.events: deque[SignalEvent] = deque(maxlen=200)
        self._load_state()

    # ---- persistence ---------------------------------------------------

    def _load_state(self) -> None:
        if not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file) as f:
                data = json.load(f)
            self.config.enabled = bool(data.get("enabled", self.config.enabled))
            watchlist = data.get("watchlist")
            if watchlist:
                cleaned = [t.strip().upper() for t in watchlist if t.strip()]
                # Legacy states stored the old default universe as a watchlist;
                # treat that as "nothing pinned" so auto-discovery takes over.
                if cleaned != list(DEFAULT_UNIVERSE):
                    self.config.watchlist = cleaned
            self.config.day_scan_minutes = int(
                data.get("day_scan_minutes", self.config.day_scan_minutes)
            )
            self.config.swing_scan_time = str(
                data.get("swing_scan_time", self.config.swing_scan_time)
            )
            if data.get("last_day_scan"):
                self._last_day_scan = datetime.fromisoformat(data["last_day_scan"])
            self._last_swing_scan_date = data.get("last_swing_scan_date")
            self._fired_keys = dict(data.get("fired_keys", {}))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("could not load free signal state: %s", exc)

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.state_file)), exist_ok=True)
        with self._lock:
            data = {
                "enabled": self.config.enabled,
                "watchlist": self.config.watchlist,
                "day_scan_minutes": self.config.day_scan_minutes,
                "swing_scan_time": self.config.swing_scan_time,
                "last_day_scan": self._last_day_scan.isoformat() if self._last_day_scan else None,
                "last_swing_scan_date": self._last_swing_scan_date,
                "fired_keys": self._fired_keys,
            }
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.state_file)

    def _event(self, kind: str, message: str) -> None:
        self.events.appendleft(SignalEvent(
            time=datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M:%S ET"),
            kind=kind,
            message=message,
        ))
        logger.info("[free-signals:%s] %s", kind, message)

    # ---- config (API) --------------------------------------------------

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.config.enabled = enabled
        self._save_state()
        self._event("info", f"Free signals {'enabled' if enabled else 'paused'}")

    def set_watchlist(self, tickers: list[str]) -> None:
        """Pin optional extra tickers — auto-discovery covers the rest."""
        cleaned = []
        seen: set[str] = set()
        for raw in tickers:
            t = raw.strip().upper()
            if t and t not in seen:
                seen.add(t)
                cleaned.append(t)
        with self._lock:
            self.config.watchlist = cleaned
        self._save_state()
        self._event("info", f"Pinned tickers updated ({len(cleaned)})")

    # ---- universe ------------------------------------------------------

    def scan_universe(self) -> list[str]:
        """Auto-discovered market movers + any pinned tickers, deduped."""
        with self._lock:
            pinned = list(self.config.watchlist)
        try:
            found = [str(t).upper() for t in self._discover()]
        except Exception as exc:
            logger.warning("universe discovery failed: %s", exc)
            found = []
        merged: list[str] = []
        seen: set[str] = set()
        for ticker in pinned + found:
            if ticker and ticker not in seen:
                seen.add(ticker)
                merged.append(ticker)
        if not merged:
            merged = list(DEFAULT_UNIVERSE)
        return merged[:MAX_SCAN_TICKERS]

    # ---- scanning ------------------------------------------------------

    def _dedup_key(self, mode: str, ticker: str, signal: str, bar_id: str) -> str:
        return f"{mode}:{ticker}:{signal}:{bar_id}"

    def _already_fired(self, key: str) -> bool:
        return key in self._fired_keys

    def _mark_fired(self, key: str) -> None:
        now = datetime.now(tz=ET).isoformat()
        self._fired_keys[key] = now
        if len(self._fired_keys) > MAX_FIRED_KEYS:
            oldest = sorted(self._fired_keys.items(), key=lambda kv: kv[1])[:100]
            for k, _ in oldest:
                self._fired_keys.pop(k, None)

    def _fire_day(self, ticker: str, result: DaySignalResult) -> None:
        bar_id = datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M")
        key = self._dedup_key("day", ticker, result.signal, bar_id)
        if self._already_fired(key):
            return
        self._mark_fired(key)
        alert = TradingViewAlert(
            secret="free",
            ticker=ticker,
            signal=result.signal,  # type: ignore[arg-type]
            mode="day",
            price=result.price,
            interval="5",
            time=datetime.now(tz=ET).strftime("%Y-%m-%d %H:%M"),
            note=result.note,
        )
        self._event("signal", f"Day {result.signal.upper()} {ticker} @ ${result.price:.2f}")
        threading.Thread(target=self._on_signal, args=(alert,), daemon=True).start()

    def _fire_swing(self, ticker: str, result: SwingSignalResult) -> None:
        bar_id = datetime.now(tz=ET).strftime("%Y-%m-%d")
        key = self._dedup_key("swing", ticker, result.signal, bar_id)
        if self._already_fired(key):
            return
        self._mark_fired(key)
        alert = TradingViewAlert(
            secret="free",
            ticker=ticker,
            signal="analyze",
            mode="swing",
            price=result.price,
            interval="D",
            note=result.note,
            direction_hint=result.direction_hint,
        )
        self._event("signal", f"Swing analyze {ticker} ({result.direction_hint}) @ ${result.price:.2f}")
        threading.Thread(target=self._on_signal, args=(alert,), daemon=True).start()

    def scan_now(self) -> dict:
        """Run one scan cycle immediately (for tests and manual trigger)."""
        day_hits = 0
        swing_hits = 0
        universe = self.scan_universe()
        for ticker in universe:
            try:
                day = scan_day_signal(ticker)
                if day:
                    self._fire_day(ticker, day)
                    day_hits += 1
            except Exception as exc:
                self._event("error", f"Day scan failed for {ticker}: {exc}")
            try:
                swing = scan_swing_signal(ticker)
                if swing:
                    self._fire_swing(ticker, swing)
                    swing_hits += 1
            except Exception as exc:
                self._event("error", f"Swing scan failed for {ticker}: {exc}")
        self._last_day_scan = datetime.now(tz=ET)
        self._last_swing_scan_date = datetime.now(tz=ET).date().isoformat()
        self._save_state()
        return {
            "day_signals": day_hits,
            "swing_signals": swing_hits,
            "tickers_scanned": len(universe),
        }

    def _due_day_scan(self, now: datetime) -> bool:
        if not market_open_now(now):
            return False
        if self._last_day_scan is None:
            return True
        return now - self._last_day_scan >= timedelta(minutes=self.config.day_scan_minutes)

    def _due_swing_scan(self, now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        hh, mm = (int(x) for x in self.config.swing_scan_time.split(":"))
        if (now.hour, now.minute) < (hh, mm):
            return False
        today = now.date().isoformat()
        return self._last_swing_scan_date != today

    def _scan_day_watchlist(self) -> None:
        universe = self.scan_universe()
        self._event("info", f"Day scan — {len(universe)} auto-discovered tickers")
        for ticker in universe:
            try:
                result = scan_day_signal(ticker)
                if result:
                    self._fire_day(ticker, result)
            except Exception as exc:
                self._event("error", f"Day scan failed for {ticker}: {exc}")
        self._last_day_scan = datetime.now(tz=ET)
        self._save_state()

    def _scan_swing_watchlist(self) -> None:
        universe = self.scan_universe()
        self._event("info", f"Swing scan — {len(universe)} auto-discovered tickers")
        for ticker in universe:
            try:
                result = scan_swing_signal(ticker)
                if result:
                    self._fire_swing(ticker, result)
            except Exception as exc:
                self._event("error", f"Swing scan failed for {ticker}: {exc}")
        self._last_swing_scan_date = datetime.now(tz=ET).date().isoformat()
        self._save_state()

    def _tick(self) -> None:
        if not self.config.enabled:
            return
        now = datetime.now(tz=ET)
        if self._due_day_scan(now):
            self._scan_day_watchlist()
        if self._due_swing_scan(now):
            self._scan_swing_watchlist()

    def _loop(self) -> None:
        self._event("info", "Free signal engine started")
        while not self._stop.wait(30):
            try:
                self._tick()
            except Exception:
                logger.exception("free signal tick failed")

    # ---- lifecycle -----------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="free-signal-engine",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def snapshot(self) -> dict:
        now = datetime.now(tz=ET)
        with self._lock:
            cfg = {
                "enabled": self.config.enabled,
                "watchlist": list(self.config.watchlist),
                "day_scan_minutes": self.config.day_scan_minutes,
                "swing_scan_time": self.config.swing_scan_time,
            }
        universe = self.scan_universe()
        return {
            "universe": universe,
            "universe_size": len(universe),
            "discovery": discovery.last_discovery_meta(),
            "running": self.running,
            "market_open": market_open_now(now),
            "due_day_scan": self._due_day_scan(now) if self.config.enabled else False,
            "due_swing_scan": self._due_swing_scan(now) if self.config.enabled else False,
            "last_day_scan": self._last_day_scan.isoformat() if self._last_day_scan else None,
            "last_swing_scan_date": self._last_swing_scan_date,
            "config": cfg,
            "events": [asdict(e) for e in list(self.events)[:100]],
        }
