"""Live Schwab execution.

``SchwabBroker`` extends ``PaperBroker`` so every call site in the app
(pipeline, orchestrator, webapp, "why?" order-detail view, stats) keeps
working unmodified — it inherits the same JSON-backed position ledger and
order/journal bookkeeping, and only overrides the methods that actually
move money: opening a position, closing one, and reading account balance.

Design choices driven by real-money safety:
- Orders are submitted "fill or cancel within N seconds" — never left
  dangling as a live working order the system has lost track of. If it
  doesn't fill in time, it's canceled and recorded as a skipped trade,
  exactly like the paper broker's "insufficient cash" path.
- Schwab is the source of truth for cash/equity (``summary()`` queries
  the live account); the local ledger exists for the audit trail (why a
  trade happened, stats by source/mode) the rest of the app already
  renders.
- ``reset_account`` is disabled — there's no "reset" on a real brokerage
  account.
"""

from __future__ import annotations

import logging
import time
import uuid

from optionsagents.brokers.schwab_client import SchwabApiError, SchwabAuthError, SchwabClient
from optionsagents.brokers.symbols import to_occ_symbol
from optionsagents.chain import ChainSnapshot, plan_mid_price
from optionsagents.orders import OrderContext
from optionsagents.paper_broker import OPTION_MULTIPLIER, LegFill, PaperBroker, Position
from optionsagents.schemas import OptionsTradePlan, StrategyType

logger = logging.getLogger(__name__)

FILL_WAIT_SECONDS = 12.0
POLL_INTERVAL_SECONDS = 1.5
FILLED_STATUSES = {"FILLED", "EXECUTED"}
DEAD_STATUSES = {"REJECTED", "CANCELED", "EXPIRED"}


def _round_tick(price: float) -> float:
    """OPRA-style tick rounding: $0.01 under $3, $0.05 at/above."""
    tick = 0.01 if price < 3.0 else 0.05
    return round(round(price / tick) * tick, 2)


def _build_order_payload(
    legs: list[dict], price: float, order_type: str,
) -> dict:
    """``legs`` items: {"instruction": ..., "symbol": ..., "quantity": ...}."""
    return {
        "orderType": order_type,
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "price": f"{price:.2f}",
        "orderLegCollection": [
            {
                "instruction": leg["instruction"],
                "quantity": leg["quantity"],
                "instrument": {"symbol": leg["symbol"], "assetType": "OPTION"},
            }
            for leg in legs
        ],
    }


