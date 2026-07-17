"""Tests for the paper<->live account-mode switch (offline, fake Schwab connection)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


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


def _signup(client, email):
    r = client.post("/api/auth/signup", json={"email": email, "password": "password123"})
    assert r.status_code == 200, r.text


def test_broker_defaults_to_paper_and_reports_unconfigured(client):
    _signup(client, "unconfigured@example.com")
    state = client.get("/api/broker").json()
    assert state["account_mode"] == "paper"
    assert state["schwab"]["configured"] is False
    assert state["live_summary"] is None


def test_cannot_switch_to_live_when_schwab_not_configured(client):
    _signup(client, "notconfigured@example.com")
    r = client.post("/api/broker/mode", json={"account_mode": "live"})
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"].lower()


def test_cannot_switch_to_live_when_configured_but_not_connected(client, monkeypatch):
    monkeypatch.setenv("SCHWAB_APP_KEY", "key")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "secret")
    monkeypatch.setenv("SCHWAB_REDIRECT_URI", "https://example.com/cb")
    _signup(client, "notconnected@example.com")
    r = client.post("/api/broker/mode", json={"account_mode": "live"})
    assert r.status_code == 400
    assert "not connected" in r.json()["detail"].lower()


def test_switch_to_live_and_back_when_connected(client, monkeypatch):
    monkeypatch.setenv("SCHWAB_APP_KEY", "key")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "secret")
    monkeypatch.setenv("SCHWAB_REDIRECT_URI", "https://example.com/cb")

    import optionsagents.brokers.schwab_client as sc
    monkeypatch.setattr(sc.SchwabClient, "is_connected", lambda self: True)
    monkeypatch.setattr(
        sc.SchwabClient, "get_account",
        lambda self: {"securitiesAccount": {"currentBalances": {
            "cashBalance": 5000.0, "liquidationValue": 5200.0,
        }}},
    )

    _signup(client, "switcher@example.com")

    r = client.post("/api/broker/mode", json={"account_mode": "live"})
    assert r.status_code == 200
    assert r.json()["account_mode"] == "live"

    state = client.get("/api/broker").json()
    assert state["account_mode"] == "live"
    assert state["live_summary"]["cash"] == 5000.0
    assert state["live_summary"]["equity"] == 5200.0

    full = client.get("/api/state").json()
    assert full["broker"]["account_mode"] == "live"
    assert full["account"]["cash"] == 5000.0  # ws.broker now points at the live broker

    back = client.post("/api/broker/mode", json={"account_mode": "paper"})
    assert back.status_code == 200
    assert back.json()["account_mode"] == "paper"
    full2 = client.get("/api/state").json()
    assert full2["broker"]["account_mode"] == "paper"


def test_live_risk_settings_editable_and_isolated_from_paper(client, monkeypatch):
    monkeypatch.setenv("SCHWAB_APP_KEY", "key")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "secret")
    monkeypatch.setenv("SCHWAB_REDIRECT_URI", "https://example.com/cb")
    import optionsagents.brokers.schwab_client as sc
    monkeypatch.setattr(sc.SchwabClient, "is_connected", lambda self: True)
    monkeypatch.setattr(
        sc.SchwabClient, "get_account",
        lambda self: {"securitiesAccount": {"currentBalances": {
            "cashBalance": 10_000.0, "liquidationValue": 10_000.0,
        }}},
    )
    _signup(client, "riskeditor@example.com")

    r = client.patch("/api/broker/live-risk", json={
        "risk_pct_per_trade": 2.5, "max_portfolio_risk_pct": 15,
    })
    assert r.status_code == 200
    assert r.json()["live_risk_pct_per_trade"] == 2.5

    # Paper risk settings (default 10%) are untouched by the live-specific update.
    settings = client.get("/api/account/settings").json()
    assert settings["account"]  # sanity: still on paper by default here
    paper_risk = settings["risk"]["risk_pct_per_trade"]
    assert paper_risk == 10  # unaffected by the live PATCH above

    client.post("/api/broker/mode", json={"account_mode": "live"})
    live_state = client.get("/api/state").json()
    assert live_state["risk"]["risk_pct_per_trade"] == 2.5
    assert live_state["risk"]["max_portfolio_risk_pct"] == 15


def test_live_risk_settings_reject_out_of_range(client, monkeypatch):
    monkeypatch.setenv("SCHWAB_APP_KEY", "key")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "secret")
    monkeypatch.setenv("SCHWAB_REDIRECT_URI", "https://example.com/cb")
    _signup(client, "outofrange@example.com")
    r = client.patch("/api/broker/live-risk", json={"risk_pct_per_trade": 50})
    assert r.status_code == 422  # pydantic field cap (le=25) rejects before it reaches the service


def test_account_reset_always_targets_paper_even_in_live_mode(client, monkeypatch):
    monkeypatch.setenv("SCHWAB_APP_KEY", "key")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "secret")
    monkeypatch.setenv("SCHWAB_REDIRECT_URI", "https://example.com/cb")
    import optionsagents.brokers.schwab_client as sc
    monkeypatch.setattr(sc.SchwabClient, "is_connected", lambda self: True)
    monkeypatch.setattr(
        sc.SchwabClient, "get_account",
        lambda self: {"securitiesAccount": {"currentBalances": {
            "cashBalance": 7000.0, "liquidationValue": 7000.0,
        }}},
    )
    _signup(client, "resettest@example.com")
    client.post("/api/broker/mode", json={"account_mode": "live"})

    r = client.post("/api/account/reset")
    assert r.status_code == 200  # must not 500 with NotImplementedError from SchwabBroker
