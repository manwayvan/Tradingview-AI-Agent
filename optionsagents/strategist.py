"""Options Strategist agent.

Takes the upstream agents' directional research decision plus the rendered
chain report and produces a typed ``OptionsTradePlan``. Uses the provider's
structured-output mode when available, with a JSON free-text fallback, and
falls back to the deterministic plan builder if the model's plan doesn't
validate against the actual chain.
"""

from __future__ import annotations

import json
import logging
import re

from optionsagents.chain import (
    ChainSnapshot,
    build_default_plan,
    render_chain_report,
    validate_plan_against_chain,
)
from optionsagents.modes import TradingMode
from optionsagents.schemas import OptionsTradePlan, StrategyType

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an options strategist on a trading desk. You receive:
1. A research decision about the underlying (direction and conviction).
2. A live options chain snapshot with liquid candidate strikes.
3. The desk's trading mode parameters (DTE window, delta band, risk limits).

Your job is to convert the directional view into ONE defined-risk options position, or no_trade.

Rules you must follow:
- Only choose strikes and expirations that appear in the chain snapshot tables. Never invent a strike or expiry.
- Only defined-risk structures: long_call, long_put, or vertical spreads. No naked short options.
- Bullish view -> long_call, bull_call_spread, or bull_put_spread (credit).
  Bearish view -> long_put, bear_put_spread, or bear_call_spread (credit).
  Weak/no view or nothing liquid -> no_trade. Do not force a trade.
- Prefer spreads when ATM implied volatility is elevated (rough guide: above 50% for single names, above 25% for index ETFs) — buying expensive premium outright needs a larger move to profit.
- Respect the mode's DTE window and delta band shown in the report.
- Estimate net_price from the quoted mids of your chosen legs (per share).
- Set contracts so the position's maximum loss stays within the max risk limit given. Max loss per contract: debit strategies = net debit x 100; credit spreads = (strike width - credit) x 100.
- Set profit_target_pct and stop_loss_pct consistent with the mode (day trading exits fast; swing trades give the thesis room).
- rationale: 2-4 sentences tying the structure to the research view and the volatility picture.
"""

_MODE_TEMPLATE = """Trading mode: {name}
- {description}
- Days-to-expiry window: {dte_min} to {dte_max}
- Target |delta| band: {delta_low:.2f} to {delta_high:.2f}
- Maximum risk for this position: ${max_risk:,.0f}
- Default profit target: {pt:g}% of risk; default stop loss: {sl:g}% of risk
"""


def _mode_context(mode: TradingMode) -> str:
    return _MODE_TEMPLATE.format(
        name=mode.name, description=mode.description,
        dte_min=mode.dte_min, dte_max=mode.dte_max,
        delta_low=mode.delta_low, delta_high=mode.delta_high,
        max_risk=mode.max_risk_per_trade,
        pt=mode.profit_target_pct, sl=mode.stop_loss_pct,
    )


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a free-text reply."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in strategist reply")
        raw = text[start:end + 1]
    return json.loads(raw)


class OptionsStrategist:
    """LLM-backed strategist with deterministic fallback.

    ``llm`` is any LangChain chat model (the pipeline passes the same
    deep-thinking model the upstream agents use). Pass ``llm=None`` to run
    purely deterministic selection — useful for offline tests and for the
    webhook fast path when no API key is configured.
    """

    def __init__(self, llm=None):
        self.llm = llm

    def propose(
        self,
        decision_context: str,
        direction: str,
        snapshot: ChainSnapshot,
        mode: TradingMode,
    ) -> OptionsTradePlan:
        """Produce a validated trade plan for the given direction.

        ``decision_context`` is the upstream research decision (markdown).
        ``direction`` is bullish/bearish/neutral, already derived from the
        portfolio rating or the TradingView signal.
        """
        if direction == "neutral":
            return build_default_plan("neutral", snapshot, mode)

        plan = None
        if self.llm is not None:
            try:
                plan = self._ask_llm(decision_context, direction, snapshot, mode)
            except Exception as exc:
                logger.warning("Strategist LLM call failed (%s); using fallback plan", exc)

        if plan is not None and plan.strategy != StrategyType.NO_TRADE:
            problems = validate_plan_against_chain(plan, snapshot)
            if problems:
                logger.warning(
                    "Strategist plan failed chain validation (%s); using fallback plan",
                    "; ".join(problems),
                )
                plan = None

        if plan is None:
            try:
                plan = build_default_plan(direction, snapshot, mode)
            except Exception as exc:
                logger.warning(
                    "Deterministic fallback plan failed for %s (%s); standing aside",
                    snapshot.underlying, exc,
                )
                plan = OptionsTradePlan(
                    strategy=StrategyType.NO_TRADE,
                    underlying=snapshot.underlying,
                    direction=direction,
                    rationale=f"Plan construction error: {exc}",
                    confidence=0.0,
                )
        return plan

    def _ask_llm(
        self, decision_context: str, direction: str,
        snapshot: ChainSnapshot, mode: TradingMode,
    ) -> OptionsTradePlan:
        chain_report = render_chain_report(snapshot, mode)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{_mode_context(mode)}\n"
                    f"Research direction on {snapshot.underlying}: **{direction}**\n\n"
                    f"Research decision context:\n{decision_context}\n\n"
                    f"{chain_report}\n"
                    "Produce the trade plan now."
                ),
            },
        ]

        # Preferred path: provider-native structured output.
        try:
            structured = self.llm.with_structured_output(OptionsTradePlan)
            result = structured.invoke(messages)
            if isinstance(result, OptionsTradePlan):
                return result
        except Exception as exc:
            logger.info("Structured output unavailable (%s); trying JSON fallback", exc)

        # Fallback: ask for raw JSON matching the schema.
        messages[-1]["content"] += (
            "\n\nRespond with ONLY a JSON object matching this schema (no prose):\n"
            + json.dumps(OptionsTradePlan.model_json_schema(), indent=None)
        )
        reply = self.llm.invoke(messages)
        text = reply.content if hasattr(reply, "content") else str(reply)
        if isinstance(text, list):  # some providers return content blocks
            text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
        return OptionsTradePlan.model_validate(_extract_json(text))
