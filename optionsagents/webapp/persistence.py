"""Runtime checks for durable user data (SQLite + workspace files)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from optionsagents.paths import data_root
from optionsagents.webapp.database import db_path

logger = logging.getLogger(__name__)


def persistence_status() -> dict:
    """Report whether accounts/sessions survive process restarts."""
    root = data_root()
    db = db_path()
    root_path = Path(root)
    db_file = Path(db)

    configured = os.environ.get("OPTIONS_DATA_DIR", "").strip()
    on_volume = configured in {"/data", "./data"} or root.startswith("/data")

    writable = False
    try:
        root_path.mkdir(parents=True, exist_ok=True)
        probe = root_path / ".write_probe"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        writable = True
    except OSError:
        writable = False

    status = {
        "data_dir": root,
        "db_path": db,
        "data_dir_configured": bool(configured),
        "data_dir_writable": writable,
        "db_exists": db_file.is_file(),
        "db_size_bytes": db_file.stat().st_size if db_file.is_file() else 0,
        "persistent_volume_recommended": on_volume or writable,
    }

    if os.environ.get("PORT") and not configured:
        status["warning"] = (
            "OPTIONS_DATA_DIR is not set — accounts reset on every deploy/restart. "
            "Mount a volume at /data and set OPTIONS_DATA_DIR=/data."
        )
        logger.warning(status["warning"])
    elif configured and not writable:
        status["warning"] = f"Data directory {root!r} is not writable."
        logger.warning(status["warning"])

    return status
