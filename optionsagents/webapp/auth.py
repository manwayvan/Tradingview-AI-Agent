"""User authentication: signup, login, sessions, password hashing."""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Cookie, HTTPException, Request, Response

from optionsagents.webapp.database import get_db, transaction

SESSION_COOKIE = "oa_session"
SESSION_DAYS = int(os.environ.get("OPTIONS_SESSION_DAYS", "30"))
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class User:
    id: str
    email: str
    display_name: str
    tradingview_username: str
    webhook_secret: str
    tv_connected_at: str | None
    autonomous_enabled: bool
    starting_cash: float
    risk_pct_per_trade: float
    max_portfolio_risk_pct: float
    created_at: str

    @classmethod
    def from_row(cls, row) -> User:
        keys = row.keys() if hasattr(row, "keys") else []
        risk_pct = float(row["risk_pct_per_trade"]) if "risk_pct_per_trade" in keys else 10.0
        portfolio_pct = (
            float(row["max_portfolio_risk_pct"]) if "max_portfolio_risk_pct" in keys else 50.0
        )
        return cls(
            id=row["id"],
            email=row["email"],
            display_name=row["display_name"] or "",
            tradingview_username=row["tradingview_username"] or "",
            webhook_secret=row["webhook_secret"],
            tv_connected_at=row["tv_connected_at"],
            autonomous_enabled=bool(row["autonomous_enabled"]),
            starting_cash=float(row["starting_cash"]),
            risk_pct_per_trade=risk_pct,
            max_portfolio_risk_pct=portfolio_pct,
            created_at=row["created_at"],
        )

    def to_public_dict(self, base_url: str = "") -> dict:
        webhook_url = f"{base_url.rstrip('/')}/webhook/tradingview" if base_url else "/webhook/tradingview"
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "tradingview_username": self.tradingview_username,
            "tv_connected": bool(self.tv_connected_at),
            "tv_connected_at": self.tv_connected_at,
            "autonomous_enabled": self.autonomous_enabled,
            "starting_cash": self.starting_cash,
            "risk_pct_per_trade": self.risk_pct_per_trade,
            "max_portfolio_risk_pct": self.max_portfolio_risk_pct,
            "webhook_url": webhook_url,
            "webhook_secret_set": bool(self.webhook_secret),
        }


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest_hex = stored.split("$", 2)
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, AttributeError):
        return False


def _new_secret() -> str:
    return secrets.token_urlsafe(32)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def get_user_by_id(user_id: str) -> User | None:
    row = get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User.from_row(row) if row else None


def get_user_by_email(email: str) -> User | None:
    row = get_db().execute(
        "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)
    ).fetchone()
    return User.from_row(row) if row else None


def get_user_by_webhook_secret(secret: str) -> User | None:
    if not secret:
        return None
    row = get_db().execute("SELECT * FROM users WHERE webhook_secret = ?", (secret,)).fetchone()
    return User.from_row(row) if row else None


def list_users() -> list[User]:
    rows = get_db().execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return [User.from_row(r) for r in rows]


