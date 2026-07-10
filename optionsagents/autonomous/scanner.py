"""Multi-factor stock screener for autonomous opportunity discovery."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from optionsagents.autonomous.config import DEFAULT_UNIVERSE

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


def _rsi(closes: pd.Series, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] else 0.0
    return float(100 - (100 / (1 + rs)))


def _pct_return(closes: pd.Series, days: int) -> float:
    if len(closes) < days + 1:
        return 0.0
    start = float(closes.iloc[-days - 1])
    end = float(closes.iloc[-1])
    if start <= 0:
        return 0.0
    return (end - start) / start


def _annualized_vol(closes: pd.Series, window: int = 20) -> float:
    if len(closes) < window + 1:
        return 0.0
    rets = closes.pct_change().dropna().tail(window)
    if rets.empty:
        return 0.0
    return float(rets.std() * (252 ** 0.5))


@dataclass
class StockCandidate:
    ticker: str
    score: float
    return_5d: float
    return_20d: float
    return_60d: float
    rel_strength_vs_spy: float
    rsi_14: float
    volume_ratio: float
    above_sma50: bool
    volatility: float
    last_price: float
    factors: dict[str, float] = field(default_factory=dict)

    def to_summary_line(self) -> str:
        trend = "above 50 SMA" if self.above_sma50 else "below 50 SMA"
        return (
            f"{self.ticker}: score={self.score:.2f}, "
            f"5d={self.return_5d:+.1%}, 20d={self.return_20d:+.1%}, "
            f"RS vs SPY={self.rel_strength_vs_spy:+.1%}, RSI={self.rsi_14:.0f}, "
            f"vol={self.volatility:.0%}, {trend}, ${self.last_price:.2f}"
        )


class MarketScanner:
    """Ranks a ticker universe using momentum, relative strength, and liquidity."""

    def __init__(
        self,
        universe: tuple[str, ...] | None = None,
        fetch_history: FetchHistory | None = None,
    ):
        self.universe = universe or DEFAULT_UNIVERSE
        self.fetch_history = fetch_history or _default_fetch

    def scan(
        self,
        trade_date: str | None = None,
        top_n: int = 12,
        benchmark: str = "SPY",
    ) -> list[StockCandidate]:
        trade_date = trade_date or date.today().isoformat()
        end_dt = date.fromisoformat(trade_date) + timedelta(days=1)
        start_dt = end_dt - timedelta(days=120)
        start = start_dt.isoformat()
        end = end_dt.isoformat()

        bench = self.fetch_history(benchmark, start, end)
        bench_ret_20d = _pct_return(bench["Close"], 20) if not bench.empty else 0.0

        candidates: list[StockCandidate] = []
        for ticker in self.universe:
            if ticker.upper() == benchmark.upper():
                continue
            try:
                data = self.fetch_history(ticker, start, end)
                if data.empty or "Close" not in data.columns:
                    continue
                closes = data["Close"]
                volumes = data.get("Volume", pd.Series(dtype=float))
                last = float(closes.iloc[-1])
                if last <= 0:
                    continue

                ret_5d = _pct_return(closes, 5)
                ret_20d = _pct_return(closes, 20)
                ret_60d = _pct_return(closes, 60)
                rel = ret_20d - bench_ret_20d
                rsi = _rsi(closes)
                vol = _annualized_vol(closes)

                vol_ratio = 1.0
                if not volumes.empty and len(volumes) >= 21:
                    recent = float(volumes.tail(5).mean())
                    avg = float(volumes.tail(21).mean())
                    vol_ratio = recent / avg if avg > 0 else 1.0

                sma50 = float(closes.tail(50).mean()) if len(closes) >= 50 else last
                above_sma50 = last >= sma50

                factors = {
                    "momentum_5d": ret_5d,
                    "momentum_20d": ret_20d,
                    "momentum_60d": ret_60d,
                    "rel_strength": rel,
                    "rsi": rsi,
                    "volume_surge": min(vol_ratio, 3.0) / 3.0,
                    "trend": 1.0 if above_sma50 else 0.0,
                    "volatility": min(vol, 1.0),
                }
                # Weighted composite: momentum + relative strength dominate.
                score = (
                    0.22 * _normalize(ret_5d, -0.08, 0.08)
                    + 0.28 * _normalize(ret_20d, -0.15, 0.15)
                    + 0.18 * _normalize(ret_60d, -0.25, 0.25)
                    + 0.20 * _normalize(rel, -0.10, 0.10)
                    + 0.07 * _normalize(rsi, 30, 70)
                    + 0.05 * factors["volume_surge"]
                )
                if above_sma50:
                    score += 0.05
                # Penalize extremely low or extreme RSI (overbought/oversold caution).
                if rsi > 75 or rsi < 25:
                    score -= 0.05

                candidates.append(
                    StockCandidate(
                        ticker=ticker.upper(),
                        score=round(score, 4),
                        return_5d=ret_5d,
                        return_20d=ret_20d,
                        return_60d=ret_60d,
                        rel_strength_vs_spy=rel,
                        rsi_14=rsi,
                        volume_ratio=vol_ratio,
                        above_sma50=above_sma50,
                        volatility=vol,
                        last_price=last,
                        factors=factors,
                    )
                )
            except Exception as exc:
                logger.debug("scanner skip %s: %s", ticker, exc)

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:top_n]


def _normalize(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.5
    clamped = max(low, min(high, value))
    return (clamped - low) / (high - low)
