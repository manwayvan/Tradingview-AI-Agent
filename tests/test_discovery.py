"""Tests for automatic market-wide ticker discovery (offline, injected fetch)."""

from __future__ import annotations

import pytest

from optionsagents.autonomous.config import DEFAULT_UNIVERSE
from optionsagents.autonomous.scanner import MarketScanner
from optionsagents.signals import discovery

pytestmark = pytest.mark.unit


def _quote(symbol, price=50.0, volume=5_000_000, cap=50e9, qtype="EQUITY"):
    return {
        "symbol": symbol,
        "regularMarketPrice": price,
        "regularMarketVolume": volume,
        "marketCap": cap,
        "quoteType": qtype,
    }


def _screen_payload(quotes):
    return {"finance": {"result": [{"quotes": quotes}]}}


def test_discover_universe_merges_screens_and_filters(monkeypatch):
    def fetch(url: str) -> dict:
        if "most_actives" in url:
            return _screen_payload([
                _quote("PLTR"),
                _quote("PENNY", price=1.2),            # under min price
                _quote("THIN", volume=1_000),           # illiquid
                _quote("BTC-USD", qtype="CRYPTOCURRENCY"),
                _quote("BRK.B"),                        # non-plain symbol
            ])
        if "day_gainers" in url:
            return _screen_payload([_quote("SMCI"), _quote("NVDA")])
        if "day_losers" in url:
            return _screen_payload([_quote("RIVN")])
        if "trending" in url:
            return _screen_payload([{"symbol": "HOOD"}, {"symbol": "^VIX"}])
        raise AssertionError(f"unexpected url {url}")

    universe = discovery.discover_universe(fetch_json=fetch, force=True)

    # Core liquid names always come first, then discovered movers.
    assert universe[0] == "SPY"
    for ticker in ("PLTR", "SMCI", "RIVN", "HOOD"):
        assert ticker in universe
    for bad in ("PENNY", "THIN", "BTC-USD", "BRK.B", "^VIX"):
        assert bad not in universe
    # NVDA is in core and discovered — deduped.
    assert universe.count("NVDA") == 1
    assert len(universe) <= discovery.MAX_UNIVERSE

    meta = discovery.last_discovery_meta()
    assert meta["auto"] is True
    assert meta["most_actives"] == 1


def test_discover_universe_falls_back_when_offline():
    def fetch(url: str) -> dict:
        raise OSError("network down")

    universe = discovery.discover_universe(fetch_json=fetch, force=True)
    assert universe == list(DEFAULT_UNIVERSE)
    assert discovery.last_discovery_meta()["auto"] is False


def test_discover_universe_caches(monkeypatch):
    calls = []

    def fetch(url: str) -> dict:
        calls.append(url)
        return _screen_payload([_quote("COIN")])

    first = discovery.discover_universe(fetch_json=fetch, force=True)
    n_calls = len(calls)
    second = discovery.discover_universe(fetch_json=fetch)  # served from cache
    assert second == first
    assert len(calls) == n_calls


def test_market_scanner_uses_universe_provider():
    scanner = MarketScanner(
        universe=("AAPL",),
        universe_provider=lambda: ["nvda", "tsla"],
    )
    assert scanner.current_universe() == ("NVDA", "TSLA")

    # Provider failure falls back to the static universe.
    def boom():
        raise RuntimeError("no data")

    scanner_fail = MarketScanner(universe=("AAPL",), universe_provider=boom)
    assert scanner_fail.current_universe() == ("AAPL",)