def create_user(email: str, password: str, display_name: str = "") -> User:
    email = email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValueError("invalid email address")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if get_user_by_email(email):
        raise ValueError("email already registered")

    user_id = secrets.token_hex(12)
    created = _iso(_now())
    with transaction() as conn:
        conn.execute(
            """INSERT INTO users
               (id, email, password_hash, display_name, webhook_secret, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, email, _hash_password(password), display_name.strip(), _new_secret(), created),
        )
    user = get_user_by_id(user_id)
    assert user is not None
    return user


def authenticate(email: str, password: str) -> User | None:
    row = get_db().execute(
        "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)
    ).fetchone()
    if not row or not _verify_password(password, row["password_hash"]):
        return None
    return User.from_row(row)


def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(48)
    expires = _iso(_now() + timedelta(days=SESSION_DAYS))
    with transaction() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (token, user_id, expires, _iso(_now())),
        )
    return token


def delete_session(token: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def get_user_for_session(token: str | None) -> User | None:
    if not token:
        return None
    now = _iso(_now())
    row = get_db().execute(
        """SELECT u.* FROM users u
           JOIN sessions s ON s.user_id = u.id
           WHERE s.token = ? AND s.expires_at > ?""",
        (token, now),
    ).fetchone()
    if row is None:
        return None
    # Sliding expiration — keep active users signed in.
    new_expires = _iso(_now() + timedelta(days=SESSION_DAYS))
    with transaction() as conn:
        conn.execute(
            "UPDATE sessions SET expires_at = ? WHERE token = ?",
            (new_expires, token),
        )
    return User.from_row(row)


def _cookie_secure(request: Request | None = None) -> bool:
    """Honor OPTIONS_COOKIE_SECURE, else auto-detect HTTPS behind a proxy."""
    raw = os.environ.get("OPTIONS_COOKIE_SECURE", "").strip().lower()
    if raw in {"1", "true", "yes"}:
        return True
    if raw in {"0", "false", "no"}:
        return False
    if request is not None:
        forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        if forwarded == "https":
            return True
        if request.url.scheme == "https":
            return True
    return False


def set_session_cookie(response: Response, token: str, request: Request | None = None) -> None:
    secure = _cookie_secure(request)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=SESSION_DAYS * 86400,
        path="/",
    )


def clear_session_cookie(response: Response, request: Request | None = None) -> None:
    secure = _cookie_secure(request)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure, samesite="lax")


def update_tradingview(user_id: str, username: str, *, mark_connected: bool = False) -> User:
    with transaction() as conn:
        if mark_connected:
            conn.execute(
                """UPDATE users SET tradingview_username = ?, tv_connected_at = ?
                   WHERE id = ?""",
                (username.strip(), _iso(_now()), user_id),
            )
        else:
            conn.execute(
                "UPDATE users SET tradingview_username = ? WHERE id = ?",
                (username.strip(), user_id),
            )
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError("user not found")
    return user


def regenerate_webhook_secret(user_id: str) -> User:
    secret = _new_secret()
    with transaction() as conn:
        conn.execute(
            "UPDATE users SET webhook_secret = ?, tv_connected_at = NULL WHERE id = ?",
            (secret, user_id),
        )
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError("user not found")
    return user


def set_autonomous_enabled(user_id: str, enabled: bool) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE users SET autonomous_enabled = ? WHERE id = ?",
            (1 if enabled else 0, user_id),
        )


def update_account_settings(
    user_id: str,
    *,
    starting_cash: float | None = None,
    risk_pct_per_trade: float | None = None,
    max_portfolio_risk_pct: float | None = None,
) -> User:
    if risk_pct_per_trade is not None and not (0.5 <= risk_pct_per_trade <= 50):
        raise ValueError("risk_pct_per_trade must be between 0.5 and 50")
    if max_portfolio_risk_pct is not None and not (5 <= max_portfolio_risk_pct <= 100):
        raise ValueError("max_portfolio_risk_pct must be between 5 and 100")
    if starting_cash is not None and starting_cash < 1000:
        raise ValueError("starting_cash must be at least $1,000")

    fields: list[str] = []
    values: list[object] = []
    if starting_cash is not None:
        fields.append("starting_cash = ?")
        values.append(starting_cash)
    if risk_pct_per_trade is not None:
        fields.append("risk_pct_per_trade = ?")
        values.append(risk_pct_per_trade)
    if max_portfolio_risk_pct is not None:
        fields.append("max_portfolio_risk_pct = ?")
        values.append(max_portfolio_risk_pct)
    if not fields:
        raise ValueError("no settings to update")

    values.append(user_id)
    with transaction() as conn:
        conn.execute(
            f"UPDATE users SET {', '.join(fields)} WHERE id = ?",
            values,
        )
    user = get_user_by_id(user_id)
    if user is None:
        raise ValueError("user not found")
    return user


def optional_user(
    oa_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User | None:
    return get_user_for_session(oa_session)


def require_user(
    oa_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User:
    user = get_user_for_session(oa_session)
    if user is None:
        raise HTTPException(status_code=401, detail="sign in required")
    return user
