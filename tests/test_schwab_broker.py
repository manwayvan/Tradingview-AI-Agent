"""Tests for the live Schwab broker adapter — entirely offline via a fake client.

No real network calls, no real credentials. Verifies OCC symbol encoding,
order payload construction, fill-or-cancel behavior, and that failures
degrade the same way PaperBroker's do (a ValueError the pipeline already
knows how to record as a skipped trade).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from optionsagents.brokers.schwab_broker import SchwabBroker, _round_tick
from optionsagents.brokers.schwab_client import SchwabApiError
from optionsagents.brokers.symbols import from_occ_symbol, to_occ_symbol
from optionsagents.chain import ChainSnapshot, OptionQuote
from optionsagents.orders import OrderContext
from optionsagents.schemas import LegAction, OptionLeg, OptionRight, OptionsTradePlan, StrategyType

pytestmark = pytest.mark.unit

TODAY = date(2026, 7, 10)
EXP = (TODAY + timedelta(days=21)).isoformat()


def test_occ_symbol_roundtrip():
    sym = to_occ_symbol("AAPL", "2024-06-21", "call", 195.0)
    assert sym == "AAPL  240621C00195000"
    parsed = from_occ_symbol(sym)
    assert parsed == {
        "underlying": "AAPL", "expiry": "2024-06-21", "right": "call", "strike": 195.0,
    }


def test_occ_symbol_put_and_short_root():
    sym = to_occ_symbol("F", "2024-01-19", "put", 12.5)
    assert sym == "F     240119P00012500"


def test_round_tick():
    assert _round_tick(1.234) == 1.23   # penny tick under $3
    assert _round_tick(4.62) == 4.60    # nickel tick at/above $3
    assert _round_tick(4.68) == 4.70


class FakeSchwabClient:
    """Records placed orders; simulates fill/reject/timeout by pre-programmed sequence."""

    def __init__(self, fill_sequence: list[str] | None = None, fill_price: float | None = None):
        self.orders: dict[str, dict] = {}
        self.canceled: list[str] = []
        self._next_id = 1
        self._poll_calls: dict[str, int] = {}
        self._fill_sequence = fill_sequence or ["FILLED"]
        self._fill_price = fill_price

    def place_order(self, payload: dict) -> str:
        oid = str(self._next_id)
        self._next_id += 1
        self.orders[oid] = {"payload": payload}
        self._poll_calls[oid] = 0
        return oid

    def get_order(self, order_id: str) -> dict:
        idx = min(self._poll_calls[order_id], len(self._fill_sequence) - 1)
        self._poll_calls[order_id] += 1
        status = self._fill_sequence[idx]
        price = self._fill_price if self._fill_price is not None else float(self.orders[order_id]["payload"]["price"])
        return {
            "status": status,
            "orderActivityCollection": [{
                "executionLegs": [{"quantity": 1, "price": price}],
            }] if status == "FILLED" else [],
        }

    def cancel_order(self, order_id: str) -> None:
        self.canceled.append(order_id)

    def get_account(self) -> dict:
        return {"securitiesAccount": {"currentBalances": {
            "cashBalance": 8765.43, "liquidationValue": 12345.67,
        }}}


@pytest.fixture
def snapshot():
    return ChainSnapshot(
        underlying="AAPL", spot=190.0, asof=TODAY,
        quotes=[
            OptionQuote(expiry=EXP, right="call", strike=195, bid=4.90, ask=5.10, iv=0.4, volume=500, open_interest=1000, delta=0.4),
        ],
    )


def _long_call_plan():
    return OptionsTradePlan(
        strategy=StrategyType.LONG_CALL, underlying="AAPL", direction="bullish",
        legs=[OptionLeg(action=LegAction.BUY, right=OptionRight.CALL, strike=195, expiry=EXP, contracts=2)],
        net_price=5.0, price_type="debit", confidence=0.7,
    )


def _ctx():
    return OrderContext(source="autonomous", ticker="AAPL", mode="swing", signal="analyze")


def test_execute_plan_fills_and_records_position(tmp_path, snapshot):
    client = FakeSchwabClient(fill_sequence=["WORKING", "FILLED"], fill_price=5.05)
    broker = SchwabBroker(str(tmp_path / "live.json"), client=client)

    pos = broker.execute_plan(_long_call_plan(), snapshot, "swing", order_ctx=_ctx())

    assert pos is not None
    assert pos.entry_net == 5.05
    assert pos.legs[0].contracts == 2
    assert broker.positions("open") == [pos]
    # order payload used the correct OCC symbol and instruction
    placed = list(client.orders.values())[0]["payload"]
    assert placed["orderLegCollection"][0]["instruction"] == "BUY_TO_OPEN"
    assert placed["orderLegCollection"][0]["instrument"]["symbol"] == to_occ_symbol("AAPL", EXP, "call", 195)
    assert placed["orderType"] == "NET_DEBIT"


def test_execute_plan_cancels_and_raises_when_never_fills(tmp_path, snapshot, monkeypatch):
    import optionsagents.brokers.schwab_broker as mod
    monkeypatch.setattr(mod, "FILL_WAIT_SECONDS", 0.05)
    monkeypatch.setattr(mod, "POLL_INTERVAL_SECONDS", 0.02)

    client = FakeSchwabClient(fill_sequence=["WORKING"])  # never fills
    broker = SchwabBroker(str(tmp_path / "live.json"), client=client)

    with pytest.raises(ValueError, match="did not fill"):
        broker.execute_plan(_long_call_plan(), snapshot, "swing", order_ctx=_ctx())

    assert broker.positions("open") == []
    assert len(client.canceled) == 1


def test_execute_plan_raises_on_rejection(tmp_path, snapshot, monkeypatch):
    import optionsagents.brokers.schwab_broker as mod
    monkeypatch.setattr(mod, "POLL_INTERVAL_SECONDS", 0.02)

    client = FakeSchwabClient(fill_sequence=["REJECTED"])
    broker = SchwabBroker(str(tmp_path / "live.json"), client=client)

    with pytest.raises(ValueError, match="rejected"):
        broker.execute_plan(_long_call_plan(), snapshot, "swing", order_ctx=_ctx())
    assert broker.positions("open") == []


def test_close_position_reverses_instruction_and_order_type(tmp_path, snapshot, monkeypatch):
    import optionsagents.brokers.schwab_broker as mod
    monkeypatch.setattr(mod, "POLL_INTERVAL_SECONDS", 0.02)

    client = FakeSchwabClient(fill_sequence=["FILLED"], fill_price=5.05)
    broker = SchwabBroker(str(tmp_path / "live.json"), client=client)
    pos = broker.execute_plan(_long_call_plan(), snapshot, "swing", order_ctx=_ctx())

    client2 = FakeSchwabClient(fill_sequence=["FILLED"], fill_price=7.20)
    broker._client = client2
    closed = broker.close_position(pos.id, 7.20, reason="profit_target")

    assert closed.status == "closed"
    assert closed.exit_net == 7.20
    # long call (debit): pnl = (exit - entry) * 100 * contracts
    assert closed.realized_pnl == pytest.approx((7.20 - 5.05) * 100 * 2)
    placed = list(client2.orders.values())[0]["payload"]
    assert placed["orderLegCollection"][0]["instruction"] == "SELL_TO_CLOSE"
    assert placed["orderType"] == "NET_CREDIT"


def test_summary_uses_live_schwab_balance(tmp_path):
    client = FakeSchwabClient()
    broker = SchwabBroker(str(tmp_path / "live.json"), client=client)
    summary = broker.summary()
    assert summary["cash"] == 8765.43
    assert summary["equity"] == 12345.67
    assert summary["live_synced"] is True


def test_summary_degrades_gracefully_when_schwab_unreachable(tmp_path):
    class BrokenClient(FakeSchwabClient):
        def get_account(self) -> dict:
            raise SchwabApiError(500, "server error")

    broker = SchwabBroker(str(tmp_path / "live.json"), client=BrokenClient())
    summary = broker.summary()
    assert summary["live_synced"] is False
    assert "cash" in summary  # falls back to local ledger view, doesn't crash


def test_reset_account_disabled_for_live(tmp_path):
    broker = SchwabBroker(str(tmp_path / "live.json"), client=FakeSchwabClient())
    with pytest.raises(NotImplementedError):
        broker.reset_account(100_000.0)


def test_no_trade_plan_skips_without_touching_schwab(tmp_path, snapshot):
    client = FakeSchwabClient()
    broker = SchwabBroker(str(tmp_path / "live.json"), client=client)
    plan = OptionsTradePlan(
        strategy=StrategyType.NO_TRADE, underlying="AAPL", direction="neutral",
        rationale="standing aside",
    )
    pos = broker.execute_plan(plan, snapshot, "swing", order_ctx=_ctx())
    assert pos is None
    assert client.orders == {}
