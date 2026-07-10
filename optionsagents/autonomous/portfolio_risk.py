"""Portfolio-level risk controls for autonomous trading."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from optionsagents.autonomous.brain import TradeDirective
from optionsagents.paper_broker import PaperBroker

ET = ZoneInfo("America/New_York")


@dataclass
class RiskVerdict:
    allowed: bool
    reason: str = ""


@dataclass
class PortfolioRiskManager:
    """Gate autonomous trades against account-wide loss and exposure limits."""

    max_daily_loss: float = 2_500.0
    max_total_open_risk: float = 5_000.0
    max_positions_per_ticker: int = 1
    _daily_realized: float = 0.0
    _daily_date: str = ""

    def _sync_daily_pnl(self, broker: PaperBroker) -> float:
        today = datetime.now(tz=ET).date().isoformat()
        if self._daily_date != today:
            self._daily_date = today
            self._daily_realized = 0.0

        realized_today = 0.0
        for pos in broker.positions("closed"):
            closed = pos.closed_at or ""
            if closed[:10] == today and pos.realized_pnl is not None:
                realized_today += pos.realized_pnl
        self._daily_realized = realized_today
        return realized_today

    def total_open_risk(self, broker: PaperBroker) -> float:
        return sum(p.max_risk for p in broker.positions("open"))

    def open_tickers(self, broker: PaperBroker) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in broker.positions("open"):
            counts[p.underlying] = counts.get(p.underlying, 0) + 1
        return counts

    def check_directive(
        self,
        directive: TradeDirective,
        broker: PaperBroker,
        mode_max_risk: float,
    ) -> RiskVerdict:
        realized_today = self._sync_daily_pnl(broker)
        if realized_today <= -self.max_daily_loss:
            return RiskVerdict(
                False,
                f"daily loss limit hit (${realized_today:,.0f} <= -${self.max_daily_loss:,.0f})",
            )

        open_risk = self.total_open_risk(broker)
        if open_risk + mode_max_risk > self.max_total_open_risk:
            return RiskVerdict(
                False,
                f"total open risk ${open_risk:,.0f} + ${mode_max_risk:,.0f} "
                f"exceeds cap ${self.max_total_open_risk:,.0f}",
            )

        ticker_count = self.open_tickers(broker).get(directive.ticker.upper(), 0)
        if ticker_count >= self.max_positions_per_ticker:
            return RiskVerdict(
                False,
                f"already {ticker_count} open position(s) on {directive.ticker}",
            )

        return RiskVerdict(True)

    def snapshot(self, broker: PaperBroker) -> dict:
        realized_today = self._sync_daily_pnl(broker)
        return {
            "daily_realized_pnl": realized_today,
            "daily_loss_remaining": self.max_daily_loss + realized_today,
            "total_open_risk": self.total_open_risk(broker),
            "max_daily_loss": self.max_daily_loss,
            "max_total_open_risk": self.max_total_open_risk,
            "kill_switch_active": realized_today <= -self.max_daily_loss,
        }
