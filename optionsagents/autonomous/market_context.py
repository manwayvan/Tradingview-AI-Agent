"""Market regime and macro context for the strategy brain."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

FetchHistory = Callable[[str, str, str], pd.DataFrame]


def _default_fetch(ticker: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf

    data = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
    if data.empty:
        return data
    if data.index.tz is not None:
        data.index = data.index.tz_localize(None)
    return data


@dataclass(frozen=True)
class MarketContext:
    """Snapshot of broad-market conditions used by the strategy brain."""

    spy_return_20d: float
    spy_return_5d: float
    vix_level: float | None
    regime: str           # risk_on | risk_off | neutral | volatile
    assessment: str       # human-readable summary for the LLM

    def to_prompt_block(self) -> str:
        vix = f"{self.vix_level:.1f}" if self.vix_level is not None else "n/a"
        return (
            f"Market regime: {self.regime}\n"
            f"SPY 5-day return: {self.spy_return_5d:+.1%}\n"
            f"SPY 20-day return: {self.spy_return_20d:+.1%}\n"
            f"VIX: {vix}\n"
            f"Assessment: {self.assessment}"
        )


def _pct_return(series: pd.Series, days: int) -> float:
    if len(series) < days + 1:
        return 0.0
    start = float(series.iloc[-days - 1])
    end = float(series.iloc[-1])
    if start <= 0:
        return 0.0
    return (end - start) / start


def build_market_context(
    trade_date: str | None = None,
    fetch_history: FetchHistory | None = None,
) -> MarketContext:
    """Build a regime snapshot from SPY and VIX data."""
    fetch_history = fetch_history or _default_fetch
    trade_date = trade_date or date.today().isoformat()
    end_dt = date.fromisoformat(trade_date) + timedelta(days=1)
    start_dt = end_dt - timedelta(days=90)
    start = start_dt.isoformat()
    end = end_dt.isoformat()

    spy = fetch_history("SPY", start, end)
    spy_close = spy["Close"] if not spy.empty and "Close" in spy.columns else pd.Series(dtype=float)
    ret_5d = _pct_return(spy_close, 5)
    ret_20d = _pct_return(spy_close, 20)

    vix_level = None
    for vix_ticker in ("^VIX", "VIXY"):
        try:
            vix = fetch_history(vix_ticker, start, end)
            if not vix.empty:
                vix_level = float(vix["Close"].iloc[-1])
                if vix_ticker == "VIXY":
                    # VIXY is not VIX; use as volatility proxy only when ^VIX fails.
                    vix_level = None
                break
        except Exception as exc:
            logger.debug("could not fetch %s: %s", vix_ticker, exc)

    regime = "neutral"
    if vix_level is not None and vix_level >= 25:
        regime = "volatile"
    elif ret_20d >= 0.03 and ret_5d >= 0:
        regime = "risk_on"
    elif ret_20d <= -0.03 or ret_5d <= -0.02:
        regime = "risk_off"

    if regime == "volatile":
        assessment = (
            "Elevated fear/volatility — prefer defined-risk spreads, smaller size, "
            "and higher conviction before trading."
        )
    elif regime == "risk_on":
        assessment = "Trend supportive — momentum long setups and swing holds favored."
    elif regime == "risk_off":
        assessment = (
            "Risk-off tape — be selective; bearish setups or stand aside unless "
            "conviction is very high."
        )
    else:
        assessment = "Mixed/choppy conditions — favor liquid ETFs and tight risk control."

    return MarketContext(
        spy_return_20d=ret_20d,
        spy_return_5d=ret_5d,
        vix_level=vix_level,
        regime=regime,
        assessment=assessment,
    )
