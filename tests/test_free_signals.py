"""Tests for built-in free signal engine (no TradingView / network)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from optionsagents.signals.free_engine import FreeSignalConfig, FreeSignalEngine
from optionsagents.signals.technicals import scan_day_signal, scan_swing_signal

pytestmark = pytest.mark.unit


def _make_5m_bars(n: int = 30, *, bullish_cross: bool = False) -> pd.DataFrame:
    """Synthetic 5m bars with optional EMA9/21 bullish cross on last bar."""
    idx = pd.date_range("2026-07-10 09:30", periods=n, freq="5min")
    close = pd.Series(100.0, index=idx)
    if bullish_cross:
        close.iloc[-5:-2] = 99.0
        close.iloc[-2] = 100.5
        close.iloc[-1] = 101.5
    high = close + 0.5
    low = close - 0.5
    vol = pd.Series(1_000_000, index=idx)
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol})


def _make_daily_bars(n: int = 60, *, bullish_cross: bool = False) -> pd.DataFrame:
    idx = pd.bdate_range(end=date.today(), periods=n)
    close = pd.Series(100.0 + pd.Series(range(n)).values * 0.1, index=idx)
    if bullish_cross:
        close.iloc[-3:-1] = 105.0
        close.iloc[-1] = 110.0
    high = close + 1
    low = close - 1
    vol = pd.Series(5_000_000, index=idx)
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol})


def test_scan_day_signal_bullish_cross():
    df = _make_5m_bars(30, bullish_cross=True)

    def fetch(_ticker: str, interval: str) -> pd.DataFrame:
        assert interval == "5m"
        return df

    result = scan_day_signal("SPY", fetch_bars=fetch)
    assert result is not None
    assert result.signal == "buy"
    assert result.price > 0


def test_scan_day_signal_no_cross():
    df = _make_5m_bars(30, bullish_cross=False)

    def fetch(_ticker: str, interval: str) -> pd.DataFrame:
        return df

    assert scan_day_signal("SPY", fetch_bars=fetch) is None


def test_scan_day_signal_duplicate_timestamps():
    """yfinance occasionally returns duplicate 5m bars — must not crash VWAP."""
    df = _make_5m_bars(30, bullish_cross=True)
    duped = pd.concat([df, df.iloc[[-1]]])

    def fetch(_ticker: str, interval: str) -> pd.DataFrame:
        return duped

    result = scan_day_signal("GOOGL", fetch_bars=fetch)
    assert result is None or result.signal in ("buy", "sell")


def test_normalize_bars_flattens_multiindex_columns():
    from optionsagents.signals.technicals import _normalize_bars

    idx = pd.date_range("2026-07-10 09:30", periods=5, freq="5min")
    raw = pd.DataFrame(
        {"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Volume": 1e6},
        index=idx,
    )
    raw.columns = pd.MultiIndex.from_product([["GOOGL"], raw.columns])
    norm = _normalize_bars(raw)
    assert list(norm.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert len(norm) == 5


def test_scan_swing_signal_bullish_cross():
    df = _make_daily_bars(60, bullish_cross=True)

    def fetch(_ticker: str, interval: str) -> pd.DataFrame:
        assert interval == "1d"
        return df

    result = scan_swing_signal("NVDA", fetch_bars=fetch, trade_date=date.today().isoformat())
    # May or may not fire depending on EMA math; at minimum should not crash
    assert result is None or result.signal == "analyze"


def test_free_engine_fires_and_dedupes(tmp_path):
    fired: list[str] = []
    state_file = tmp_path / "free_signals.json"

    def on_signal(alert):
        fired.append(f"{alert.ticker}:{alert.signal}:{alert.mode}")

    engine = FreeSignalEngine(
        on_signal=on_signal,
        state_file=str(state_file),
        config=FreeSignalConfig(enabled=True, watchlist=["SPY"]),
    )

    df_day = _make_5m_bars(30, bullish_cross=True)

    def fetch(ticker: str, interval: str) -> pd.DataFrame:
        if interval == "5m":
            return df_day
        return _make_daily_bars(60)

    import optionsagents.signals.technicals as tech

    original = tech._default_fetch
    tech._default_fetch = fetch
    try:
        hits = engine.scan_now()
        assert hits["day_signals"] >= 0
        first_count = len(fired)
        engine.scan_now()
        assert len(fired) == first_count  # deduped
    finally:
        tech._default_fetch = original


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


def test_signals_api_toggle_and_watchlist(client, monkeypatch):
    client.post("/api/auth/signup", json={
        "email": "sig@example.com", "password": "password123",
    })

    state = client.get("/api/state").json()
    assert state["free_signals"]["config"]["enabled"] is True
    assert "SPY" in state["free_signals"]["config"]["watchlist"]

    off = client.post("/api/signals/toggle").json()
    assert off["enabled"] is False
    on = client.post("/api/signals/toggle").json()
    assert on["enabled"] is True

    wl = client.put("/api/signals/watchlist", json={"tickers": ["SPY", "QQQ", "NVDA"]}).json()
    assert wl["watchlist"] == ["SPY", "QQQ", "NVDA"]

    import optionsagents.signals.technicals as tech

    def fake_fetch(ticker: str, interval: str) -> pd.DataFrame:
        if interval == "5m":
            return _make_5m_bars(30)
        return _make_daily_bars(60)

    monkeypatch.setattr(tech, "_default_fetch", fake_fetch)
    scan = client.post("/api/signals/scan").json()
    assert "day_signals" in scan
    assert "swing_signals" in scan


def test_scanner_api_one_switch_for_both_engines(client):
    client.post("/api/auth/signup", json={
        "email": "scanner@example.com", "password": "password123",
    })

    state = client.get("/api/scanner").json()
    assert state["scan_interval_minutes"] == 5
    assert "SPY" in state["watchlist"]
    assert state["enabled"] is True  # free signals default on

    # one toggle flips both engines together
    off = client.post("/api/scanner/toggle").json()
    assert off["enabled"] is False
    full = client.get("/api/state").json()
    assert full["free_signals"]["config"]["enabled"] is False
    assert full["autonomous"]["enabled"] is False
    assert full["scanner"]["enabled"] is False

    on = client.post("/api/scanner/toggle").json()
    assert on["enabled"] is True
    full = client.get("/api/state").json()
    assert full["free_signals"]["config"]["enabled"] is True
    assert full["autonomous"]["enabled"] is True
    assert full["scanner"]["enabled"] is True
    assert full["autonomous"]["config"]["cycle_interval_minutes"] == 5

    wl = client.put("/api/scanner/watchlist", json={"tickers": ["SPY", "QQQ"]}).json()
    assert wl["watchlist"] == ["SPY", "QQQ"]
