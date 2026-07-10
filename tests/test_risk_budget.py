"""Tests for percent-of-equity risk budgeting."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from optionsagents.paper_broker import PaperBroker
from optionsagents.risk import (
    daily_loss_cap,
    mode_for_budget,
    portfolio_risk_cap,
    trade_risk_budget,
)

pytestmark = pytest.mark.unit


def test_trade_risk_budget_from_equity():
    assert trade_risk_budget(100_000, 80_000, 10) == 10_000
    assert trade_risk_budget(50_000, 50_000, 5) == 2_500
    assert trade_risk_budget(10_000, 500, 10) == 500  # capped by cash


def test_trade_risk_budget_too_small():
    assert trade_risk_budget(1_000, 1_000, 1) == 0  # below $50 floor


def test_portfolio_and_daily_caps():
    assert portfolio_risk_cap(100_000, 50) == 50_000
    assert daily_loss_cap(100_000, 10) == 30_000


def test_mode_for_budget_overrides_cap():
    mode = mode_for_budget("day", 8_000)
    assert mode.max_risk_per_trade == 8_000


def test_paper_broker_reset(tmp_path):
    broker = PaperBroker(str(tmp_path / "acct.json"), starting_cash=50_000)
    summary = broker.reset_account(25_000, clear_history=True)
    assert summary["cash"] == 25_000
    assert summary["starting_cash"] == 25_000
    assert summary["open_positions"] == 0


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("OPTIONS_APP_DB", str(db))
    monkeypatch.setenv("OPTIONS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPTIONS_COOKIE_SECURE", "false")

    from optionsagents.webapp.database import get_db
    from optionsagents.webhook_server import app

    get_db()
    return TestClient(app)


def test_account_settings_api(client):
    client.post("/api/auth/signup", json={
        "email": "risk@example.com", "password": "password123",
    })

    state = client.get("/api/state").json()
    assert state["risk"]["risk_pct_per_trade"] == 10
    assert state["risk"]["trade_budget_usd"] == 10_000

    updated = client.patch("/api/account/settings", json={
        "risk_pct_per_trade": 5,
        "starting_cash": 50_000,
        "reset_paper": True,
    }).json()
    assert updated["user"]["risk_pct_per_trade"] == 5
    assert updated["account"]["cash"] == 50_000
    assert updated["risk"]["trade_budget_usd"] == 2_500

    reset = client.post("/api/account/reset").json()
    assert reset["account"]["cash"] == 50_000
