"""Trading-mode presets: day trading vs swing trading.

A mode pins the contract-selection window (days to expiry), the delta band
used to shortlist candidate strikes, and the risk/exit parameters the
strategist and paper broker enforce. Values are deliberately conservative
defaults for paper testing; tune them in config or via CLI flags once you
have a feel for fill quality on your tickers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class TradingMode:
    name: str
    # Contract selection window
    dte_min: int              # minimum days to expiry
    dte_max: int              # maximum days to expiry
    delta_low: float          # lower bound of |delta| band for candidate strikes
    delta_high: float         # upper bound of |delta| band for candidate strikes
    # Risk / exits (percentages are of the position's entry debit or max risk)
    max_risk_per_trade: float     # USD max loss allowed per position
    profit_target_pct: float      # close when unrealized P&L >= this % of entry risk
    stop_loss_pct: float          # close when unrealized loss >= this % of entry risk
    max_open_positions: int
    # Liquidity gates for candidate strikes
    min_open_interest: int
    max_spread_pct: float         # (ask-bid)/mid must be below this
    description: str = ""


DAY = TradingMode(
    name="day",
    dte_min=0,
    dte_max=5,
    delta_low=0.45,
    delta_high=0.70,
    max_risk_per_trade=500.0,
    profit_target_pct=50.0,
    stop_loss_pct=30.0,
    max_open_positions=3,
    min_open_interest=500,
    max_spread_pct=10.0,
    description=(
        "Intraday to 5-DTE contracts, higher delta for responsiveness, tight "
        "stops. Positions are expected to be closed the same session; theta "
        "decay makes overnight holds expensive at this DTE."
    ),
)

SWING = TradingMode(
    name="swing",
    dte_min=14,
    dte_max=60,
    delta_low=0.30,
    delta_high=0.60,
    max_risk_per_trade=1000.0,
    profit_target_pct=100.0,
    stop_loss_pct=50.0,
    max_open_positions=5,
    min_open_interest=100,
    max_spread_pct=15.0,
    description=(
        "2-8 week contracts sized for multi-day holds. Wider stops and a "
        "2:1 reward target; spreads are preferred when implied volatility "
        "is elevated to reduce vega exposure."
    ),
)

_MODES = {m.name: m for m in (DAY, SWING)}


def get_mode(name: str, **overrides) -> TradingMode:
    """Look up a mode by name, optionally overriding individual fields."""
    try:
        mode = _MODES[name.lower()]
    except KeyError:
        raise ValueError(
            f"Unknown trading mode {name!r}; expected one of {sorted(_MODES)}"
        ) from None
    return replace(mode, **overrides) if overrides else mode
