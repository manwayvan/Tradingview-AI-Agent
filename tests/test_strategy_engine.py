"""Tests for the set-and-forget strategy engine (no network, stub callbacks)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from optionsagents.engine import Strategy, StrategyEngine, market_open_now

pytestmark = pytest.mark.unit

ET = ZoneInfo("America/New_York")


def et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# 2026-07-10 is a Friday; 2026-07-11 a Saturday.
FRI_MORNING = et(2026, 7, 10, 9, 45)
FRI_LATE = et(2026, 7, 10, 14, 0)
SAT = et(2026, 7, 11, 11, 0)


def strat(**kw):
    defaults = dict(id="s1", ticker="SPY", mode="swing", trigger="daily",
                    signal="analyze", run_time="10:00", interval_minutes=30)
    defaults.update(kw)
    return Strategy(**defaults)


def test_market_hours():
    assert market_open_now(FRI_MORNING)
    assert market_open_now(FRI_LATE)
    assert not market_open_now(SAT)
    assert not market_open_now(et(2026, 7, 10, 8, 0))
    assert not market_open_now(et(2026, 7, 10, 17, 0))


def test_daily_not_due_before_run_time():
    assert not strat().due(FRI_MORNING)          # 9:45 < 10:00


def test_daily_due_after_run_time_once_per_day():
    s = strat()
    assert s.due(FRI_LATE)
    s.last_run = FRI_LATE.isoformat()
    assert not s.due(et(2026, 7, 10, 15, 0))     # already ran today


def test_daily_due_again_next_weekday():
    s = strat(last_run=FRI_LATE.isoformat())
    assert not s.due(SAT)                        # weekend
    assert s.due(et(2026, 7, 13, 10, 30))        # Monday


def test_interval_respects_market_hours_and_spacing():
    s = strat(trigger="interval", interval_minutes=30)
    assert s.due(FRI_MORNING)                    # never ran, market open
    s.last_run = FRI_MORNING.isoformat()
    assert not s.due(et(2026, 7, 10, 10, 0))     # 15 min later
    assert s.due(et(2026, 7, 10, 10, 20))        # 35 min later
    s.last_run = None
    assert not s.due(et(2026, 7, 10, 8, 0))      # pre-market


def test_webhook_and_disabled_never_due():
    assert not strat(trigger="webhook").due(FRI_LATE)
    assert not strat(enabled=False).due(FRI_LATE)
    assert not strat(running=True).due(FRI_LATE)


def test_engine_add_toggle_remove_persistence(tmp_path):
    path = str(tmp_path / "strategies.json")
    e1 = StrategyEngine(run_strategy=lambda s: "ok", check_positions=list,
                        strategies_file=path)
    s = e1.add(ticker="NVDA", mode="day", trigger="interval", signal="buy",
               interval_minutes=15)
    e1.set_enabled(s.id, False)

    e2 = StrategyEngine(run_strategy=lambda s: "ok", check_positions=list,
                        strategies_file=path)
    loaded = e2.get(s.id)
    assert loaded is not None
    assert loaded.ticker == "NVDA" and loaded.enabled is False
    assert e2.remove(s.id)
    assert e2.get(s.id) is None


def test_engine_execute_records_outcome_and_errors(tmp_path):
    path = str(tmp_path / "strategies.json")

    def runner(s):
        if s.ticker == "BAD":
            raise RuntimeError("boom")
        return "opened long_call"

    engine = StrategyEngine(run_strategy=runner, check_positions=list,
                            strategies_file=path)
    ok = engine.add(ticker="SPY", mode="day", trigger="interval", signal="buy")
    bad = engine.add(ticker="BAD", mode="day", trigger="interval", signal="buy")

    engine._execute(ok)
    engine._execute(bad)
    assert ok.last_result == "opened long_call"
    assert bad.last_result.startswith("error: boom")
    assert not ok.running and not bad.running
    kinds = [ev.kind for ev in engine.events]
    assert "error" in kinds and "run" in kinds


def test_snapshot_shape(tmp_path):
    engine = StrategyEngine(run_strategy=lambda s: "ok", check_positions=list,
                            strategies_file=str(tmp_path / "s.json"))
    engine.add(ticker="SPY", mode="swing", trigger="daily")
    snap = engine.snapshot()
    assert set(snap) == {"running", "market_open", "strategies", "events"}
    assert snap["strategies"][0]["schedule"] == "daily at 10:00 ET"
