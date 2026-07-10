"""Central paths for persisted data (local dev vs cloud volume)."""

from __future__ import annotations

import os


def data_root() -> str:
    """Base directory for SQLite, user workspaces, and paper accounts.

  Set ``OPTIONS_DATA_DIR`` in production (e.g. ``/data`` on Railway/Render)
  and mount a persistent volume at that path.
    """
    return os.environ.get(
        "OPTIONS_DATA_DIR",
        os.path.join(os.path.expanduser("~"), ".tradingagents"),
    )
