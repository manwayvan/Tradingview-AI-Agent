"""Portfolio-level risk controls for all trading entry paths."""

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
    """Gate trades against account-wide loss and exposure limits."""

    max_daily_loss: float = 2_500.0
    max_total_open_risk: float = 5_000.0
    max_positions_per_ticker: int = 1
    include_unrealized_in_kill_switch: bool = True
    _daily_realized: float = 0.0
    _daily_date: str = ""

    def refresh_limits(self, *, max_daily_loss: float, max_total_open_risk: float) -> None:
        self.max_daily_loss = max_daily_loss
        self.max_total_open_risk = max_total_open_risk

    def _sync_daily_pnl(self, broker: PaperBroker) -> tuple[float, float]:
        """Return (realized_today, total_daily_pnl including unrealized if enabled)."""
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

        total = realized_today
        if self.include_unrealized_in_kill_switch:
            for pos in broker.positions("open"):
                if pos.unrealized_pnl is not None:
                    total += pos.unrealized_pnl
        return realized_today, total

    def total_open_risk(self, broker: PaperBroker) -> float:
        return sum(p.max_risk for p in broker.positions("open"))

    def open_tickers(self, broker: PaperBroker) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in broker.positions("open"):
            counts[p.underlying] = counts.get(p.underlying, 0) + 1
        return counts

    def check_trade(
        self,
        ticker: str,
        broker: PaperBroker,
        mode_max_risk: float,
    ) -> RiskVerdict:
        realized_today, daily_pnl = self._sync_daily_pnl(broker)
        if daily_pnl <= -self.max_daily_loss:
            detail = (
                f"daily P&L ${daily_pnl:,.0f} (realized ${realized_today:,.0f}) "
                f"<= -${self.max_daily_loss:,.0f} kill switch"
            )
            return RiskVerdict(False, detail)

        open_risk = self.total_open_risk(broker)
        if open_risk + mode_max_risk > self.max_total_open_risk:
            return RiskVerdict(
                False,
                f"total open risk ${open_risk:,.0f} + ${mode_max_risk:,.0f} "
                f"exceeds cap ${self.max_total_open_risk:,.0f}",
            )

        ticker_count = self.open_tickers(broker).get(ticker.upper(), 0)
        if ticker_count >= self.max_positions_per_ticker:
            return RiskVerdict(
                False,
                f"already {ticker_count} open position(s) on {ticker.upper()}",
            )

        return RiskVerdict(True)

    def check_directive(
        self,
        directive: TradeDirective,
        broker: PaperBroker,
        mode_max_risk: float,
    ) -> RiskVerdict:
        return self.check_trade(directive.ticker, broker, mode_max_risk)

    def snapshot(self, broker: PaperBroker) -> dict:
        realized_today, daily_pnl = self._sync_daily_pnl(broker)
        return {
            "daily_realized_pnl": round(realized_today, 2),
            "daily_pnl_including_unrealized": round(daily_pnl, 2),
            "daily_loss_remaining": round(self.max_daily_loss + daily_pnl, 2),
            "total_open_risk": round(self.total_open_risk(broker), 2),
            "max_daily_loss": self.max_daily_loss,
            "max_total_open_risk": self.max_total_open_risk,
            "kill_switch_active": daily_pnl <= -self.max_daily_loss,
        }
