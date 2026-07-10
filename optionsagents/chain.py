"""Options chain snapshotting and analysis.

``fetch_chain_snapshot`` pulls live chains from yfinance for the expiries
inside a mode's DTE window and computes the metrics the strategist needs
(ATM IV, expected move, put/call ratios, delta per strike). Everything
downstream — the rendered report, plan validation, the deterministic
fallback plan — operates on the ``ChainSnapshot`` dataclass, so tests and
the paper broker can run on synthetic snapshots without network access.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from optionsagents.greeks import bs_delta
from optionsagents.modes import TradingMode
from optionsagents.schemas import (
    LegAction,
    OptionLeg,
    OptionRight,
    OptionsTradePlan,
    StrategyType,
)

logger = logging.getLogger(__name__)

RISK_FREE_RATE = 0.05


@dataclass
class OptionQuote:
    expiry: str          # YYYY-MM-DD
    right: str           # "call" | "put"
    strike: float
    bid: float
    ask: float
    iv: float            # implied volatility as decimal, e.g. 0.45
    volume: int
    open_interest: int
    delta: float = 0.0

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return 0.5 * (self.bid + self.ask)
        return max(self.bid, self.ask, 0.0)

    @property
    def spread_pct(self) -> float:
        mid = self.mid
        if mid <= 0:
            return float("inf")
        return 100.0 * (self.ask - self.bid) / mid


@dataclass
class ChainSnapshot:
    underlying: str
    spot: float
    asof: date
    quotes: list[OptionQuote] = field(default_factory=list)

    def expiries(self) -> list[str]:
        return sorted({q.expiry for q in self.quotes})

    def lookup(self, expiry: str, right: str, strike: float) -> OptionQuote | None:
        for q in self.quotes:
            if q.expiry == expiry and q.right == right and abs(q.strike - strike) < 1e-6:
                return q
        return None

    def dte(self, expiry: str) -> int:
        return (datetime.strptime(expiry, "%Y-%m-%d").date() - self.asof).days

    # ---- Metrics -----------------------------------------------------

    def _nearest_expiry(self) -> str | None:
        exps = self.expiries()
        return exps[0] if exps else None

    def atm_quotes(self, expiry: str) -> tuple[OptionQuote | None, OptionQuote | None]:
        """The call and put closest to spot for an expiry."""
        calls = [q for q in self.quotes if q.expiry == expiry and q.right == "call"]
        puts = [q for q in self.quotes if q.expiry == expiry and q.right == "put"]
        atm_call = min(calls, key=lambda q: abs(q.strike - self.spot), default=None)
        atm_put = min(puts, key=lambda q: abs(q.strike - self.spot), default=None)
        return atm_call, atm_put

    def atm_iv(self) -> float | None:
        exp = self._nearest_expiry()
        if not exp:
            return None
        call, put = self.atm_quotes(exp)
        ivs = [q.iv for q in (call, put) if q and q.iv > 0]
        return sum(ivs) / len(ivs) if ivs else None

    def expected_move_pct(self) -> float | None:
        """Straddle-implied expected move to nearest expiry, as % of spot."""
        exp = self._nearest_expiry()
        if not exp or self.spot <= 0:
            return None
        call, put = self.atm_quotes(exp)
        if not call or not put:
            return None
        straddle = call.mid + put.mid
        if straddle <= 0:
            return None
        return 100.0 * straddle / self.spot

    def put_call_volume_ratio(self) -> float | None:
        cv = sum(q.volume for q in self.quotes if q.right == "call")
        pv = sum(q.volume for q in self.quotes if q.right == "put")
        return (pv / cv) if cv > 0 else None

    def put_call_oi_ratio(self) -> float | None:
        co = sum(q.open_interest for q in self.quotes if q.right == "call")
        po = sum(q.open_interest for q in self.quotes if q.right == "put")
        return (po / co) if co > 0 else None

    def candidates(self, mode: TradingMode, right: str) -> list[OptionQuote]:
        """Liquid strikes inside the mode's delta band, best-first."""
        out = [
            q for q in self.quotes
            if q.right == right
            and mode.delta_low <= abs(q.delta) <= mode.delta_high
            and q.open_interest >= mode.min_open_interest
            and q.spread_pct <= mode.max_spread_pct
            and q.mid > 0
        ]
        # Prefer liquidity, then delta near the middle of the band.
        band_mid = 0.5 * (mode.delta_low + mode.delta_high)
        out.sort(key=lambda q: (abs(abs(q.delta) - band_mid), -q.open_interest))
        return out


