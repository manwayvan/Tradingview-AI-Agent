"""Tests for trade gates, earnings blocks, and performance stats."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from optionsagents.autonomous.market_context import MarketContext
from optionsagents.autonomous.portfolio_risk import PortfolioRiskManager
from optionsagents.chain import ChainSnapshot, OptionQuote, build_default_plan
from optionsagents.earnings import earnings_block_reason
from optionsagents.modes import get_mode
from optionsagents.orders import OrderContext
from optionsagents.paper_broker import LegFill, PaperBroker, Position
from optionsagents.schemas import StrategyType
from optionsagents.stats import compute_stats
from optionsagents.trade_gates import TradeRequest, check_all_gates, check_regime_gate

pytestmark = pytest.mark.unit


def test_kill_switch_includes_unrealized(tmp_path):
    broker = PaperBroker(str(tmp_path / "acct.json"))
    broker._state["positions"].append(
        Position(
            id="open1", underlying="TEST", strategy="long_call", direction="bullish",
            mode="day", legs=[LegFill("buy", "call", 100, "2026-08-21", 1, 2.0)],
            entry_net=2.0, price_type="debit", max_risk=200.0,
            profit_target_pct=50, stop_loss_pct=30, opened_at="2026-07-10T10:00:00Z",
            status="open", unrealized_pnl=-600.0,
        )
    )
    risk = PortfolioRiskManager(max_daily_loss=500.0)
    verdict = risk.check_trade("NVDA", broker, 200.0)
    assert not verdict.allowed
    assert "kill switch" in verdict.reason.lower()


def test_regime_blocks_risk_off_bullish():
    market = MarketContext(
        spy_return_20d=-0.05, spy_return_5d=-0.03, vix_level=22.0,
        regime="risk_off", assessment="test",
    )
    verdict = check_regime_gate(market, direction="bullish", signal="buy", conviction=0.5)
    assert not verdict.allowed


def test_earnings_block_reason():
    with patch("optionsagents.earnings.days_to_next_earnings", return_value=1):
        blocked, reason = earnings_block_reason("NVDA")
    assert blocked
    assert "earnings" in reason.lower()


def test_check_all_gates_blocks_earnings(tmp_path):
    broker = PaperBroker(str(tmp_path / "acct.json"))
    risk = PortfolioRiskManager(max_daily_loss=5000, max_total_open_risk=10000)
    request = TradeRequest(ticker="NVDA", mode="day", signal="buy", direction="bullish")
    market = MarketContext(
        spy_return_20d=0.04, spy_return_5d=0.01, vix_level=18.0,
        regime="risk_on", assessment="test",
    )
    with patch("optionsagents.trade_gates.earnings_block_reason", return_value=(True, "earnings in 1 day(s)")):
        verdict = check_all_gates(request, broker, risk, 500.0, market=market)
    assert not verdict.allowed


def test_build_default_plan_credit_spread_high_iv():
    mode = get_mode("swing")
    quotes = []
    for strike in (95, 100, 105, 110):
        quotes.append(OptionQuote(
            expiry="2026-08-21", right="put", strike=float(strike),
            bid=1.0, ask=1.05, iv=0.45, volume=100, open_interest=500, delta=-0.35,
        ))
    snap = ChainSnapshot(
        underlying="TEST", spot=100.0, asof=datetime(2026, 7, 10).date(),
        quotes=quotes, iv_rank=65.0,
    )
    plan = build_default_plan("bullish", snap, mode)
    assert plan.strategy == StrategyType.BULL_PUT_SPREAD


def test_compute_stats_from_closed_positions(tmp_path):
    broker = PaperBroker(str(tmp_path / "acct.json"))
    today = datetime.now(tz=ZoneInfo("America/New_York")).isoformat()
    broker._state["positions"].append(
        Position(
            id="w1", underlying="WIN", strategy="long_call", direction="bullish",
            mode="day", legs=[LegFill("buy", "call", 100, "2026-08-21", 1, 2.0)],
            entry_net=2.0, price_type="debit", max_risk=200.0,
            profit_target_pct=50, stop_loss_pct=30, opened_at=today,
            status="closed", closed_at=today, realized_pnl=120.0, exit_reason="profit_target",
        )
    )
    ctx = OrderContext(
        source="free_signal", ticker="WIN", mode="day", signal="buy",
        source_label="Free built-in signal",
    )
    broker.record_order(
        ctx, status="open", position_id="w1", strategy="long_call",
        max_risk=200.0, filled_at=today,
    )
    broker.update_order_for_close("w1", broker.get_position("w1"))
    stats = compute_stats(broker)
    assert stats["total_closed"] == 1
    assert stats["wins"] == 1
    assert stats["realized_pnl"] == 120.0
    assert "free_signal" in stats["by_source"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    db = data_dir / "app.db"
    monkeypatch.setenv("OPTIONS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPTIONS_APP_DB", str(db))
    monkeypatch.setenv("OPTIONS_COOKIE_SECURE", "false")

    import optionsagents.webapp.database as database

    database._conn = None

    from optionsagents.webapp.database import get_db
    from optionsagents.webhook_server import app

    get_db()
    return TestClient(app)


def test_stats_api(client):
    client.post("/api/auth/signup", json={
        "email": "stats@example.com", "password": "password123",
    })
    r = client.get("/api/stats").json()
    assert "stats" in r
    assert "expectancy" in r["stats"]
