"""Technical signal rules matching the pine/ TradingView templates (free, in-app)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

import pandas as pd

FetchBars = Callable[[str, str], pd.DataFrame]


def _default_fetch(ticker: str, interval: str) -> pd.DataFrame:
    import yfinance as yf

    if interval == "5m":
        data = yf.Ticker(ticker).history(period="5d", interval="5m", auto_adjust=True)
    else:
        data = yf.Ticker(ticker).history(period="6mo", interval="1d", auto_adjust=True)
    if data.empty:
        return data
    if data.index.tz is not None:
        data.index = data.index.tz_localize(None)
    return data


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _session_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP reset per calendar day (matches intraday Pine logic)."""
    tp = (df["High"] + df["Low"] + df["Close"]) / 3.0
    out = pd.Series(index=df.index, dtype=float)
    for day, chunk in df.groupby(df.index.date):
        vol = chunk["Volume"].replace(0, pd.NA)
        cum_vol = vol.cumsum()
        cum_pv = (tp.loc[chunk.index] * vol).cumsum()
        out.loc[chunk.index] = cum_pv / cum_vol
    return out


@dataclass(frozen=True)
class DaySignalResult:
    signal: str  # buy | sell
    price: float
    note: str


@dataclass(frozen=True)
class SwingSignalResult:
    signal: str  # analyze
    price: float
    note: str
    direction_hint: str  # bullish | bearish


def scan_day_signal(
    ticker: str,
    fetch_bars: FetchBars | None = None,
) -> DaySignalResult | None:
    """EMA9/21 cross + VWAP filter on 5-minute bars (pine/day_trade_signal.pine)."""
    fetch_bars = fetch_bars or _default_fetch
    df = fetch_bars(ticker, "5m")
    if len(df) < 25:
        return None

    close = df["Close"]
    ema_fast = _ema(close, 9)
    ema_slow = _ema(close, 21)
    vwap = _session_vwap(df)

    prev_fast, curr_fast = float(ema_fast.iloc[-2]), float(ema_fast.iloc[-1])
    prev_slow, curr_slow = float(ema_slow.iloc[-2]), float(ema_slow.iloc[-1])
    price = float(close.iloc[-1])
    vwap_now = float(vwap.iloc[-1])

    if prev_fast <= prev_slow and curr_fast > curr_slow and price > vwap_now:
        return DaySignalResult(
            signal="buy",
            price=price,
            note=f"Free day signal: EMA9/21 bullish cross above VWAP on 5m ({ticker})",
        )
    if prev_fast >= prev_slow and curr_fast < curr_slow and price < vwap_now:
        return DaySignalResult(
            signal="sell",
            price=price,
            note=f"Free day signal: EMA9/21 bearish cross below VWAP on 5m ({ticker})",
        )
    return None


def scan_swing_signal(
    ticker: str,
    trade_date: str | None = None,
    fetch_bars: FetchBars | None = None,
) -> SwingSignalResult | None:
    """EMA20/50 cross + RSI band on daily bars (pine/swing_trade_signal.pine)."""
    fetch_bars = fetch_bars or _default_fetch
    df = fetch_bars(ticker, "1d")
    if len(df) < 55:
        return None

    close = df["Close"]
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    rsi = _rsi(close, 14)

    prev20, curr20 = float(ema20.iloc[-2]), float(ema20.iloc[-1])
    prev50, curr50 = float(ema50.iloc[-2]), float(ema50.iloc[-1])
    rsi_now = float(rsi.iloc[-1])
    price = float(close.iloc[-1])
    bar_date = str(df.index[-1].date())

    trade_date = trade_date or date.today().isoformat()
    if bar_date > trade_date:
        return None

    if prev20 <= prev50 and curr20 > curr50 and rsi_now < 70:
        return SwingSignalResult(
            signal="analyze",
            price=price,
            note=f"Free swing signal: daily EMA20/50 bullish cross, RSI {rsi_now:.1f}",
            direction_hint="bullish",
        )
    if prev20 >= prev50 and curr20 < curr50 and rsi_now > 30:
        return SwingSignalResult(
            signal="analyze",
            price=price,
            note=f"Free swing signal: daily EMA20/50 bearish cross, RSI {rsi_now:.1f}",
            direction_hint="bearish",
        )
    return None
