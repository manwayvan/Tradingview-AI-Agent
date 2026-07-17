"""Tests for chain analysis, plan validation, and the fallback plan builder.

All tests run on synthetic snapshots; no network access.
"""

from datetime import date, timedelta

import pytest

from optionsagents.chain import (
    ChainSnapshot,
    OptionQuote,
    build_default_plan,
    plan_mid_price,
    render_chain_report,
    validate_plan_against_chain,
)
from optionsagents.modes import get_mode
from optionsagents.schemas import (
    LegAction,
    OptionLeg,
    OptionRight,
    OptionsTradePlan,
    StrategyType,
)

pytestmark = pytest.mark.unit

TODAY = date(2026, 7, 10)
EXP = (TODAY + timedelta(days=21)).isoformat()


def quote(right, strike, bid, ask, iv=0.40, delta=0.5, oi=1000, vol=500):
    return OptionQuote(
        expiry=EXP, right=right, strike=strike, bid=bid, ask=ask,
        iv=iv, volume=vol, open_interest=oi, delta=delta,
    )


@pytest.fixture
def snapshot():
    return ChainSnapshot(
        underlying="TEST", spot=100.0, asof=TODAY,
        quotes=[
            quote("call", 95, 6.90, 7.10, delta=0.68),
            quote("call", 100, 3.90, 4.10, delta=0.52),
            quote("call", 105, 1.95, 2.05, delta=0.35),
            quote("call", 110, 0.90, 1.00, delta=0.20, oi=50),   # thin OI
            quote("put", 105, 6.90, 7.10, delta=-0.65),
            quote("put", 100, 3.90, 4.10, delta=-0.48),
            quote("put", 95, 1.95, 2.05, delta=-0.32),
        ],
    )


def test_metrics(snapshot):
    assert snapshot.atm_iv() == pytest.approx(0.40)
    # ATM straddle = 4.00 + 4.00 = 8.00 -> 8% expected move
    assert snapshot.expected_move_pct() == pytest.approx(8.0)
    assert snapshot.put_call_volume_ratio() == pytest.approx(3 / 4)
    assert snapshot.expiries() == [EXP]


def test_candidates_respect_filters(snapshot):
    mode = get_mode("swing")
    calls = snapshot.candidates(mode, "call")
    strikes = [q.strike for q in calls]
    assert 100 in strikes and 105 in strikes
    assert 110 not in strikes  # open interest below minimum
    assert 95 not in strikes   # delta 0.68 above swing band


def test_render_report_mentions_candidates(snapshot):
    report = render_chain_report(snapshot, get_mode("swing"))
    assert "Candidate calls" in report
    assert "$100.00" in report


def test_validate_plan_catches_missing_strike(snapshot):
    plan = OptionsTradePlan(
        strategy=StrategyType.LONG_CALL, underlying="TEST", direction="bullish",
        legs=[OptionLeg(action=LegAction.BUY, right=OptionRight.CALL,
                        strike=999, expiry=EXP)],
        net_price=1.0,
    )
    problems = validate_plan_against_chain(plan, snapshot)
    assert problems and "999" in problems[0]


def test_plan_mid_price_debit_spread(snapshot):
    plan = OptionsTradePlan(
        strategy=StrategyType.BULL_CALL_SPREAD, underlying="TEST", direction="bullish",
        legs=[
            OptionLeg(action=LegAction.BUY, right=OptionRight.CALL, strike=100, expiry=EXP),
            OptionLeg(action=LegAction.SELL, right=OptionRight.CALL, strike=105, expiry=EXP),
        ],
        net_price=2.0, price_type="debit",
    )
    assert validate_plan_against_chain(plan, snapshot) == []
    # 4.00 mid - 2.00 mid = 2.00 debit
    assert plan_mid_price(plan, snapshot) == pytest.approx(2.0)


def test_fallback_plan_bullish(snapshot):
    plan = build_default_plan("bullish", snapshot, get_mode("swing"))
    assert plan.strategy == StrategyType.LONG_CALL
    assert plan.legs[0].strike == 100  # delta 0.52, closest to band midpoint 0.45
    assert plan.net_price == pytest.approx(4.0)
    assert validate_plan_against_chain(plan, snapshot) == []


def test_fallback_plan_neutral_is_no_trade(snapshot):
    plan = build_default_plan("neutral", snapshot, get_mode("swing"))
    assert plan.strategy == StrategyType.NO_TRADE
    assert plan.legs == []


def test_fallback_plan_illiquid_chain_is_no_trade():
    empty = ChainSnapshot(underlying="TEST", spot=100.0, asof=TODAY, quotes=[])
    plan = build_default_plan("bullish", empty, get_mode("swing"))
    assert plan.strategy == StrategyType.NO_TRADE


EXP2 = (TODAY + timedelta(days=35)).isoformat()


def _multi_expiry_snapshot(iv_rank: float) -> ChainSnapshot:
    """Two expiries both offer in-band-delta strikes, so a naive spread
    picker (sorting by strike across the whole candidate pool) can pair
    legs from different expiries — reproduces the production bug where
    AAPL's autonomous cycle threw 'spread legs must share expiry and right'."""
    return ChainSnapshot(
        underlying="TEST", spot=100.0, asof=TODAY, iv_rank=iv_rank,
        quotes=[
            OptionQuote(expiry=EXP, right="put", strike=100, bid=3.90, ask=4.10, iv=0.40, volume=500, open_interest=1000, delta=-0.48),
            OptionQuote(expiry=EXP, right="put", strike=95, bid=1.95, ask=2.05, iv=0.40, volume=500, open_interest=1000, delta=-0.32),
            OptionQuote(expiry=EXP2, right="put", strike=99, bid=3.60, ask=3.80, iv=0.40, volume=500, open_interest=1000, delta=-0.47),
            OptionQuote(expiry=EXP, right="call", strike=100, bid=3.90, ask=4.10, iv=0.40, volume=500, open_interest=1000, delta=0.52),
            OptionQuote(expiry=EXP, right="call", strike=105, bid=1.95, ask=2.05, iv=0.40, volume=500, open_interest=1000, delta=0.35),
            OptionQuote(expiry=EXP2, right="call", strike=101, bid=3.60, ask=3.80, iv=0.40, volume=500, open_interest=1000, delta=0.53),
        ],
    )


def test_fallback_credit_spread_keeps_legs_on_one_expiry_bullish():
    snapshot = _multi_expiry_snapshot(iv_rank=70.0)
    plan = build_default_plan("bullish", snapshot, get_mode("swing"))
    assert plan.strategy == StrategyType.BULL_PUT_SPREAD
    assert plan.legs[0].expiry == plan.legs[1].expiry
    assert validate_plan_against_chain(plan, snapshot) == []


def test_fallback_credit_spread_keeps_legs_on_one_expiry_bearish():
    snapshot = _multi_expiry_snapshot(iv_rank=70.0)
    plan = build_default_plan("bearish", snapshot, get_mode("swing"))
    assert plan.strategy == StrategyType.BEAR_CALL_SPREAD
    assert plan.legs[0].expiry == plan.legs[1].expiry
    assert validate_plan_against_chain(plan, snapshot) == []
