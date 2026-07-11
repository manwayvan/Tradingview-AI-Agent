"""Performance analytics from paper account orders and positions."""

from __future__ import annotations

from collections import defaultdict

from optionsagents.orders import TradeOrder
from optionsagents.paper_broker import PaperBroker


def _source_stats() -> dict:
    return {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "win_rate_pct": 0.0}


def _finalize_bucket(bucket: dict) -> dict:
    trades = bucket["trades"]
    wins = bucket["wins"]
    bucket["win_rate_pct"] = round(100.0 * wins / trades, 1) if trades else 0.0
    bucket["pnl"] = round(bucket["pnl"], 2)
    return bucket


def _equity_curve(broker: PaperBroker) -> list[float]:
    """Approximate equity path from closed trades."""
    summary = broker.summary()
    starting = float(broker._state.get("starting_cash", summary["starting_cash"]))
    curve = [starting]
    realized_running = 0.0
    closed = sorted(
        broker.positions("closed"),
        key=lambda p: p.closed_at or p.opened_at,
    )
    for pos in closed:
        if pos.realized_pnl is not None:
            realized_running += pos.realized_pnl
            curve.append(starting + realized_running)
    curve.append(summary["equity"])
    return curve


def _max_drawdown(curve: list[float]) -> tuple[float, float]:
    if len(curve) < 2:
        return 0.0, 0.0
    peak = curve[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for value in curve:
        peak = max(peak, value)
        dd = peak - value
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = (100.0 * dd / peak) if peak > 0 else 0.0
    return round(max_dd, 2), round(max_dd_pct, 2)


def compute_stats(broker: PaperBroker, orders: list[TradeOrder] | None = None) -> dict:
    orders = orders if orders is not None else broker.list_orders(500)
    closed_positions = broker.positions("closed")
    open_positions = broker.positions("open")

    pnls = [p.realized_pnl for p in closed_positions if p.realized_pnl is not None]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    breakeven = len([x for x in pnls if x == 0])
    total_closed = len(pnls)
    realized = sum(pnls)

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    win_rate = (100.0 * len(wins) / total_closed) if total_closed else 0.0
    loss_rate = len(losses) / total_closed if total_closed else 0.0
    win_r = len(wins) / total_closed if total_closed else 0.0
    expectancy = (win_r * avg_win) + (loss_rate * avg_loss) if total_closed else 0.0

    curve = _equity_curve(broker)
    max_dd, max_dd_pct = _max_drawdown(curve)
    summary = broker.summary()

    by_source: dict[str, dict] = defaultdict(_source_stats)
    by_mode: dict[str, dict] = defaultdict(_source_stats)
    pos_by_id = {p.id: p for p in closed_positions}

    for order in orders:
        if order.status in ("skipped", "open"):
            continue
        pos = pos_by_id.get(order.position_id or "")
        pnl = pos.realized_pnl if pos else order.realized_pnl
        if pnl is None:
            continue
        for key, bucket in ((order.source, by_source), (order.mode, by_mode)):
            b = bucket[key]
            b["trades"] += 1
            b["pnl"] += pnl
            if pnl > 0:
                b["wins"] += 1
            elif pnl < 0:
                b["losses"] += 1

    skipped = sum(1 for o in orders if o.status == "skipped")

    return {
        "total_closed": total_closed,
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": breakeven,
        "win_rate_pct": round(win_rate, 1),
        "realized_pnl": round(realized, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "open_positions": len(open_positions),
        "skipped_orders": skipped,
        "by_source": {k: _finalize_bucket(dict(v)) for k, v in by_source.items()},
        "by_mode": {k: _finalize_bucket(dict(v)) for k, v in by_mode.items()},
        "equity": summary["equity"],
        "starting_cash": summary["starting_cash"],
    }
