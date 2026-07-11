"""Pydantic schemas for the options layer.

``OptionsTradePlan`` is the strategist's structured output: a concrete,
defined-risk options position with exits attached. ``TradingViewAlert`` is
the inbound webhook payload shape fired by TradingView alerts.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class OptionRight(str, Enum):
    CALL = "call"
    PUT = "put"


class LegAction(str, Enum):
    BUY = "buy"
    SELL = "sell"


class StrategyType(str, Enum):
    """Defined-risk strategies the paper broker knows how to margin.

    Naked short options are deliberately not representable: every strategy
    here has a computable maximum loss, which is what the risk gate sizes
    against.
    """

    LONG_CALL = "long_call"
    LONG_PUT = "long_put"
    BULL_CALL_SPREAD = "bull_call_spread"       # debit: buy low call, sell high call
    BEAR_PUT_SPREAD = "bear_put_spread"         # debit: buy high put, sell low put
    BULL_PUT_SPREAD = "bull_put_spread"         # credit: sell high put, buy low put
    BEAR_CALL_SPREAD = "bear_call_spread"       # credit: sell low call, buy high call
    NO_TRADE = "no_trade"


class OptionLeg(BaseModel):
    action: LegAction = Field(description="buy or sell this leg (to open)")
    right: OptionRight = Field(description="call or put")
    strike: float = Field(gt=0, description="Strike price, must exist in the provided chain")
    expiry: str = Field(
        description="Expiration date as YYYY-MM-DD, must be one of the provided expirations"
    )
    contracts: int = Field(default=1, ge=1, le=100, description="Number of contracts")

    @field_validator("expiry")
    @classmethod
    def _valid_date(cls, v: str) -> str:
        datetime.strptime(v, "%Y-%m-%d")
        return v

    def dte(self, asof: date | None = None) -> int:
        asof = asof or date.today()
        return (datetime.strptime(self.expiry, "%Y-%m-%d").date() - asof).days

    def key(self) -> tuple[str, str, float]:
        return (self.expiry, self.right.value, self.strike)


class OptionsTradePlan(BaseModel):
    """A concrete options position proposal with exits attached."""

    strategy: StrategyType = Field(description="Which defined-risk strategy to open")
    underlying: str = Field(description="Ticker symbol of the underlying")
    direction: Literal["bullish", "bearish", "neutral"] = Field(
        description="Directional thesis this position expresses"
    )
    legs: list[OptionLeg] = Field(
        default_factory=list,
        description=(
            "The option legs. Empty for no_trade. One leg for long_call/long_put, "
            "exactly two legs (one buy, one sell, same expiry and right) for spreads."
        ),
    )
    net_price: float = Field(
        default=0.0,
        ge=0,
        description=(
            "Estimated net price per share to open (option prices are per share; "
            "one contract = 100 shares). For debit strategies this is the debit "
            "paid; for credit spreads the credit received."
        ),
    )
    price_type: Literal["debit", "credit"] = Field(
        default="debit", description="Whether net_price is paid (debit) or received (credit)"
    )
    profit_target_pct: float = Field(
        default=50.0, gt=0, le=500,
        description="Close when unrealized profit reaches this percent of max risk",
    )
    stop_loss_pct: float = Field(
        default=50.0, gt=0, le=100,
        description="Close when unrealized loss reaches this percent of max risk",
    )
    time_horizon: str = Field(
        default="", description="Expected holding period, e.g. 'intraday' or '2-3 weeks'"
    )
    rationale: str = Field(
        default="", description="Short justification tying the trade to the research decision"
    )
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Strategist's confidence in the setup, 0-1"
    )

    @model_validator(mode="after")
    def _check_legs(self) -> OptionsTradePlan:
        n = len(self.legs)
        if self.strategy == StrategyType.NO_TRADE:
            if n != 0:
                raise ValueError("no_trade plan must have no legs")
            return self
        if self.strategy in (StrategyType.LONG_CALL, StrategyType.LONG_PUT):
            if n != 1 or self.legs[0].action != LegAction.BUY:
                raise ValueError(f"{self.strategy.value} requires exactly one buy leg")
            want = OptionRight.CALL if self.strategy == StrategyType.LONG_CALL else OptionRight.PUT
            if self.legs[0].right != want:
                raise ValueError(f"{self.strategy.value} leg must be a {want.value}")
            return self
        # Vertical spreads
        if n != 2:
            raise ValueError(f"{self.strategy.value} requires exactly two legs")
        buy = next((leg for leg in self.legs if leg.action == LegAction.BUY), None)
        sell = next((leg for leg in self.legs if leg.action == LegAction.SELL), None)
        if buy is None or sell is None:
            raise ValueError("spread requires one buy leg and one sell leg")
        if buy.expiry != sell.expiry or buy.right != sell.right:
            raise ValueError("spread legs must share expiry and right")
        if buy.contracts != sell.contracts:
            raise ValueError("spread legs must have equal contract counts")
        want_right = {
            StrategyType.BULL_CALL_SPREAD: OptionRight.CALL,
            StrategyType.BEAR_CALL_SPREAD: OptionRight.CALL,
            StrategyType.BULL_PUT_SPREAD: OptionRight.PUT,
            StrategyType.BEAR_PUT_SPREAD: OptionRight.PUT,
        }[self.strategy]
        if buy.right != want_right:
            raise ValueError(f"{self.strategy.value} must use {want_right.value}s")
        expected_credit = self.strategy in (
            StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD
        )
        if expected_credit and self.price_type != "credit":
            raise ValueError(f"{self.strategy.value} is a credit spread; price_type must be credit")
        if not expected_credit and self.price_type != "debit":
            raise ValueError(f"{self.strategy.value} is a debit spread; price_type must be debit")
        return self

    @property
    def contracts(self) -> int:
        return self.legs[0].contracts if self.legs else 0

    def spread_width(self) -> float:
        if len(self.legs) != 2:
            return 0.0
        return abs(self.legs[0].strike - self.legs[1].strike)

    def max_risk_per_contract(self) -> float:
        """Maximum loss per contract in USD (option multiplier 100)."""
        if self.strategy == StrategyType.NO_TRADE:
            return 0.0
        if self.price_type == "debit":
            return self.net_price * 100.0
        return max(self.spread_width() - self.net_price, 0.0) * 100.0

    def max_risk_total(self) -> float:
        return self.max_risk_per_contract() * self.contracts


# Plain-English explainers shown in the GUI's order detail view, so every
# executed order doubles as a lesson in what the structure is and when it
# gets used.
STRATEGY_EDUCATION: dict[str, str] = {
    StrategyType.LONG_CALL.value: (
        "A long call gives you the right to buy 100 shares at the strike until "
        "expiry. It profits when the stock rises enough (and fast enough) to "
        "outrun time decay. Maximum loss is the premium paid; upside is "
        "unlimited. Used when the view is bullish and implied volatility is "
        "reasonable, so the premium isn't overpriced."
    ),
    StrategyType.LONG_PUT.value: (
        "A long put gives you the right to sell 100 shares at the strike until "
        "expiry. It profits when the stock falls enough to outrun time decay. "
        "Maximum loss is the premium paid. Used when the view is bearish and "
        "the premium is fairly priced."
    ),
    StrategyType.BULL_CALL_SPREAD.value: (
        "A bull call spread buys a call and sells a higher-strike call, same "
        "expiry. The sold call pays for part of the bought one, lowering cost "
        "and time-decay drag, in exchange for capping profit at the strike "
        "width. Maximum loss is the net debit. Used for a bullish view when "
        "implied volatility is elevated — you're partly selling the expensive "
        "premium back."
    ),
    StrategyType.BEAR_PUT_SPREAD.value: (
        "A bear put spread buys a put and sells a lower-strike put, same "
        "expiry. Cheaper than a lone put with profit capped at the strike "
        "width; maximum loss is the net debit. Used for a bearish view when "
        "puts are expensive (elevated implied volatility)."
    ),
    StrategyType.BULL_PUT_SPREAD.value: (
        "A bull put credit spread sells a put and buys a cheaper lower-strike "
        "put as protection, collecting a net credit up front. It profits if "
        "the stock stays above the short strike — it can win even if the "
        "stock goes nowhere. Maximum loss is the strike width minus the "
        "credit. Used for a bullish-to-neutral view when implied volatility "
        "is high (rich premium to sell)."
    ),
    StrategyType.BEAR_CALL_SPREAD.value: (
        "A bear call credit spread sells a call and buys a cheaper higher-"
        "strike call as protection, collecting a net credit. It profits if "
        "the stock stays below the short strike. Maximum loss is the strike "
        "width minus the credit. Used for a bearish-to-neutral view when "
        "implied volatility is high."
    ),
    StrategyType.NO_TRADE.value: (
        "Standing aside. Not trading is a position too — it's the right call "
        "when there's no directional edge, the options are too illiquid to "
        "fill fairly, or the risk doesn't fit the account rules."
    ),
}


def render_trade_plan(plan: OptionsTradePlan) -> str:
    """Render a plan to the markdown shape used in reports and logs."""
    lines = [
        f"**Strategy**: {plan.strategy.value}",
        f"**Underlying**: {plan.underlying}",
        f"**Direction**: {plan.direction}",
    ]
    if plan.legs:
        lines.append("**Legs**:")
        for leg in plan.legs:
            lines.append(
                f"- {leg.action.value.upper()} {leg.contracts}x {leg.expiry} "
                f"{leg.strike:g} {leg.right.value.upper()}"
            )
        lines += [
            f"**Net {plan.price_type}**: ${plan.net_price:.2f}/share "
            f"(${plan.net_price * 100:.0f}/contract)",
            f"**Max risk**: ${plan.max_risk_total():,.0f}",
            f"**Profit target**: {plan.profit_target_pct:g}% of risk | "
            f"**Stop loss**: {plan.stop_loss_pct:g}% of risk",
            f"**Time horizon**: {plan.time_horizon}",
        ]
    lines += [
        f"**Confidence**: {plan.confidence:.0%}",
        "",
        f"**Rationale**: {plan.rationale}",
    ]
    return "\n".join(lines)


class TradingViewAlert(BaseModel):
    """Payload TradingView alerts POST to the webhook server.

    Configure the alert message in TradingView as JSON (see pine/ examples).
    ``secret`` must match the TRADINGVIEW_WEBHOOK_SECRET environment
    variable; requests with a wrong or missing secret are rejected.
    """

    secret: str = Field(default="", description="Shared secret for authentication")
    ticker: str = Field(description="Underlying symbol, e.g. NVDA")
    signal: Literal["buy", "sell", "analyze"] = Field(
        default="analyze",
        description=(
            "buy/sell skips the full agent debate and hands the strategist a "
            "directional signal (fast path for day trading); analyze runs the "
            "complete multi-agent research pipeline first."
        ),
    )
    mode: Literal["day", "swing"] = Field(default="day")
    price: float | None = Field(default=None, description="Trigger price from the chart")
    interval: str | None = Field(default=None, description="Chart interval, e.g. '5' or 'D'")
    time: str | None = Field(default=None, description="Alert fire time from TradingView")
    note: str | None = Field(default=None, description="Free-form context from the alert")
    direction_hint: Literal["bullish", "bearish", "neutral"] | None = Field(
        default=None,
        description="Technical direction from swing scanners; biases research when neutral.",
    )

    @field_validator("ticker")
    @classmethod
    def _clean_ticker(cls, v: str) -> str:
        # TradingView sends e.g. "NASDAQ:NVDA"; keep just the symbol.
        return v.split(":")[-1].strip().upper()
