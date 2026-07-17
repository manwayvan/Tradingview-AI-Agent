"""Pre-trade gates: regime, earnings, and portfolio risk (all entry paths)."""

from __future__ import annotations

from dataclasses import dataclass

from optionsagents.autonomous.market_context import MarketContext
from optionsagents.autonomous.portfolio_risk import PortfolioRiskManager, RiskVerdict
from optionsagents.earnings import earnings_block_reason


@dataclass(frozen=True)
class TradeRequest:
    """Normalized trade intent for gate checks."""

    ticker: str
    mode: str
    signal: str  # analyze | buy | sell
    direction: str = "neutral"  # bullish | bearish | neutral
    conviction: float | None = None


def direction_from_signal(signal: str) -> str:
    return {"buy": "bullish", "sell": "bearish"}.get(signal.lower(), "neutral")


def check_regime_gate(
    market: MarketContext,
    *,
    direction: str,
    signal: str,
    conviction: float | None = None,
) -> RiskVerdict:
    """Hard blocks for macro regime — applies to every entry path."""
    conv = conviction if conviction is not None else (0.55 if signal in ("buy", "sell") else 0.5)

    if market.regime == "risk_off" and direction == "bullish" and conv < 0.75:
        return RiskVerdict(
            False,
            f"risk_off regime blocks new bullish trades (conviction {conv:.0%} < 75%)",
        )

    if market.regime == "volatile" and direction == "bullish" and conv < 0.65:
        return RiskVerdict(
            False,
            f"volatile regime blocks new bullish trades (conviction {conv:.0%} < 65%)",
        )

    if (
        market.regime == "risk_off" and market.spy_return_5d < -0.03
        and signal == "buy" and conv < 0.8
    ):
        return RiskVerdict(
            False,
            "sharp risk-off tape — fast buy signals blocked unless conviction ≥ 80%",
        )

    return RiskVerdict(True)


def check_all_gates(
    request: TradeRequest,
    broker,
    risk: PortfolioRiskManager,
    mode_max_risk: float,
    *,
    market: MarketContext | None = None,
) -> RiskVerdict:
    """Portfolio + earnings + regime checks before any new fill."""
    verdict = risk.check_trade(
        request.ticker,
        broker,
        mode_max_risk,
    )
    if not verdict.allowed:
        return verdict

    blocked, reason = earnings_block_reason(request.ticker)
    if blocked:
        return RiskVerdict(False, reason)

    if market is not None:
        direction = request.direction
        if direction == "neutral":
            direction = direction_from_signal(request.signal)
        regime_verdict = check_regime_gate(
            market,
            direction=direction,
            signal=request.signal,
            conviction=request.conviction,
        )
        if not regime_verdict.allowed:
            return regime_verdict

    return RiskVerdict(True)
