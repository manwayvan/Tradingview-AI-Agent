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
    hv_20d: float | None = None
    iv_rank: float | None = None

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


def _compute_iv_rank_proxy(closes) -> tuple[float | None, float | None]:
    """HV20 and its 1-year percentile rank (IV rank proxy)."""
    import pandas as pd

    if len(closes) < 60:
        return None, None
    series = pd.Series(closes)
    rolling = series.pct_change().rolling(20).std() * (252 ** 0.5)
    rolling = rolling.dropna()
    if rolling.empty:
        return None, None
    hv_now = float(rolling.iloc[-1])
    window = rolling.iloc[-252:] if len(rolling) >= 252 else rolling
    lo, hi = float(window.min()), float(window.max())
    if hi <= lo:
        return hv_now, 50.0
    rank = 100.0 * (hv_now - lo) / (hi - lo)
    return hv_now, max(0.0, min(100.0, rank))


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

    hv_20d, iv_rank = None, None
    try:
        hist_1y = tk.history(period="1y", interval="1d", auto_adjust=True)
        if not hist_1y.empty and "Close" in hist_1y.columns:
            hv_20d, iv_rank = _compute_iv_rank_proxy(hist_1y["Close"].tolist())
    except Exception as exc:
        logger.debug("%s: could not compute IV rank proxy: %s", ticker, exc)

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
    snapshot = ChainSnapshot(
        underlying=ticker.upper(), spot=spot, asof=today,
        hv_20d=hv_20d, iv_rank=iv_rank,
    )

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

    atm = snapshot.atm_iv()
    if atm is not None and snapshot.iv_rank is not None and hv_20d:
        if atm > hv_20d * 1.15:
            snapshot.iv_rank = min(100.0, snapshot.iv_rank + 15.0)
        elif atm < hv_20d * 0.85:
            snapshot.iv_rank = max(0.0, snapshot.iv_rank - 15.0)
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
        f"- 20-day historical volatility: {fmt(snapshot.hv_20d, '{:.1%}')}",
        f"- IV rank (volatility percentile): {fmt(snapshot.iv_rank, '{:.0f}%')}",
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
    """Deterministic fallback when the LLM strategist is unavailable.

    Uses IV rank: credit spreads when vol is high, debit spreads moderate,
    long options when vol is low.
    """
    if direction not in ("bullish", "bearish"):
        return OptionsTradePlan(
            strategy=StrategyType.NO_TRADE,
            underlying=snapshot.underlying,
            direction="neutral",
            rationale="No directional edge; standing aside.",
            confidence=0.0,
        )

    iv_rank = snapshot.iv_rank if snapshot.iv_rank is not None else 25.0
    width = max(snapshot.spot * 0.025, 2.5)
    if iv_rank >= 50:
        return _build_credit_spread(direction, snapshot, mode, width, iv_rank)
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

    long_leg = cands[0]
    if iv_rank >= 30:
        return _build_debit_spread(direction, snapshot, mode, long_leg, width, iv_rank)
    return _build_long_option(direction, snapshot, mode, long_leg, iv_rank)


def _build_long_option(
    direction: str,
    snapshot: ChainSnapshot,
    mode: TradingMode,
    q: OptionQuote,
    iv_rank: float,
) -> OptionsTradePlan:
    right = "call" if direction == "bullish" else "put"
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
            f"Fallback: long {right} (IV rank {iv_rank:.0f}% — vol relatively low). "
            f"delta {q.delta:+.2f}, OI {q.open_interest}."
        ),
        confidence=0.45,
    )


def _pick_spread_pair(
    snapshot: ChainSnapshot,
    mode: TradingMode,
    right: str,
    long_leg: OptionQuote,
    width: float,
) -> tuple[OptionQuote, OptionQuote] | None:
    same_exp = [q for q in snapshot.candidates(mode, right) if q.expiry == long_leg.expiry]
    if len(same_exp) < 2:
        return None
    ordered = sorted(same_exp, key=lambda q: q.strike)
    idx = next(
        (i for i, q in enumerate(ordered) if abs(q.strike - long_leg.strike) < 1e-6),
        None,
    )
    if idx is None:
        return None
    if right == "call":
        if idx + 1 >= len(ordered):
            return None
        return long_leg, ordered[idx + 1]
    if idx - 1 < 0:
        return None
    return long_leg, ordered[idx - 1]