def fetch_chain_snapshot(ticker: str, mode: TradingMode, max_expiries: int = 3) -> ChainSnapshot:
    """Pull a live chain snapshot from yfinance for the mode's DTE window.

    Imports yfinance lazily so the rest of the package stays usable
    offline (tests, synthetic snapshots).
    """
    import yfinance as yf

    tk = yf.Ticker(ticker)
    today = date.today()

    hist = tk.history(period="1d")
    if hist.empty:
        raise ValueError(f"No price data for {ticker!r}")
    spot = float(hist["Close"].iloc[-1])

    all_exps = list(tk.options or [])
    in_window = [
        e for e in all_exps
        if mode.dte_min <= (datetime.strptime(e, "%Y-%m-%d").date() - today).days <= mode.dte_max
    ]
    if not in_window and all_exps:
        # Nothing inside the window (e.g. day mode on a ticker with only
        # monthlies): fall back to the nearest expiry beyond dte_min.
        beyond = [
            e for e in all_exps
            if (datetime.strptime(e, "%Y-%m-%d").date() - today).days >= mode.dte_min
        ]
        in_window = beyond[:1]
        if in_window:
            logger.warning(
                "%s: no expiries within %d-%d DTE; using nearest available %s",
                ticker, mode.dte_min, mode.dte_max, in_window[0],
            )
    snapshot = ChainSnapshot(underlying=ticker.upper(), spot=spot, asof=today)

    for expiry in in_window[:max_expiries]:
        try:
            chain = tk.option_chain(expiry)
        except Exception as exc:
            logger.warning("%s: failed to fetch chain for %s: %s", ticker, expiry, exc)
            continue
        t_years = max((datetime.strptime(expiry, "%Y-%m-%d").date() - today).days, 0.5) / 365.0
        for frame, right in ((chain.calls, "call"), (chain.puts, "put")):
            for row in frame.itertuples():
                iv = float(getattr(row, "impliedVolatility", 0.0) or 0.0)
                strike = float(row.strike)
                q = OptionQuote(
                    expiry=expiry,
                    right=right,
                    strike=strike,
                    bid=float(row.bid or 0.0),
                    ask=float(row.ask or 0.0),
                    iv=iv,
                    volume=int(row.volume) if row.volume == row.volume else 0,  # NaN guard
                    open_interest=(
                        int(row.openInterest) if row.openInterest == row.openInterest else 0
                    ),
                    delta=bs_delta(spot, strike, t_years, iv, right == "call", RISK_FREE_RATE)
                    if iv > 0 else 0.0,
                )
                snapshot.quotes.append(q)
    return snapshot


