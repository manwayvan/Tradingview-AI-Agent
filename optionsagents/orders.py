"""Trade order records with execution rationale for learning and audit."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SOURCE_LABELS = {
    "autonomous": "Autonomous AI",
    "free_signal": "Free built-in signal",
    "tradingview": "TradingView alert",
    "strategy": "Scheduled plan",
    "manual": "Manual close",
    "unknown": "System",
}


@dataclass
class OrderContext:
    """Upstream context captured before the strategist runs."""

    source: str
    ticker: str
    mode: str
    signal: str
    direction: str = ""
    source_label: str = ""
    source_rationale: str = ""
    decision_context: str = ""
    rating: str | None = None
    conviction: float | None = None
    source_ref: str | None = None

    def label(self) -> str:
        return self.source_label or SOURCE_LABELS.get(self.source, self.source)


@dataclass
class TradeOrder:
    """One trade attempt or fill with teachable execution context."""

    id: str
    status: str  # filled | open | closed | skipped
    created_at: str
    ticker: str
    mode: str
    signal: str
    direction: str
    source: str
    source_label: str
    trigger_summary: str
    teach_summary: str
    source_rationale: str = ""
    decision_context: str = ""
    plan_rationale: str = ""
    rating: str | None = None
    conviction: float | None = None
    confidence: float | None = None
    position_id: str | None = None
    strategy: str | None = None
    max_risk: float | None = None
    entry_net: float | None = None
    price_type: str | None = None
    filled_at: str | None = None
    closed_at: str | None = None
    realized_pnl: float | None = None
    exit_reason: str | None = None
    warnings: list[str] = field(default_factory=list)
    # Teachable context: what the market looked like at entry, which risk
    # rules governed the fill, and a plain-English explainer of the structure.
    chain_conditions: dict[str, Any] = field(default_factory=dict)
    mode_rules: dict[str, Any] = field(default_factory=dict)
    strategy_education: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_trigger_summary(ctx: OrderContext, plan_rationale: str = "") -> str:
    parts = [ctx.label(), ctx.ticker.upper(), ctx.signal.upper(), f"{ctx.mode} mode"]
    if ctx.conviction is not None:
        parts.append(f"{ctx.conviction:.0%} conviction")
    if plan_rationale:
        snippet = plan_rationale.strip().split(".")[0][:80]
        if snippet:
            parts.append(snippet)
    return " · ".join(parts)


def build_teach_summary(
    ctx: OrderContext,
    *,
    plan_rationale: str = "",
    strategy: str | None = None,
    status: str = "filled",
    warnings: list[str] | None = None,
) -> str:
    """Plain-language explanation of why this order happened."""
    lines: list[str] = []
    lines.append(f"This trade was triggered by {ctx.label()}.")

    if ctx.source_rationale:
        lines.append(f"Setup: {ctx.source_rationale.strip()}")
    elif ctx.decision_context:
        snippet = ctx.decision_context.strip()[:400]
        lines.append(f"Context: {snippet}")

    if ctx.signal == "analyze":
        lines.append(
            "The system ran full multi-agent research on the ticker before "
            "picking an options structure."
        )
    elif ctx.signal in ("buy", "sell"):
        lines.append(
            f"A fast {ctx.signal} signal fired — the strategist skipped the "
            "full debate and sized a directional options trade."
        )

    if plan_rationale:
        lines.append(f"Options plan: {plan_rationale.strip()}")

    if strategy and status == "filled":
        lines.append(f"Executed structure: {strategy.replace('_', ' ')}.")

    if ctx.conviction is not None:
        lines.append(f"AI conviction at entry: {ctx.conviction:.0%}.")

    if warnings:
        lines.append(f"Notes: {'; '.join(warnings)}")

    if status == "skipped":
        lines.append("No position was opened — see plan rationale or warnings above.")

    return " ".join(lines)


def order_from_position(pos, *, source: str = "unknown") -> TradeOrder:
    """Backfill a minimal order record from a legacy position."""
    label = SOURCE_LABELS.get(source, source)
    rationale = getattr(pos, "rationale", "") or ""
    status = "closed" if pos.status == "closed" else "open"
    return TradeOrder(
        id=f"legacy-{pos.id}",
        status=status,
        created_at=pos.opened_at,
        ticker=pos.underlying,
        mode=pos.mode,
        signal="analyze",
        direction=pos.direction,
        source=source,
        source_label=label,
        trigger_summary=f"{label} · {pos.underlying} · {pos.strategy}",
        teach_summary=rationale or "Legacy trade — rationale was not recorded at entry.",
        plan_rationale=rationale,
        position_id=pos.id,
        strategy=pos.strategy,
        max_risk=pos.max_risk,
        entry_net=pos.entry_net,
        price_type=pos.price_type,
        filled_at=pos.opened_at,
        closed_at=pos.closed_at,
        realized_pnl=pos.realized_pnl,
        exit_reason=pos.exit_reason,
    )
