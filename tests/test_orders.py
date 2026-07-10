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
