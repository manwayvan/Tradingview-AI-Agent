"""SQLite persistence for users and sessions."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from optionsagents.paths import data_root

_DEFAULT_DB = os.path.join(data_root(), "app.db")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def db_path() -> str:
    return os.environ.get("OPTIONS_APP_DB", _DEFAULT_DB)


def _connect() -> sqlite3.Connection:
    path = db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = _connect()
            init_schema(_conn)
        return _conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            tradingview_username TEXT NOT NULL DEFAULT '',
            webhook_secret TEXT NOT NULL,
            tv_connected_at TEXT,
            autonomous_enabled INTEGER NOT NULL DEFAULT 1,
            starting_cash REAL NOT NULL DEFAULT 100000,
            risk_pct_per_trade REAL NOT NULL DEFAULT 10,
            max_portfolio_risk_pct REAL NOT NULL DEFAULT 50,
            scanner_migrated INTEGER NOT NULL DEFAULT 1,
            account_mode TEXT NOT NULL DEFAULT 'paper',
            live_risk_pct_per_trade REAL NOT NULL DEFAULT 1,
            live_max_portfolio_risk_pct REAL NOT NULL DEFAULT 10,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        """
    )
    conn.commit()
    _migrate_users(conn)


def _migrate_users(conn: sqlite3.Connection) -> None:
    """Add columns introduced after initial deploy without breaking old DBs."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "risk_pct_per_trade" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN risk_pct_per_trade REAL NOT NULL DEFAULT 10"
        )
    if "max_portfolio_risk_pct" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN max_portfolio_risk_pct REAL NOT NULL DEFAULT 50"
        )
    if "account_mode" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN account_mode TEXT NOT NULL DEFAULT 'paper'"
        )
    if "live_risk_pct_per_trade" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN live_risk_pct_per_trade REAL NOT NULL DEFAULT 1"
        )
    if "live_max_portfolio_risk_pct" not in cols:
        conn.execute(
            "ALTER TABLE users ADD COLUMN live_max_portfolio_risk_pct REAL NOT NULL DEFAULT 10"
        )
    if "scanner_migrated" not in cols:
        # One-time: the AI brain used to default off and require a manual
        # toggle. The unified Scanner switch should just run once deployed,
        # so flip existing accounts on exactly once; never touch this again
        # afterwards, so a user's own later choice to pause it always sticks.
        conn.execute(
            "ALTER TABLE users ADD COLUMN scanner_migrated INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            "UPDATE users SET autonomous_enabled = 1, scanner_migrated = 1 "
            "WHERE scanner_migrated = 0"
        )
    conn.commit()


@contextmanager
def transaction():
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
