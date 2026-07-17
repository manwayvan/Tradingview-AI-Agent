"""Tests for trade order tracking with execution rationale."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from optionsagents.orders import OrderContext, build_teach_summary
from optionsagents.paper_broker import PaperBroker

pytestmark = pytest.mark.unit


def test_record_order_on_fill(tmp_path):
    broker = PaperBroker(str(tmp_path / "acct.json"), starting_cash=50_000)
    ctx = OrderContext(
        source="free_signal",
        ticker="PLTR",
        mode="day",
        signal="sell",
        source_label="Free built-in signal",
        source_rationale="EMA9/21 bearish cross below VWAP",
        decision_context="Free signal: SELL PLTR at 127.38",
    )
    broker.record_order(ctx, status="skipped", plan_rationale="No chain in test.")
    orders = broker.list_orders()
    assert len(orders) == 1
    assert orders[0].ticker == "PLTR"
    assert orders[0].source == "free_signal"
    assert "Free built-in signal" in orders[0].teach_summary


def test_teach_summary_explains_analyze_path():
    ctx = OrderContext(
        source="autonomous",
        ticker="UBER",
        mode="swing",
        signal="analyze",
        source_rationale="Strong RS vs SPY",
        conviction=0.72,
    )
    text = build_teach_summary(
        ctx, plan_rationale="Bull call spread on momentum.", strategy="bull_call_spread",
    )
    assert "Autonomous AI" in text
    assert "multi-agent research" in text


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


def test_orders_api(client):
    client.post("/api/auth/signup", json={
        "email": "orders@example.com", "password": "password123",
    })
    state = client.get("/api/state").json()
    assert "orders" in state
    assert client.get("/api/orders").json().get("orders") is not None


def test_filled_order_records_teachable_context(tmp_path):
    from datetime import date, timedelta

    from optionsagents.chain import ChainSnapshot, OptionQuote
    from optionsagents.schemas import (
        LegAction,
        OptionLeg,
        OptionRight,
        OptionsTradePlan,
        StrategyType,
    )

    exp = (date.today() + timedelta(days=21)).isoformat()
    snapshot = ChainSnapshot(
        underlying="TEST", spot=100.0, asof=date.today(),
        quotes=[
            OptionQuote(expiry=exp, right="call", strike=100, bid=3.95, ask=4.05,
                        iv=0.4, volume=500, open_interest=1000, delta=0.5),
            OptionQuote(expiry=exp, right="put", strike=100, bid=3.95, ask=4.05,
                        iv=0.4, volume=400, open_interest=900, delta=-0.5),
        ],
    )
    plan = OptionsTradePlan(
        strategy=StrategyType.LONG_CALL, underlying="TEST", direction="bullish",
        legs=[OptionLeg(action=LegAction.BUY, right=OptionRight.CALL,
                        strike=100, expiry=exp)],
        net_price=4.0, price_type="debit",
    )
    broker = PaperBroker(str(tmp_path / "acct.json"), starting_cash=10_000,
                         slippage_pct=0.0)
    ctx = OrderContext(source="autonomous", ticker="TEST", mode="swing",
                       signal="buy", direction="bullish",
                       source_rationale="scan rank #1")
    pos = broker.execute_plan(
        plan, snapshot, "swing", order_ctx=ctx,
        mode_rules={"mode": "swing", "dte_window": "14-60"},
    )
    order = next(o for o in broker.list_orders() if o.position_id == pos.id)
    assert order.chain_conditions["spot"] == 100.0
    assert "expected_move_pct" in order.chain_conditions
    assert order.mode_rules["dte_window"] == "14-60"
    assert "call" in order.strategy_education.lower()

    # Reload from disk: teachable context survives persistence.
    broker2 = PaperBroker(str(tmp_path / "acct.json"))
    order2 = next(o for o in broker2.list_orders() if o.position_id == pos.id)
    assert order2.chain_conditions["spot"] == 100.0
    assert order2.strategy_education == order.strategy_education