def _build_debit_spread(
    direction: str,
    snapshot: ChainSnapshot,
    mode: TradingMode,
    long_leg: OptionQuote,
    width: float,
    iv_rank: float,
) -> OptionsTradePlan:
    right = "call" if direction == "bullish" else "put"
    pair = _pick_spread_pair(snapshot, mode, right, long_leg, width)
    if pair is None:
        return _build_long_option(direction, snapshot, mode, long_leg, iv_rank)
    buy_leg, sell_leg = pair
    strat = (
        StrategyType.BULL_CALL_SPREAD if direction == "bullish"
        else StrategyType.BEAR_PUT_SPREAD
    )
    net = max(buy_leg.mid - sell_leg.mid, 0.05)
    return OptionsTradePlan(
        strategy=strat,
        underlying=snapshot.underlying,
        direction=direction,
        legs=[
            OptionLeg(action=LegAction.BUY, right=OptionRight(right), strike=buy_leg.strike, expiry=buy_leg.expiry, contracts=1),
            OptionLeg(action=LegAction.SELL, right=OptionRight(right), strike=sell_leg.strike, expiry=sell_leg.expiry, contracts=1),
        ],
        net_price=round(net, 2),
        price_type="debit",
        profit_target_pct=mode.profit_target_pct,
        stop_loss_pct=mode.stop_loss_pct,
        time_horizon="intraday" if mode.name == "day" else "2-4 weeks",
        rationale=(
            f"Fallback: debit {right} spread (IV rank {iv_rank:.0f}% — moderate vol). "
            f"Long {buy_leg.strike:g} / short {sell_leg.strike:g}."
        ),
        confidence=0.42,
    )


def _nearest_expiry_group(cands: list[OptionQuote]) -> list[OptionQuote] | None:
    """Group candidates by expiry; return the nearest expiry with >=2 quotes.

    ``snapshot.candidates()`` pools every expiry inside the mode's DTE
    window, sorted by delta proximity — so the two nearest-in-strike
    candidates can land on different expiries. A spread's legs must share
    one expiry, so we pick a single expiry up front.
    """
    by_expiry: dict[str, list[OptionQuote]] = {}
    for q in cands:
        by_expiry.setdefault(q.expiry, []).append(q)
    for expiry in sorted(by_expiry):
        if len(by_expiry[expiry]) >= 2:
            return by_expiry[expiry]
    return None


def _build_credit_spread(
    direction: str,
    snapshot: ChainSnapshot,
    mode: TradingMode,
    width: float,
    iv_rank: float,
) -> OptionsTradePlan:
    if direction == "bullish":
        right = "put"
        group = _nearest_expiry_group(snapshot.candidates(mode, right))
        if group is None:
            return OptionsTradePlan(
                strategy=StrategyType.NO_TRADE,
                underlying=snapshot.underlying,
                direction=direction,
                rationale="Not enough liquid puts in a single expiry for a credit spread fallback.",
                confidence=0.0,
            )
        ordered = sorted(group, key=lambda q: q.strike, reverse=True)
        sell_leg, buy_leg = ordered[0], ordered[1]
        strat = StrategyType.BULL_PUT_SPREAD
        net = max(sell_leg.mid - buy_leg.mid, 0.05)
        legs = [
            OptionLeg(action=LegAction.SELL, right=OptionRight.PUT, strike=sell_leg.strike, expiry=sell_leg.expiry, contracts=1),
            OptionLeg(action=LegAction.BUY, right=OptionRight.PUT, strike=buy_leg.strike, expiry=buy_leg.expiry, contracts=1),
        ]
        label = f"bull put spread {sell_leg.strike:g}/{buy_leg.strike:g}"
    else:
        right = "call"
        group = _nearest_expiry_group(snapshot.candidates(mode, right))
        if group is None:
            return OptionsTradePlan(
                strategy=StrategyType.NO_TRADE,
                underlying=snapshot.underlying,
                direction=direction,
                rationale="Not enough liquid calls in a single expiry for a credit spread fallback.",
                confidence=0.0,
            )
        ordered = sorted(group, key=lambda q: q.strike)
        sell_leg, buy_leg = ordered[0], ordered[1]
        strat = StrategyType.BEAR_CALL_SPREAD
        net = max(sell_leg.mid - buy_leg.mid, 0.05)
        legs = [
            OptionLeg(action=LegAction.SELL, right=OptionRight.CALL, strike=sell_leg.strike, expiry=sell_leg.expiry, contracts=1),
            OptionLeg(action=LegAction.BUY, right=OptionRight.CALL, strike=buy_leg.strike, expiry=buy_leg.expiry, contracts=1),
        ]
        label = f"bear call spread {sell_leg.strike:g}/{buy_leg.strike:g}"

    return OptionsTradePlan(
        strategy=strat,
        underlying=snapshot.underlying,
        direction=direction,
        legs=legs,
        net_price=round(net, 2),
        price_type="credit",
        profit_target_pct=mode.profit_target_pct,
        stop_loss_pct=mode.stop_loss_pct,
        time_horizon="intraday" if mode.name == "day" else "2-4 weeks",
        rationale=(
            f"Fallback: {label} (IV rank {iv_rank:.0f}% — elevated vol, sell premium)."
        ),
        confidence=0.4,
    )
