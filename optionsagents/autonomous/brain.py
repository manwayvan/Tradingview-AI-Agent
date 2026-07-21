"""AI strategy brain — selects opportunities and trading modes."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from optionsagents.autonomous.market_context import MarketContext
from optionsagents.autonomous.scanner import StockCandidate

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are the chief investment officer of an autonomous options desk.
You receive:
1. Current market regime and macro context.
2. A ranked list of stock candidates from a quantitative screener.
3. The desk's open positions and recent performance memory.
4. Available capital and risk budget.

Your job: decide which (if any) trades to execute THIS cycle to maximize risk-adjusted returns.

Rules:
- Output JSON only, matching the schema exactly.
- Prefer 0-2 high-conviction trades per cycle. Standing aside is valid and often correct.
- For each trade pick: ticker, mode (day or swing), signal (analyze | buy | sell), conviction (0-1), rationale.
  - Use "analyze" for swing setups needing full multi-agent research (higher conviction, multi-day holds).
  - Use "buy"/"sell" for fast directional day-trade setups with clear momentum.
- day mode: 0-5 DTE, intraday momentum, needs strong 5-day move and liquid name.
- swing mode: 2-8 week holds, needs solid 20-60 day trend and relative strength.
- In risk_off or volatile regimes, lower size by being more selective (higher conviction bar).
- Never recommend a ticker already at max open positions.
- Bearish views use signal "sell"; bullish use "buy" or "analyze" with bullish intent.
- conviction must reflect genuine edge — do not inflate scores.
"""


@dataclass
class TradeDirective:
    ticker: str
    mode: str           # day | swing
    signal: str         # analyze | buy | sell
    conviction: float
    rationale: str

    @classmethod
    def from_dict(cls, raw: dict) -> TradeDirective:
        return cls(
            ticker=str(raw["ticker"]).upper(),
            mode=str(raw.get("mode", "swing")).lower(),
            signal=str(raw.get("signal", "analyze")).lower(),
            conviction=float(raw.get("conviction", 0.5)),
            rationale=str(raw.get("rationale", "")),
        )


@dataclass
class BrainDecision:
    market_assessment: str
    directives: list[TradeDirective] = field(default_factory=list)
    stand_aside: bool = False
    reasoning: str = ""


def _extract_json(text: str) -> dict:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in brain reply")
        raw = text[start : end + 1]
    return json.loads(raw)


def _parse_decision(data: dict) -> BrainDecision:
    directives = [
        TradeDirective.from_dict(item)
        for item in data.get("directives", [])
        if isinstance(item, dict) and item.get("ticker")
    ]
    return BrainDecision(
        market_assessment=str(data.get("market_assessment", "")),
        directives=directives,
        stand_aside=bool(data.get("stand_aside", not directives)),
        reasoning=str(data.get("reasoning", "")),
    )


