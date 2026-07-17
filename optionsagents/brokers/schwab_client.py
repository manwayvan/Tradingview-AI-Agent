"""Low-level Schwab Trader API client: OAuth2 token handling + REST calls.

Schwab's Trader API (developer.schwab.com) is the official successor to TD
Ameritrade's API, closely following the same conventions TDA used. This
client isolates every endpoint path and payload shape in one place so it's
easy to check against Schwab's current docs and adjust — API surfaces
shift over time and this was written from documented conventions rather
than a live integration test (this environment has no Schwab credentials
or network path to verify against). Before arming live trading, place one
small manual test order and confirm in the Schwab app that everything
matches before trusting the automatic loop.

Auth flow (one-time, interactive — see scripts/schwab_login.py):
1. User opens the authorize URL, logs into Schwab, grants access.
2. Schwab redirects to our redirect_uri with a ``code`` query param.
3. We exchange that code for an access + refresh token pair.
4. Access tokens are short-lived (~30 min) and refreshed automatically
   here. The refresh token itself expires after about a week of
   inactivity — Schwab requires re-running the interactive login
   periodically. ``needs_reauth`` surfaces that state to the UI instead
   of failing silently mid-cycle.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

AUTH_BASE = "https://api.schwabapi.com/v1/oauth"
API_BASE = "https://api.schwabapi.com/trader/v1"
TOKEN_REFRESH_MARGIN_SECONDS = 60


class SchwabAuthError(RuntimeError):
    """Raised when the stored credentials can't authenticate (needs re-login)."""


class SchwabApiError(RuntimeError):
    """Raised for non-2xx responses from the Schwab API."""

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"Schwab API error {status}: {body[:500]}")


@dataclass
class SchwabCredentials:
    app_key: str
    app_secret: str
    redirect_uri: str

    @classmethod
    def from_env(cls) -> "SchwabCredentials | None":
        key = os.environ.get("SCHWAB_APP_KEY", "").strip()
        secret = os.environ.get("SCHWAB_APP_SECRET", "").strip()
        redirect = os.environ.get("SCHWAB_REDIRECT_URI", "").strip()
        if not (key and secret and redirect):
            return None
        return cls(app_key=key, app_secret=secret, redirect_uri=redirect)


@dataclass
class SchwabTokens:
    access_token: str
    refresh_token: str
    expires_at: float  # unix timestamp
    account_hash: str | None = None  # encrypted account id, once fetched


class SchwabTokenStore:
    """Persists tokens to a per-user JSON file (outside the repo, like the
    webhook secrets and paper account ledgers)."""

    def __init__(self, path: str):
        self.path = path

    def load(self) -> SchwabTokens | None:
        if not os.path.exists(self.path):
            return None
        try:
            with open(self.path) as f:
                data = json.load(f)
            return SchwabTokens(**data)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.warning("could not load Schwab token store %s: %s", self.path, exc)
            return None

    def save(self, tokens: SchwabTokens) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(asdict(tokens), f, indent=2)
        os.replace(tmp, self.path)

    def clear(self) -> None:
        if os.path.exists(self.path):
            os.remove(self.path)


