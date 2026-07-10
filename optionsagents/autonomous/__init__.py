"""Autonomous AI trading brain — self-directed stock picking and execution.

The autonomous layer scans a liquid-options universe, scores candidates with
multi-factor quantitative analysis, asks an LLM strategy brain to choose the
best opportunities and trading modes, enforces portfolio-level risk limits, and
executes trades through the existing :class:`~optionsagents.pipeline.OptionsPipeline`.
"""

from optionsagents.autonomous.brain import StrategyBrain, TradeDirective
from optionsagents.autonomous.orchestrator import AutonomousOrchestrator
from optionsagents.autonomous.scanner import MarketScanner, StockCandidate

__all__ = [
    "AutonomousOrchestrator",
    "MarketScanner",
    "StockCandidate",
    "StrategyBrain",
    "TradeDirective",
]
