"""Tests for session persistence and data durability."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


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


def test_session_persists_across_requests(client):
    client.post("/api/auth/signup", json={
        "email": "persist@example.com",
        "password": "password123",
        "display_name": "Persist",
    })
    assert client.get("/api/auth/me").status_code == 200
    assert client.get("/api/state").status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["user"]["email"] == "persist@example.com"


def test_login_restores_session(client):
    client.post("/api/auth/signup", json={
        "email": "again@example.com", "password": "password123",
    })
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401

    login = client.post("/api/auth/login", json={
        "email": "again@example.com", "password": "password123",
    })
    assert login.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_health_reports_persistence(client):
    client.get("/health")  # ensure lifespan initialized DB
    h = client.get("/health").json()
    assert h["status"] == "ok"
    assert h["persistence"]["db_exists"] is True
    assert h["persistence"]["data_dir_writable"] is True


def test_cookie_secure_auto_https(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("OPTIONS_DATA_DIR", str(data_dir))
    monkeypatch.delenv("OPTIONS_COOKIE_SECURE", raising=False)

    from optionsagents.webapp.database import get_db
    from optionsagents.webhook_server import app

    get_db()
    client = TestClient(app)
    r = client.post(
        "/api/auth/signup",
        json={"email": "https@example.com", "password": "password123"},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert r.status_code == 200
    assert "oa_session" in r.cookies
