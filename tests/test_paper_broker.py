"""Tests for the paper broker: fills, cash accounting, exits, persistence."""

from datetime import date, timedelta

import pytest

from optionsagents.chain import ChainSnapshot, OptionQuote
from optionsagents.paper_broker import PaperBroker
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


def quote(right, strike, bid, ask, **kw):
    return OptionQuote(
        expiry=EXP, right=right, strike=strike, bid=bid, ask=ask,
        iv=kw.get("iv", 0.4), volume=kw.get("volume", 500),
        open_interest=kw.get("oi", 1000), delta=kw.get("delta", 0.5),
    )


def snapshot(call_mid=4.0, put_mid=4.0, spread=0.10):
    h = spread / 2
    return ChainSnapshot(
        underlying="TEST", spot=100.0, asof=TODAY,
        quotes=[
            quote("call", 100, call_mid - h, call_mid + h),
            quote("call", 105, 2.0 - h, 2.0 + h),
            quote("put", 100, put_mid - h, put_mid + h),
            quote("put", 95, 2.0 - h, 2.0 + h),
        ],
    )


def long_call_plan(contracts=1):
    return OptionsTradePlan(
        strategy=StrategyType.LONG_CALL, underlying="TEST", direction="bullish",
        legs=[OptionLeg(action=LegAction.BUY, right=OptionRight.CALL,
                        strike=100, expiry=EXP, contracts=contracts)],
        net_price=4.0, price_type="debit",
        profit_target_pct=50, stop_loss_pct=30,
    )


def credit_spread_plan():
    return OptionsTradePlan(
        strategy=StrategyType.BULL_PUT_SPREAD, underlying="TEST", direction="bullish",
        legs=[
            OptionLeg(action=LegAction.SELL, right=OptionRight.PUT, strike=100, expiry=EXP),
            OptionLeg(action=LegAction.BUY, right=OptionRight.PUT, strike=95, expiry=EXP),
        ],
        net_price=2.0, price_type="credit",
        profit_target_pct=50, stop_loss_pct=100,
    )


@pytest.fixture
def broker(tmp_path):
    return PaperBroker(str(tmp_path / "acct.json"), starting_cash=10_000, slippage_pct=0.0)


def test_long_call_round_trip_pnl(broker):
    pos = broker.execute_plan(long_call_plan(), snapshot())
    assert pos is not None
    assert pos.entry_net == pytest.approx(4.0)
    assert broker.cash == pytest.approx(10_000 - 400)
    assert pos.max_risk == pytest.approx(400)

    closed = broker.close_position(pos.id, net_now=6.0)
    assert closed.realized_pnl == pytest.approx(200)      # (6-4) * 100
    assert broker.cash == pytest.approx(10_000 + 200)


def test_credit_spread_accounting(broker):
    pos = broker.execute_plan(credit_spread_plan(), snapshot())
    # width 5 - credit 2 = 3.00/share max risk; that cash is locked at open
    assert pos.max_risk == pytest.approx(300)
    assert broker.cash == pytest.approx(10_000 - 300)

    # Buy back at 0.50: profit = (2.00 - 0.50) * 100 = 150
    closed = broker.close_position(pos.id, net_now=0.5)
    assert closed.realized_pnl == pytest.approx(150)
    assert broker.cash == pytest.approx(10_150)


def test_credit_spread_losing_close(broker):
    pos = broker.execute_plan(credit_spread_plan(), snapshot())
    closed = broker.close_position(pos.id, net_now=4.0)   # paying 4 to close
    assert closed.realized_pnl == pytest.approx(-200)
    assert broker.cash == pytest.approx(9_800)


def test_slippage_worsens_fills(tmp_path):
    broker = PaperBroker(str(tmp_path / "a.json"), starting_cash=10_000, slippage_pct=5.0)
    pos = broker.execute_plan(long_call_plan(), snapshot())
    assert pos.entry_net == pytest.approx(4.20)           # mid 4.00 + 5%


def test_insufficient_cash_rejected(tmp_path):
    broker = PaperBroker(str(tmp_path / "a.json"), starting_cash=100, slippage_pct=0.0)
    with pytest.raises(ValueError, match="insufficient"):
        broker.execute_plan(long_call_plan(), snapshot())


def test_no_trade_plan_journals_without_position(broker):
    plan = OptionsTradePlan(
        strategy=StrategyType.NO_TRADE, underlying="TEST", direction="neutral",
    )
    assert broker.execute_plan(plan, snapshot()) is None
    assert broker.positions() == []
    assert broker._state["journal"][-1]["event"] == "no_trade"


def test_profit_target_exit(broker):
    pos = broker.execute_plan(long_call_plan(), snapshot())
    # target 50% of 400 risk = +200 -> mid 6.00 triggers it
    closed = broker.check_exits(pos, snapshot(call_mid=6.0))
    assert closed is not None and closed.exit_reason == "profit_target"


def test_stop_loss_exit(broker):
    pos = broker.execute_plan(long_call_plan(), snapshot())
    # stop 30% of 400 = -120 -> mid 2.50 (loss 150) triggers it
    closed = broker.check_exits(pos, snapshot(call_mid=2.5))
    assert closed is not None and closed.exit_reason == "stop_loss"


def test_no_exit_inside_bands(broker):
    pos = broker.execute_plan(long_call_plan(), snapshot())
    assert broker.check_exits(pos, snapshot(call_mid=4.5)) is None
    assert pos.status == "open"
    assert pos.unrealized_pnl == pytest.approx(50)


def test_persistence_round_trip(tmp_path):
    path = str(tmp_path / "acct.json")
    b1 = PaperBroker(path, starting_cash=10_000, slippage_pct=0.0)
    pos = b1.execute_plan(long_call_plan(), snapshot())

    b2 = PaperBroker(path, slippage_pct=0.0)  # reload from disk
    assert b2.cash == pytest.approx(10_000 - 400)
    loaded = b2.get_position(pos.id)
    assert loaded is not None and loaded.strategy == "long_call"
    b2.close_position(loaded.id, net_now=4.0)
    assert b2.cash == pytest.approx(10_000)


def test_position_lookup_by_prefix(broker):
    pos = broker.execute_plan(long_call_plan(), snapshot())
    assert broker.get_position(pos.id[:4]).id == pos.id
