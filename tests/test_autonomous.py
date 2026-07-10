"""Offline tests for the autonomous AI trading brain."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from optionsagents.autonomous.brain import StrategyBrain, TradeDirective
from optionsagents.autonomous.config import DEFAULT_UNIVERSE, AutonomousConfig
from optionsagents.autonomous.market_context import MarketContext, build_market_context
from optionsagents.autonomous.orchestrator import AutonomousOrchestrator
from optionsagents.autonomous.portfolio_risk import PortfolioRiskManager
from optionsagents.autonomous.scanner import MarketScanner, StockCandidate
from optionsagents.paper_broker import PaperBroker

pytestmark = pytest.mark.unit


def _synthetic_history(
    ticker: str, start: str, end: str, *, drift: float = 0.002, base: float = 100.0
) -> pd.DataFrame:
    start_dt = date.fromisoformat(start)
    end_dt = date.fromisoformat(end)
    days = (end_dt - start_dt).days
    rows = []
    price = base
    for i in range(days):
        d = start_dt + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        price *= 1 + drift
        rows.append({
            "Open": price * 0.99,
            "High": price * 1.01,
            "Low": price * 0.98,
            "Close": price,
            "Volume": 1_000_000 + i * 1000,
        })
    idx = pd.date_range(start=start, periods=len(rows), freq="B")
    return pd.DataFrame(rows, index=idx[: len(rows)])


def _fetch_factory(drifts: dict[str, float]):
    def fetch(ticker: str, start: str, end: str) -> pd.DataFrame:
        sym = ticker.lstrip("^")
        drift = drifts.get(sym, drifts.get(ticker, 0.0))
        base = 400.0 if sym == "SPY" else 150.0
        if sym == "VIX":
            return _synthetic_history(ticker, start, end, drift=0.0, base=18.0)
        return _synthetic_history(ticker, start, end, drift=drift, base=base)

    return fetch


@pytest.fixture
def bullish_fetch():
    return _fetch_factory({"SPY": 0.001, "NVDA": 0.004, "AMD": 0.003, "TSLA": -0.003})


def test_scanner_ranks_momentum_leaders(bullish_fetch):
    scanner = MarketScanner(
        universe=("NVDA", "AMD", "TSLA"),
        fetch_history=bullish_fetch,
    )
    ranked = scanner.scan(trade_date="2026-07-10", top_n=3, benchmark="SPY")
    assert len(ranked) == 3
    assert ranked[0].ticker == "NVDA"
    assert ranked[0].score >= ranked[-1].score
    assert "score=" in ranked[0].to_summary_line()


def test_market_context_risk_on(bullish_fetch):
    ctx = build_market_context(trade_date="2026-07-10", fetch_history=bullish_fetch)
    assert ctx.regime in ("risk_on", "neutral", "volatile", "risk_off")
    assert "SPY" in ctx.to_prompt_block()


def test_brain_rules_fallback_selects_bullish_candidate():
    brain = StrategyBrain(llm=None)
    candidates = [
        StockCandidate(
            ticker="NVDA", score=0.82, return_5d=0.04, return_20d=0.12,
            return_60d=0.20, rel_strength_vs_spy=0.08, rsi_14=58,
            volume_ratio=1.2, above_sma50=True, volatility=0.35, last_price=120.0,
        ),
        StockCandidate(
            ticker="TSLA", score=0.35, return_5d=-0.02, return_20d=-0.08,
            return_60d=-0.10, rel_strength_vs_spy=-0.10, rsi_14=40,
            volume_ratio=0.9, above_sma50=False, volatility=0.55, last_price=200.0,
        ),
    ]
    market = MarketContext(
        spy_return_20d=0.03, spy_return_5d=0.01, vix_level=16.0,
        regime="risk_on", assessment="test",
    )
    decision = brain.decide(
        candidates=candidates,
        market=market,
        portfolio_summary={"equity": 100_000, "cash": 80_000, "open_positions": 0, "realized_pnl": 0},
        max_trades=2,
    )
    assert not decision.stand_aside
    assert decision.directives[0].ticker == "NVDA"
    assert decision.directives[0].conviction >= 0.55


def test_brain_stands_aside_in_sharp_risk_off():
    brain = StrategyBrain(llm=None)
    market = MarketContext(
        spy_return_20d=-0.05, spy_return_5d=-0.04, vix_level=30.0,
        regime="risk_off", assessment="sharp selloff",
    )
    decision = brain.decide(
        candidates=[], market=market,
        portfolio_summary={"equity": 100_000, "cash": 100_000, "open_positions": 0, "realized_pnl": 0},
    )
    assert decision.stand_aside


def test_portfolio_risk_blocks_daily_loss(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from optionsagents.paper_broker import LegFill, Position

    broker = PaperBroker(str(tmp_path / "acct.json"))
    today = datetime.now(tz=ZoneInfo("America/New_York")).isoformat()
    broker._state["positions"].append(
        Position(
            id="loss1", underlying="TEST", strategy="long_call", direction="bullish",
            mode="day", legs=[LegFill("buy", "call", 100, "2026-08-21", 1, 2.0)],
            entry_net=2.0, price_type="debit", max_risk=200.0,
            profit_target_pct=50, stop_loss_pct=30, opened_at=today,
            status="closed", closed_at=today, realized_pnl=-600.0, exit_reason="stop",
        )
    )
    risk = PortfolioRiskManager(max_daily_loss=500.0)
    directive = TradeDirective(
        ticker="NVDA", mode="day", signal="buy", conviction=0.8, rationale="test",
    )
    verdict = risk.check_directive(directive, broker, mode_max_risk=500.0)
    assert not verdict.allowed
    assert "daily loss" in verdict.reason.lower()


def test_orchestrator_cycle_executes_trades(tmp_path):
    executed: list[TradeDirective] = []

    def execute(d: TradeDirective) -> str:
        executed.append(d)
        return "opened long_call (id abc, max risk $500)"

    broker = PaperBroker(str(tmp_path / "acct.json"))
    config = AutonomousConfig(
        enabled=True,
        universe=("NVDA",),
        scan_top_n=1,
        max_trades_per_cycle=1,
        cycle_interval_minutes=60,
        min_conviction=0.5,
        state_file=str(tmp_path / "auto.json"),
    )

    scanner = MarketScanner(
        universe=("NVDA",),
        fetch_history=_fetch_factory({"NVDA": 0.004, "SPY": 0.001}),
    )

    orch = AutonomousOrchestrator(
        execute_trade=execute,
        get_portfolio_summary=broker.summary,
        get_open_tickers=lambda: {},
        get_broker=lambda: broker,
        config=config,
        scanner=scanner,
        brain=StrategyBrain(None),
    )
    orch._run_cycle()
    assert orch._last_result is not None
    assert orch._last_result.candidates_scanned >= 1
    assert len(executed) >= 1


def test_orchestrator_respects_min_conviction(tmp_path):
    executed: list[TradeDirective] = []

    def execute(d: TradeDirective) -> str:
        executed.append(d)
        return "opened"

    broker = PaperBroker(str(tmp_path / "acct.json"))
    config = AutonomousConfig(
        enabled=True,
        universe=("NVDA",),
        min_conviction=0.99,
        state_file=str(tmp_path / "auto.json"),
    )
    scanner = MarketScanner(
        universe=("NVDA",),
        fetch_history=_fetch_factory({"NVDA": 0.004, "SPY": 0.001}),
    )
    orch = AutonomousOrchestrator(
        execute_trade=execute,
        get_portfolio_summary=broker.summary,
        get_open_tickers=lambda: {},
        get_broker=lambda: broker,
        config=config,
        scanner=scanner,
        brain=StrategyBrain(None),
    )
    orch._run_cycle()
    assert executed == []


def test_default_universe_has_liquid_names():
    assert "SPY" in DEFAULT_UNIVERSE
    assert "NVDA" in DEFAULT_UNIVERSE
    assert len(DEFAULT_UNIVERSE) >= 20
