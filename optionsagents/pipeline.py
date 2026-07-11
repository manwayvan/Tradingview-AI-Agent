"""End-to-end options trading pipeline.

Two entry paths converge on the same execution flow:

- ``run(ticker)`` — full research: the upstream TradingAgents multi-agent
  graph debates the underlying and produces a portfolio rating, which is
  mapped to a direction for the options strategist. Best for swing trades,
  where a few minutes of LLM deliberation is cheap relative to the holding
  period.
- ``run_signal(ticker, "buy"|"sell")`` — fast path: a TradingView alert
  (or the CLI) supplies the direction and only the strategist LLM call
  runs. Built for day trading, where the setup is gone by the time a full
  debate finishes.

Both paths end with: chain snapshot -> strategist -> risk gate -> paper fill.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date

from optionsagents.chain import ChainSnapshot, fetch_chain_snapshot, render_chain_report
from optionsagents.modes import TradingMode, get_mode
from optionsagents.orders import OrderContext
from optionsagents.paper_broker import PaperBroker, Position
from optionsagents.schemas import OptionsTradePlan, StrategyType, render_trade_plan
from optionsagents.strategist import OptionsStrategist

logger = logging.getLogger(__name__)

# Upstream 5-tier portfolio rating -> options direction.
RATING_TO_DIRECTION = {
    "buy": "bullish",
    "overweight": "bullish",
    "hold": "neutral",
    "underweight": "bearish",
    "sell": "bearish",
}

DEFAULT_ACCOUNT_FILE = os.path.join(
    os.path.expanduser("~"), ".tradingagents", "paper_account.json"
)


@dataclass
class PipelineResult:
    ticker: str
    mode: str
    direction: str
    rating: str | None            # upstream rating (full-research path only)
    plan: OptionsTradePlan
    position: Position | None     # None when no trade was opened
    chain_report: str
    decision_context: str
    warnings: list[str] = field(default_factory=list)

    def report_markdown(self) -> str:
        lines = [
            f"# Options Trade Report: {self.ticker} ({self.mode} mode)",
            "",
            f"- Direction: **{self.direction}**"
            + (f" (rating: {self.rating})" if self.rating else ""),
            "",
            "## Trade Plan",
            render_trade_plan(self.plan),
            "",
        ]
        if self.position:
            lines += [
                "## Paper Fill",
                f"- Position ID: `{self.position.id}`",
                f"- Filled net {self.position.price_type}: ${self.position.entry_net:.2f}/share",
                f"- Max risk: ${self.position.max_risk:,.0f}",
                "",
            ]
        if self.warnings:
            lines += ["## Warnings"] + [f"- {w}" for w in self.warnings] + [""]
        lines += ["## Chain Snapshot", self.chain_report]
        return "\n".join(lines)


class OptionsPipeline:
    def __init__(
        self,
        mode: str | TradingMode = "swing",
        config: dict | None = None,
        account_file: str = DEFAULT_ACCOUNT_FILE,
        starting_cash: float = 100_000.0,
        use_llm_strategist: bool = True,
        debug: bool = False,
        broker: PaperBroker | None = None,
    ):
        self.mode = mode if isinstance(mode, TradingMode) else get_mode(mode)
        self.config = config
        self.debug = debug
        # Pass a shared broker when several pipelines (e.g. day + swing in
        # the web server) trade one account: two PaperBroker instances on
        # the same file would overwrite each other's state on save.
        self.broker = broker or PaperBroker(account_file, starting_cash=starting_cash)
        self._graph = None            # TradingAgentsGraph, built lazily
        self._strategist = None
        self._use_llm_strategist = use_llm_strategist

    # ---- lazy LLM wiring ----------------------------------------------

    def _get_graph(self):
        if self._graph is None:
            from tradingagents.default_config import DEFAULT_CONFIG
            from tradingagents.graph.trading_graph import TradingAgentsGraph

            self._graph = TradingAgentsGraph(
                debug=self.debug, config=self.config or DEFAULT_CONFIG.copy()
            )
        return self._graph

    def _get_strategist(self) -> OptionsStrategist:
        if self._strategist is None:
            llm = None
            if self._use_llm_strategist:
                try:
                    llm = self._get_graph().deep_thinking_llm
                except Exception as exc:
                    logger.warning(
                        "Could not initialize LLM for strategist (%s); "
                        "using deterministic strike selection", exc,
                    )
            self._strategist = OptionsStrategist(llm)
        return self._strategist

    # ---- entry points --------------------------------------------------

    def run(
        self,
        ticker: str,
        trade_date: str | None = None,
        order_ctx: OrderContext | None = None,
        direction_hint: str | None = None,
    ) -> PipelineResult:
        """Full research path: multi-agent debate -> rating -> options trade."""
        trade_date = trade_date or date.today().isoformat()
        graph = self._get_graph()
        final_state, rating = graph.propagate(ticker, trade_date)
        direction = RATING_TO_DIRECTION.get(str(rating).strip().lower(), "neutral")
        decision_context = final_state.get("final_trade_decision", "") or str(rating)
        if order_ctx and order_ctx.decision_context:
            decision_context = f"{order_ctx.decision_context}\n\nResearch result:\n{decision_context}"

        if direction == "neutral" and direction_hint in ("bullish", "bearish"):
            direction = direction_hint
            decision_context += (
                f"\n\nTechnical setup suggests {direction_hint}; "
                "research was neutral/hold — using technical bias for options plan."
            )
        elif (
            direction_hint in ("bullish", "bearish")
            and direction in ("bullish", "bearish")
            and direction != direction_hint
        ):
            decision_context += (
                f"\n\nNote: technical hint ({direction_hint}) differs from "
                f"research direction ({direction}). Using research rating."
            )

        return self._trade(ticker, direction, decision_context, rating=str(rating), order_ctx=order_ctx)

    def run_signal(
        self,
        ticker: str,
        signal: str,
        context: str = "",
        order_ctx: OrderContext | None = None,
    ) -> PipelineResult:
        """Fast path: direction comes from a TradingView alert or the CLI."""
        direction = {"buy": "bullish", "sell": "bearish"}.get(signal.lower(), "neutral")
        decision_context = context or (
            f"External {signal.upper()} signal received for {ticker} "
            f"(TradingView alert fast path; no in-house research this round)."
        )
        return self._trade(ticker, direction, decision_context, rating=None, order_ctx=order_ctx)

    # ---- shared execution flow ------------------------------------------

    def _trade(
        self,
        ticker: str,
        direction: str,
        decision_context: str,
        rating: str | None,
        order_ctx: OrderContext | None = None,
    ) -> PipelineResult:
        warnings: list[str] = []
        snapshot = fetch_chain_snapshot(ticker, self.mode)
        chain_report = render_chain_report(snapshot, self.mode)

        if not snapshot.quotes:
            plan = OptionsTradePlan(
                strategy=StrategyType.NO_TRADE, underlying=ticker.upper(),
                direction="neutral",
                rationale=f"No options chain available for {ticker} in the "
                          f"{self.mode.name}-mode DTE window.",
            )
            if order_ctx:
                self.broker.record_order(
                    order_ctx,
                    status="skipped",
                    plan_rationale=plan.rationale,
                    warnings=["no chain data"],
                )
            return PipelineResult(
                ticker=ticker.upper(), mode=self.mode.name, direction=direction,
                rating=rating, plan=plan, position=None,
                chain_report=chain_report, decision_context=decision_context,
                warnings=["no chain data"],
            )

        plan = self._get_strategist().propose(
            decision_context, direction, snapshot, self.mode
        )
        plan = self._risk_gate(plan, warnings)

        position = None
        if plan.strategy != StrategyType.NO_TRADE:
            open_count = len(self.broker.positions("open"))
            if open_count >= self.mode.max_open_positions:
                warnings.append(
                    f"max open positions reached ({open_count}); trade skipped"
                )
                if order_ctx:
                    self.broker.record_order(
                        order_ctx,
                        status="skipped",
                        plan_rationale=plan.rationale,
                        strategy=plan.strategy.value,
                        confidence=plan.confidence,
                        warnings=warnings,
                    )
                plan = plan.model_copy(update={"strategy": StrategyType.NO_TRADE, "legs": []})
            else:
                ctx = order_ctx
                if ctx is None:
                    ctx = OrderContext(
                        source="unknown",
                        ticker=ticker.upper(),
                        mode=self.mode.name,
                        signal="analyze",
                        direction=direction,
                        decision_context=decision_context,
                        rating=rating,
                    )
                else:
                    ctx = OrderContext(
                        source=ctx.source,
                        ticker=ctx.ticker,
                        mode=ctx.mode,
                        signal=ctx.signal,
                        direction=direction or ctx.direction,
                        source_label=ctx.source_label,
                        source_rationale=ctx.source_rationale,
                        decision_context=decision_context,
                        rating=rating or ctx.rating,
                        conviction=ctx.conviction,
                        source_ref=ctx.source_ref,
                    )
                mode_rules = {
                    "mode": self.mode.name,
                    "dte_window": f"{self.mode.dte_min}-{self.mode.dte_max}",
                    "delta_band": f"{self.mode.delta_low:.2f}-{self.mode.delta_high:.2f}",
                    "max_risk_per_trade": self.mode.max_risk_per_trade,
                    "min_open_interest": self.mode.min_open_interest,
                    "max_spread_pct": self.mode.max_spread_pct,
                }
                try:
                    position = self.broker.execute_plan(
                        plan, snapshot, self.mode.name, order_ctx=ctx,
                        mode_rules=mode_rules,
                    )
                except ValueError as exc:
                    warnings.append(f"paper fill failed: {exc}")
                    self.broker.record_order(
                        ctx,
                        status="skipped",
                        plan_rationale=plan.rationale,
                        strategy=plan.strategy.value,
                        confidence=plan.confidence,
                        warnings=warnings,
                    )

        result = PipelineResult(
            ticker=ticker.upper(), mode=self.mode.name, direction=direction,
            rating=rating, plan=plan, position=position,
            chain_report=chain_report, decision_context=decision_context,
            warnings=warnings,
        )
        self._save_report(result)
        return result

    def _risk_gate(self, plan: OptionsTradePlan, warnings: list[str]) -> OptionsTradePlan:
        """Clamp position size to the mode's max risk; never scale up."""
        if plan.strategy == StrategyType.NO_TRADE or not plan.legs:
            return plan
        per_contract = plan.max_risk_per_contract()
        if per_contract <= 0:
            warnings.append("plan has non-positive max risk; rejected")
            return plan.model_copy(update={"strategy": StrategyType.NO_TRADE, "legs": []})
        if per_contract > self.mode.max_risk_per_trade:
            warnings.append(
                f"single contract risks ${per_contract:,.0f} > mode limit "
                f"${self.mode.max_risk_per_trade:,.0f}; trade rejected"
            )
            return plan.model_copy(update={"strategy": StrategyType.NO_TRADE, "legs": []})
        allowed = int(self.mode.max_risk_per_trade // per_contract)
        if plan.contracts > allowed:
            warnings.append(
                f"contracts clamped {plan.contracts} -> {allowed} to keep max loss "
                f"within ${self.mode.max_risk_per_trade:,.0f}"
            )
            legs = [leg.model_copy(update={"contracts": allowed}) for leg in plan.legs]
            plan = plan.model_copy(update={"legs": legs})
        return plan

    # ---- maintenance -----------------------------------------------------

    def check_positions(self) -> list[Position]:
        """Mark all open positions and apply stop/target/expiry exits.

        Returns the positions that were closed. Run this periodically (the
        webhook server exposes it, and the CLI has a ``mark`` command).
        """
        closed = []
        open_positions = self.broker.positions("open")
        by_ticker: dict[str, list[Position]] = {}
        for p in open_positions:
            by_ticker.setdefault(p.underlying, []).append(p)
        for ticker, positions in by_ticker.items():
            try:
                snapshot = self._snapshot_for_positions(ticker, positions)
            except Exception as exc:
                logger.warning("could not refresh chain for %s: %s", ticker, exc)
                continue
            for p in positions:
                done = self.broker.check_exits(p, snapshot)
                if done is not None:
                    closed.append(done)
        return closed

    def _snapshot_for_positions(
        self, ticker: str, positions: list[Position]
    ) -> ChainSnapshot:
        """Fetch a snapshot wide enough to cover every held expiry."""
        max_dte = 1
        for p in positions:
            for leg in p.legs:
                from datetime import datetime
                dte = (datetime.strptime(leg.expiry, "%Y-%m-%d").date() - date.today()).days
                max_dte = max(max_dte, dte)
        from dataclasses import replace
        wide = replace(self.mode, dte_min=0, dte_max=max_dte)
        return fetch_chain_snapshot(ticker, wide, max_expiries=8)

    def _save_report(self, result: PipelineResult) -> None:
        try:
            base = None
            if self.config and self.config.get("results_dir"):
                base = self.config["results_dir"]
            else:
                base = os.path.join(os.path.expanduser("~"), ".tradingagents", "logs")
            outdir = os.path.join(base, "options", result.ticker)
            os.makedirs(outdir, exist_ok=True)
            stamp = date.today().isoformat()
            path = os.path.join(outdir, f"{stamp}_{result.mode}.md")
            with open(path, "w") as f:
                f.write(result.report_markdown())
            logger.info("saved options report to %s", path)
        except OSError as exc:
            logger.warning("could not save report: %s", exc)
