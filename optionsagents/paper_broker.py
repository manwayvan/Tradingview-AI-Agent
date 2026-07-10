"""Local paper-trading engine for options.

TradingView's Paper Trading is a closed system with no public order API, so
this broker simulates the same thing locally: fills at the quoted mid
(plus configurable slippage), an account ledger persisted to JSON, mark-
to-market against live chains, and automatic stop-loss / profit-target
exits. Every fill is also logged in a human-readable trade journal so you
can mirror the positions in TradingView's paper account and compare.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from optionsagents.chain import ChainSnapshot, plan_mid_price
from optionsagents.orders import (
    OrderContext,
    TradeOrder,
    build_teach_summary,
    build_trigger_summary,
    order_from_position,
)
from optionsagents.schemas import OptionsTradePlan, StrategyType

logger = logging.getLogger(__name__)

OPTION_MULTIPLIER = 100.0


@dataclass
class LegFill:
    action: str          # buy | sell
    right: str           # call | put
    strike: float
    expiry: str
    contracts: int
    fill_price: float    # per share


@dataclass
class Position:
    id: str
    underlying: str
    strategy: str
    direction: str
    mode: str
    legs: list[LegFill]
    entry_net: float          # per share; sign per price_type convention
    price_type: str           # debit | credit
    max_risk: float           # USD, total
    profit_target_pct: float
    stop_loss_pct: float
    opened_at: str
    rationale: str = ""
    status: str = "open"      # open | closed
    closed_at: str | None = None
    exit_net: float | None = None
    realized_pnl: float | None = None
    exit_reason: str | None = None
    last_mark: float | None = None      # latest net mid per share
    unrealized_pnl: float | None = None

    @property
    def contracts(self) -> int:
        return self.legs[0].contracts if self.legs else 0

    def pnl_at(self, net_now: float) -> float:
        """P&L in USD if closed at ``net_now`` (net mid, price_type convention)."""
        if self.price_type == "debit":
            per_share = net_now - self.entry_net
        else:
            # Credit position: entered by receiving entry_net, exit by paying net_now.
            per_share = self.entry_net - net_now
        return per_share * OPTION_MULTIPLIER * self.contracts


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _order_from_dict(raw: dict) -> TradeOrder:
    return TradeOrder(**{k: v for k, v in raw.items() if k in TradeOrder.__dataclass_fields__})


def _backfill_orders(state: dict) -> list[TradeOrder]:
    """Ensure every position has a linked order record."""
    orders: list[TradeOrder] = list(state.get("orders") or [])
    by_position = {o.position_id: o for o in orders if o.position_id}
    for pos in state["positions"]:
        if pos.id not in by_position:
            orders.insert(0, order_from_position(pos))
    return orders


class PaperBroker:
    """JSON-file-backed paper account."""

    def __init__(
        self,
        account_file: str,
        starting_cash: float = 100_000.0,
        slippage_pct: float = 2.0,
    ):
        """``slippage_pct`` worsens fills by this percent of the net mid,
        approximating crossing part of the bid-ask spread."""
        self.account_file = account_file
        self.slippage_pct = slippage_pct
        # Coarse lock: the GUI engine, webhook background tasks, and manual
        # CLI-triggered actions can all mutate the account concurrently.
        self._lock = threading.RLock()
        self._state = self._load(starting_cash)

    # ---- persistence -------------------------------------------------

    def _load(self, starting_cash: float) -> dict:
        if os.path.exists(self.account_file):
            with open(self.account_file) as f:
                state = json.load(f)
            state["positions"] = [
                Position(**{**p, "legs": [LegFill(**leg) for leg in p["legs"]]})
                for p in state["positions"]
            ]
            if "orders" not in state:
                state["orders"] = []
            else:
                state["orders"] = [_order_from_dict(o) for o in state["orders"]]
            state["orders"] = _backfill_orders(state)
            return state
        return {
            "cash": starting_cash,
            "starting_cash": starting_cash,
            "positions": [],
            "journal": [],
            "orders": [],
        }

    def _save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.account_file)), exist_ok=True)
        state = dict(self._state)
        state["positions"] = [asdict(p) for p in self._state["positions"]]
        state["orders"] = [o.to_dict() for o in self._state.get("orders", [])]
        tmp = self.account_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self.account_file)

    def _journal(self, event: str, **details) -> None:
        entry = {"time": _now(), "event": event, **details}
        self._state["journal"].append(entry)
        logger.info("journal: %s %s", event, details)

    # ---- account views -----------------------------------------------

    @property
    def cash(self) -> float:
        return self._state["cash"]

    def positions(self, status: str | None = None) -> list[Position]:
        pos = self._state["positions"]
        return [p for p in pos if status is None or p.status == status]

    def get_position(self, position_id: str) -> Position | None:
        for p in self._state["positions"]:
            if p.id == position_id or p.id.startswith(position_id):
                return p
        return None

    def summary(self) -> dict:
        open_pos = self.positions("open")
        closed = self.positions("closed")
        realized = sum(p.realized_pnl or 0.0 for p in closed)
        unrealized = sum(p.unrealized_pnl or 0.0 for p in open_pos)
        return {
            "cash": round(self.cash, 2),
            "starting_cash": self._state["starting_cash"],
            "open_positions": len(open_pos),
            "closed_positions": len(closed),
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "equity": round(self.cash + self._locked_value(), 2),
        }

    def _locked_value(self) -> float:
        """Marked value of open positions (entry value if never marked)."""
        total = 0.0
        for p in self.positions("open"):
            net = p.last_mark if p.last_mark is not None else p.entry_net
            if p.price_type == "debit":
                total += net * OPTION_MULTIPLIER * p.contracts
            else:
                # Credit position: locked margin minus cost to close.
                width_risk = p.max_risk / max(p.contracts, 1) / OPTION_MULTIPLIER + p.entry_net
                total += (width_risk - net) * OPTION_MULTIPLIER * p.contracts
        return total

    def get_order(self, order_id: str) -> TradeOrder | None:
        for o in self._state.get("orders", []):
            if o.id == order_id or (order_id.startswith("legacy-") and o.id == order_id):
                return o
            if o.position_id and (o.position_id == order_id or o.position_id.startswith(order_id)):
                return o
        return None

    def list_orders(self, limit: int = 100) -> list[TradeOrder]:
        orders = list(self._state.get("orders", []))
        return list(reversed(orders[-limit:]))

    def record_order(
        self,
        ctx: OrderContext,
        *,
        status: str,
        plan_rationale: str = "",
        strategy: str | None = None,
        confidence: float | None = None,
        warnings: list[str] | None = None,
        position_id: str | None = None,
        max_risk: float | None = None,
        entry_net: float | None = None,
        price_type: str | None = None,
        filled_at: str | None = None,
        order_id: str | None = None,
    ) -> TradeOrder:
        oid = order_id or uuid.uuid4().hex[:10]
        teach = build_teach_summary(
            ctx,
            plan_rationale=plan_rationale,
            strategy=strategy,
            status=status,
            warnings=warnings,
        )
        order = TradeOrder(
            id=oid,
            status=status,
            created_at=_now(),
            ticker=ctx.ticker.upper(),
            mode=ctx.mode,
            signal=ctx.signal,
            direction=ctx.direction or "neutral",
            source=ctx.source,
            source_label=ctx.label(),
            trigger_summary=build_trigger_summary(ctx, plan_rationale),
            teach_summary=teach,
            source_rationale=ctx.source_rationale,
            decision_context=ctx.decision_context,
            plan_rationale=plan_rationale,
            rating=ctx.rating,
            conviction=ctx.conviction,
            confidence=confidence,
            position_id=position_id,
            strategy=strategy,
            max_risk=max_risk,
            entry_net=entry_net,
            price_type=price_type,
            filled_at=filled_at or (_now() if status in ("filled", "open") else None),
            warnings=list(warnings or []),
        )
        self._state.setdefault("orders", []).append(order)
        return order

    def update_order_for_close(self, position_id: str, pos: Position) -> None:
        for order in reversed(self._state.get("orders", [])):
            if order.position_id == position_id:
                order.status = "closed"
                order.closed_at = pos.closed_at
                order.realized_pnl = pos.realized_pnl
                order.exit_reason = pos.exit_reason
                break

    # ---- trading -----------------------------------------------------

    def execute_plan(
        self,
        plan: OptionsTradePlan,
        snapshot: ChainSnapshot,
        mode_name: str = "",
        order_ctx: OrderContext | None = None,
    ) -> Position | None:
        """Fill a validated plan at net mid +/- slippage. Returns None for no_trade."""
        with self._lock:
            return self._execute_plan_locked(plan, snapshot, mode_name, order_ctx)

    def _execute_plan_locked(
        self,
        plan: OptionsTradePlan,
        snapshot: ChainSnapshot,
        mode_name: str = "",
        order_ctx: OrderContext | None = None,
    ) -> Position | None:
        if plan.strategy == StrategyType.NO_TRADE or not plan.legs:
            self._journal("no_trade", underlying=plan.underlying, rationale=plan.rationale)
            if order_ctx:
                self.record_order(
                    order_ctx,
                    status="skipped",
                    plan_rationale=plan.rationale,
                    strategy=plan.strategy.value,
                    confidence=plan.confidence,
                )
            self._save()
            return None

        net_mid = plan_mid_price(plan, snapshot)
        if net_mid is None or net_mid <= 0:
            raise ValueError("plan legs have no usable quotes; cannot fill")

        slip = net_mid * self.slippage_pct / 100.0
        # Slippage always hurts: pay more debit, receive less credit.
        fill_net = net_mid + slip if plan.price_type == "debit" else net_mid - slip
        fill_net = round(max(fill_net, 0.01), 2)

        if plan.price_type == "debit":
            cost = fill_net * OPTION_MULTIPLIER * plan.contracts
            max_risk = cost
        else:
            credit = fill_net * OPTION_MULTIPLIER * plan.contracts
            margin = plan.spread_width() * OPTION_MULTIPLIER * plan.contracts
            cost = margin - credit          # cash locked
            max_risk = cost

        if cost > self.cash:
            raise ValueError(
                f"insufficient paper cash: need ${cost:,.0f}, have ${self.cash:,.0f}"
            )

        leg_fills = []
        for leg in plan.legs:
            q = snapshot.lookup(leg.expiry, leg.right.value, leg.strike)
            leg_fills.append(LegFill(
                action=leg.action.value,
                right=leg.right.value,
                strike=leg.strike,
                expiry=leg.expiry,
                contracts=leg.contracts,
                fill_price=round(q.mid, 2),
            ))

        pos = Position(
            id=uuid.uuid4().hex[:8],
            underlying=plan.underlying.upper(),
            strategy=plan.strategy.value,
            direction=plan.direction,
            mode=mode_name,
            legs=leg_fills,
            entry_net=fill_net,
            price_type=plan.price_type,
            max_risk=round(max_risk, 2),
            profit_target_pct=plan.profit_target_pct,
            stop_loss_pct=plan.stop_loss_pct,
            opened_at=_now(),
            rationale=plan.rationale,
        )
        self._state["cash"] -= cost
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
            )
        self._journal(
            "open", position_id=pos.id, underlying=pos.underlying,
            strategy=pos.strategy,
            legs=[f"{lf.action} {lf.contracts}x {lf.expiry} {lf.strike:g} {lf.right}"
                  for lf in leg_fills],
            net=f"{plan.price_type} {fill_net:.2f}", max_risk=pos.max_risk,
        )
        self._save()
        return pos

    def close_position(
        self, position_id: str, net_now: float, reason: str = "manual"
    ) -> Position:
        """Close at ``net_now`` (net mid per share, position's price_type convention)."""
        with self._lock:
            return self._close_position_locked(position_id, net_now, reason)

    def _close_position_locked(
        self, position_id: str, net_now: float, reason: str = "manual"
    ) -> Position:
        pos = self.get_position(position_id)
        if pos is None or pos.status != "open":
            raise ValueError(f"no open position {position_id!r}")

        slip = net_now * self.slippage_pct / 100.0
        # Closing a debit position sells it (receive less); closing a credit
        # position buys it back (pay more).
        exit_net = round(max(net_now - slip if pos.price_type == "debit" else net_now + slip, 0.0), 2)

        pnl = pos.pnl_at(exit_net)
        if pos.price_type == "debit":
            self._state["cash"] += exit_net * OPTION_MULTIPLIER * pos.contracts
        else:
            margin = pos.max_risk + pos.entry_net * OPTION_MULTIPLIER * pos.contracts
            self._state["cash"] += margin - exit_net * OPTION_MULTIPLIER * pos.contracts

        pos.status = "closed"
        pos.closed_at = _now()
        pos.exit_net = exit_net
        pos.realized_pnl = round(pnl, 2)
        pos.exit_reason = reason
        pos.unrealized_pnl = None
        self.update_order_for_close(pos.id, pos)
        self._journal(
            "close", position_id=pos.id, underlying=pos.underlying,
            exit_net=exit_net, pnl=pos.realized_pnl, reason=reason,
        )
        self._save()
        return pos

    def reset_account(self, starting_cash: float, *, clear_history: bool = True) -> dict:
        """Reset paper cash to starting_cash; optionally wipe positions and journal."""
        with self._lock:
            if clear_history:
                self._state = {
                    "cash": starting_cash,
                    "starting_cash": starting_cash,
                    "positions": [],
                    "orders": [],
                    "journal": [{
                        "time": _now(),
                        "event": "account_reset",
                        "starting_cash": starting_cash,
                        "cleared": True,
                    }],
                }
            else:
                self._state["cash"] = starting_cash
                self._state["starting_cash"] = starting_cash
                self._journal(
                    "account_reset",
                    starting_cash=starting_cash,
                    cleared=False,
                )
            self._save()
        return self.summary()

    # ---- mark-to-market and exits --------------------------------------

    def mark_position(self, pos: Position, snapshot: ChainSnapshot) -> float | None:
        """Recompute the position's net mid from a fresh snapshot."""
        with self._lock:
            return self._mark_position_locked(pos, snapshot)

    def _mark_position_locked(self, pos: Position, snapshot: ChainSnapshot) -> float | None:
        net = 0.0
        for leg in pos.legs:
            q = snapshot.lookup(leg.expiry, leg.right, leg.strike)
            if q is None or q.mid <= 0:
                return None
            net += q.mid if leg.action == "buy" else -q.mid
        net = net if pos.price_type == "debit" else -net
        pos.last_mark = round(net, 2)
        pos.unrealized_pnl = round(pos.pnl_at(net), 2)
        self._save()
        return net

    def check_exits(self, pos: Position, snapshot: ChainSnapshot) -> Position | None:
        """Mark the position and close it if stop/target/expiry rules trigger.

        Returns the closed position, or None if it stays open.
        """
        with self._lock:
            return self._check_exits_locked(pos, snapshot)

    def _check_exits_locked(self, pos: Position, snapshot: ChainSnapshot) -> Position | None:
        if pos.status != "open":
            return None
        net = self.mark_position(pos, snapshot)
        if net is None:
            logger.warning("position %s: no quotes to mark against", pos.id)
            return None

        pnl = pos.pnl_at(net)
        risk = pos.max_risk if pos.max_risk > 0 else 1.0
        nearest_dte = min(snapshot.dte(leg.expiry) for leg in pos.legs)

        if pnl >= risk * pos.profit_target_pct / 100.0:
            return self.close_position(pos.id, net, reason="profit_target")
        if pnl <= -risk * pos.stop_loss_pct / 100.0:
            return self.close_position(pos.id, net, reason="stop_loss")
        if nearest_dte <= 0:
            return self.close_position(pos.id, net, reason="expiry")
        return None
