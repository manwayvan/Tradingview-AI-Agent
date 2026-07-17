"""Configuration for the autonomous trading brain."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw else default


# Liquid names with active options markets — used when no custom universe is set.
DEFAULT_UNIVERSE: tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA",
    "AMD", "NFLX", "BA", "JPM", "XOM", "COIN", "PLTR", "SMCI", "ARM", "CRWD",
    "AVGO", "ORCL", "DIS", "UBER", "SHOP", "SNAP", "MARA", "SOFI", "HOOD", "RIVN",
)


@dataclass(frozen=True)
class AutonomousConfig:
    """Tunable knobs for the self-directed trading loop."""

    enabled: bool = False
    universe: tuple[str, ...] = DEFAULT_UNIVERSE
    scan_top_n: int = 12          # candidates passed to the strategy brain
    max_trades_per_cycle: int = 2
    cycle_interval_minutes: int = 5
    min_conviction: float = 0.55  # brain must score above this to trade
    max_daily_loss: float = 2_500.0
    max_total_open_risk: float = 5_000.0
    max_positions_per_ticker: int = 1
    use_full_research: bool = True  # analyze path vs fast buy/sell signals
    state_file: str = field(
        default_factory=lambda: os.path.join(
            os.path.expanduser("~"), ".tradingagents", "autonomous_state.json"
        )
    )

    @classmethod
    def from_env(cls) -> AutonomousConfig:
        universe_raw = os.environ.get("AUTONOMOUS_UNIVERSE", "")
        universe = tuple(
            t.strip().upper() for t in universe_raw.split(",") if t.strip()
        ) or DEFAULT_UNIVERSE
        return cls(
            enabled=_env_bool("AUTONOMOUS_ENABLED", False),
            universe=universe,
            scan_top_n=_env_int("AUTONOMOUS_SCAN_TOP_N", 12),
            max_trades_per_cycle=_env_int("AUTONOMOUS_MAX_TRADES_PER_CYCLE", 2),
            cycle_interval_minutes=_env_int("AUTONOMOUS_CYCLE_MINUTES", 5),
            min_conviction=_env_float("AUTONOMOUS_MIN_CONVICTION", 0.55),
            max_daily_loss=_env_float("AUTONOMOUS_MAX_DAILY_LOSS", 2_500.0),
            max_total_open_risk=_env_float("AUTONOMOUS_MAX_OPEN_RISK", 5_000.0),
            max_positions_per_ticker=_env_int("AUTONOMOUS_MAX_PER_TICKER", 1),
            use_full_research=_env_bool("AUTONOMOUS_USE_FULL_RESEARCH", True),
            state_file=os.environ.get(
                "AUTONOMOUS_STATE_FILE",
                os.path.join(
                    os.path.expanduser("~"), ".tradingagents", "autonomous_state.json"
                ),
            ),
        )

    def with_overrides(self, **kwargs) -> AutonomousConfig:
        return replace(self, **kwargs)