class SchwabBroker(PaperBroker):
    """Places real orders through Schwab; mirrors fills into the same
    local ledger shape ``PaperBroker`` uses so the rest of the app is
    unaware which broker it's talking to."""

    def __init__(self, account_file: str, client: SchwabClient):
        super().__init__(account_file, starting_cash=0.0, slippage_pct=0.0)
        self._client = client

    # ---- account state (Schwab is authoritative) ------------------------

    def summary(self) -> dict:
        local = super().summary()
        try:
            account = self._client.get_account()
            bal = account.get("securitiesAccount", {}).get("currentBalances", {})
            cash = bal.get("cashBalance")
            equity = bal.get("liquidationValue")
            if cash is not None and equity is not None:
                local["cash"] = round(float(cash), 2)
                local["equity"] = round(float(equity), 2)
                local["live_synced"] = True
                return local
            logger.warning("Schwab account response missing expected balance fields")
        except SchwabAuthError as exc:
            local["live_synced"] = False
            local["live_error"] = str(exc)
            return local
        except SchwabApiError as exc:
            logger.warning("could not fetch live Schwab balance: %s", exc)
        local["live_synced"] = False
        return local

    def reset_account(self, starting_cash: float, *, clear_history: bool = True) -> dict:
        raise NotImplementedError(
            "Cannot reset a live brokerage account — manage funding/positions "
            "directly with Schwab. Switch to the paper account to reset that instead."
        )

    # ---- order construction ----------------------------------------------

    def _open_legs_payload(self, plan: OptionsTradePlan) -> list[dict]:
        legs = []
        for leg in plan.legs:
            instruction = "BUY_TO_OPEN" if leg.action.value == "buy" else "SELL_TO_OPEN"
            legs.append({
                "instruction": instruction,
                "symbol": to_occ_symbol(plan.underlying, leg.expiry, leg.right.value, leg.strike),
                "quantity": leg.contracts,
            })
        return legs

    def _close_legs_payload(self, pos: Position) -> list[dict]:
        legs = []
        for leg in pos.legs:
            # Reverse of how it was opened.
            instruction = "SELL_TO_CLOSE" if leg.action == "buy" else "BUY_TO_CLOSE"
            legs.append({
                "instruction": instruction,
                "symbol": to_occ_symbol(pos.underlying, leg.expiry, leg.right, leg.strike),
                "quantity": leg.contracts,
            })
        return legs

    # ---- fill-or-cancel polling -------------------------------------------

    def _submit_and_await_fill(self, payload: dict) -> tuple[str, float]:
        """Places the order, polls until filled/dead/timeout. Returns
        (schwab_order_id, actual_fill_price). Raises ValueError if the
        order doesn't fill in time (canceling it first) or is rejected —
        the same exception type the paper broker raises for a failed
        fill, so pipeline.py's existing skip-recording handles it
        unchanged."""
        try:
            order_id = self._client.place_order(payload)
        except SchwabAuthError as exc:
            # Refresh token expired mid-cycle (e.g. after ~7 days idle) — degrade
            # to the same "skip this trade" path a paper fill failure takes,
            # rather than crashing the whole autonomous/scanner cycle.
            raise ValueError(f"Schwab needs reauthorization: {exc}") from exc
        deadline = time.monotonic() + FILL_WAIT_SECONDS
        last_status = "UNKNOWN"
        while time.monotonic() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            try:
                order = self._client.get_order(order_id)
            except (SchwabApiError, SchwabAuthError) as exc:
                logger.warning("could not poll Schwab order %s: %s", order_id, exc)
                continue
            last_status = str(order.get("status", "UNKNOWN")).upper()
            if last_status in FILLED_STATUSES:
                fill_price = _extract_fill_price(order, float(payload["price"]))
                return order_id, fill_price
            if last_status in DEAD_STATUSES:
                raise ValueError(f"Schwab order {order_id} {last_status.lower()}")
        try:
            self._client.cancel_order(order_id)
        except (SchwabApiError, SchwabAuthError) as exc:
            logger.warning("could not cancel unfilled Schwab order %s: %s", order_id, exc)
        raise ValueError(
            f"Schwab order {order_id} did not fill within {FILL_WAIT_SECONDS:g}s "
            f"(last status {last_status}); canceled"
        )

    # ---- execute / close ---------------------------------------------------

    def execute_plan(
        self,
        plan: OptionsTradePlan,
        snapshot: ChainSnapshot,
        mode_name: str = "",
        order_ctx: OrderContext | None = None,
        mode_rules: dict | None = None,
    ) -> Position | None:
        with self._lock:
            return self._execute_plan_locked(plan, snapshot, mode_name, order_ctx, mode_rules)

    def _execute_plan_locked(
        self,
        plan: OptionsTradePlan,
        snapshot: ChainSnapshot,
        mode_name: str = "",
        order_ctx: OrderContext | None = None,
        mode_rules: dict | None = None,
    ) -> Position | None:
        if plan.strategy == StrategyType.NO_TRADE or not plan.legs:
            return super()._execute_plan_locked(plan, snapshot, mode_name, order_ctx, mode_rules)

        net_mid = plan_mid_price(plan, snapshot)
        if net_mid is None or net_mid <= 0:
            raise ValueError("plan legs have no usable quotes; cannot fill")
        price = _round_tick(net_mid)
        order_type = "NET_DEBIT" if plan.price_type == "debit" else "NET_CREDIT"
        payload = _build_order_payload(self._open_legs_payload(plan), price, order_type)

        order_id, fill_price = self._submit_and_await_fill(payload)

        max_risk = (
            fill_price * OPTION_MULTIPLIER * plan.contracts
            if plan.price_type == "debit"
            else plan.spread_width() * OPTION_MULTIPLIER * plan.contracts
                 - fill_price * OPTION_MULTIPLIER * plan.contracts
        )

        leg_fills = [
            LegFill(
                action=leg.action.value, right=leg.right.value, strike=leg.strike,
                expiry=leg.expiry, contracts=leg.contracts, fill_price=fill_price,
            )
            for leg in plan.legs
        ]
        pos = Position(
            id=uuid.uuid4().hex[:8],
            underlying=plan.underlying.upper(),
            strategy=plan.strategy.value,
            direction=plan.direction,
            mode=mode_name,
            legs=leg_fills,
            entry_net=fill_price,
            price_type=plan.price_type,
            max_risk=round(max(max_risk, 0.0), 2),
            profit_target_pct=plan.profit_target_pct,
            stop_loss_pct=plan.stop_loss_pct,
            opened_at=_now_iso(),
            rationale=plan.rationale,
        )
        self._state["positions"].append(pos)
        if order_ctx:
            self.record_order(
                order_ctx,
                status="open",
                plan_rationale=plan.rationale,
                strategy=pos.strategy,
                confidence=plan.confidence,
                position_id=pos.id,
                max_risk=pos.max_risk,
                entry_net=pos.entry_net,
                price_type=pos.price_type,
                filled_at=pos.opened_at,
                chain_conditions={**self._chain_conditions(snapshot), "schwab_order_id": order_id},
                mode_rules=mode_rules,
            )
        self._journal(
            "live_open", position_id=pos.id, underlying=pos.underlying,
            strategy=pos.strategy, schwab_order_id=order_id,
            legs=[f"{lf.action} {lf.contracts}x {lf.expiry} {lf.strike:g} {lf.right}"
                  for lf in leg_fills],
            net=f"{plan.price_type} {fill_price:.2f}", max_risk=pos.max_risk,
        )
        self._save()
        return pos

    def close_position(
        self, position_id: str, net_now: float, reason: str = "manual"
    ) -> Position:
        with self._lock:
            return self._close_position_locked(position_id, net_now, reason)

    def _close_position_locked(
        self, position_id: str, net_now: float, reason: str = "manual"
    ) -> Position:
        pos = self.get_position(position_id)
        if pos is None or pos.status != "open":
            raise ValueError(f"no open position {position_id!r}")

        price = _round_tick(max(net_now, 0.01))
        # Closing a debit position sells to close (we receive -> credit order);
        # closing a credit position buys to close (we pay -> debit order).
        order_type = "NET_CREDIT" if pos.price_type == "debit" else "NET_DEBIT"
        payload = _build_order_payload(self._close_legs_payload(pos), price, order_type)

        order_id, fill_price = self._submit_and_await_fill(payload)

        pnl = pos.pnl_at(fill_price)
        pos.status = "closed"
        pos.closed_at = _now_iso()
        pos.exit_net = fill_price
        pos.realized_pnl = round(pnl, 2)
        pos.exit_reason = reason
        pos.unrealized_pnl = None
        self.update_order_for_close(pos.id, pos)
        self._journal(
            "live_close", position_id=pos.id, underlying=pos.underlying,
            schwab_order_id=order_id, exit_net=fill_price, pnl=pos.realized_pnl,
            reason=reason,
        )
        self._save()
        return pos


def _extract_fill_price(order: dict, fallback: float) -> float:
    """Best-effort extraction of the actual average fill price from a
    Schwab order status response. Falls back to the submitted limit price
    (with a warning) if the response shape doesn't match what's expected —
    the trade already happened; we should never crash the fill path on a
    parsing mismatch, just flag it for review."""
    try:
        activities = order.get("orderActivityCollection", [])
        total_qty = 0.0
        total_value = 0.0
        for act in activities:
            for leg in act.get("executionLegs", []):
                qty = float(leg.get("quantity", 0))
                px = float(leg.get("price", 0))
                total_qty += qty
                total_value += qty * px
        if total_qty > 0:
            return round(total_value / total_qty, 2)
    except (TypeError, ValueError, KeyError) as exc:
        logger.warning("could not parse Schwab fill price, using submitted price: %s", exc)
    return round(fallback, 2)


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
