"""Earnings calendar checks to avoid IV-crush entries."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from functools import lru_cache

logger = logging.getLogger(__name__)

DEFAULT_BLOCK_DAYS = 2


def _parse_earnings_date(raw) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


@lru_cache(maxsize=256)
def days_to_next_earnings(ticker: str, asof: str | None = None) -> int | None:
    """Calendar days until next reported earnings, or None if unknown."""
    asof_date = date.fromisoformat(asof) if asof else date.today()
    try:
        import yfinance as yf

        tk = yf.Ticker(ticker.upper())
        candidates: list[date] = []

        cal = getattr(tk, "calendar", None)
        if cal is not None:
            if hasattr(cal, "get"):
                earn = cal.get("Earnings Date") or cal.get("EarningsDate")
                if earn is not None:
                    if isinstance(earn, (list, tuple)):
                        for item in earn:
                            d = _parse_earnings_date(item)
                            if d:
                                candidates.append(d)
                    else:
                        d = _parse_earnings_date(earn)
                        if d:
                            candidates.append(d)
            elif hasattr(cal, "empty") and not cal.empty:
                for col in cal.columns:
                    if "earn" in str(col).lower():
                        for val in cal[col].tolist():
                            d = _parse_earnings_date(val)
                            if d:
                                candidates.append(d)

        try:
            edf = tk.get_earnings_dates(limit=4)
            if edf is not None and not edf.empty:
                for idx in edf.index:
                    d = _parse_earnings_date(idx)
                    if d:
                        candidates.append(d)
        except Exception:
            pass

        future = sorted({d for d in candidates if d >= asof_date})
        if not future:
            return None
        return (future[0] - asof_date).days
    except Exception as exc:
        logger.debug("earnings lookup failed for %s: %s", ticker, exc)
        return None


def earnings_block_reason(
    ticker: str,
    *,
    block_days: int = DEFAULT_BLOCK_DAYS,
    asof: str | None = None,
) -> tuple[bool, str]:
    """Return (blocked, reason) for new entries near earnings."""
    days = days_to_next_earnings(ticker.upper(), asof)
    if days is None:
        return False, ""
    if 0 <= days <= block_days:
        when = "today" if days == 0 else f"in {days} day(s)"
        return True, f"earnings {when} — new entries blocked within {block_days} days of report"
    return False, ""
