"""OCC option symbol formatting.

Schwab's Trader API (like every US options broker, including the old TD
Ameritrade API it replaced) identifies option contracts with the standard
OCC 21-character symbol: 6-char root (space-padded), 6-digit expiry
(YYMMDD), C/P, and an 8-digit strike (dollars * 1000, zero-padded).

Example: AAPL $195 call expiring 2024-06-21 -> "AAPL  240621C00195000"
"""

from __future__ import annotations

from datetime import datetime


def to_occ_symbol(underlying: str, expiry: str, right: str, strike: float) -> str:
    """``expiry`` is YYYY-MM-DD, ``right`` is 'call'/'put' (or 'C'/'P')."""
    root = underlying.upper().strip()[:6].ljust(6)
    yymmdd = datetime.strptime(expiry, "%Y-%m-%d").strftime("%y%m%d")
    cp = "C" if right[0].upper() == "C" else "P"
    strike_int = round(strike * 1000)
    if strike_int < 0 or strike_int > 99_999_999:
        raise ValueError(f"strike {strike} out of representable range for OCC symbol")
    return f"{root}{yymmdd}{cp}{strike_int:08d}"


def from_occ_symbol(symbol: str) -> dict:
    """Parse an OCC symbol back into its parts (inverse of ``to_occ_symbol``)."""
    if len(symbol) < 21:
        raise ValueError(f"not a valid OCC symbol: {symbol!r}")
    root = symbol[:6].strip()
    yymmdd = symbol[6:12]
    cp = symbol[12]
    strike_raw = symbol[13:21]
    expiry = datetime.strptime(yymmdd, "%y%m%d").strftime("%Y-%m-%d")
    strike = int(strike_raw) / 1000.0
    return {
        "underlying": root,
        "expiry": expiry,
        "right": "call" if cp == "C" else "put",
        "strike": strike,
    }
