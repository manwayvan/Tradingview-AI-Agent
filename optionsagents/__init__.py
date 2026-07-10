"""Options trading extension for the TradingAgents framework.

Layers an options workflow on top of the upstream multi-agent equity
pipeline: the agents produce a directional rating for the underlying, and
this package converts it into a concrete, defined-risk options trade,
executes it against a local paper account, and exposes a TradingView
webhook server so alerts fired from TradingView charts can drive the
whole loop for paper testing.
"""

from optionsagents.modes import DAY, SWING, TradingMode, get_mode
from optionsagents.schemas import (
    OptionLeg,
    OptionsTradePlan,
    StrategyType,
    TradingViewAlert,
)

__all__ = [
    "DAY",
    "SWING",
    "TradingMode",
    "get_mode",
    "OptionLeg",
    "OptionsTradePlan",
    "StrategyType",
    "TradingViewAlert",
]
