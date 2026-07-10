"""Black-Scholes pricing and greeks, stdlib-only.

Used to attach deltas to option-chain rows (so strike shortlists can be
delta-banded) and to sanity-check strategist output. Precision beyond the
model's own assumptions isn't needed here — vendor implied vols are noisy
and paper fills happen at quoted mids, not model prices.
"""

from __future__ import annotations

import math

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _d1_d2(spot: float, strike: float, t: float, iv: float, rate: float) -> tuple[float, float]:
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    return d1, d1 - iv * math.sqrt(t)


def bs_price(
    spot: float, strike: float, t_years: float, iv: float,
    is_call: bool, rate: float = 0.05,
) -> float:
    """Black-Scholes European option price."""
    if t_years <= 0 or iv <= 0:
        # At/after expiry (or degenerate vol) the option is worth intrinsic.
        intrinsic = spot - strike if is_call else strike - spot
        return max(intrinsic, 0.0)
    d1, d2 = _d1_d2(spot, strike, t_years, iv, rate)
    if is_call:
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * t_years) * _norm_cdf(d2)
    return strike * math.exp(-rate * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_delta(
    spot: float, strike: float, t_years: float, iv: float,
    is_call: bool, rate: float = 0.05,
) -> float:
    """Option delta. Calls in (0, 1), puts in (-1, 0)."""
    if t_years <= 0 or iv <= 0:
        if is_call:
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    d1, _ = _d1_d2(spot, strike, t_years, iv, rate)
    return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0


def bs_gamma(spot: float, strike: float, t_years: float, iv: float, rate: float = 0.05) -> float:
    if t_years <= 0 or iv <= 0:
        return 0.0
    d1, _ = _d1_d2(spot, strike, t_years, iv, rate)
    return _norm_pdf(d1) / (spot * iv * math.sqrt(t_years))


def bs_theta_per_day(
    spot: float, strike: float, t_years: float, iv: float,
    is_call: bool, rate: float = 0.05,
) -> float:
    """Theta expressed per calendar day (typically negative for long options)."""
    if t_years <= 0 or iv <= 0:
        return 0.0
    d1, d2 = _d1_d2(spot, strike, t_years, iv, rate)
    term1 = -(spot * _norm_pdf(d1) * iv) / (2.0 * math.sqrt(t_years))
    if is_call:
        annual = term1 - rate * strike * math.exp(-rate * t_years) * _norm_cdf(d2)
    else:
        annual = term1 + rate * strike * math.exp(-rate * t_years) * _norm_cdf(-d2)
    return annual / 365.0


def bs_vega(spot: float, strike: float, t_years: float, iv: float, rate: float = 0.05) -> float:
    """Vega per 1.00 change in vol (divide by 100 for per-vol-point)."""
    if t_years <= 0 or iv <= 0:
        return 0.0
    d1, _ = _d1_d2(spot, strike, t_years, iv, rate)
    return spot * _norm_pdf(d1) * math.sqrt(t_years)


def implied_vol(
    price: float, spot: float, strike: float, t_years: float,
    is_call: bool, rate: float = 0.05,
) -> float | None:
    """Solve for implied volatility via bisection; None when unsolvable."""
    if t_years <= 0 or price <= 0:
        return None
    intrinsic = max(spot - strike if is_call else strike - spot, 0.0)
    if price <= intrinsic:
        return None
    lo, hi = 1e-4, 5.0
    if bs_price(spot, strike, t_years, hi, is_call, rate) < price:
        return None
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if bs_price(spot, strike, t_years, mid, is_call, rate) < price:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return 0.5 * (lo + hi)
