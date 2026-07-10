"""Tests for plan schema validation and the TradingView alert payload."""

import pytest
from pydantic import ValidationError

from optionsagents.schemas import (
    LegAction,
    OptionLeg,
    OptionRight,
    OptionsTradePlan,
    StrategyType,
    TradingViewAlert,
)

pytestmark = pytest.mark.unit

EXP = "2026-08-21"


def leg(action, right, strike, contracts=1):
    return OptionLeg(action=action, right=right, strike=strike,
                     expiry=EXP, contracts=contracts)


def test_long_call_valid():
    plan = OptionsTradePlan(
        strategy=StrategyType.LONG_CALL, underlying="NVDA", direction="bullish",
        legs=[leg(LegAction.BUY, OptionRight.CALL, 150)], net_price=5.0,
    )
    assert plan.max_risk_per_contract() == pytest.approx(500)


def test_long_call_rejects_sell_leg():
    with pytest.raises(ValidationError, match="one buy leg"):
        OptionsTradePlan(
            strategy=StrategyType.LONG_CALL, underlying="NVDA", direction="bullish",
            legs=[leg(LegAction.SELL, OptionRight.CALL, 150)], net_price=5.0,
        )


def test_no_trade_rejects_legs():
    with pytest.raises(ValidationError, match="no legs"):
        OptionsTradePlan(
            strategy=StrategyType.NO_TRADE, underlying="NVDA", direction="neutral",
            legs=[leg(LegAction.BUY, OptionRight.CALL, 150)],
        )


def test_credit_spread_requires_credit_price_type():
    with pytest.raises(ValidationError, match="credit"):
        OptionsTradePlan(
            strategy=StrategyType.BULL_PUT_SPREAD, underlying="SPY", direction="bullish",
            legs=[
                leg(LegAction.SELL, OptionRight.PUT, 500),
                leg(LegAction.BUY, OptionRight.PUT, 495),
            ],
            net_price=1.5, price_type="debit",
        )


def test_credit_spread_max_risk_is_width_minus_credit():
    plan = OptionsTradePlan(
        strategy=StrategyType.BULL_PUT_SPREAD, underlying="SPY", direction="bullish",
        legs=[
            leg(LegAction.SELL, OptionRight.PUT, 500),
            leg(LegAction.BUY, OptionRight.PUT, 495),
        ],
        net_price=1.5, price_type="credit",
    )
    assert plan.max_risk_per_contract() == pytest.approx(350)  # (5 - 1.5) * 100


def test_spread_legs_must_match_expiry_right_and_contracts():
    with pytest.raises(ValidationError, match="equal contract"):
        OptionsTradePlan(
            strategy=StrategyType.BULL_CALL_SPREAD, underlying="SPY", direction="bullish",
            legs=[
                leg(LegAction.BUY, OptionRight.CALL, 500, contracts=2),
                leg(LegAction.SELL, OptionRight.CALL, 505, contracts=1),
            ],
            net_price=2.0,
        )
    with pytest.raises(ValidationError, match="calls"):
        OptionsTradePlan(
            strategy=StrategyType.BULL_CALL_SPREAD, underlying="SPY", direction="bullish",
            legs=[
                leg(LegAction.BUY, OptionRight.PUT, 500),
                leg(LegAction.SELL, OptionRight.PUT, 505),
            ],
            net_price=2.0,
        )


def test_alert_strips_exchange_prefix_and_defaults():
    alert = TradingViewAlert.model_validate(
        {"secret": "s", "ticker": "NASDAQ:nvda", "signal": "buy"}
    )
    assert alert.ticker == "NVDA"
    assert alert.mode == "day"


def test_alert_rejects_unknown_signal():
    with pytest.raises(ValidationError):
        TradingViewAlert.model_validate({"ticker": "SPY", "signal": "yolo"})
