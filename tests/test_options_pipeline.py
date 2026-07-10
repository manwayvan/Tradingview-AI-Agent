"""Tests for the pipeline's risk gate and rating mapping (no network/LLM)."""

import pytest

from optionsagents.pipeline import RATING_TO_DIRECTION, OptionsPipeline
from optionsagents.schemas import (
    LegAction,
    OptionLeg,
    OptionRight,
    OptionsTradePlan,
    StrategyType,
)

pytestmark = pytest.mark.unit

EXP = "2026-08-21"


@pytest.fixture
def pipeline(tmp_path):
    return OptionsPipeline(
        mode="day",  # max_risk_per_trade=500
        account_file=str(tmp_path / "acct.json"),
        use_llm_strategist=False,
    )


def plan(net_price, contracts=1):
    return OptionsTradePlan(
        strategy=StrategyType.LONG_CALL, underlying="TEST", direction="bullish",
        legs=[OptionLeg(action=LegAction.BUY, right=OptionRight.CALL,
                        strike=100, expiry=EXP, contracts=contracts)],
        net_price=net_price, price_type="debit",
    )


def test_rating_mapping_covers_all_tiers():
    assert RATING_TO_DIRECTION["buy"] == "bullish"
    assert RATING_TO_DIRECTION["overweight"] == "bullish"
    assert RATING_TO_DIRECTION["hold"] == "neutral"
    assert RATING_TO_DIRECTION["underweight"] == "bearish"
    assert RATING_TO_DIRECTION["sell"] == "bearish"


def test_risk_gate_clamps_contracts(pipeline):
    warnings = []
    # 2.00 debit = $200/contract risk; 5 contracts = $1000 > $500 limit
    gated = pipeline._risk_gate(plan(2.0, contracts=5), warnings)
    assert gated.contracts == 2
    assert any("clamped" in w for w in warnings)


def test_risk_gate_rejects_oversized_single_contract(pipeline):
    warnings = []
    gated = pipeline._risk_gate(plan(9.0), warnings)  # $900 > $500 limit
    assert gated.strategy == StrategyType.NO_TRADE
    assert gated.legs == []
    assert any("rejected" in w for w in warnings)


def test_risk_gate_passes_within_limit(pipeline):
    warnings = []
    gated = pipeline._risk_gate(plan(3.0), warnings)
    assert gated.contracts == 1
    assert warnings == []
