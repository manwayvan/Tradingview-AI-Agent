"""Automatic market-wide ticker discovery — no watchlist required.

Pulls candidates from Yahoo Finance's free public screeners (most actives,
day gainers, day losers, trending) and filters them down to liquid,
options-friendly names. Falls back to a built-in liquid universe whenever
the endpoints are unreachable, so scanning never stops.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from collections.abc import Callable

from optionsagents.autonomous.config import DEFAULT_UNIVERSE

logger = logging.getLogger(__name__)

# Always considered alongside discovered movers: index ETFs + mega-caps with
# the deepest options markets.
CORE_TICKERS: tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "NVDA", "TSLA", "AAPL", "MSFT", "AMZN", "META", "AMD",
)

SCREENS: tuple[str, ...] = ("most_actives", "day_gainers", "day_losers")
_SCREEN_URL = (
    "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved"
    "?scrIds={screen}&count={count}"
)
_TRENDING_URL = "https://query1.finance.yahoo.com/v1/finance/trending/US?count=20"

# Liquidity / quality filters — options need active, non-penny underlyings.
MIN_PRICE = 5.0
MAX_PRICE = 2000.0
MIN_DOLLAR_VOLUME = 25_000_000.0
MIN_MARKET_CAP = 2_000_000_000.0
MAX_UNIVERSE = 35

_OK_TTL = 600.0    # reuse a successful discovery for 10 minutes
_FAIL_TTL = 120.0  # retry sooner after a failure

FetchJson = Callable[[str], dict]

_lock = threading.Lock()
_cache: dict = {"tickers": [], "at": 0.0, "ttl": 0.0, "meta": {}}


def _default_fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (options-ai-agent)"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _screen_quotes(data: dict) -> list[dict]:
    try:
        return data["finance"]["result"][0]["quotes"] or []
    except (KeyError, IndexError, TypeError):
        return []


def _plain_symbol(sym: str) -> bool:
    """Plain US equity/ETF symbols only — no indices, futures, crypto, warrants."""
    return bool(sym) and len(sym) <= 5 and sym.isalpha()


def _tradeable(quote: dict) -> bool:
    sym = str(quote.get("symbol", "")).upper()
    if not _plain_symbol(sym):
        return False
    if quote.get("quoteType") not in (None, "EQUITY", "ETF"):
        return False
    try:
        price = float(quote.get("regularMarketPrice") or 0.0)
        volume = float(
            quote.get("regularMarketVolume")
            or quote.get("averageDailyVolume3Month")
            or 0.0
        )
    except (TypeError, ValueError):
        return False
    if not MIN_PRICE <= price <= MAX_PRICE:
        return False
    if price * volume < MIN_DOLLAR_VOLUME:
        return False
    cap = quote.get("marketCap")
    if cap is not None:
        try:
            if float(cap) < MIN_MARKET_CAP:
                return False
        except (TypeError, ValueError):
            pass
    return True


def discover_universe(
    max_size: int = MAX_UNIVERSE,
    fetch_json: FetchJson | None = None,
    force: bool = False,
) -> list[str]:
    """Return today's scan universe: core liquid names + discovered movers."""
    now = time.time()
    with _lock:
        if not force and _cache["tickers"] and now - _cache["at"] < _cache["ttl"]:
            return list(_cache["tickers"])

    fetch = fetch_json or _default_fetch_json
    discovered: list[str] = []
    meta: dict[str, int] = {}

    for screen in SCREENS:
        try:
            data = fetch(_SCREEN_URL.format(screen=screen, count=25))
            picked = [
                str(q["symbol"]).upper()
                for q in _screen_quotes(data)
                if _tradeable(q)
            ]
            meta[screen] = len(picked)
            discovered.extend(picked)
        except Exception as exc:
            logger.debug("screen %s unavailable: %s", screen, exc)

    try:
        data = fetch(_TRENDING_URL)
        trending = [
            str(q.get("symbol", "")).upper()
            for q in _screen_quotes(data)
            if _plain_symbol(str(q.get("symbol", "")).upper())
        ]
        meta["trending"] = len(trending)
        discovered.extend(trending)
    except Exception as exc:
        logger.debug("trending unavailable: %s", exc)

    seen: set[str] = set()
    universe: list[str] = []
    for ticker in list(CORE_TICKERS) + discovered:
        if ticker not in seen:
            seen.add(ticker)
            universe.append(ticker)

    if discovered:
        universe = universe[:max_size]
        ttl = _OK_TTL
    else:
        # Every free endpoint failed — fall back to the built-in liquid set.
        universe = list(DEFAULT_UNIVERSE)
        ttl = _FAIL_TTL

    with _lock:
        _cache.update({
            "tickers": list(universe),
            "at": now,
            "ttl": ttl,
            "meta": {**meta, "auto": bool(discovered)},
        })
    return universe


def last_discovery_meta() -> dict:
    """Counts per source from the most recent discovery (for the UI)."""
    with _lock:
        return dict(_cache["meta"])
