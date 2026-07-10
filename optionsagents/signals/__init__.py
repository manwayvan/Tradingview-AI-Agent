"""Built-in free signal engine — no TradingView subscription required."""

from optionsagents.signals.free_engine import FreeSignalEngine, SignalEvent
from optionsagents.signals.technicals import scan_day_signal, scan_swing_signal

__all__ = [
    "FreeSignalEngine",
    "SignalEvent",
    "scan_day_signal",
    "scan_swing_signal",
]
