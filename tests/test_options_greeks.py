"""Unit tests for the Black-Scholes helpers."""

import math

import pytest

from optionsagents.greeks import (
    bs_delta,
    bs_gamma,
    bs_price,
    bs_theta_per_day,
    bs_vega,
    implied_vol,
)

pytestmark = pytest.mark.unit

SPOT, STRIKE, T, IV, RATE = 100.0, 100.0, 30 / 365, 0.40, 0.05


def test_put_call_parity():
    call = bs_price(SPOT, STRIKE, T, IV, is_call=True, rate=RATE)
    put = bs_price(SPOT, STRIKE, T, IV, is_call=False, rate=RATE)
    parity = SPOT - STRIKE * math.exp(-RATE * T)
    assert call - put == pytest.approx(parity, abs=1e-9)


def test_atm_call_delta_near_half():
    delta = bs_delta(SPOT, STRIKE, T, IV, is_call=True)
    assert 0.5 < delta < 0.6  # slightly above 0.5 due to drift/lognormal skew


def test_deltas_bounded_and_signed():
    for k in (60, 80, 100, 120, 140):
        c = bs_delta(SPOT, k, T, IV, is_call=True)
        p = bs_delta(SPOT, k, T, IV, is_call=False)
        assert 0.0 <= c <= 1.0
        assert -1.0 <= p <= 0.0
        assert c - p == pytest.approx(1.0, abs=1e-9)  # call-put delta parity


def test_delta_monotonic_in_strike():
    deltas = [bs_delta(SPOT, k, T, IV, is_call=True) for k in (80, 90, 100, 110, 120)]
    assert deltas == sorted(deltas, reverse=True)


def test_expiry_returns_intrinsic():
    assert bs_price(110, 100, 0.0, IV, is_call=True) == pytest.approx(10.0)
    assert bs_price(90, 100, 0.0, IV, is_call=False) == pytest.approx(10.0)
    assert bs_price(90, 100, 0.0, IV, is_call=True) == 0.0


def test_long_option_theta_negative_gamma_vega_positive():
    assert bs_theta_per_day(SPOT, STRIKE, T, IV, is_call=True) < 0
    assert bs_gamma(SPOT, STRIKE, T, IV) > 0
    assert bs_vega(SPOT, STRIKE, T, IV) > 0


def test_implied_vol_round_trip():
    price = bs_price(SPOT, STRIKE, T, 0.35, is_call=True, rate=RATE)
    recovered = implied_vol(price, SPOT, STRIKE, T, is_call=True, rate=RATE)
    assert recovered == pytest.approx(0.35, abs=1e-4)


def test_implied_vol_below_intrinsic_is_none():
    assert implied_vol(5.0, 110, 100, T, is_call=True) is None
