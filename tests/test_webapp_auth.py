"""Offline tests for webapp auth and database."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.setenv("OPTIONS_APP_DB", str(db))
    monkeypatch.setenv("OPTIONS_COOKIE_SECURE", "false")

    from optionsagents.webapp.database import get_db
    from optionsagents.webhook_server import app

    get_db()
    return TestClient(app)


def test_signup_login_and_me(client):
    r = client.post("/api/auth/signup", json={
        "email": "trader@example.com",
        "password": "secretpass",
        "display_name": "Trader",
    })
    assert r.status_code == 200
    assert r.json()["user"]["email"] == "trader@example.com"
    assert "oa_session" in r.cookies

    r2 = client.get("/api/auth/me")
    assert r2.status_code == 200
    assert r2.json()["user"]["display_name"] == "Trader"


def test_login_invalid_password(client):
    client.post("/api/auth/signup", json={
        "email": "a@b.com", "password": "password123",
    })
    r = client.post("/api/auth/login", json={
        "email": "a@b.com", "password": "wrongone",
    })
    assert r.status_code == 401


def test_state_requires_auth(client):
    r = client.get("/api/state")
    assert r.status_code == 401


def test_tradingview_setup_and_webhook(client):
    client.post("/api/auth/signup", json={
        "email": "tv@example.com", "password": "password123",
    })
    secret = None
    setup = client.get("/api/tradingview/setup")
    assert setup.status_code == 200
    body = setup.json()
    secret = body["webhook_secret"]
    assert "pine_day" in body
    assert "REPLACE_WITH_YOUR_SECRET" not in body["pine_day"]

    connect = client.post("/api/tradingview/connect", json={
        "tradingview_username": "my_tv_user",
        "confirm": True,
    })
    assert connect.status_code == 200
    assert connect.json()["user"]["tv_connected"] is True

    webhook = client.post("/webhook/tradingview", json={
        "secret": secret,
        "ticker": "SPY",
        "signal": "buy",
        "mode": "day",
    })
    assert webhook.status_code == 200
    assert webhook.json()["accepted"] is True


def test_webhook_rejects_bad_secret(client):
    client.post("/api/auth/signup", json={
        "email": "x@y.com", "password": "password123",
    })
    r = client.post("/webhook/tradingview", json={
        "secret": "not-valid",
        "ticker": "SPY",
        "signal": "buy",
        "mode": "day",
    })
    assert r.status_code == 401
