"""Account-percent risk budgeting for paper options trades."""

from __future__ import annotations

from optionsagents.modes import TradingMode, get_mode

MIN_TRADE_RISK_USD = 50.0


def trade_risk_budget(
    equity: float,
    cash: float,
    risk_pct: float,
    *,
    floor_usd: float = MIN_TRADE_RISK_USD,
) -> float:
    """USD max loss allowed for the next trade from a % of account equity."""
    if equity <= 0 or risk_pct <= 0:
        return 0.0
    budget = equity * (risk_pct / 100.0)
    budget = min(budget, max(cash, 0.0))
    if budget < floor_usd:
        return 0.0
    return round(budget, 2)


def portfolio_risk_cap(equity: float, portfolio_risk_pct: float) -> float:
    """Max total open risk across all positions as % of equity."""
    if equity <= 0 or portfolio_risk_pct <= 0:
        return 0.0
    return round(equity * (portfolio_risk_pct / 100.0), 2)


def daily_loss_cap(equity: float, risk_pct: float, *, multiplier: float = 3.0) -> float:
    """Kill switch when daily realized loss exceeds this USD amount."""
    if equity <= 0 or risk_pct <= 0:
        return 0.0
    return round(equity * (risk_pct / 100.0) * multiplier, 2)


def mode_for_budget(mode_name: str, budget: float) -> TradingMode:
    """Return a trading mode with max_risk_per_trade set from the account budget."""
    base = get_mode(mode_name)
    if budget <= 0:
        return base
    return get_mode(mode_name, max_risk_per_trade=max(budget, MIN_TRADE_RISK_USD))