class SchwabClient:
    """Authenticated REST client. Raises SchwabAuthError when the refresh
    token itself has expired and the user must re-run the interactive
    login (scripts/schwab_login.py)."""

    def __init__(
        self,
        credentials: SchwabCredentials,
        token_store: SchwabTokenStore,
        session: requests.Session | None = None,
    ):
        self.credentials = credentials
        self.token_store = token_store
        self.session = session or requests.Session()

    # ---- OAuth -----------------------------------------------------

    def authorize_url(self, state: str = "") -> str:
        params = {
            "response_type": "code",
            "client_id": self.credentials.app_key,
            "redirect_uri": self.credentials.redirect_uri,
        }
        if state:
            params["state"] = state
        return f"{AUTH_BASE}/authorize?{urlencode(params)}"

    def _basic_auth_header(self) -> dict:
        raw = f"{self.credentials.app_key}:{self.credentials.app_secret}".encode()
        return {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}

    def exchange_code(self, code: str) -> SchwabTokens:
        """First-time exchange: authorization code -> access + refresh token."""
        resp = self.session.post(
            f"{AUTH_BASE}/token",
            headers={
                **self._basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.credentials.redirect_uri,
            },
            timeout=15,
        )
        if not resp.ok:
            raise SchwabAuthError(f"token exchange failed: {resp.status_code} {resp.text[:300]}")
        body = resp.json()
        tokens = SchwabTokens(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            expires_at=time.time() + float(body.get("expires_in", 1800)),
        )
        self.token_store.save(tokens)
        return tokens

    def _refresh(self, tokens: SchwabTokens) -> SchwabTokens:
        resp = self.session.post(
            f"{AUTH_BASE}/token",
            headers={
                **self._basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": tokens.refresh_token},
            timeout=15,
        )
        if not resp.ok:
            raise SchwabAuthError(
                "Schwab refresh token expired or revoked — reconnect via "
                "scripts/schwab_login.py"
            )
        body = resp.json()
        new_tokens = SchwabTokens(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token", tokens.refresh_token),
            expires_at=time.time() + float(body.get("expires_in", 1800)),
            account_hash=tokens.account_hash,
        )
        self.token_store.save(new_tokens)
        return new_tokens

    def _valid_tokens(self) -> SchwabTokens:
        tokens = self.token_store.load()
        if tokens is None:
            raise SchwabAuthError("Schwab is not connected — run scripts/schwab_login.py")
        if time.time() >= tokens.expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
            tokens = self._refresh(tokens)
        return tokens

    def is_connected(self) -> bool:
        try:
            self._valid_tokens()
            return True
        except SchwabAuthError:
            return False

    # ---- REST --------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        tokens = self._valid_tokens()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {tokens.access_token}"
        resp = self.session.request(
            method, f"{API_BASE}{path}", headers=headers, timeout=15, **kwargs
        )
        if resp.status_code == 401:
            # Access token rejected mid-use (clock skew, revoked) — refresh once and retry.
            tokens = self._refresh(tokens)
            headers["Authorization"] = f"Bearer {tokens.access_token}"
            resp = self.session.request(
                method, f"{API_BASE}{path}", headers=headers, timeout=15, **kwargs
            )
        if not resp.ok:
            raise SchwabApiError(resp.status_code, resp.text)
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    def account_hash(self) -> str:
        """The encrypted account identifier Schwab requires on every
        account-scoped call (never the raw account number)."""
        tokens = self.token_store.load()
        if tokens and tokens.account_hash:
            return tokens.account_hash
        accounts = self._request("GET", "/accounts/accountNumbers")
        if not accounts:
            raise SchwabApiError(404, "no linked Schwab accounts")
        account_hash = accounts[0]["hashValue"]
        tokens = self._valid_tokens()
        tokens.account_hash = account_hash
        self.token_store.save(tokens)
        return account_hash

    def get_account(self) -> dict:
        return self._request(
            "GET", f"/accounts/{self.account_hash()}", params={"fields": "positions"}
        )

    def place_order(self, payload: dict) -> str:
        """Submit an order; returns the Schwab order id (parsed from the
        Location response header, per Schwab's create-order convention)."""
        tokens = self._valid_tokens()
        resp = self.session.post(
            f"{API_BASE}/accounts/{self.account_hash()}/orders",
            headers={
                "Authorization": f"Bearer {tokens.access_token}",
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
            timeout=15,
        )
        if not resp.ok:
            raise SchwabApiError(resp.status_code, resp.text)
        location = resp.headers.get("Location", "")
        order_id = location.rstrip("/").rsplit("/", 1)[-1] if location else ""
        if not order_id:
            raise SchwabApiError(resp.status_code, "order placed but no order id returned")
        return order_id

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"/accounts/{self.account_hash()}/orders/{order_id}")

    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"/accounts/{self.account_hash()}/orders/{order_id}")
