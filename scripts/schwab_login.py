"""One-time interactive Schwab OAuth login — links a real Schwab account to
one of this app's users so it can place live orders through the Trader API.

Why interactive: Schwab's OAuth flow requires you to log into schwab.com
in a real browser and approve access; that consent step can't be automated
or done on your behalf. Run this once (and again whenever the refresh
token expires, roughly every ~7 days of inactivity — the app's Account tab
will show "Schwab: not connected" when that happens).

Setup, one time only:
1. Register a developer app at https://developer.schwab.com and request
   the Trader API (individual/retail trading, not just market data).
   Approval can take a few days on Schwab's end.
2. Set your app's redirect URI to something reachable from your browser —
   for local use, Schwab allows "https://127.0.0.1" (no port needed, since
   you'll copy/paste the redirected URL by hand rather than running a
   local callback server).
3. Export three environment variables before running this script:
     export SCHWAB_APP_KEY=...       # "Client ID" / consumer key from the app
     export SCHWAB_APP_SECRET=...    # "Client Secret"
     export SCHWAB_REDIRECT_URI=https://127.0.0.1
   Also set these on the server (Railway/Render/etc service variables) —
   the running app needs them too to refresh tokens and place orders.

Usage:
    python scripts/schwab_login.py --email you@example.com

Set OPTIONS_DATA_DIR / OPTIONS_APP_DB first if the server uses a
non-default data directory (e.g. a mounted volume in production) — this
script must write the token file to the exact path the running server
will read it from.
"""

from __future__ import annotations

import argparse
import sys
from urllib.parse import parse_qs, urlparse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", required=True, help="the app account to link Schwab to")
    args = parser.parse_args()

    from optionsagents.brokers.schwab_client import (
        SchwabClient,
        SchwabCredentials,
        SchwabTokenStore,
    )
    from optionsagents.webapp.auth import get_user_by_email
    from optionsagents.webapp.database import get_db
    from optionsagents.webapp.workspaces import schwab_token_path

    get_db()
    user = get_user_by_email(args.email)
    if user is None:
        print(f"No app account found for {args.email!r}. Sign up in the web app first.", file=sys.stderr)
        return 1

    creds = SchwabCredentials.from_env()
    if creds is None:
        print(
            "SCHWAB_APP_KEY / SCHWAB_APP_SECRET / SCHWAB_REDIRECT_URI are not all set "
            "in this shell's environment. Export them and try again.",
            file=sys.stderr,
        )
        return 1

    token_file = schwab_token_path(user.id)
    client = SchwabClient(creds, SchwabTokenStore(token_file))

    print()
    print("1. Open this URL in a browser, log into Schwab, and approve access:")
    print()
    print(f"   {client.authorize_url()}")
    print()
    print("2. Schwab will redirect you to your redirect URI with a 'code' in the URL,")
    print("   e.g. https://127.0.0.1/?code=C0.xxxxx&session=...")
    print("   The page itself may not load (that's fine) — copy the full URL from the")
    print("   address bar.")
    print()
    redirected = input("Paste the full redirected URL here: ").strip()

    code = _extract_code(redirected)
    if not code:
        print("Could not find a 'code' parameter in that URL.", file=sys.stderr)
        return 1

    client.exchange_code(code)
    print(f"\nSaved Schwab tokens to {token_file}")

    try:
        account = client.get_account()
        balances = account.get("securitiesAccount", {}).get("currentBalances", {})
        print("Connected. Current account snapshot:")
        print(f"  cash:   ${balances.get('cashBalance', '?')}")
        print(f"  equity: ${balances.get('liquidationValue', '?')}")
    except Exception as exc:  # noqa: BLE001 — best-effort confirmation only
        print(f"Tokens saved, but the confirmation account lookup failed: {exc}")
        print("Double-check SCHWAB_APP_KEY/SECRET/REDIRECT_URI and your app's approval status.")
        return 1

    print(
        "\nDone. In the app's Account tab, switch to Live trading whenever you're ready — "
        "it stays on Paper until you do."
    )
    return 0


def _extract_code(redirected_url: str) -> str | None:
    parsed = urlparse(redirected_url)
    qs = parse_qs(parsed.query)
    values = qs.get("code")
    return values[0] if values else None


if __name__ == "__main__":
    raise SystemExit(main())