def render_chain_report(snapshot: ChainSnapshot, mode: TradingMode) -> str:
    """Markdown chain report handed to the options strategist."""

    def fmt(v, pattern="{:.2f}", na="n/a"):
        return pattern.format(v) if v is not None else na

    lines = [
        f"### Options Chain Snapshot: {snapshot.underlying}",
        f"- As of: {snapshot.asof.isoformat()}",
        f"- Spot price: ${snapshot.spot:.2f}",
        f"- Trading mode: {mode.name} (target DTE {mode.dte_min}-{mode.dte_max}, "
        f"|delta| {mode.delta_low:.2f}-{mode.delta_high:.2f})",
        f"- ATM implied volatility (nearest expiry): {fmt(snapshot.atm_iv(), '{:.1%}')}",
        f"- Expected move to nearest expiry: {fmt(snapshot.expected_move_pct(), '{:.1f}%')}",
        f"- Put/Call volume ratio: {fmt(snapshot.put_call_volume_ratio())}",
        f"- Put/Call open-interest ratio: {fmt(snapshot.put_call_oi_ratio())}",
        f"- Available expirations: {', '.join(snapshot.expiries()) or 'none'}",
        "",
    ]
    for right in ("call", "put"):
        cands = snapshot.candidates(mode, right)[:12]
        lines.append(f"#### Candidate {right}s (liquid, inside delta band)")
        if not cands:
            lines.append("_None passed the liquidity/delta filters._")
            lines.append("")
            continue
        lines.append("| Expiry | Strike | Bid | Ask | Mid | IV | Delta | OI | Vol |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for q in cands:
            lines.append(
                f"| {q.expiry} | {q.strike:g} | {q.bid:.2f} | {q.ask:.2f} | {q.mid:.2f} "
                f"| {q.iv:.0%} | {q.delta:+.2f} | {q.open_interest} | {q.volume} |"
            )
        lines.append("")
    return "\n".join(lines)


def validate_plan_against_chain(plan: OptionsTradePlan, snapshot: ChainSnapshot) -> list[str]:
    """Return a list of problems; empty means the plan is executable."""
    problems: list[str] = []
    if plan.strategy == StrategyType.NO_TRADE:
        return problems
    if plan.underlying.upper() != snapshot.underlying.upper():
        problems.append(
            f"plan underlying {plan.underlying} does not match chain {snapshot.underlying}"
        )
    for leg in plan.legs:
        q = snapshot.lookup(leg.expiry, leg.right.value, leg.strike)
        if q is None:
            problems.append(
                f"leg {leg.expiry} {leg.strike:g} {leg.right.value} not found in chain"
            )
        elif q.mid <= 0:
            problems.append(
                f"leg {leg.expiry} {leg.strike:g} {leg.right.value} has no usable quote"
            )
    return problems


def plan_mid_price(plan: OptionsTradePlan, snapshot: ChainSnapshot) -> float | None:
    """Net mid per share for the plan (positive number in the plan's price_type
    convention: debit for debit strategies, credit for credit spreads)."""
    net = 0.0
    for leg in plan.legs:
        q = snapshot.lookup(leg.expiry, leg.right.value, leg.strike)
        if q is None or q.mid <= 0:
            return None
        net += q.mid if leg.action == LegAction.BUY else -q.mid
    return net if plan.price_type == "debit" else -net


def build_default_plan(
    direction: str, snapshot: ChainSnapshot, mode: TradingMode
) -> OptionsTradePlan:
    """Deterministic fallback: a single long option at the best candidate strike.

    Used when the LLM strategist is unavailable or produced a plan that
    fails chain validation. Sizing is left at one contract; the pipeline's
    risk gate scales it.
    """
    if direction not in ("bullish", "bearish"):
        return OptionsTradePlan(
            strategy=StrategyType.NO_TRADE,
            underlying=snapshot.underlying,
            direction="neutral",
            rationale="No directional edge; standing aside.",
            confidence=0.0,
        )
    right = "call" if direction == "bullish" else "put"
    cands = snapshot.candidates(mode, right)
    if not cands:
        return OptionsTradePlan(
            strategy=StrategyType.NO_TRADE,
            underlying=snapshot.underlying,
            direction=direction,
            rationale=(
                f"No {right}s passed liquidity filters (OI>={mode.min_open_interest}, "
                f"spread<={mode.max_spread_pct}%): not trading illiquid contracts."
            ),
            confidence=0.0,
        )
    q = cands[0]
    strat = StrategyType.LONG_CALL if right == "call" else StrategyType.LONG_PUT
    return OptionsTradePlan(
        strategy=strat,
        underlying=snapshot.underlying,
        direction=direction,
        legs=[OptionLeg(
            action=LegAction.BUY,
            right=OptionRight(right),
            strike=q.strike,
            expiry=q.expiry,
            contracts=1,
        )],
        net_price=round(q.mid, 2),
        price_type="debit",
        profit_target_pct=mode.profit_target_pct,
        stop_loss_pct=mode.stop_loss_pct,
        time_horizon="intraday" if mode.name == "day" else "2-4 weeks",
        rationale=(
            f"Fallback selection: most liquid {right} inside the {mode.name}-mode "
            f"delta band (delta {q.delta:+.2f}, OI {q.open_interest})."
        ),
        confidence=0.4,
    )