class StrategyBrain:
    """LLM-backed strategy selector with deterministic fallback."""

    def __init__(self, llm=None):
        self.llm = llm

    def decide(
        self,
        candidates: list[StockCandidate],
        market: MarketContext,
        portfolio_summary: dict,
        memory_context: str = "",
        open_tickers: dict[str, int] | None = None,
        max_trades: int = 2,
    ) -> BrainDecision:
        open_tickers = open_tickers or {}
        if self.llm is not None:
            try:
                return self._ask_llm(
                    candidates, market, portfolio_summary,
                    memory_context, open_tickers, max_trades,
                )
            except Exception as exc:
                logger.warning("Strategy brain LLM failed (%s); using rules fallback", exc)
        return self._rules_fallback(
            candidates, market, open_tickers, max_trades,
        )

    def _ask_llm(
        self,
        candidates: list[StockCandidate],
        market: MarketContext,
        portfolio_summary: dict,
        memory_context: str,
        open_tickers: dict[str, int],
        max_trades: int,
    ) -> BrainDecision:
        candidate_block = "\n".join(c.to_summary_line() for c in candidates) or "(no candidates)"
        open_block = ", ".join(f"{t}({n})" for t, n in open_tickers.items()) or "none"
        user = f"""{market.to_prompt_block()}

Account: equity ${portfolio_summary.get('equity', 0):,.0f}, cash ${portfolio_summary.get('cash', 0):,.0f},
risk budget ${portfolio_summary.get('trade_risk_budget_usd', 0):,.0f} per trade ({portfolio_summary.get('risk_pct_per_trade', 10):g}% of equity),
open positions: {portfolio_summary.get('open_positions', 0)}, realized P&L ${portfolio_summary.get('realized_pnl', 0):,.0f}.
Tickers with open exposure: {open_block}
Max trades this cycle: {max_trades}

Quantitative candidates (ranked):
{candidate_block}

{f"Past decision memory:{chr(10)}{memory_context}" if memory_context else ""}

Respond with JSON:
{{
  "market_assessment": "one sentence",
  "stand_aside": false,
  "reasoning": "2-3 sentences",
  "directives": [
    {{"ticker": "NVDA", "mode": "swing", "signal": "analyze", "conviction": 0.72, "rationale": "..."}}
  ]
}}"""
        from langchain_core.messages import HumanMessage, SystemMessage

        reply = self.llm.invoke([SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=user)])
        text = reply.content if hasattr(reply, "content") else str(reply)
        return _parse_decision(_extract_json(text))

    def _rules_fallback(
        self,
        candidates: list[StockCandidate],
        market: MarketContext,
        open_tickers: dict[str, int],
        max_trades: int,
    ) -> BrainDecision:
        """Deterministic CIO logic when no LLM is available."""
        if market.regime == "risk_off" and market.spy_return_5d < -0.03:
            return BrainDecision(
                market_assessment=market.assessment,
                stand_aside=True,
                reasoning="Risk-off tape with sharp 5-day drawdown — standing aside.",
            )

        directives: list[TradeDirective] = []
        for cand in candidates:
            if len(directives) >= max_trades:
                break
            if open_tickers.get(cand.ticker, 0) > 0:
                continue
            if cand.score < 0.55:
                continue

            bullish = cand.return_20d > 0 and cand.above_sma50
            bearish = cand.return_20d < -0.05 and not cand.above_sma50

            if bullish and cand.return_5d > 0.01:
                mode = "day" if abs(cand.return_5d) > 0.03 else "swing"
                # Full multi-agent "analyze" needs an LLM. Without one, take the
                # fast directional path so the scanner keeps trading instead of
                # raising "API key for provider 'openai' is not set".
                signal = "buy" if (mode == "day" or self.llm is None) else "analyze"
                conviction = min(0.95, 0.5 + cand.score * 0.4)
                rationale = (
                    f"Rules fallback: positive 20d momentum ({cand.return_20d:+.1%}), "
                    f"relative strength {cand.rel_strength_vs_spy:+.1%}, score {cand.score:.2f}."
                )
                if self.llm is None and mode == "swing":
                    rationale += " (no LLM key — using buy instead of full analyze)."
                directives.append(TradeDirective(
                    ticker=cand.ticker,
                    mode=mode,
                    signal=signal,
                    conviction=round(conviction, 2),
                    rationale=rationale,
                ))
            elif bearish and market.regime in ("risk_off", "volatile"):
                directives.append(TradeDirective(
                    ticker=cand.ticker,
                    mode="swing",
                    signal="sell",
                    conviction=round(0.5 + abs(cand.return_20d) * 2, 2),
                    rationale=(
                        f"Rules fallback: bearish trend ({cand.return_20d:+.1%}) in "
                        f"{market.regime} regime."
                    ),
                ))

        return BrainDecision(
            market_assessment=market.assessment,
            directives=directives,
            stand_aside=not directives,
            reasoning="Deterministic rules engine (no LLM).",
        )
